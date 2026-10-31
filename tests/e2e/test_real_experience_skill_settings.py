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

"""Baseline servers run independently of the caller's configured database."""

import asyncio
import os

import pytest

from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.client import PowerContextClient
from powercontext.http import CreateScopeRequest
from powercontext.server.settings import ServerSettings
from tests.e2e.real_experience_skill.harness import _start_server
from tests.e2e.real_experience_skill.test_memory_routing import _RunningServer


@pytest.mark.parametrize("fixture", ["baseline", "memory-routing"])
def test_baseline_server_ignores_configured_seekdb_environment(tmp_path, monkeypatch, fixture):
    configured_path = tmp_path / "configured-seekdb"
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "seekdb")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_PATH", str(configured_path))
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL", "test")
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS", "120")
    if fixture == "baseline":
        server = _start_server(tmp_path / "baseline.db", tmp_path / "scheduler.db")
    else:
        server = _RunningServer(tmp_path)
        server.start()

    async def scenario():
        async with PowerContextClient(server.base_url) as client:
            scope = await client.create_scope(
                CreateScopeRequest(title="Baseline", summary="Isolated settings", idempotency_key="baseline")
            )
            assert (await client.get_scope(scope.scope_id)).scope_id == scope.scope_id

    try:
        asyncio.run(scenario())
    finally:
        server.stop()

    assert not configured_path.exists()
    assert os.environ["POWERCONTEXT_SERVER_DATABASE_PATH"] == str(configured_path)
    configured = ServerSettings()
    assert isinstance(configured.database, SeekDBConfig)
    assert configured.database.path == configured_path
    assert configured.runtime.experience_schedule_seconds == 120
