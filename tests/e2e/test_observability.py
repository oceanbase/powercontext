from __future__ import annotations

import asyncio
import json
import logging

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.logging import OperationalContextFilter
from powercontext.server.settings import ServerSettings
from powercontext.server.tracing import ServerTracing


def test_observability_signals_correlate_without_counting_the_mcp_bridge(caplog, tmp_path) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing = ServerTracing(provider)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
        ),
        tracing=tracing,
    )
    scope_id = "project:sensitive-evaluation"

    def create_http_client(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **_: object,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=True,
        )

    async def scenario() -> str:
        transport = StreamableHttpTransport(
            "http://testserver/mcp/",
            httpx_client_factory=create_http_client,
        )
        async with (
            app.router.lifespan_context(app),
            create_http_client() as http_client,
            Client(transport) as mcp_client,
        ):
            direct = await http_client.get("/v1/capabilities")
            assert direct.status_code == 200
            await mcp_client.call_tool("list_memory_entries", {"scope_id": scope_id})
            return (await http_client.get("/metrics")).text

    correlation_filter = OperationalContextFilter()
    caplog.handler.addFilter(correlation_filter)
    try:
        with caplog.at_level(logging.INFO):
            metrics = asyncio.run(scenario())
    finally:
        caplog.handler.removeFilter(correlation_filter)

    access_records = [
        record for record in caplog.records if getattr(record, "event", None) == "transport.request.completed"
    ]
    http_record = next(record for record in access_records if record.operation == "get_capabilities")
    mcp_record = next(record for record in access_records if record.operation == "mcp.tools.call")
    http_span = next(span for span in exporter.get_finished_spans() if span.name == "HTTP get_capabilities")
    assert http_record.request_id == format(http_span.context.span_id, "016x")
    assert http_record.trace_id
    assert mcp_record.trace_id

    assert (
        'powercontext_server_transport_requests_total{operation="mcp.tools.call",outcome="success",transport="mcp"} 1.0'
        in metrics
    )
    assert (
        'powercontext_server_application_operations_total{operation="list_memory_entries",outcome="success"} 1.0'
        in metrics
    )
    assert 'operation="list_memory_entries",outcome="success",transport="http"' not in metrics

    spans = exporter.get_finished_spans()
    mcp_span = next(span for span in spans if span.name == "MCP mcp.tools.call")
    application_span = next(span for span in spans if span.name == "powercontext list_memory_entries")
    assert mcp_record.request_id == format(mcp_span.context.span_id, "016x")
    assert application_span.parent is not None
    assert application_span.parent.span_id == mcp_span.context.span_id
    assert not any(span.name == "HTTP list_memory_entries" for span in spans)

    signal_payload = json.dumps(
        {
            "logs": [vars(record) for record in access_records],
            "metrics": metrics,
            "spans": [dict(span.attributes or {}) for span in spans],
        },
        default=str,
    )
    assert scope_id not in signal_payload
