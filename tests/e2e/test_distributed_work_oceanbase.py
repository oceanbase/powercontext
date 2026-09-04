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
import multiprocessing
import os
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import SecretStr
from sqlalchemy import delete

from powercontext.builtin.persistence.migration import migrate_database
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.tables import (
    BUILTIN_TABLES,
    WORK_ATTEMPTS_TABLE,
    WORK_ITEMS_TABLE,
    WORK_KEYS_TABLE,
    WORK_LANES_TABLE,
)
from powercontext.builtin.persistence.work import WorkRepository, WorkSpec
from powercontext.builtin.runtime.config import DeploymentConfig, HandoffReportConfig
from powercontext.server.cli import _migrate_configured_database
from powercontext.server.factory import create_server_app
from powercontext.server.settings import DashboardConfig, McpConfig, MetricsConfig, ServerSettings

_OCEANBASE_URL = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
_KIND = "test.distributed.claim"


class _RoundRobinASGITransport(httpx.AsyncBaseTransport):
    def __init__(self, *apps) -> None:
        self._transports = tuple(httpx.ASGITransport(app=app) for app in apps)
        self._next = 0
        self.hits: list[int] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        index = self._next % len(self._transports)
        self._next += 1
        self.hits.append(index)
        return await self._transports[index].handle_async_request(request)

    async def aclose(self) -> None:
        for transport in self._transports:
            await transport.aclose()


def _api_settings(url: str, instance_id: str, *, mcp: bool) -> ServerSettings:
    return ServerSettings(
        database=OceanBaseConfig(url=SecretStr(url)),
        deployment=DeploymentConfig(mode="distributed", role="api", id=instance_id),
        dashboard=DashboardConfig(enabled=False),
        handoff_report=HandoffReportConfig(enabled=False),
        mcp=McpConfig(enabled=mcp),
        metrics=MetricsConfig(enabled=False),
    )


def _claim_process(url: str, gate: Any, results: Any, worker_id: str) -> None:
    async def claim() -> None:
        config = OceanBaseConfig(url=SecretStr(url))
        async with OceanBaseProfile.open(config, tables=BUILTIN_TABLES, create_schema=False) as profile:
            async with profile.database.transaction() as connection:
                claims = await WorkRepository().claim(
                    connection,
                    worker_id=worker_id,
                    supported={_KIND: frozenset({1})},
                    lease_seconds=120,
                    limit=1,
                )
            results.put((worker_id, len(claims)))

    if not gate.wait(timeout=15):
        results.put((worker_id, -1))
        return
    asyncio.run(claim())


@pytest.mark.skipif(
    _OCEANBASE_URL is None,
    reason="set POWERCONTEXT_TEST_OCEANBASE_URL to a dedicated OceanBase MySQL-mode test database",
)
def test_oceanbase_rc_allows_only_one_claim_across_worker_processes() -> None:
    assert _OCEANBASE_URL is not None
    marker = uuid4().hex
    lane_key = marker.ljust(64, "0")
    logical_key = marker.ljust(64, "1")
    config = OceanBaseConfig(url=SecretStr(_OCEANBASE_URL))

    async def arrange() -> str:
        async with OceanBaseProfile.open(config, tables=BUILTIN_TABLES, create_schema=False) as profile:
            await migrate_database(profile.database)
            async with profile.database.transaction() as connection:
                enqueued = await WorkRepository().enqueue(
                    connection,
                    WorkSpec(
                        kind=_KIND,
                        payload_version=1,
                        scope_id=f"oceanbase-multiprocess:{marker}",
                        lane_key=lane_key,
                        logical_key=logical_key,
                        payload={},
                    ),
                )
            return enqueued.work.work_id

    work_id = asyncio.run(arrange())
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=_claim_process, args=(_OCEANBASE_URL, gate, results, f"worker-{index}"))
        for index in range(2)
    ]
    try:
        for process in processes:
            process.start()
        gate.set()
        outcomes = [results.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0
        assert sorted(count for _, count in outcomes) == [0, 1]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

        async def cleanup() -> None:
            async with (
                OceanBaseProfile.open(config, tables=BUILTIN_TABLES, create_schema=False) as profile,
                profile.database.transaction() as connection,
            ):
                await connection.execute(delete(WORK_ATTEMPTS_TABLE).where(WORK_ATTEMPTS_TABLE.c.work_id == work_id))
                await connection.execute(delete(WORK_KEYS_TABLE).where(WORK_KEYS_TABLE.c.work_id == work_id))
                await connection.execute(delete(WORK_ITEMS_TABLE).where(WORK_ITEMS_TABLE.c.work_id == work_id))
                await connection.execute(delete(WORK_LANES_TABLE).where(WORK_LANES_TABLE.c.lane_key == lane_key))

        asyncio.run(cleanup())


@pytest.mark.skipif(
    _OCEANBASE_URL is None,
    reason="set POWERCONTEXT_TEST_OCEANBASE_URL to a dedicated OceanBase MySQL-mode test database",
)
def test_two_api_replicas_share_http_operations_and_stateless_mcp_without_affinity() -> None:
    assert _OCEANBASE_URL is not None
    marker = uuid4().hex
    first_settings = _api_settings(_OCEANBASE_URL, f"api-a-{marker}", mcp=True)
    second_settings = _api_settings(_OCEANBASE_URL, f"api-b-{marker}", mcp=True)

    async def scenario() -> None:
        await _migrate_configured_database(first_settings)
        first = create_server_app(settings=first_settings)
        second = create_server_app(settings=second_settings)
        round_robin = _RoundRobinASGITransport(first, second)

        def create_http_client(
            headers: dict[str, str] | None = None,
            timeout: httpx.Timeout | None = None,
            auth: httpx.Auth | None = None,
            **_: object,
        ) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=round_robin,
                base_url="http://testserver",
                headers=headers,
                timeout=timeout,
                auth=auth,
                follow_redirects=True,
            )

        async with (
            first.router.lifespan_context(first),
            second.router.lifespan_context(second),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://first") as first_http,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=second), base_url="http://second") as second_http,
        ):
            capabilities = await first_http.get("/v1/capabilities")
            assert capabilities.status_code == 200
            assert capabilities.json()["memory_extraction"] is True

            scope_id = f"distributed-api:{marker}"
            captured = await first_http.post(
                "/v1/sources/content",
                json={"scope_id": scope_id, "source_id": "one", "content": "durable reference"},
            )
            assert captured.status_code == 202
            submitted = await second_http.post(
                "/v1/memory/flush",
                headers={"Prefer": "respond-async"},
                json={"scope_id": scope_id},
            )
            assert submitted.status_code == 202
            operation_id = submitted.json()["operation_id"]
            visible = await first_http.get(f"/v1/operations/{operation_id}")
            assert visible.status_code == 200
            assert visible.json()["status"] == "queued"

            transport = StreamableHttpTransport(
                "http://testserver/mcp/",
                httpx_client_factory=create_http_client,
            )
            async with Client(transport) as client:
                tools = {tool.name for tool in await client.list_tools()}
                assert "list_memory_entries" in tools
                result = await client.call_tool("list_memory_entries", {"scope_id": scope_id})
                assert result.structured_content == {"memory": None, "entries": []}

        assert set(round_robin.hits) == {0, 1}

    asyncio.run(scenario())
