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

"""Local admission regressions; native split-role acceptance lives in test_background_metrics."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from typer.testing import CliRunner

from powercontext.builtin.persistence.migrations.connections import MaintenanceConnections
from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server import cli
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from powercontext.server.tracing import ServerTracing


@pytest.mark.parametrize("access_mode", ["disabled", "enforced"])
def test_background_cli_rejects_uninitialized_remote_storage_before_business_writes(tmp_path, monkeypatch, access_mode):
    path = tmp_path / "empty.db"
    path.touch()
    before = path.read_bytes()

    def inspect_local_database(_self, operation, *, writable=False):
        # Exercise the real remote admission decision with an actual empty SQL
        # inventory. This substitutes only the network connection, not native OB.
        assert writable is False
        engine = create_engine(f"sqlite:///{path}")
        try:
            with engine.connect() as connection:
                return operation(connection, None, lambda: None)
        finally:
            engine.dispose()

    def unexpected_business_profile(*args, **kwargs):
        pytest.fail("uninitialized remote storage reached a business profile")

    monkeypatch.setattr(MaintenanceConnections, "run", inspect_local_database)
    monkeypatch.setattr(cli, "open_builtin_access_control", unexpected_business_profile)
    monkeypatch.setattr(cli, "open_builtin_runtime", unexpected_business_profile)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "oceanbase")
    monkeypatch.setenv(
        "POWERCONTEXT_SERVER_DATABASE_URL", "mysql+aoceanbase://test@localhost:2881/test?charset=utf8mb4"
    )
    monkeypatch.setenv("POWERCONTEXT_SERVER_ACCESS_MODE", access_mode)
    if access_mode == "enforced":
        monkeypatch.setenv("POWERCONTEXT_SERVER_AUTH_TOKEN", "isolated-background-test-token")
    result = CliRunner().invoke(cli.app, ["run", "--role", "background", "--no-env-file"])

    assert isinstance(result.exception, MigrationError), result.output
    assert result.exception.code == "migration_required"
    assert path.read_bytes() == before


def test_managed_storage_remains_usable_by_background_runner_and_api(tmp_path, monkeypatch):
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'managed.db'}"),
        mcp=McpConfig(enabled=False),
    )
    runner = deployment_runner(settings)
    plan = runner.plan()
    assert runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True).state == "ready"

    with TestClient(create_server_app(settings=settings)) as client:
        created = client.post(
            "/v1/scopes",
            json={
                "title": "Preserved scope",
                "summary": "Created before background startup",
                "idempotency_key": "scope",
            },
        )
        assert created.status_code == 201, created.text
        scope_id = created.json()["scope_id"]

    async def serve(app, configured, stopped):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            assert (await client.get("/health/live")).status_code == 200
            metrics = await client.get("/metrics")
            assert metrics.status_code == 200
            assert 'powercontext_server_readiness_state{state="ready"} 1.0' in metrics.text
        stopped.set()

    monkeypatch.setattr(cli, "serve_background_metrics", serve)
    # SQLite supports the all role. Invoke the runner with a real local runtime
    # to check its admitted lifecycle; the native OB test covers split-role CLI.
    asyncio.run(cli._run_background_async(settings, ServerTracing.context_only(role="background")))

    with TestClient(create_server_app(settings=settings)) as client:
        response = client.get(f"/v1/scopes/{scope_id}")
        assert response.status_code == 200, response.text
        assert response.json()["title"] == "Preserved scope"
        assert client.get("/health/ready").status_code == 200
    assert runner.verify().state == "ready"
