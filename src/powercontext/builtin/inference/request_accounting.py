# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Budget admission at the serialized SDK request boundary.

HTTP request hooks see provider templates, schemas and retry history after
assembly. Model-parameter snapshots are used only for selection and local tests.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx2
from anthropic import AnthropicError
from openai import OpenAIError
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.instrumented import InstrumentedModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.profiles import DEFAULT_PROMPTED_OUTPUT_TEMPLATE
from pydantic_ai.settings import ModelSettings
from typing_extensions import override

from powercontext.builtin.inference.budget import (
    AdmissionDecision,
    AdmissionPhase,
    EffectiveRequestBudget,
    RequestEstimate,
    admit_request,
    raise_if_rejected,
)
from powercontext.builtin.inference.errors import InferenceBudgetExceededError, InferenceConfigurationError
from powercontext.builtin.inference.model_payload import PrivacyProtocol, contains_opaque_content
from powercontext.builtin.inference.tokens import TokenEstimator, resolve_token_estimator

#: Request parameters that end up in the provider body.
_SENT_PARAMETER_FIELDS = (
    "function_tools",
    "native_tools",
    "output_mode",
    "output_object",
    "output_tools",
    "prompted_output_template",
    "allow_text_output",
    "allow_image_output",
    "instruction_parts",
    "thinking",
)

#: Model settings that add body content. Transport knobs such as timeouts and
#: headers are excluded: they never become prompt tokens.
_SENT_SETTING_FIELDS = (
    "extra_body",
    "thinking",
    "citations",
    "cache",
    "stop_sequences",
    "tool_choice",
    "parallel_tool_calls",
    "max_tokens",
    "temperature",
    "top_p",
    "top_k",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "service_tier",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _parameters_snapshot(parameters: ModelRequestParameters) -> dict[str, Any]:
    snapshot = {name: getattr(parameters, name, None) for name in _SENT_PARAMETER_FIELDS}
    if parameters.output_mode == "prompted" and not parameters.prompted_output_template:
        # The provider fills an unset template with its documented default text.
        snapshot["prompted_output_template"] = DEFAULT_PROMPTED_OUTPUT_TEMPLATE
    return snapshot


def _settings_snapshot(model_settings: ModelSettings | None) -> dict[str, Any]:
    if not model_settings:
        return {}
    return {name: model_settings[name] for name in _SENT_SETTING_FIELDS if name in model_settings}


def render_outbound_request(
    messages: Sequence[ModelMessage],
    model_settings: ModelSettings | None,
    parameters: ModelRequestParameters,
) -> str:
    """Render a model-parameter snapshot for selection and in-process tests."""

    return "\n".join((
        _canonical(ModelMessagesTypeAdapter.dump_python(list(messages), mode="json")),
        _canonical(_parameters_snapshot(parameters)),
        _canonical(_settings_snapshot(model_settings)),
    ))


def estimate_outbound_request(
    messages: Sequence[ModelMessage],
    model_settings: ModelSettings | None,
    parameters: ModelRequestParameters,
    *,
    estimator: TokenEstimator,
) -> RequestEstimate:
    """Estimate a snapshot; only the serialized HTTP body admits an SDK send."""

    return RequestEstimate(
        estimated_input_tokens=estimator.estimate(render_outbound_request(messages, model_settings, parameters)),
        estimator=estimator.profile,
        scope="selection",
    )


@dataclass(slots=True)
class _RequestExchange:
    """One generation exchange, which may contain several provider requests."""

    budget: EffectiveRequestBudget
    operation: str
    stage: str | None
    estimator: TokenEstimator
    attempts: int = 0
    sent_requests: int = 0
    phase: AdmissionPhase = "initial_dispatch"
    decisions: list[AdmissionDecision] = field(default_factory=list)


_CURRENT_EXCHANGE: ContextVar[_RequestExchange | None] = ContextVar(
    "powercontext_inference_request_exchange", default=None
)


@contextmanager
def budgeted_exchange(
    budget: EffectiveRequestBudget,
    *,
    operation: str,
    stage: str | None = None,
) -> Iterator[_RequestExchange]:
    """Bind one exchange so every request it sends is admitted first."""

    exchange = _RequestExchange(
        budget=budget,
        operation=operation,
        stage=stage,
        estimator=resolve_token_estimator(budget.estimator),
    )
    token = _CURRENT_EXCHANGE.set(exchange)
    try:
        yield exchange
    finally:
        _CURRENT_EXCHANGE.reset(token)


def current_exchange() -> _RequestExchange | None:
    """Return the exchange bound to the current task, if any."""

    return _CURRENT_EXCHANGE.get()


# Count extension fields conservatively; provider metadata and generation
# controls do not occupy the model's input.
_REQUEST_CONTROLS = frozenset({
    "model",
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
    "stream",
    "stream_options",
    "temperature",
    "top_p",
    "top_k",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "logprobs",
    "top_logprobs",
    "logit_bias",
    "service_tier",
    "store",
    "metadata",
    "user",
    "prompt_cache_key",
    "prompt_cache_retention",
    "safety_identifier",
    "parallel_tool_calls",
})
_OUTPUT_FIELDS = ("max_tokens", "max_completion_tokens", "max_output_tokens")


def estimate_http_request(body: dict[str, Any], *, estimator: TokenEstimator) -> RequestEstimate:
    """Count the serialized input, after provider templates and schema transforms."""

    carrier = {key: body[key] for key in ("messages", "input", "system") if key in body}
    protocol: PrivacyProtocol | None = "openai-responses" if "input" in body else None
    if (
        body.get("previous_response_id")
        or body.get("conversation")
        or contains_opaque_content(carrier, protocol=protocol)
    ):
        raise InferenceConfigurationError("budget-inline-context-required")
    content = {key: value for key, value in body.items() if key not in _REQUEST_CONTROLS}
    return RequestEstimate(
        estimated_input_tokens=estimator.estimate(_canonical(content)),
        estimator=estimator.profile,
        scope="complete_request",
    )


def _require_admission(exchange: _RequestExchange, estimate: RequestEstimate) -> None:
    decision = admit_request(
        exchange.budget, estimate, operation=exchange.operation, stage=exchange.stage, phase=exchange.phase
    )
    exchange.decisions.append(decision)
    try:
        raise_if_rejected(decision)
    except InferenceBudgetExceededError as error:
        error.requests_sent = exchange.sent_requests
        raise


class _AdmissionRejected(OpenAIError, AnthropicError):
    """Carry a local rejection through SDKs without connection-error retries."""


async def admit_http_request(request: httpx2.Request) -> None:
    """Check the SDK body and preserve provider retries for actual upstream failures."""

    try:
        await _admit_http_request(request)
    except (InferenceBudgetExceededError, InferenceConfigurationError) as error:
        # Both SDKs propagate their own base errors without treating them as a
        # connection failure. The inference adapter restores the original cause.
        raise _AdmissionRejected("budget-admission-rejected") from error


async def _admit_http_request(request: httpx2.Request) -> None:
    """Check the final SDK body before its HTTP client enters the transport."""

    exchange = _CURRENT_EXCHANGE.get()
    if exchange is None:
        return
    if request.method != "POST":
        raise InferenceConfigurationError("budget-request-method-unsupported")
    raw = await request.aread()
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise InferenceConfigurationError("budget-request-body-unsupported") from error
    if not isinstance(body, dict):
        raise InferenceConfigurationError("budget-request-body-unsupported")
    outputs = [body[key] for key in _OUTPUT_FIELDS if key in body]
    if not outputs or any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in outputs):
        raise InferenceConfigurationError("budget-output-limit-required")
    output = max(outputs)
    ceiling = min(
        limit
        for limit in (exchange.budget.output_reserve_tokens, exchange.budget.output_tokens_limit)
        if limit is not None
    )
    if output > ceiling:
        decision = AdmissionDecision(
            allowed=False,
            operation=exchange.operation,
            stage=exchange.stage,
            dimension="output_tokens",
            estimated_value=output,
            limit_value=ceiling,
            output_reserve_tokens=exchange.budget.output_reserve_tokens,
            safety_margin_tokens=exchange.budget.safety_margin_tokens,
            estimator_id=exchange.estimator.profile.estimator_id,
            estimator_version=exchange.estimator.profile.version,
            phase=exchange.phase,
        )
        error = InferenceBudgetExceededError(decision)
        error.requests_sent = exchange.sent_requests
        raise error
    _require_admission(exchange, estimate_http_request(body, estimator=exchange.estimator))
    exchange.sent_requests += 1


class BudgetedModel(WrapperModel):
    """Bind request identity and check the final SDK body with a task-local hook.

    In-process test models have no wire format and use their prepared model
    parameters. External models must expose the complete HTTP request.
    """

    def __init__(self, wrapped: Model) -> None:
        super().__init__(wrapped)
        provider = self.wrapped.provider
        sdk = None if provider is None else provider.client
        client = getattr(sdk, "_client", None)
        self._http_accounting = isinstance(client, httpx2.AsyncClient)
        if isinstance(client, httpx2.AsyncClient):
            if admit_http_request not in client.event_hooks["request"]:
                client.event_hooks["request"].append(admit_http_request)
        elif self.wrapped.system not in {"test", "function"}:
            raise InferenceConfigurationError("budget-accounting-unsupported")

    @classmethod
    def wrap(cls, model: Model) -> Model:
        """Keep explicit instrumentation visible to the Agent after wrapping."""

        if isinstance(model, InstrumentedModel):
            return InstrumentedModel(cls(model.wrapped), model.instrumentation_settings)
        return cls(model)

    def _admit(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> None:
        exchange = _CURRENT_EXCHANGE.get()
        if exchange is None:
            return
        continuation = any(isinstance(message, ModelResponse) and message.state == "suspended" for message in messages)
        exchange.phase = (
            "continuation_dispatch"
            if continuation
            else "initial_dispatch"
            if exchange.attempts == 0
            else "retry_dispatch"
        )
        exchange.attempts += 1
        if not self._http_accounting:
            settings, parameters = self.wrapped.prepare_request(model_settings, model_request_parameters)
            estimate = estimate_outbound_request(messages, settings, parameters, estimator=exchange.estimator)
            _require_admission(exchange, estimate.model_copy(update={"scope": "complete_request"}))
            exchange.sent_requests += 1

    @override
    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self._admit(messages, model_settings, model_request_parameters)
        return await self.wrapped.request(messages, model_settings, model_request_parameters)

    @override
    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        self._admit(messages, model_settings, model_request_parameters)
        async with self.wrapped.request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as response_stream:
            yield response_stream


__all__ = [
    "BudgetedModel",
    "budgeted_exchange",
    "current_exchange",
    "estimate_http_request",
    "estimate_outbound_request",
    "render_outbound_request",
]
