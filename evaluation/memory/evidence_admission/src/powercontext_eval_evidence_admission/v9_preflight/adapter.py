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

"""Real SDK request construction with injected transport and private, content-free audit summaries."""

from __future__ import annotations

import asyncio
import contextvars
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import httpx
from openai import AsyncOpenAI
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.inference import GenerationResult, InferenceUsage, InvalidInferenceOutputError
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from ..manifests import EvaluationError
from ..models import canonical, digest
from ..v6.audit import (
    _CURRENT,
    AttemptJournal,
    AuditTransport,
    ResponseCaptureModel,
    _write_new,
    audit_summary,
    classify,
)
from ..v9.codec import INSTRUCTIONS, Request, TemplateOutput, render, restore


@dataclass(frozen=True)
class Config:
    model: str
    base_url: str
    max_output_tokens: int = 2048
    timeout_seconds: float = 60

    def __post_init__(self):
        url = urlsplit(self.base_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or not self.model.strip()
            or self.max_output_tokens != 2048
            or self.timeout_seconds <= 0
        ):
            raise EvaluationError("invalid_preflight_configuration")


def normalized_schema(value):
    """Ignore SDK schema annotations, retaining validation constraints and property names."""
    if isinstance(value, list):
        return [normalized_schema(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if key in {"title", "description", "discriminator"}:
            continue
        if key in {"properties", "$defs"}:
            result[key] = {name: normalized_schema(schema) for name, schema in child.items()}
        else:
            result[key] = normalized_schema(child)
    return result


def request_facts(body: dict, payload: dict, instructions: str, output_type, config: Config):
    messages = body.get("messages", [])
    if [m.get("role") for m in messages] != ["system", "system", "user"]:
        raise EvaluationError("sdk_message_contract_changed")
    if messages[0].get("content") != instructions or json.loads(messages[2]["content"]) != payload:
        raise EvaluationError("sdk_payload_or_instructions_changed")
    schema_message = messages[1]["content"]
    try:
        actual_schema, _ = json.JSONDecoder().raw_decode(schema_message[schema_message.index("{") :])
    except (ValueError, TypeError):
        raise EvaluationError("sdk_output_schema_missing") from None
    if normalized_schema(actual_schema) != normalized_schema(output_type.model_json_schema()):
        raise EvaluationError("sdk_output_schema_changed")
    if (
        body.get("model") != config.model
        or body.get("stream") is not False
        or body.get("max_tokens") != config.max_output_tokens
        or "max_completion_tokens" in body
        or body.get("response_format") != {"type": "json_object"}
        or body.get("temperature") != 0
        or set(body) - {"model", "stream", "messages", "max_tokens", "response_format", "temperature"}
    ):
        raise EvaluationError("sdk_provider_settings_changed")
    from powercontext.builtin.inference import character_token_estimator

    estimator = character_token_estimator()
    return {
        "http_request_hash": digest(body),
        "http_request_bytes": len(canonical(body)),
        "user_payload_hash": digest(payload),
        "instructions_hash": digest(instructions),
        "sdk_output_schema_hash": digest(actual_schema),
        "schema_validation_constraints_preserved": True,
        "message_roles": [m["role"] for m in messages],
        "model_visible_text_estimated_tokens": sum(estimator.estimate(m["content"]) for m in messages),
        "http_json_estimated_tokens": estimator.estimate(canonical(body).decode()),
        "requested_model": config.model,
        "max_tokens": config.max_output_tokens,
        "provider_input_tokens": None,
        "provider_output_tokens": None,
    }


class Journal(AttemptJournal):
    def response(self, attempt, body, status):
        super().response(attempt, body, status)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            return
        if not isinstance(payload, dict):
            return
        fingerprint = payload.get("system_fingerprint")
        attempt.facts["system_fingerprint_hash"] = digest(fingerprint) if isinstance(fingerprint, str) else None
        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            choice = choices[0]
            finish = choice.get("finish_reason")
            attempt.facts["provider_finish_reason"] = (
                finish
                if finish
                in {"stop", "length", "tool_calls", "content_filter", "insufficient_system_resource", "aborted"}
                else "unknown"
            )
            message = choice.get("message")
            if isinstance(message, dict):
                attempt.facts["tool_calls_present"] = bool(message.get("tool_calls"))
                attempt.facts["refusal_present"] = bool(message.get("refusal"))
                attempt.facts["empty_content"] = (
                    not isinstance(message.get("content"), str) or not message["content"].strip()
                )
        attempt.event(
            "provider_metadata",
            **{
                k: attempt.facts.get(k)
                for k in (
                    "provider_finish_reason",
                    "system_fingerprint_hash",
                    "tool_calls_present",
                    "refusal_present",
                    "empty_content",
                )
            },
        )


@dataclass(frozen=True)
class ExpectedRequest:
    payload: dict
    instructions: str
    output_type: type
    config: Config


_EXPECTED: contextvars.ContextVar[ExpectedRequest | None] = contextvars.ContextVar("sdk_expected_request", default=None)


class Transport(AuditTransport):
    async def handle_async_request(self, request):
        attempt = _CURRENT.get()
        if attempt is None:
            raise EvaluationError("transport_without_audit_attempt")
        if attempt.facts.get("http_requests", 0):
            raise EvaluationError("preflight_request_limit_exceeded")
        expected = _EXPECTED.get()
        if expected is None:
            raise EvaluationError("missing_expected_request")
        if request.method != "POST" or str(request.url) != expected.config.base_url.rstrip("/") + "/chat/completions":
            raise EvaluationError("sdk_endpoint_changed")
        body = json.loads(await request.aread())
        facts = request_facts(body, expected.payload, expected.instructions, expected.output_type, expected.config)
        attempt.facts.update(http_requests=1, **facts)
        _write_new(attempt.journal.directory / f"{attempt.attempt_id}.request.json", canonical(body))
        attempt.event("http_request", **facts)
        return await super().handle_async_request(request)


class QualityFailure(EvaluationError):
    """An explicit post-render review failed; protocol validity does not imply semantic quality."""

    def __init__(self, category: Literal["semantic_quality_error", "atomicity_error"]):
        self.category = category
        super().__init__(category)


def failure(error, facts):
    if isinstance(error, asyncio.CancelledError):
        return "cancelled", "cancelled"
    if facts.get("http_status", 0) >= 400:
        return "provider", "provider_http_error"
    if facts.get("output_tokens", 0) is not None and facts.get("output_tokens", 0) > 2048:
        return "usage", "output_usage_limit_exceeded"
    if facts.get("response_capture_complete") is False:
        return "audit", "response_capture_incomplete"
    finish = facts.get("provider_finish_reason")
    if finish in {"aborted", "insufficient_system_resource"}:
        return "response", "provider_aborted" if finish == "aborted" else "provider_resource_interrupted"
    if facts.get("refusal_present"):
        return "response", "provider_refusal"
    if facts.get("tool_calls_present"):
        return "response", "unexpected_tool_calls"
    if finish == "stop" and facts.get("empty_content"):
        return "parse", "empty_content"
    if isinstance(error, QualityFailure):
        return "quality", error.category
    if isinstance(error, EvaluationError):
        code = str(error)
        if code.startswith("sdk_") or code in {"preflight_request_limit_exceeded", "missing_expected_request"}:
            return "request", code
        if code.startswith("v9_") or code in {
            "invalid_evidence_reference",
            "copy_without_candidate_support",
            "copy_span_out_of_bounds_or_too_large",
            "copy_splits_utf8",
            "copy_splits_protected_sequence",
            "rendered_candidate_too_large",
            "quote_request_binding_mismatch",
        }:
            return "protocol", code
    cause = error
    while cause is not None:
        if isinstance(cause, EvaluationError) and (
            str(cause).startswith("sdk_") or str(cause) == "preflight_request_limit_exceeded"
        ):
            return "request", str(cause)
        if isinstance(cause, httpx.RequestError) and not isinstance(cause, httpx.TimeoutException):
            return "transport", "transport_error"
        cause = cause.__cause__
    return classify(error, facts)


class Adapter:
    def __init__(self, v5_generator, v9_generator, journal, config):
        self.generators = {"v5": v5_generator, "v9": v9_generator}
        self.journal, self.config = journal, config

    async def generate(
        self, value, /, *, protocol, sample_id, call_index, instructions, request_hash=None, review=None
    ):
        if protocol == "v9":
            if not isinstance(value, Request):
                raise EvaluationError("v9_request_required")
            payload, output_type = value.payload, TemplateOutput
            if not isinstance(request_hash, str):
                raise EvaluationError("v9_origin_binding_required")
            input_hash = request_hash
            if instructions != INSTRUCTIONS:
                raise EvaluationError("v9_instructions_must_be_frozen")
        elif protocol == "v5" and isinstance(value, AtomicMemoryExtractionInput):
            payload, output_type = value.model_dump(mode="json"), AtomicMemoryExtractionOutput
            input_hash = digest(payload)
        else:
            raise EvaluationError("unsupported_preflight_protocol")
        attempt = self.journal.begin(
            digest({"sample_id": sample_id, "protocol": protocol, "call_index": call_index}), input_hash
        )
        attempt.output_type = output_type
        expected = ExpectedRequest(payload, instructions, output_type, self.config)
        attempt.facts.update(protocol=protocol, call_index=call_index, quality_review="not_run")
        token = _CURRENT.set(attempt)
        expected_token = _EXPECTED.set(expected)
        try:
            if protocol == "v9":
                restore(value, payload, request_hash=input_hash)
            result = await self.generators[protocol].generate(payload if protocol == "v9" else value)
            attempt.facts["structured_output_validated"] = True
            if attempt.facts.get("response_capture_complete") is not True:
                raise EvaluationError("response_capture_incomplete")
            if attempt.facts.get("output_tokens", 0) is not None and attempt.facts.get("output_tokens", 0) > 2048:
                raise EvaluationError("output_usage_limit_exceeded")
            output = render(result.output, value, request_hash=input_hash) if protocol == "v9" else result.output
            supplied = {e.evidence_id for e in (value.value.evidence if protocol == "v9" else value.evidence)}
            for candidate in output.candidates:
                AtomicMemoryContent(kind=candidate.kind, text=candidate.text)
                if not set(candidate.evidence_ids) <= supplied:
                    raise InvalidInferenceOutputError(
                        "atomic-memory-extract", "candidate cites unavailable Source evidence"
                    )
            attempt.facts["protocol_output_validated"] = True
            if review is not None:
                review(output)
                attempt.facts["quality_review"] = "passed_explicit_review"
            known = attempt.facts.get("usage_known", False)
            usage = InferenceUsage(
                requests=1,
                input_tokens=attempt.facts.get("input_tokens") if known else None,
                output_tokens=attempt.facts.get("output_tokens") if known else None,
            )
        except asyncio.CancelledError as error:
            attempt.facts["failure_stage"], category = failure(error, attempt.facts)
            try:
                attempt.finish("cancelled", category)
            except Exception:
                # Preserve cancellation; missing durable terminal remains an audit failure in the summary.
                pass
            raise
        except Exception as error:
            attempt.facts["failure_stage"], category = failure(error, attempt.facts)
            attempt.finish("failed", category)
            raise
        else:
            attempt.finish("success", None)
            return GenerationResult(output=output, usage=usage)
        finally:
            _EXPECTED.reset(expected_token)
            _CURRENT.reset(token)


@asynccontextmanager
async def open_adapter(config, journal, transport, *, v5_instructions, api_key):
    """Caller injects transport and key; this function never reads credential configuration."""
    async with httpx.AsyncClient(
        transport=Transport(transport), follow_redirects=False, timeout=config.timeout_seconds
    ) as client:
        async with AsyncOpenAI(
            api_key=api_key, base_url=config.base_url, http_client=cast(Any, client), max_retries=0
        ) as sdk:
            profile = OpenAIModelProfile(openai_chat_supports_max_completion_tokens=False)
            model = ResponseCaptureModel(
                OpenAIChatModel(config.model, provider=OpenAIProvider(openai_client=sdk), profile=profile)
            )
            limits = InferenceLimits(
                max_requests=1,
                max_output_tokens_per_request=2048,
                output_tokens_limit=2048,
                timeout_seconds=config.timeout_seconds,
                allow_continuations=False,
            )
            generators = []
            for instructions, input_type, output_type in (
                (v5_instructions, AtomicMemoryExtractionInput, AtomicMemoryExtractionOutput),
                (INSTRUCTIONS, dict, TemplateOutput),
            ):
                generators.append(
                    PydanticAIStructuredGenerator(
                        model=model,
                        instructions=instructions,
                        input_type=input_type,
                        output_type=output_type,
                        limits=limits,
                        model_settings={"temperature": 0},
                        name="evidence_admission_sdk_preflight",
                    )
                )
            yield Adapter(generators[0], generators[1], journal, config)


def summary(directory):
    result = audit_summary(directory)
    rows = [json.loads(line) for line in (directory / "attempts.jsonl").read_bytes().splitlines()]
    for row in rows:
        if row["kind"] == "http_request":
            raw = (directory / f"{row['attempt_id']}.request.json").read_bytes()
            if digest(json.loads(raw)) != row["http_request_hash"] or len(raw) != row["http_request_bytes"]:
                raise EvaluationError("audit_request_integrity_mismatch")
    starts = [r for r in rows if r["kind"] == "start"]
    if len(starts) != len({r["attempt_id"] for r in starts}):
        raise EvaluationError("duplicate_attempt_start")
    for start in starts:
        events = [r for r in rows if r["attempt_id"] == start["attempt_id"]]
        requests = [r for r in events if r["kind"] == "http_request"]
        responses = [r for r in events if r["kind"] == "http_response"]
        terminals = [r for r in events if r["kind"] == "terminal"]
        if len(requests) > 1 or len(responses) > len(requests):
            raise EvaluationError("attempt_http_count_mismatch")
        if terminals and terminals[0].get("http_requests", 0) != len(requests):
            raise EvaluationError("attempt_http_count_mismatch")
        if responses and terminals:
            for key in (
                "response_hash",
                "captured_response_hash",
                "response_bytes",
                "usage_known",
                "input_tokens",
                "output_tokens",
            ):
                if responses[0].get(key) != terminals[0].get(key):
                    raise EvaluationError("attempt_response_terminal_mismatch")
        if (
            responses
            and responses[0]["response_capture_complete"]
            and responses[0]["response_hash"] != responses[0]["captured_response_hash"]
        ):
            raise EvaluationError("audit_response_integrity_mismatch")
    result["known_usage_subtotal_input_tokens"] = result.pop("known_input_tokens")
    result["known_usage_subtotal_output_tokens"] = result.pop("known_output_tokens")
    result["input_tokens_total"] = result["known_usage_subtotal_input_tokens"] if result["usage_complete"] else None
    result["output_tokens_total"] = result["known_usage_subtotal_output_tokens"] if result["usage_complete"] else None
    result["http_requests"] = sum(r["kind"] == "http_request" for r in rows)
    return result
