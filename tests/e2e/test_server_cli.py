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

import json
from types import TracebackType
from typing import Self

import httpx
import pytest
from typer.testing import CliRunner

import powercontext.client.cli as client_cli
from powercontext.cli.app import create_cli
from powercontext.client import PowerContextClient
from powercontext.http import Capabilities, MemorySearchMode, PreparedContextSchema
from powercontext.server.app import create_app


def test_capabilities_flow_through_server_sdk_and_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    server_capabilities = Capabilities(
        source_types=["git-commit"],
        artifact_families=["memory", "handoff"],
        memory_extraction=True,
        handoff_generation=True,
        search_modes=[MemorySearchMode.FTS],
        context_versions=[PreparedContextSchema.POWERCONTEXT_PREPARED_CONTEXT_V1],
    )
    server_app = create_app(capability_provider=lambda: server_capabilities)

    class InProcessClient:
        def __init__(self) -> None:
            self._http_client: httpx.AsyncClient | None = None
            self._sdk: PowerContextClient | None = None

        async def __aenter__(self) -> Self:
            self._http_client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server_app),
                base_url="http://testserver",
            )
            self._sdk = PowerContextClient("http://testserver", http_client=self._http_client)
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            assert self._http_client is not None
            await self._http_client.aclose()

        async def get_capabilities(self) -> Capabilities:
            assert self._sdk is not None
            return await self._sdk.get_capabilities()

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: InProcessClient())
    result = CliRunner().invoke(create_cli([]), ["--json", "capabilities"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "source_types": ["git-commit"],
        "artifact_families": ["memory", "handoff"],
        "memory_extraction": True,
        "experience_generation": False,
        "managed_skill_generation": False,
        "external_skill_registry": False,
        "handoff_generation": True,
        "search_modes": ["fts"],
        "context_versions": ["powercontext.prepared-context.v1"],
    }
