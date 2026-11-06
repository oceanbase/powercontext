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

"""Failure-isolated OpenTelemetry spans for PowerContext Client calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.context import Context, Token
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, set_span_in_context

_INSTRUMENTATION_NAME = "powercontext.client"
_GENAI_CLIENT = ContextVar("powercontext_genai_client", default=False)


@contextmanager
def genai_client_context() -> Iterator[None]:
    """Mark adapter-owned calls as GenAI context-service dependencies.

    This scopes semantic attributes only; it never configures providers,
    exporters or consumer-specific filters, and does not record content.
    """
    token = _GENAI_CLIENT.set(True)
    try:
        yield
    finally:
        _GENAI_CLIENT.reset(token)


class ClientSpan:
    def __init__(self, span: Span | None, token: Token[Context] | None) -> None:
        self.span = span
        self.token = token
        self.finished = False

    @classmethod
    def start(cls, operation: str) -> ClientSpan:
        span: Span | None = None
        try:
            attributes = {
                "powercontext.operation.name": operation,
                "powercontext.operation.unit": "dependency",
            }
            if _GENAI_CLIENT.get():
                attributes["gen_ai.provider.name"] = "powercontext"
                attributes["gen_ai.operation.name"] = (
                    "retrieval" if operation == "prepare_context" else f"powercontext.{operation}"
                )
                if operation == "prepare_context":
                    attributes["gen_ai.data_source.id"] = "powercontext"
            span = trace.get_tracer(_INSTRUMENTATION_NAME).start_span(
                f"PowerContextClient {operation}",
                kind=SpanKind.CLIENT,
                attributes=attributes,
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


__all__ = ["ClientSpan", "genai_client_context"]
