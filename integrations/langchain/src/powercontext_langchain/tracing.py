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

"""Adapter-owned tracing without requiring unreleased core helper symbols."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode, set_span_in_context


@contextmanager
def genai_client_context() -> Iterator[None]:
    """Mark adapter-owned SDK calls in the local OTel context, never wire baggage.

    Cores supporting this marker enrich their own dependency spans. Older cores
    ignore it and retain their existing operational telemetry and HTTP behavior.
    """
    token = otel_context.attach(otel_context.set_value("powercontext.genai_client", True))
    try:
        yield
    finally:
        otel_context.detach(token)


@contextmanager
def recall_content_observation(query: str, *, enabled: bool) -> Iterator[Callable[[str | None, str], None]]:
    """Opt-in, bounded adapter I/O; operational server spans remain content-free.

    The embedding application owns the exporter and access to recorded content.
    Empty output is recorded explicitly and is distinct from missing telemetry.
    """
    span = None

    def record(key: str, value: str | None) -> None:
        if span is None:
            return
        with suppress(Exception):
            encoded = b"" if value is None else value.encode("utf-8")
            bounded = None if value is None else encoded[:16384].decode("utf-8", errors="ignore")
            span.set_attribute(f"{key}.value", json.dumps(bounded, ensure_ascii=False))
            span.set_attribute(f"{key}.mime_type", "application/json")
            span.set_attribute(f"powercontext.content.{key}.truncated", len(encoded) > 16384)

    def output(content: str | None, status: str) -> None:
        record("output", content)
        if span is not None:
            with suppress(Exception):
                span.set_attribute("powercontext.context.status", status)

    token = None
    if enabled:
        with suppress(Exception):
            span = trace.get_tracer("powercontext.langchain").start_span(
                "PowerContextMiddleware recall",
                attributes={"gen_ai.provider.name": "powercontext", "gen_ai.operation.name": "retrieval"},
            )
            token = otel_context.attach(set_span_in_context(span))
            record("input", query)
    try:
        yield output
    except BaseException:
        if span is not None:
            with suppress(Exception):
                span.set_status(Status(StatusCode.ERROR))
        raise
    finally:
        if token is not None:
            with suppress(Exception):
                otel_context.detach(token)
        if span is not None:
            with suppress(Exception):
                span.end()
