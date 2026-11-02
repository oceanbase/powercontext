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
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import ServerSettings


@pytest.mark.parametrize("max_requests", [1, 2])
def test_configured_http_limits_preserve_real_http_and_mcp_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_requests: int
) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_MAX_REQUEST_BODY_BYTES", "1024")
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_MAX_CONCURRENT_REQUESTS", str(max_requests))
    app = create_server_app(
        settings=ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"))
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on", timeout_graceful_shutdown=1))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Server did not start"

        with httpx.Client(base_url=base_url) as http:
            for content in (b"x" * 1025, iter([b"x" * 512, b"x" * 513])):
                oversized = http.post("/v1/scope-bindings/resolve", content=content)
                assert oversized.status_code == 413
                assert oversized.json()["error"]["code"] == "request_too_large"
            resolved = http.post("/v1/scope-bindings/resolve", json={"binding_keys": []})
            assert resolved.status_code == 200
            scope_id = resolved.json()["scope_id"]
            initialized = http.post(
                "/mcp/",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "resource-admission-test", "version": "1"},
                    },
                },
            )
            assert initialized.status_code == 200
            assert "mcp-session-id" not in initialized.headers
            headers = {"Accept": "application/json, text/event-stream"}
            notified = http.post(
                "/mcp/", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
            assert notified.status_code == 202
            called = http.post(
                "/mcp/",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "resolve_scope_binding", "arguments": {"binding_keys": []}},
                },
            )
            assert called.status_code == 200
            result_line = next(line for line in called.text.splitlines() if line.startswith("data:"))
            result = json.loads(result_line.removeprefix("data:"))["result"]
            assert not result.get("isError", False)
            assert result["structuredContent"]["scope_id"] == scope_id

        async def tools() -> None:
            async with Client(StreamableHttpTransport(f"{base_url}/mcp/")) as client:
                resolved = await client.call_tool("resolve_scope_binding", {"binding_keys": []})
                assert not resolved.is_error
                assert resolved.structured_content is not None
                assert resolved.structured_content["scope_id"] == scope_id

        asyncio.run(tools())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
