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

"""Transparent MCP gateway that injects one declared fault in front of a real Server.

Payloads are forwarded unchanged. A transport fault answers the host with a 502 gateway error either before the
request is forwarded or after the Server's complete response was received and discarded, so whether the call reached
the Server is known from this gateway rather than inferred from the host. An ``after_forward`` fault delivers the
response unchanged and then runs a declared harness action, such as a concurrent write by another client.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.background import BackgroundTask, BackgroundTasks
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

FaultMode = Literal["reset_before_forward", "drop_after_forward", "after_forward"]
HOP_BY_HOP = frozenset(
    {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "host", "content-length"}
)


@dataclass(frozen=True, slots=True)
class Fault:
    operation_id: str
    mode: FaultMode
    count: int = 1
    action: str | None = None


@dataclass(frozen=True, slots=True)
class GatewayEvent:
    """One logical tools/call observed by the gateway; never contains arguments or results."""

    operation_id: str
    fault: FaultMode | None
    forwarded: bool


@dataclass
class GatewayLog:
    events: list[GatewayEvent] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, event: GatewayEvent) -> None:
        with self.lock:
            self.events.append(event)

    def snapshot(self) -> list[GatewayEvent]:
        with self.lock:
            return list(self.events)


def tool_calls(body: bytes) -> list[str]:
    """Return the tool names of JSON-RPC tools/call messages in a request body."""

    try:
        decoded = json.loads(body) if body else None
    except ValueError:
        return []
    messages = decoded if isinstance(decoded, list) else [decoded]
    return [
        message["params"]["name"]
        for message in messages
        if isinstance(message, dict)
        and message.get("method") == "tools/call"
        and isinstance(message.get("params"), dict)
        and isinstance(message["params"].get("name"), str)
    ]


def _gateway(
    upstream: str, faults: tuple[Fault, ...], actions: Mapping[str, Callable[[], None]], log: GatewayLog
) -> Starlette:
    declared = {fault.operation_id: fault for fault in faults}
    remaining = {fault.operation_id: fault.count for fault in faults}
    lock = threading.Lock()
    client = httpx.AsyncClient(timeout=None, trust_env=False)  # noqa: S113 - SSE streams stay open for the session

    def take_fault(names: list[str]) -> Fault | None:
        with lock:
            for name in names:
                if remaining.get(name, 0) > 0:
                    remaining[name] -= 1
                    return declared[name]
        return None

    origin = upstream.rstrip("/").removesuffix("/mcp")

    async def forward(request: Request) -> Response:
        body = await request.body()
        mcp = request.url.path == "/mcp" or request.url.path.startswith("/mcp/")
        # A host plugin's hooks share the MCP endpoint's origin; their HTTP calls pass through without faults.
        names = tool_calls(body) if mcp else []
        fault = take_fault(names)
        mode = None if fault is None else fault.mode
        headers = {key: value for key, value in request.headers.items() if key.lower() not in HOP_BY_HOP}
        target = upstream.rstrip("/") + "/" + request.path_params.get("path", "") if mcp else origin + request.url.path
        if mode == "reset_before_forward":
            for name in names:
                log.add(GatewayEvent(name, mode, forwarded=False))
            return Response(status_code=502)
        upstream_request = client.build_request(
            request.method, target, headers=headers, content=body, params=request.query_params
        )
        response = await client.send(upstream_request, stream=True)
        for name in names:
            log.add(GatewayEvent(name, mode, forwarded=True))
        if mode == "drop_after_forward":
            await response.aread()
            await response.aclose()
            return Response(status_code=502)
        response_headers = {key: value for key, value in response.headers.items() if key.lower() not in HOP_BY_HOP}
        background = BackgroundTasks([BackgroundTask(response.aclose)])
        if mode == "after_forward" and fault is not None and fault.action is not None:
            # Starlette runs a synchronous task in its thread pool after the response was fully sent.
            background.add_task(actions[fault.action])
        return StreamingResponse(
            response.aiter_raw(), status_code=response.status_code, headers=response_headers, background=background
        )

    methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]
    return Starlette(
        routes=[
            Route("/mcp/{path:path}", forward, methods=methods),
            Route("/mcp", forward, methods=methods),
            Route("/{rest:path}", forward, methods=methods),
        ]
    )


@dataclass(frozen=True, slots=True)
class Gateway:
    url: str
    log: GatewayLog


@contextmanager
def intercept(
    upstream_mcp_url: str,
    faults: tuple[Fault, ...],
    actions: Mapping[str, Callable[[], None]] | None = None,
    *,
    port: int = 0,
) -> Iterator[Gateway]:
    """Serve the gateway on ``port``; an installed host plugin that pins its endpoint needs a fixed one."""

    actions = actions or {}
    missing = {fault.action for fault in faults if fault.action} - set(actions)
    if missing:
        raise ValueError(f"Undeclared gateway actions: {sorted(missing)}")  # noqa: TRY003
    log = GatewayLog()
    listener = socket.socket()
    listener.bind(("127.0.0.1", port))
    host, port = listener.getsockname()
    server = uvicorn.Server(uvicorn.Config(_gateway(upstream_mcp_url, faults, actions, log), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while thread.is_alive() and not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("The fault gateway did not start")  # noqa: TRY003
        yield Gateway(f"http://{host}:{port}/mcp/", log)
    finally:
        server.should_exit = True
        thread.join(timeout=30)
