# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import threading
from contextlib import asynccontextmanager

import httpx
import pytest
from pydantic import AnyHttpUrl

from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings
from tests.builtin.runtime.test_topic_memory_terminal_frontier import _budget, _seed
from tests.e2e.server_storage import prepare_server_database
from tests.e2e.test_access_control_regressions import ADMIN, _Authentication, _grant, _scope, _server
from tests.e2e.test_topic_memory_terminal_frontier import _Handler, _Provider


@asynccontextmanager
async def _recovery_server(tmp_path, backend, inference):
    if backend == "casbin":
        # Casbin's injected provider supports these reads but not spawned processing.
        async with _server(tmp_path, backend) as (app, client, _):
            yield app, client
        return
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    settings = ServerSettings(
        database=database,
        inference=inference,
        runtime=RuntimeConfig(artifact_processing_families=("topic-memory",)),
        access=AccessControlConfig(mode="enforced", deployment_id="recovery", background_principal_id=ADMIN.id),
        mcp=McpConfig(enabled=False),
        metrics=MetricsConfig(enabled=False),
    )
    await asyncio.to_thread(prepare_server_database, settings)
    async with open_builtin_access_control(database, bootstrap_administrators=(ADMIN,), deployment_id="recovery"):
        pass
    app = create_server_app(
        settings=settings,
        authentication_provider=_Authentication(),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield app, client


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_processing_status_is_scoped_and_only_admin_can_grant_retry(tmp_path, monkeypatch, backend):
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-provider")
    provider = _Provider(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        async with _recovery_server(
            tmp_path,
            backend,
            inference=InferenceConfig(
                generation_model="openai-chat:local-test",
                generation_base_url=AnyHttpUrl(f"http://127.0.0.1:{provider.server_port}/v1"),
            ),
        ) as (app, client):
            scope = await _scope(client)
            for role in ("scope.admin", "scope.contributor"):
                grant = await client.post(
                    "/v1/access/bindings/create",
                    json={
                        "subject": {"type": ADMIN.type, "id": ADMIN.id},
                        "resource": {"type": "scope", "scope_id": scope},
                        "role": role,
                        "idempotency_key": f"background-{role}",
                    },
                )
                assert grant.status_code == 201, grant.text
            captured = await client.post(
                "/v1/sources/content",
                json={"scope_id": scope, "source_id": "blocked", "content": "retained evidence"},
            )
            assert captured.status_code == 202, captured.text
            database = app.state.application._provider.database
            await _seed(database, scope_id=scope)
            before = await _budget(database)
            status_path = "/v1/topic-memory/processing/status"
            block_path = "/v1/topic-memory/processing/blocks/get"
            retry_path = "/v1/topic-memory/processing/retry"
            observed = await client.post(status_path, json={"scope_id": scope})
            assert observed.status_code == 200, observed.text
            block = observed.json()["block"]
            assert block["source_through"] is None and block["budget_source_through"] == 1
            assert block["legacy_source_through"] is None
            payload = {"scope_id": scope, "block_id": block["block_id"]}
            await _grant(client, scope, "reader", "scope.viewer")
            await _grant(client, scope, "writer", "scope.contributor")
            await _grant(client, scope, "manager", "scope.admin")
            for principal in ("reader", "writer"):
                headers = {"Authorization": f"Bearer {principal}"}
                assert (await client.post(status_path, headers=headers, json={"scope_id": scope})).status_code == 200
                assert (await client.post(block_path, headers=headers, json=payload)).status_code == 200
                assert (await client.post(retry_path, headers=headers, json=payload)).status_code == 403
            stranger = {"Authorization": "Bearer stranger"}
            assert (await client.post(status_path, headers=stranger, json={"scope_id": scope})).status_code == 403
            assert (await client.post(block_path, headers=stranger, json=payload)).status_code == 403
            assert await _budget(database) == before
            manager = {"Authorization": "Bearer manager"}
            accepted = await client.post(retry_path, headers=manager, json=payload)
            if backend == "casbin":
                assert accepted.status_code == 503, accepted.text
                assert await _budget(database) == before
                return
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["block"]["blocked_at"] == block["blocked_at"]
            assert accepted.json()["block"]["retried_at"] is not None
            duplicate = await client.post(retry_path, headers=manager, json=payload)
            assert duplicate.status_code == 200 and duplicate.json() == accepted.json()
            assert (await client.post(status_path, json={"scope_id": scope})).json()["block"] is None
            missing = await client.post(retry_path, headers=manager, json=payload | {"block_id": "missing-block"})
            assert missing.status_code == 404 and missing.json()["error"]["code"] == "topic_memory_block_not_found"

    try:
        asyncio.run(scenario())
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
