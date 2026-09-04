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
from typing import Literal, cast

from pydantic import SecretStr

import powercontext.server.factory as factory
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.runtime import BuiltinRuntime, RuntimeCapabilities
from powercontext.builtin.runtime.config import DeploymentConfig, HandoffReportConfig
from powercontext.server.settings import DashboardConfig, McpConfig, MetricsConfig, ServerSettings


def _distributed_settings(
    role: Literal["api", "scheduler", "worker"],
    *,
    mcp: bool = True,
    metrics: bool = True,
) -> ServerSettings:
    return ServerSettings.model_validate({
        "database": OceanBaseConfig(
            url=SecretStr("mysql+aoceanbase://root@127.0.0.1:2881/powercontext?charset=utf8mb4")
        ),
        "deployment": DeploymentConfig(mode="distributed", role=role, id=f"{role}-a"),
        "dashboard": DashboardConfig(enabled=False),
        "handoff_report": HandoffReportConfig(enabled=False),
        "mcp": McpConfig(enabled=mcp),
        "metrics": MetricsConfig(enabled=metrics),
    })


def test_scheduler_and_worker_roles_expose_only_the_management_plane() -> None:
    for role in ("scheduler", "worker"):
        app = factory.create_server_app(settings=_distributed_settings(role))
        paths = {getattr(route, "path", "") for route in app.routes}
        assert paths == {"/health/live", "/health/ready", "/metrics"}
        assert set(app.openapi()["paths"]) == {"/health/live", "/health/ready"}


def test_distributed_api_mounts_stateless_mcp(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def capture_mount(_app, **kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(factory, "mount_mcp", capture_mount)
    app = factory.create_server_app(settings=_distributed_settings("api", metrics=False))

    assert any(getattr(route, "path", None) == "/v1/operations" for route in app.routes)
    assert captured["stateless_http"] is True


def test_distributed_api_advertises_worker_backed_memory_extraction() -> None:
    class RuntimeWithoutLocalInference:
        async def capabilities(self) -> RuntimeCapabilities:
            return RuntimeCapabilities(memory_extraction=False, memory_search_modes=())

    capabilities = asyncio.run(
        factory._server_capabilities(
            cast(BuiltinRuntime, RuntimeWithoutLocalInference()),
            accepts_distributed_memory_work=True,
        )
    )

    assert capabilities.memory_extraction is True
