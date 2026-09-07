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
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Self

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from starlette.middleware import Middleware

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.runtime import MemoryEntriesPage
from powercontext.server.app import create_app
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.authz import (
    AccessAuditContext,
    AccessBinding,
    AccessBindingState,
    AccessControlService,
    AccessRole,
    BuiltinAuthorizationProvider,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.repository import ACCESS_TABLES, RelationalAccessRepository
from powercontext.server.mcp import mount_mcp
from powercontext.server.middleware import AuthenticationMiddleware

ADMIN = PrincipalRef(type="service", id="admin")
BOB = PrincipalRef(type="user", id="bob")


class _MemoryApplication:
    def for_scope(self, scope_id: str) -> Self:
        del scope_id
        return self

    async def logical_artifacts(self):
        return ()

    async def list(self, *, include_inactive: bool = False) -> MemoryEntriesPage:
        del include_inactive
        return MemoryEntriesPage(memory_ref=None)


def test_mcp_internal_bridge_preserves_principal_and_audits_mcp_transport() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await repository.create_binding(
                AccessBinding(
                    binding_id="seed-admin",
                    subject=ADMIN,
                    resource=ResourceRef.server(),
                    role=AccessRole.SERVER_ADMIN,
                    granted_by=ADMIN,
                    reason="test bootstrap",
                    created_at=datetime.now(UTC),
                    expires_at=None,
                    state=AccessBindingState.ACTIVE,
                    version=1,
                    policy_revision="pending",
                    idempotency_key="seed-admin",
                )
            )
            service = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
            )
            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=ResourceRef.scope("scope-a"),
                    role=AccessRole.SCOPE_VIEWER,
                    idempotency_key="bob-scope-a-viewer",
                ),
                context=AccessAuditContext(transport="test", operation="seed"),
            )
            authentication = StaticBearerAuthenticationProvider("bob-token", BOB)
            app = create_app(
                application=SimpleNamespace(memory=_MemoryApplication(), records=_MemoryApplication()),
                access_control=service,
                authentication_provider=authentication,
                middleware=(
                    Middleware(
                        AuthenticationMiddleware,
                        provider=authentication,
                    ),
                ),
            )
            mount_mcp(app)

            def create_http_client(
                headers: dict[str, str] | None = None,
                timeout: httpx.Timeout | None = None,
                auth: httpx.Auth | None = None,
                **_: object,
            ) -> httpx.AsyncClient:
                combined_headers = {"Authorization": "Bearer bob-token", **(headers or {})}
                return httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                    headers=combined_headers,
                    timeout=timeout,
                    auth=auth,
                    follow_redirects=True,
                )

            transport = StreamableHttpTransport(
                "http://testserver/mcp/",
                httpx_client_factory=create_http_client,
            )
            async with app.router.lifespan_context(app), Client(transport) as client:
                result = await client.call_tool("list_memory_entries", {"scope_id": "scope-a"})
                assert result.is_error is False

            audit = await repository.list_audit(resource=ResourceRef.server())
            decision = next(event for event in audit if event.operation == "list_memory_entries")
            assert decision.transport == "mcp"
            assert decision.principal == BOB
            assert decision.allowed is True

    asyncio.run(scenario())
