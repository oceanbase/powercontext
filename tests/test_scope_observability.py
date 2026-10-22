"""Scope observations preserve attribution without changing global metric labels."""

from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from powercontext._logging import log_context
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.logging import JsonFormatter, OperationalContextFilter
from powercontext.server.settings import McpConfig, ServerSettings
from powercontext.server.tracing import ServerTracing


def test_scope_activity_and_inventory_keep_exact_scope_and_multi_scope_units(tmp_path, caplog):
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing = ServerTracing(provider)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}"),
            mcp=McpConfig(enabled=False),
        ),
        tracing=tracing,
    )
    try:
        with TestClient(app) as client, caplog.at_level(logging.INFO, logger="powercontext.server.app"):
            scopes = []
            for name in ("one", "two"):
                response = client.post(
                    "/v1/scopes", json={"title": name, "summary": "Scope metrics fixture", "idempotency_key": name}
                )
                assert response.status_code == 201
                scopes.append(response.json()["scope_id"])
            caplog.clear()
            response = client.post(
                "/v1/memory/remember", json={"scope_id": scopes[0], "kind": "fact", "text": "private-memory-text"}
            )
            assert response.status_code == 200
            result = client.post("/v1/stats", json={"selection": {"mode": "exact", "scope_ids": scopes}})
            assert result.status_code == 200
            counts = {
                row["scope_id"]: row["inventory"]["memory"]["entries"]["total"] for row in result.json()["by_scope"]
            }
            assert counts == {scopes[0]: 1, scopes[1]: 0}
            records = [r for r in caplog.records if getattr(r, "event", None) == "application.scope.completed"]
            assert [(r.operation, r.scope, r.unit) for r in records] == [
                ("remember_memory", scopes[0], "application"),
                *(("get_stats", scope, "application") for scope in sorted(scopes)),
            ]
            for record in records:
                OperationalContextFilter().filter(record)
                payload = json.loads(JsonFormatter().format(record))
                assert payload["scope"] in scopes
                assert payload["duration_ms"] >= 0
                assert "private-memory-text" not in json.dumps(payload)
            metrics = client.get("/metrics").text
            assert all(scope not in metrics for scope in scopes)
        span = next(s for s in exporter.get_finished_spans() if s.name == "powercontext remember_memory")
        assert (span.attributes or {})["powercontext.scope.id"] == scopes[0]
        span = next(s for s in exporter.get_finished_spans() if s.name == "powercontext get_stats")
        assert (span.attributes or {})["powercontext.scope.ids"] == tuple(sorted(scopes))
    finally:
        tracing.shutdown()


def test_model_scope_attribution_preserves_positional_tracer_arguments():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing = ServerTracing(provider, instrumented=True)
    try:
        assert tracing.instrumentation is not None
        tracer = tracing.instrumentation.tracer
        with log_context({"scope": "selected-scope"}):
            span = tracer.start_span("model span", None, SpanKind.INTERNAL, {"bounded": True})
            span.end()
            with tracer.start_as_current_span("model current span", None, SpanKind.INTERNAL, {"bounded": True}):
                pass
        for span in exporter.get_finished_spans():
            assert (span.attributes or {})["powercontext.scope.id"] == "selected-scope"
            assert (span.attributes or {})["bounded"] is True
    finally:
        tracing.shutdown()
