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

import httpx
import pytest

from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    MemoryCapacityExceededError,
    MemoryEntryInput,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    RetireMemoryEntryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime import RememberMemoryRequest as RuntimeRememberMemoryRequest
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.builtin.scope import ScopeDraft
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.errors import ArtifactNotFoundError, RevisionConflictError
from powercontext.http import GetMemoryCapacityRequest, RememberMemoryRequest
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.authz import PrincipalRef
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings


@pytest.mark.parametrize("enabled", [False, True])
def test_runtime_compaction_requires_enablement_and_recovers_capacity(tmp_path, enabled):
    async def scenario():
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime-capacity.db'}"),
            runtime=RuntimeConfig(
                memory_max_active_entries=1,
                memory_max_manifest_entries=1,
                memory_compaction_enabled=enabled,
                memory_compaction_min_tombstone_revisions=0,
            ),
        )
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Capacity", summary="Runtime compaction", idempotency_key="capacity")
            )
            memory = runtime.memory.for_scope(scope.scope_id)
            with pytest.raises(ArtifactNotFoundError):
                await memory.compact(dry_run=True)
            assert (await memory.list()).memory_ref is None
            await memory.remember(
                RuntimeRememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Old fact"),))
            )
            entry = (await memory.list()).entries[0]
            retired = await memory.retire(RetireMemoryEntryRequest(citation=entry.citation))
            request = RuntimeRememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="New fact"),))
            with pytest.raises(MemoryCapacityExceededError):
                await memory.remember(request)
            before = await memory.capacity()
            preview = await memory.compact(dry_run=True, limit=1, reason="Recover capacity")
            assert preview.entry_ids == (entry.entry.entry_id,)
            assert preview.memory.as_ref() == retired.memory_ref
            assert await memory.capacity() == before
            if not enabled:
                with pytest.raises(CapabilityNotSupportedError, match="compaction"):
                    await memory.compact()
                assert await memory.capacity() == before
                return
            with pytest.raises(RevisionConflictError):
                await memory.compact(expected_revision=entry.memory_ref.revision)
            assert await memory.capacity() == before
            result = await memory.compact(expected_revision=preview.memory.revision, limit=1, reason="Recover capacity")
            assert result.entry_ids == preview.entry_ids
            assert result.reclaimed_bytes == preview.reclaimed_bytes
            assert result.memory.revision == retired.memory_ref.revision + 1
            assert (await memory.capacity()).manifest_entry_count == 0
            assert await memory.get(GetMemoryEntryRequest(citation=entry.citation)) == entry
            changes = await memory.changes(since_revision=retired.memory_ref.revision)
            assert changes.revisions[0].changes[0].op == "compact"
            await memory.remember(request)
            assert (await memory.capacity()).manifest_entry_count == 1

    asyncio.run(scenario())


def test_capacity_and_refusal_through_server_and_client(tmp_path):
    async def scenario():
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'capacity.db'}"),
                runtime=RuntimeConfig(memory_max_active_entries=1, memory_max_manifest_entries=1),
                auth=BearerAuthConfig(enabled=False),
                mcp=McpConfig(enabled=False),
            )
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope_id = (await client.get_default_scope()).scope_id
            request = GetMemoryCapacityRequest(scope_id=scope_id)
            with pytest.raises(ServerResponseError) as missing:
                await client.get_memory_capacity(request)
            assert missing.value.status_code == 404
            written = await client.remember_memory(
                RememberMemoryRequest(scope_id=scope_id, kind="fact", text="First fact.")
            )
            capacity = await client.get_memory_capacity(request)
            assert capacity.memory_ref == written.memory
            assert capacity.active_entry_count == capacity.manifest_entry_count == 1
            assert capacity.budget.max_manifest_entries == 1
            assert capacity.exceeded == []
            rejected = await transport.post(
                "/v1/memory/remember", json={"scope_id": scope_id, "kind": "fact", "text": "Second fact."}
            )
            assert rejected.status_code == 409, rejected.text
            error = rejected.json()["error"]
            assert error["code"] == "memory_capacity_exceeded"
            assert error["details"] == {"dimension": "manifest_entries", "limit": 1, "observed": 2}
            assert await client.get_memory_capacity(request) == capacity
            # Generic Artifact management must inherit the same deployment limit.
            create = await transport.post(
                f"/v1/scopes/{scope_id}/artifacts",
                json={
                    "family": "memory",
                    "content": {
                        "entries": [{"kind": "fact", "text": "Generic one."}, {"kind": "fact", "text": "Generic two."}]
                    },
                },
            )
            assert create.status_code == 409, create.text
            assert create.json()["error"]["code"] == "memory_capacity_exceeded"
            record = await transport.get(f"/v1/scopes/{scope_id}/artifacts/memory/{written.memory.artifact_id}")
            replace = await transport.put(
                f"/v1/scopes/{scope_id}/artifacts/memory/{written.memory.artifact_id}",
                headers={"If-Match": record.headers["etag"]},
                json={"content": {"entries": [{"kind": "fact", "text": "Generic append."}]}},
            )
            assert replace.status_code == 409, replace.text
            assert replace.json()["error"]["code"] == "memory_capacity_exceeded"
            assert await client.get_memory_capacity(request) == capacity

    asyncio.run(scenario())


def test_capacity_requires_scope_access(tmp_path):
    async def scenario():
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'access.db'}"),
                access=AccessControlConfig(mode="enforced"),
                mcp=McpConfig(enabled=False),
            ),
            authentication_provider=StaticBearerAuthenticationProvider(
                "test-token", PrincipalRef(type="user", id="outsider")
            ),
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            anonymous = await client.post("/v1/memory/capacity", json={"scope_id": "private"})
            assert anonymous.status_code == 401
            denied = await client.post(
                "/v1/memory/capacity", json={"scope_id": "private"}, headers={"Authorization": "Bearer test-token"}
            )
            assert denied.status_code == 403

    asyncio.run(scenario())
