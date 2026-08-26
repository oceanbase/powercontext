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

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import RuntimeConfig
from powercontext.server.app import create_app
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    McpConfig,
    MetricsConfig,
    ServerLoggingConfig,
    ServerSettings,
)


def _settings(
    database,
    *,
    mcp: bool = False,
    metrics: bool = True,
    scope_cache_size: int | None = None,
) -> ServerSettings:
    return ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        mcp=McpConfig(enabled=mcp),
        logging=ServerLoggingConfig(access=False),
        metrics=MetricsConfig(enabled=metrics),
        runtime=RuntimeConfig() if scope_cache_size is None else RuntimeConfig(scope_cache_size=scope_cache_size),
    )


def test_http_metrics_use_declared_operations_and_exclude_infrastructure(tmp_path) -> None:
    app = create_server_app(settings=_settings(tmp_path / "runtime.db"))

    with TestClient(app) as client:
        assert client.get("/v1/capabilities").status_code == 200
        assert client.post("/v1/memory/flush", json={"scope_id": "project:metrics"}).status_code == 200
        assert client.post("/v1/artifact-candidates/list", json={"scope_id": "project:metrics"}).status_code == 200
        assert client.get("/health/live").status_code == 200
        response = client.get("/metrics")

    metrics = response.text
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert (
        'powercontext_server_transport_requests_total{operation="get_capabilities",outcome="success",transport="http"} 1.0'
        in metrics
    )
    assert 'powercontext_server_application_operations_total{operation="flush_memory",outcome="noop"} 1.0' in metrics
    assert (
        'powercontext_server_transport_requests_total{operation="list_artifact_candidates",outcome="success",transport="http"} 1.0'
        in metrics
    )
    assert (
        'powercontext_server_application_operations_total{operation="list_artifact_candidates",outcome="success"} 1.0'
        in metrics
    )
    assert "get_liveness" not in metrics
    assert "project:metrics" not in metrics
    assert "scope_id" not in metrics
    assert "powercontext_server_runtime_ready 1.0" in metrics


def test_metrics_endpoint_is_absent_when_disabled(tmp_path) -> None:
    app = create_server_app(settings=_settings(tmp_path / "runtime.db", metrics=False))

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 404


def test_one_off_scope_ids_keep_runtime_scope_cache_bounded(tmp_path) -> None:
    app = create_server_app(settings=_settings(tmp_path / "runtime.db", scope_cache_size=3))

    with TestClient(app) as client:
        for index in range(12):
            response = client.post(
                "/v1/context/prepare",
                json={"scope_id": f"one-off-{index}", "query": "short query"},
            )
            assert response.status_code == 200
        metrics = client.get("/metrics").text

    assert 'powercontext_server_runtime_scopes{state="active"} 0.0' in metrics
    assert 'powercontext_server_runtime_scopes{state="cached"} 3.0' in metrics
    assert "one-off-" not in metrics


def test_mcp_metrics_count_one_logical_request_and_one_application_operation(tmp_path) -> None:
    app = create_server_app(settings=_settings(tmp_path / "runtime.db", mcp=True))

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
            Client(transport) as client,
            create_http_client() as http_client,
        ):
            await client.call_tool("list_memory_entries", {"scope_id": "project:metrics"})
            return (await http_client.get("/metrics")).text

    metrics = asyncio.run(scenario())

    assert (
        'powercontext_server_transport_requests_total{operation="mcp.tools.call",outcome="success",transport="mcp"} 1.0'
        in metrics
    )
    assert (
        'powercontext_server_application_operations_total{operation="list_memory_entries",outcome="success"} 1.0'
        in metrics
    )
    assert 'operation="list_memory_entries",outcome="success",transport="http"' not in metrics


def test_metrics_failure_does_not_change_application_behavior() -> None:
    class FailingMetrics:
        def observe_application(self, operation: str, outcome: str, started_at: float) -> None:
            del operation, outcome, started_at
            raise RuntimeError

    metrics: Any = FailingMetrics()
    response = TestClient(create_app(metrics=metrics)).get("/v1/capabilities")

    assert response.status_code == 200
