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
import json
import socket
from collections.abc import AsyncIterator, Generator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from sse_starlette.sse import EventSourceResponse
from starlette.middleware import Middleware
from typing_extensions import override

from powercontext.builtin.authz import (
    AccessAuditContext,
    AccessBinding,
    AccessBindingState,
    AccessControlService,
    AccessRole,
    BuiltinAuthorizationProvider,
    CreateBinding,
    GroupRef,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.repository import ACCESS_TABLES, RelationalAccessRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.runtime.atomic_memory import AtomicMemoryAccess, AtomicMemoryPage
from powercontext.server.app import create_app
from powercontext.server.authentication import (
    AuthenticationRejectedError,
    AuthenticationRequest,
    AuthenticationResult,
    ProviderReadiness,
)
from powercontext.server.factory import create_server_app
from powercontext.server.mcp import mount_mcp
from powercontext.server.middleware import AuthenticationMiddleware
from powercontext.server.settings import AccessControlConfig, ServerSettings
from tests.e2e.server_storage import prepare_server_database

ALICE = PrincipalRef(type="user", id="alice")
BOB = PrincipalRef(type="user", id="bob")
TEAM = GroupRef(type="group", id="alice-team")
FIRST_ACTOR = PrincipalRef(type="service", id="first-actor")
CURRENT_ACTOR = PrincipalRef(type="service", id="current-actor")


class _AuthenticationProvider:
    def __init__(self) -> None:
        self.identities = {
            "Bearer alice-token": AuthenticationResult(subject=ALICE, actor=FIRST_ACTOR, subject_groups=(TEAM,)),
            "Bearer bob-token": AuthenticationResult(subject=BOB),
        }

    async def authenticate(self, request: AuthenticationRequest, /) -> AuthenticationResult:
        result = self.identities.get(request.headers.get("authorization", ""))
        if result is None:
            raise AuthenticationRejectedError
        return result

    async def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(ready=True)


class _MemoryApplication:
    def __init__(self, database, scope_id: str = "") -> None:
        self.database = database
        self.scope_id = scope_id

    def for_scope(self, scope_id: str) -> Self:
        return type(self)(self.database, scope_id)

    async def list(self, *, context, **kwargs):
        assert context is not None
        async with self.database.transaction() as connection:
            await AtomicMemoryAccess().filters(connection, self.scope_id, context)
        return AtomicMemoryPage(items=())


def _rpc_body(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    if response.headers["content-type"].startswith("application/json"):
        return response.json()
    return next(json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: "))


class _TestServer(uvicorn.Server):
    @override
    @contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        # Pytest owns process signals. SSE must not capture this temporary listener
        # through Uvicorn's SIGTERM handler and later shut down unrelated streams.
        yield


@asynccontextmanager
async def _http_client(app: FastAPI, *, over_tcp: bool) -> AsyncIterator[httpx.AsyncClient]:
    if not over_tcp:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            yield client
        return

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = _TestServer(
            uvicorn.Config(app, lifespan="off", access_log=False, log_level="warning", log_config=None)
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        message = "MCP test server stopped before startup"
                        raise RuntimeError(message)
                    await asyncio.sleep(0.01)
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{listener.getsockname()[1]}", trust_env=False
            ) as client:
                yield client
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=10)


@pytest.mark.parametrize("over_tcp", [False, True], ids=["asgi", "tcp"])
def test_mcp_authorization_and_audit_use_each_http_requests_identity(over_tcp: bool) -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            for subject, scope_id in ((TEAM, "alice-scope"), (BOB, "bob-scope")):
                await repository.create_binding(
                    AccessBinding(
                        binding_id=scope_id,
                        subject=subject,
                        resource=ResourceRef.scope(scope_id),
                        role=AccessRole.SCOPE_VIEWER,
                        granted_by=FIRST_ACTOR,
                        reason="test bootstrap",
                        created_at=datetime.now(UTC),
                        expires_at=None,
                        state=AccessBindingState.ACTIVE,
                        version=1,
                        policy_revision="pending",
                        idempotency_key=scope_id,
                    )
                )
            access = AccessControlService(
                BuiltinAuthorizationProvider(repository), relationships=repository, audit=repository
            )
            authentication = _AuthenticationProvider()
            memory = _MemoryApplication(profile.database)
            app = create_app(
                application=SimpleNamespace(atomic_memory=memory, records=memory),
                access_control=access,
                authentication_provider=authentication,
                middleware=(Middleware(AuthenticationMiddleware, provider=authentication),),
            )
            mount_mcp(app)
            async with app.router.lifespan_context(app), _http_client(app, over_tcp=over_tcp) as client:
                headers = {"Accept": "application/json, text/event-stream", "Authorization": "Bearer alice-token"}
                initialized = await client.post(
                    "/mcp/",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": "initialize",
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {},
                            "clientInfo": {"name": "identity-regression", "version": "1"},
                        },
                    },
                )
                headers["MCP-Protocol-Version"] = _rpc_body(initialized)["result"]["protocolVersion"]
                # Reuse an issued session on affected servers, or send a stale ID to a stateless server.
                headers["mcp-session-id"] = initialized.headers.get("mcp-session-id", "stale-alice-session")
                notified = await client.post(
                    "/mcp/",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                    },
                )
                assert notified.status_code == 202

                async def call(token: str, scope_id: str, request_id: str) -> dict[str, Any]:
                    response = await client.post(
                        "/mcp/",
                        headers={
                            **headers,
                            "Authorization": f"Bearer {token}-token",
                        },
                        json={
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "method": "tools/call",
                            "params": {"name": "list_memory_entries", "arguments": {"scope_id": scope_id}},
                        },
                    )
                    return _rpc_body(response)["result"]

                alice = await call("alice", "alice-scope", "alice-read")
                assert not alice.get("isError", False)
                bob_denied = await call("bob", "alice-scope", "bob-denied")
                assert bob_denied["isError"] is True
                assert "403" in bob_denied["content"][0]["text"]
                bob = await call("bob", "bob-scope", "bob-read")
                assert not bob.get("isError", False)

                concurrent = await asyncio.gather(
                    call("alice", "alice-scope", "alice-concurrent"),
                    call("bob", "bob-scope", "bob-concurrent"),
                )
                assert all(not result.get("isError", False) for result in concurrent)

                # The same credential now resolves to a different actor with no group grant.
                authentication.identities["Bearer alice-token"] = AuthenticationResult(
                    subject=ALICE, actor=CURRENT_ACTOR
                )
                reduced = await call("alice", "alice-scope", "alice-reduced")
                assert reduced["isError"] is True
                assert "403" in reduced["content"][0]["text"]

                rejected = await client.post(
                    "/mcp/",
                    headers={**headers, "Authorization": "Bearer invalid"},
                    json={
                        "jsonrpc": "2.0",
                        "id": "invalid",
                        "method": "tools/list",
                    },
                )
                assert rejected.status_code == 401

                # An old session ID cannot substitute for missing or revoked credentials.
                del authentication.identities["Bearer alice-token"]
                for authorization in (None, "Bearer alice-token"):
                    rejected_headers = {key: value for key, value in headers.items() if key != "Authorization"}
                    if authorization is not None:
                        rejected_headers["Authorization"] = authorization
                    rejected_call = await client.post(
                        "/mcp/",
                        headers=rejected_headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": "revoked",
                            "method": "tools/call",
                            "params": {"name": "list_memory_entries", "arguments": {"scope_id": "alice-scope"}},
                        },
                    )
                    assert rejected_call.status_code == 401

            decisions = list(
                {
                    event.request_id: event
                    for event in await repository.list_audit(resource=ResourceRef.server())
                    if event.operation == "list_memory_entries"
                }.values()
            )
            assert len(decisions) == 6
            assert all(event.transport == "mcp" for event in decisions)
            assert all(event.request_id is not None for event in decisions)
            assert decisions[0].principal == ALICE
            assert decisions[0].actor == FIRST_ACTOR
            assert decisions[0].matched_subject == TEAM
            assert decisions[1].principal == BOB and decisions[1].allowed is False
            assert decisions[2].principal == BOB and decisions[2].allowed is True
            assert {event.principal for event in decisions[3:5]} == {ALICE, BOB}
            assert decisions[-1].principal == ALICE and decisions[-1].allowed is False
            assert decisions[-1].actor == CURRENT_ACTOR
            assert decisions[-1].matched_subject is None

    asyncio.run(scenario())


class _SwitchableAuth(httpx.Auth):
    principal_name = "alice"

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self.principal_name}-token"
        yield request


@pytest.mark.parametrize("overlap", [False, True], ids=["restart", "overlapping-listeners"])
def test_mcp_tcp_requests_survive_other_listener_shutdown(overlap: bool) -> None:
    async def scenario() -> None:
        streaming_app = FastAPI()

        @streaming_app.get("/events")
        async def events():
            async def generate():
                # Keep the stream alive while its shutdown listener starts.
                await asyncio.sleep(0.1)
                yield {"data": "ready"}

            return EventSourceResponse(generate())

        app = create_app()
        mount_mcp(app)
        async with app.router.lifespan_context(app), AsyncExitStack() as clients:
            client = await clients.enter_async_context(_http_client(app, over_tcp=True)) if overlap else None
            async with _http_client(streaming_app, over_tcp=True) as streaming_client:
                streamed = await streaming_client.get("/events")
                assert "data: ready" in streamed.text

            # A fast restart can hide an old SSE watcher's delayed shutdown work.
            await asyncio.sleep(1.1)
            if client is None:
                client = await clients.enter_async_context(_http_client(app, over_tcp=True))
            response = await client.post(
                "/mcp/",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": "restart",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "shutdown-regression", "version": "1"},
                    },
                },
            )
            assert "serverInfo" in _rpc_body(response)["result"]

    asyncio.run(scenario())


def test_mcp_client_writes_reconnects_and_preserves_memory_after_server_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
        settings = ServerSettings(database=database, access=AccessControlConfig(mode="enforced"))
        await asyncio.to_thread(prepare_server_database, settings)
        authentication = _AuthenticationProvider()
        authentication.identities["Bearer alice-token"] = AuthenticationResult(subject=ALICE)
        auth = _SwitchableAuth()

        async with open_builtin_access_control(database, bootstrap_administrators=(ALICE,)) as access:

            def server_app() -> FastAPI:
                return create_server_app(
                    settings=settings,
                    access_control=access,
                    authentication_provider=authentication,
                )

            app = server_app()
            async with app.router.lifespan_context(app), _http_client(app, over_tcp=True) as http_client:
                url = str(http_client.base_url.join("/mcp/"))
                async with Client(StreamableHttpTransport(url, auth=auth)) as client:
                    tools = {tool.name for tool in await client.list_tools()}
                    assert {"create_scope", "remember_memory", "list_memory_entries"} <= tools
                    scope = await client.call_tool(
                        "create_scope",
                        {
                            "title": "MCP reconnect",
                            "summary": "Persistent authenticated writes",
                            "idempotency_key": "reconnect",
                        },
                    )
                    assert scope.structured_content is not None
                    scope_id = scope.structured_content["scope_id"]
                    await access.create_binding(
                        ALICE,
                        CreateBinding(
                            subject=BOB,
                            resource=ResourceRef.scope(scope_id),
                            role=AccessRole.SCOPE_VIEWER,
                            idempotency_key="bob-viewer",
                        ),
                        context=AccessAuditContext(transport="test", operation="seed"),
                    )
                    await client.call_tool(
                        "remember_memory", {"scope_id": scope_id, "kind": "fact", "text": "first write"}
                    )

                    # Change credentials on the same SDK connection after Alice initialized it.
                    auth.principal_name = "bob"
                    denied = await client.call_tool(
                        "remember_memory",
                        {"scope_id": scope_id, "kind": "fact", "text": "unauthorized write"},
                        raise_on_error=False,
                    )
                    assert denied.is_error
                    assert "403" in denied.content[0].text
                    readable = await client.call_tool("list_memory_entries", {"scope_id": scope_id})
                    assert readable.structured_content is not None
                    assert [entry["text"] for entry in readable.structured_content["entries"]] == ["first write"]

                    auth.principal_name = "alice"
                    await client.call_tool(
                        "remember_memory", {"scope_id": scope_id, "kind": "fact", "text": "write after denied request"}
                    )

                # A fresh SDK connection sees committed writes, including after a failed request.
                auth.principal_name = "bob"
                async with Client(StreamableHttpTransport(url, auth=auth)) as reconnected:
                    saved = await reconnected.call_tool("list_memory_entries", {"scope_id": scope_id})
                    assert saved.structured_content is not None
                    assert {entry["text"] for entry in saved.structured_content["entries"]} == {
                        "first write",
                        "write after denied request",
                    }

            # Let an SSE shutdown watcher poll after the old listener has exited.
            await asyncio.sleep(0.75)
            restarted = server_app()
            async with (
                restarted.router.lifespan_context(restarted),
                _http_client(restarted, over_tcp=True) as http_client,
                Client(StreamableHttpTransport(str(http_client.base_url.join("/mcp/")), auth=auth)) as client,
            ):
                restored = await client.call_tool("list_memory_entries", {"scope_id": scope_id})
                assert restored.structured_content == saved.structured_content

    asyncio.run(scenario())
