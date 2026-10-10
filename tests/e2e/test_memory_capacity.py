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

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.builtin.scope import ScopeDraft
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.http import GetMemoryCapacityRequest, ListMemoryEntriesRequest, RememberMemoryRequest
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.authz import PrincipalRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings


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
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope_id = (await client.get_default_scope()).scope_id
            request = GetMemoryCapacityRequest(scope_id=scope_id)
            with pytest.raises(ServerResponseError) as missing:
                await client.get_memory_capacity(request)
            assert (missing.value.status_code, missing.value.code) == (422, "legacy_memory_operation_unsupported")
            written = await client.remember_memory(
                RememberMemoryRequest(scope_id=scope_id, kind="fact", text="First fact.")
            )
            before = await client.list_memory_entries(
                ListMemoryEntriesRequest(scope_id=scope_id, include_inactive=True)
            )
            with pytest.raises(ServerResponseError) as capacity:
                await client.get_memory_capacity(request)
            assert (capacity.value.status_code, capacity.value.code) == (422, "legacy_memory_operation_unsupported")
            second = await client.remember_memory(
                RememberMemoryRequest(scope_id=scope_id, kind="fact", text="Second fact.")
            )
            assert second.records[0].artifact != written.records[0].artifact
            current = await client.list_memory_entries(
                ListMemoryEntriesRequest(scope_id=scope_id, include_inactive=True)
            )
            assert len(current.entries) == 2
            create = await transport.post(
                f"/v1/scopes/{scope_id}/artifacts",
                json={"family": "memory", "content": {"entries": [{"kind": "fact", "text": "Generic one."}]}},
            )
            assert create.status_code == 422, create.text
            assert create.json()["error"]["code"] == "legacy_memory_operation_unsupported"
            replace = await transport.put(
                f"/v1/scopes/{scope_id}/artifacts/memory/legacy-collection",
                headers={"If-Match": '"legacy-revision"'},
                json={"content": {"entries": [{"kind": "fact", "text": "Generic append."}]}},
            )
            assert replace.status_code == 422, replace.text
            assert replace.json()["error"]["code"] == "legacy_memory_operation_unsupported"
            assert (
                await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope_id, include_inactive=True))
                == current
            )
            assert before.entries == written.records

    asyncio.run(scenario())


def test_legacy_capacity_authentication_and_explicit_refusal(tmp_path):
    """Retired capacity authenticates; supported context reads keep Scope permissions."""

    async def scenario():
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'access.db'}")
        async with open_builtin_access_control(database) as access:
            app = create_server_app(
                settings=ServerSettings(
                    database=database,
                    access=AccessControlConfig(mode="enforced"),
                    mcp=McpConfig(enabled=False),
                ),
                access_control=access,
                authentication_provider=StaticBearerAuthenticationProvider(
                    "test-token", PrincipalRef(type="user", id="outsider")
                ),
            )
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client,
            ):
                private = await app.state.application.scopes.create(
                    ScopeDraft(title="Private", summary="No outsider access", idempotency_key="private-read")
                )
                anonymous = await client.post("/v1/memory/capacity", json={"scope_id": private.scope_id})
                assert anonymous.status_code == 401
                denied = await client.post(
                    "/v1/context/prepare",
                    json={"scope_id": private.scope_id, "query": "private"},
                    headers={"Authorization": "Bearer test-token"},
                )
                assert denied.status_code == 403
                unsupported = await client.post(
                    "/v1/memory/capacity",
                    json={"scope_id": private.scope_id},
                    headers={"Authorization": "Bearer test-token"},
                )
                assert unsupported.status_code == 422
                assert unsupported.json()["error"]["code"] == "legacy_memory_operation_unsupported"

    asyncio.run(scenario())
