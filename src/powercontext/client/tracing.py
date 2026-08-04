"""Failure-isolated OpenTelemetry spans for PowerContext Client calls."""

from __future__ import annotations

from contextlib import suppress

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.context import Context, Token
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, set_span_in_context

_INSTRUMENTATION_NAME = "powercontext.client"


class ClientSpan:
    def __init__(self, span: Span | None, token: Token[Context] | None) -> None:
        self.span = span
        self.token = token
        self.finished = False

    @classmethod
    def start(cls, operation: str) -> ClientSpan:
        span: Span | None = None
        try:
            span = trace.get_tracer(_INSTRUMENTATION_NAME).start_span(
                f"PowerContextClient {operation}",
                kind=SpanKind.CLIENT,
                attributes={
                    "powercontext.operation.name": operation,
                    "powercontext.operation.unit": "dependency",
                },
            )
            token = otel_context.attach(set_span_in_context(span))
        except Exception:
            if span is not None:
                with suppress(Exception):
                    span.end()
            return cls(None, None)
        return cls(span, token)

    def inject(self, headers: dict[str, str]) -> None:
        with suppress(Exception):
            propagate.inject(headers)

    def finish(
        self,
        outcome: str,
        *,
        status_code: int | None = None,
        error: BaseException | None = None,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        if self.span is not None:
            with suppress(Exception):
                self.span.set_attribute("powercontext.operation.outcome", outcome)
                if status_code is not None:
                    self.span.set_attribute("http.response.status_code", status_code)
                if error is not None:
                    self.span.set_attribute("error.type", type(error).__qualname__)
                if outcome == "failure":
                    self.span.set_status(Status(StatusCode.ERROR))
        if self.token is not None:
            with suppress(Exception):
                otel_context.detach(self.token)
        if self.span is not None:
            with suppress(Exception):
                self.span.end()


__all__ = ["ClientSpan"]
