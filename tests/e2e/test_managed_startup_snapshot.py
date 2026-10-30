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

"""Healthy concurrent revisions must not cause managed startup to reject storage."""

import asyncio

import aiosqlite
import pytest
from fastapi.testclient import TestClient

from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, atomic_memory_rebuild, open_builtin_contexts
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings
from tests.builtin.persistence.test_server_vector_migrations import ControlledEmbedding, profile


@pytest.mark.parametrize("entry", ["sdk", "server"])
@pytest.mark.parametrize("vector", [False, True])
def test_healthy_concurrent_update_does_not_block_managed_startup(tmp_path, monkeypatch, entry, vector):
    embedding = ControlledEmbedding(profile(3)) if vector else None
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'managed.db'}", journal_mode="WAL"),
        mcp=McpConfig(enabled=False),
        access=AccessControlConfig(mode="disabled"),
        auth=BearerAuthConfig(enabled=False),
    )
    app = create_server_app(settings=settings, embedding_model=embedding)
    with TestClient(app) as client:
        scope_response = client.post(
            "/v1/scopes",
            json={
                "title": "Concurrent startup",
                "summary": "Concurrent update",
                "idempotency_key": "concurrent-startup",
            },
        )
        assert scope_response.status_code == 201, scope_response.text
        scope = scope_response.json()["scope_id"]
        response = client.post(
            "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Before concurrent revision."}
        )
        assert response.status_code == 200, response.text
        artifact = response.json()["records"][0]["artifact"]
        path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{artifact['artifact_id']}"
        etag = client.get(path).headers["ETag"]
        original = atomic_memory_rebuild._record
        updated = False

        async def interleaved_record(*args, **kwargs):
            nonlocal updated
            record = await original(*args, **kwargs)
            # Schedule a real HTTP update between authoritative and projection
            # reads in async startup. The synchronous migration runner already
            # owns a snapshot and is not the boundary under test.
            driver = args[0].connection.connection.driver_connection
            if not updated and isinstance(driver, aiosqlite.Connection):
                updated = True
                replacement = client.put(
                    path,
                    headers={"If-Match": etag},
                    json={"content": {"kind": "fact", "text": "After concurrent revision."}},
                )
                assert replacement.status_code == 200, replacement.text
            return record

        monkeypatch.setattr(atomic_memory_rebuild, "_record", interleaved_record)

        async def start():
            async with open_builtin_contexts(BuiltinConfig(database=settings.database), embedding_model=embedding):
                pass

        try:
            if entry == "sdk":
                asyncio.run(start())
            else:
                with TestClient(create_server_app(settings=settings, embedding_model=embedding)):
                    pass
        finally:
            monkeypatch.setattr(atomic_memory_rebuild, "_record", original)
            assert (
                deployment_runner(settings, embedding_profile=None if embedding is None else embedding.profile)
                .verify()
                .state
                == "ready"
            )
            assert client.get(path).json()["content"]["text"] == "After concurrent revision."
