"""OpenTelemetry spans emitted directly from Bub interception hooks."""

from __future__ import annotations

from typing import Any

from bub import hookimpl
from bub.hooks.interception import LlmCallRequest, LlmCallResult
from bub.turn import TurnState
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode


class BubTracingPlugin:
    """Record Bub model calls in the evaluator-owned tracer provider."""

    def __init__(self, framework: Any) -> None:
        del framework
        self.tracer = trace.get_tracer("powercontext.e2e.bub")
        self.active_model_spans: dict[str, Span] = {}

    @hookimpl
    def before_llm_call(self, request: LlmCallRequest, state: TurnState) -> None:
        del state
        span = self.tracer.start_span("bub.model")
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", request.model)
        span.set_attribute("bub.run_id", request.run_id)
        span.set_attribute("bub.tool_names", request.tool_names)
        self.active_model_spans[request.run_id] = span

    @hookimpl
    def after_llm_call(self, request: LlmCallRequest, result: LlmCallResult, state: TurnState) -> None:
        del request, state
        span = self.active_model_spans.pop(result.run_id, None)
        if span is None:
            return

        if result.text:
            span.set_attribute("output.value", result.text)
        if result.error is not None:
            span.record_exception(result.error)
            span.set_status(Status(StatusCode.ERROR, str(result.error)))
        _record_usage(span, result.usage)
        span.end()


def _record_usage(span: Span, usage: dict[str, Any] | None) -> None:
    if usage is None:
        return
    for source, target in (
        ("prompt_tokens", "gen_ai.usage.input_tokens"),
        ("input_tokens", "gen_ai.usage.input_tokens"),
        ("completion_tokens", "gen_ai.usage.output_tokens"),
        ("output_tokens", "gen_ai.usage.output_tokens"),
    ):
        value = usage.get(source)
        if isinstance(value, int) and not isinstance(value, bool):
            span.set_attribute(target, value)
