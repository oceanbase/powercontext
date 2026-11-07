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

"""Disposable real PowerContext Servers, case seeds, and content-free final-state observations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import socket
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, cast

import httpx
import uvicorn
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams
from starlette.applications import Starlette
from starlette.middleware import Middleware as AsgiMiddleware
from starlette.routing import Mount
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from typing_extensions import override

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.server.authentication import (
    AuthenticationRejectedError,
    AuthenticationRequest,
    AuthenticationResult,
    ProviderReadiness,
)
from powercontext.server.authz import AccessBinding, AccessBindingState, AccessRole, ResourceRef
from powercontext.server.authz.models import PrincipalRef
from powercontext.server.authz.repository import ACCESS_TABLES, RelationalAccessRepository
from powercontext.server.context import is_internal_bridge
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, ServerSettings

from .environment import loopback_proxy_environment

# A Server with model configuration needs more than 30 seconds to finish startup on Windows.
STARTUP_SECONDS = 180
DREAM_CONTRACT = {"X-PowerContext-Dream-Contract": "2"}
# Harness MCP connections are loopback-only; retain MCP's connect and SSE read timeouts.
LOOPBACK_MCP_HTTP_CLIENT = partial(httpx.AsyncClient, trust_env=False, timeout=httpx.Timeout(30.0, read=300.0))
# Synthetic credentials that exist only inside the disposable access-profile Server.
ADMIN = PrincipalRef(type="user", id="eval-admin")
VIEWER = PrincipalRef(type="user", id="eval-viewer")
TOKENS = {"eval-admin-token": ADMIN, "eval-viewer-token": VIEWER}  # noqa: S105
ADMIN_HEADERS = {"Authorization": "Bearer eval-admin-token"}
VIEWER_HEADERS = {"Authorization": "Bearer eval-viewer-token"}


@dataclass(frozen=True, slots=True)
class ServedRequest:
    """Content-free record of one HTTP request the Server routed, including MCP's internal bridge.

    ``mcp_call`` is the index of the MCP tools/call that sent a bridge request; records written before calls were
    correlated have none.
    """

    operation_id: str
    status: int
    internal: bool
    mcp_call: int | None = None


@dataclass
class RequestLog:
    requests: list[ServedRequest] = field(default_factory=list)
    # Tool name of every MCP tools/call the Server received, indexed by ServedRequest.mcp_call.
    mcp_calls: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> list[ServedRequest]:
        with self.lock:
            return list(self.requests)

    def snapshot_mcp_calls(self) -> list[str]:
        with self.lock:
            return list(self.mcp_calls)


# The MCP tools/call whose handler is running; its bridge requests run in the same context.
_mcp_call: ContextVar[int | None] = ContextVar("eval_mcp_call", default=None)


class _McpCallRecorder(Middleware):
    """Number each tools/call, including one the MCP layer rejects before any bridge request."""

    def __init__(self, log: RequestLog) -> None:
        self.log = log

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        with self.log.lock:
            self.log.mcp_calls.append(context.message.name)
            index = len(self.log.mcp_calls) - 1
        token = _mcp_call.set(index)
        try:
            return await call_next(context)
        finally:
            _mcp_call.reset(token)


class _RequestRecorder:
    """Observe routed operations without reading or changing requests and responses."""

    def __init__(self, app: ASGIApp, *, log: RequestLog) -> None:
        self.app = app
        self.log = log

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        internal = is_internal_bridge()
        mcp_call = _mcp_call.get() if internal else None
        status = 500

        async def record_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, record_status)
        finally:
            operation_id = getattr(scope.get("route"), "operation_id", None)
            if isinstance(operation_id, str):
                with self.log.lock:
                    self.log.requests.append(ServedRequest(operation_id, status, internal, mcp_call))


class _TwoTokenAuthentication:
    """Authenticate the two synthetic evaluation Principals of the access profile."""

    async def authenticate(self, request: AuthenticationRequest, /) -> AuthenticationResult:
        scheme, _, credential = (request.headers.get("authorization") or "").partition(" ")
        principal = TOKENS.get(credential) if scheme.casefold() == "bearer" else None
        if principal is None:
            raise AuthenticationRejectedError
        return AuthenticationResult(subject=principal, credential_id=f"eval-{principal.id}")

    async def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(ready=True)


def _blackhole() -> tuple[int, Callable[[], None]]:
    """Accept model connections and never answer, so admitted Dream runs stay queued or running."""

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    held: list[socket.socket] = []

    def accept() -> None:
        while True:
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            held.append(connection)

    threading.Thread(target=accept, daemon=True).start()

    def close() -> None:
        listener.close()
        for connection in held:
            connection.close()

    return listener.getsockname()[1], close


@dataclass(frozen=True, slots=True)
class EvalServer:
    base_url: str
    requests: RequestLog
    tool_catalog: tuple[dict[str, Any], ...]
    tool_catalog_sha256: str
    host_headers: dict[str, str]
    seed_headers: dict[str, str]

    @property
    def mcp_url(self) -> str:
        return f"{self.base_url}/mcp/"


def _settings(profile: str, directory: Path, database_url: str, model_port: int | None) -> ServerSettings:
    database = SQLiteConfig(url=database_url)
    common: dict[str, Any] = {"workspace": directory, "database": database, "mcp": McpConfig(enabled=True)}
    if profile == "default":
        return ServerSettings(**common)
    if profile == "access":
        return ServerSettings(**common, access=AccessControlConfig(mode="enforced"))
    if profile == "dream":
        # Dream admission needs a declared generation model; the endpoint is a local socket that never answers.
        return ServerSettings(
            **common,
            runtime=RuntimeConfig(dream_max_pending_per_scope=1),
            inference=InferenceConfig.model_validate(
                {
                    "generation_model": "openai:eval-unreachable",
                    "generation_base_url": f"http://127.0.0.1:{model_port}/v1",
                }
            ),
        )
    raise ValueError(f"Unknown server profile: {profile}")  # noqa: TRY003


async def _catalog(mcp_url: str, headers: dict[str, str]) -> tuple[dict[str, Any], ...]:
    async with Client(
        StreamableHttpTransport(mcp_url, headers=headers, httpx_client_factory=LOOPBACK_MCP_HTTP_CLIENT)
    ) as client:
        tools = await client.list_tools()
    return tuple(
        sorted(
            (
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                    "read_only": bool(tool.annotations and tool.annotations.readOnlyHint),
                }
                for tool in tools
            ),
            key=lambda item: item["name"],
        )
    )


async def _seed_admin(database_url: str) -> None:
    async with SQLiteProfile.open(SQLiteConfig(url=database_url), tables=ACCESS_TABLES) as profile:
        await RelationalAccessRepository(profile.database).create_binding(
            AccessBinding(
                binding_id="eval-admin",
                subject=ADMIN,
                resource=ResourceRef.server(),
                role=AccessRole.SERVER_ADMIN,
                granted_by=ADMIN,
                reason="evaluation bootstrap",
                created_at=datetime.now(UTC),
                expires_at=None,
                state=AccessBindingState.ACTIVE,
                version=1,
                policy_revision="pending",
                idempotency_key="eval-admin",
            )
        )


def _record_mcp_calls(app: FastAPI, log: RequestLog) -> None:
    mount = next(route for route in app.routes if isinstance(route, Mount) and route.name == "mcp")
    # FastMCP runs its first middleware outermost; the Server's own MCP rejections happen in inner middleware.
    cast(Starlette, mount.app).state.fastmcp_server.middleware.insert(0, _McpCallRecorder(log))


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


@contextmanager
def serve(profile: str) -> Iterator[EvalServer]:
    """Start one real Server with an isolated database and stop it with its data."""

    with loopback_proxy_environment() if profile == "dream" else nullcontext():
        directory = Path(tempfile.mkdtemp(prefix="powercontext-tool-recovery-"))
        model_port, close_model = _blackhole() if profile == "dream" else (None, lambda: None)
        database_url = f"sqlite+aiosqlite:///{(directory / 'eval.db').as_posix()}"
        settings = _settings(profile, directory, database_url, model_port)
        log = RequestLog()
        app = create_server_app(
            settings=settings,
            middleware=(AsgiMiddleware(_RequestRecorder, log=log),),
            authentication_provider=_TwoTokenAuthentication() if profile == "access" else None,
        )
        _record_mcp_calls(app, log)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        host, port = listener.getsockname()
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + STARTUP_SECONDS
            while thread.is_alive() and not server.started and time.monotonic() < deadline:
                time.sleep(0.05)
            if not server.started:
                raise RuntimeError("The evaluation Server did not start")  # noqa: TRY003
            if profile == "access":
                asyncio.run(_seed_admin(database_url))
            host_headers = VIEWER_HEADERS if profile == "access" else {}
            seed_headers = ADMIN_HEADERS if profile == "access" else {}
            base_url = f"http://{host}:{port}"
            catalog = asyncio.run(_catalog(f"{base_url}/mcp/", host_headers))
            yield EvalServer(base_url, log, catalog, canonical_sha256(catalog), host_headers, seed_headers)
        finally:
            server.should_exit = True
            thread.join(timeout=30)
            close_model()
            shutil.rmtree(directory, ignore_errors=True)


def _ok(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    return response.json()


def _scope(http: httpx.Client) -> str:
    created = http.post(
        "/v1/scopes",
        json={"title": "Tool recovery", "summary": "Evaluation Scope", "idempotency_key": "seed-scope"},
    )
    return _ok(created)["scope_id"]


def _source(http: httpx.Client, scope_id: str) -> dict[str, str]:
    captured = http.post(
        "/v1/sources/content",
        json={"scope_id": scope_id, "source_id": "release-notes", "content": "Fixed a flaky retry in CI."},
    )
    return _ok(captured)["source"]


PROPOSAL = {
    "situation": "CI retried a flaky integration test.",
    "action": "Pinned the test database image.",
    "outcome": "The test became deterministic.",
    "lesson": "Pin service images used by integration tests.",
}


def _candidate(http: httpx.Client, scope_id: str, source: dict[str, str]) -> str:
    proposed = http.post(
        "/v1/experience/propose",
        json={"scope_id": scope_id, "proposal": PROPOSAL, "source_refs": [source], "artifact_refs": []},
    )
    return _ok(proposed)["candidate_id"]


def _experience(http: httpx.Client, scope_id: str) -> dict[str, Any]:
    candidate_id = _candidate(http, scope_id, _source(http, scope_id))
    approved = http.post(
        "/v1/candidates/approve", json={"scope_id": scope_id, "candidate_id": candidate_id, "expected_version": 1}
    )
    return _ok(approved)["result_artifact"]


def seed(name: str, server: EvalServer) -> dict[str, Any]:  # noqa: C901
    """Create the case's starting state through public HTTP and return prompt placeholders."""

    with httpx.Client(base_url=server.base_url, timeout=60, headers=server.seed_headers, trust_env=False) as http:
        if name == "none":
            return {}
        scope_id = _scope(http)
        if name == "scope":
            return {"scope_id": scope_id}
        if name == "scope_with_recent_expiry":
            # A date the model can recognize as past lets it decline before calling; a time earlier today
            # leaves the future-expiry rule, which the input schema cannot express, to the Server.
            expired = (datetime.now(UTC) - timedelta(hours=2)).replace(microsecond=0)
            return {"scope_id": scope_id, "recent_past_expiry": expired.isoformat().replace("+00:00", "Z")}
        if name == "scope_with_source":
            source = _source(http, scope_id)
            return {"scope_id": scope_id, "source_name": source["name"], "source_id": source["source_id"]}
        if name == "viewer_scope":
            granted = http.post(
                "/v1/access/bindings/create",
                json={
                    "subject": {"type": VIEWER.type, "id": VIEWER.id},
                    "resource": {"type": "scope", "scope_id": scope_id},
                    "role": AccessRole.SCOPE_VIEWER.value,
                    "idempotency_key": "eval-viewer",
                },
            )
            _ok(granted)
            # Server administration alone does not read Scope content; the final-state check needs Scope access.
            _ok(
                http.post(
                    "/v1/access/bindings/create",
                    json={
                        "subject": {"type": ADMIN.type, "id": ADMIN.id},
                        "resource": {"type": "scope", "scope_id": scope_id},
                        "role": AccessRole.SCOPE_ADMIN.value,
                        "idempotency_key": "eval-admin-scope",
                    },
                )
            )
            return {"scope_id": scope_id}
        if name == "memory_scope":
            _ok(
                http.post(
                    "/v1/memory/remember",
                    json={"scope_id": scope_id, "kind": "decision", "text": "We use uv for environment management."},
                )
            )
            return {"scope_id": scope_id}
        if name == "stale_candidate":
            candidate_id = _candidate(http, scope_id, _source(http, scope_id))
            source_refs = [{"name": "content", "source_id": "release-notes"}]
            _ok(
                http.post(
                    "/v1/candidates/revise",
                    json={
                        "scope_id": scope_id,
                        "candidate_id": candidate_id,
                        "expected_version": 1,
                        "proposal": {**PROPOSAL, "lesson": "Pin every external image used by CI."},
                        "source_refs": source_refs,
                        "artifact_refs": [],
                    },
                )
            )
            return {"scope_id": scope_id, "candidate_id": candidate_id}
        if name == "atomic_memory":
            records = _ok(
                http.post(
                    "/v1/memory/remember",
                    json={"scope_id": scope_id, "kind": "decision", "text": "We use uv for environment management."},
                )
            )
            return {"scope_id": scope_id, "artifact_id": records["records"][0]["artifact"]["artifact_id"]}
        if name == "stale_etag":
            records = _ok(
                http.post(
                    "/v1/memory/remember",
                    json={"scope_id": scope_id, "kind": "decision", "text": "We use uv for environment management."},
                )
            )
            artifact_id = records["records"][0]["artifact"]["artifact_id"]
            path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}"
            current = http.get(path)
            current.raise_for_status()
            stale_etag = current.headers["ETag"]
            _ok(
                http.put(
                    path,
                    headers={"If-Match": stale_etag},
                    json={"content": {"kind": "decision", "text": "We use uv and pin its version in CI."}},
                )
            )
            return {"scope_id": scope_id, "artifact_id": artifact_id, "stale_etag": stale_etag}
        if name == "experience":
            artifact = _experience(http, scope_id)
            return {"scope_id": scope_id, "artifact_id": artifact["artifact_id"], "revision": artifact["revision"]}
        if name == "queued_dream":
            artifact = _experience(http, scope_id)
            _ok(
                http.post(
                    f"/v1/scopes/{scope_id}/dream",
                    headers=DREAM_CONTRACT,
                    json={"operation": "refine_experience", "artifacts": [artifact], "idempotency_key": "seed-dream"},
                )
            )
            return {"scope_id": scope_id, "artifact_id": artifact["artifact_id"], "revision": artifact["revision"]}
    raise ValueError(f"Unknown seed: {name}")  # noqa: TRY003


def actions(server: EvalServer, placeholders: dict[str, Any]) -> dict[str, Callable[[], None]]:
    """Harness actions a gateway fault may run; each acts as another client through public HTTP."""

    def concurrent_atomic_write() -> None:
        path = f"/v1/scopes/{placeholders['scope_id']}/artifacts/atomic-memory/{placeholders['artifact_id']}"
        with httpx.Client(base_url=server.base_url, timeout=60, headers=server.seed_headers, trust_env=False) as http:
            current = http.get(path)
            current.raise_for_status()
            _ok(
                http.put(
                    path,
                    headers={"If-Match": current.headers["ETag"]},
                    json={"content": {"kind": "decision", "text": "We use uv and pin its version in CI."}},
                )
            )

    return {"concurrent_atomic_write": concurrent_atomic_write}


def observe(name: str | None, server: EvalServer, placeholders: dict[str, Any]) -> dict[str, Any]:
    """Read content-free final state through public HTTP, so success is never taken from assistant prose."""

    if name is None:
        return {}
    with httpx.Client(base_url=server.base_url, timeout=60, headers=server.seed_headers, trust_env=False) as http:
        if name == "scope_titles":
            titles = [item["title"] for item in _ok(http.get("/v1/scopes", params={"limit": 100}))["items"]]
            # Titles are caller content; only duplicate counts leave the Server.
            return {"scopes": len(titles), "max_scopes_sharing_title": max(map(titles.count, titles), default=0)}
        if name == "candidate":
            candidate = _ok(
                http.post(
                    "/v1/candidates/get",
                    json={"scope_id": placeholders["scope_id"], "candidate_id": placeholders["candidate_id"]},
                )
            )
            return {"candidate_status": candidate["status"], "candidate_version": candidate["version"]}
        if name == "atomic_revision":
            path = f"/v1/scopes/{placeholders['scope_id']}/artifacts/atomic-memory/{placeholders['artifact_id']}"
            return {"artifact_revision": _ok(http.get(path))["revision"]}
        if name == "dream_runs":
            runs = _ok(http.get(f"/v1/scopes/{placeholders['scope_id']}/dream", headers=DREAM_CONTRACT))
            return {"dream_runs": len(runs["runs"])}
    raise ValueError(f"Unknown observation: {name}")  # noqa: TRY003
