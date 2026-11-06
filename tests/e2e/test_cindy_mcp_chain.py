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

"""Cindy provider-source acceptance over TCP; does not launch Desktop or a model."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, ServerSettings

CINDY_COMMIT = "6e12261af3121a1fc8f56a23481f3b99bb32787c"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROVIDER_SCRIPT = """
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const { CustomMcpProvider } = await import(pathToFileURL(process.argv[1]).href);
const input = JSON.parse(readFileSync(0, 'utf8'));
const context = { agentKind: input.agentKind, workingDir: '', vendorOptions: {} };
const provider = new CustomMcpProvider(input.config, () => input.token);
const config = input.agentKind === 'claude-code'
  ? provider.toClaudeSdkConfig(context)
  : provider.toCodexMcpConfig(context);
process.stdout.write(JSON.stringify({ config, env: provider.getExtraEnv(context) ?? {} }));
"""


@pytest.fixture(name="cindy_provider")
def cindy_provider() -> Path:
    source = os.environ.get("POWERCONTEXT_CINDY_SOURCE")
    if not source:
        pytest.skip("set POWERCONTEXT_CINDY_SOURCE to the Cindy v0.1.115 source checkout")
    root = Path(source).resolve()
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607 - fixed executable, no shell.
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert revision == CINDY_COMMIT, "provider acceptance requires the pinned Cindy 0.1.115 source"
    provider = root / "apps/desktop/src/main/mcp-integrations/custom-mcp-provider.ts"
    assert provider.is_file()
    return provider


@pytest.fixture(name="cindy_server")
def cindy_server(tmp_path: Path, cindy_provider: Path) -> Iterator[tuple[str, str]]:
    token = uuid4().hex
    storage = tmp_path / "server"
    storage.mkdir()
    app = create_server_app(
        settings=ServerSettings(
            auth=BearerAuthConfig(token=SecretStr(token)),
            access=AccessControlConfig(mode="enforced"),
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{storage / 'cindy.db'}"),
        ),
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started:
            assert thread.is_alive(), "the isolated Server exited before startup"
            assert time.monotonic() < deadline, "the isolated Server did not start"
            time.sleep(0.05)
        yield url, token
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        assert not thread.is_alive(), "the isolated Server was not cleaned up"
        shutil.rmtree(storage)


def _native_transport(provider: Path, url: str, token: str, agent_kind: str) -> StreamableHttpTransport:
    node = shutil.which("node")
    assert node is not None, "Cindy provider-source acceptance requires Node 22.18+"
    portable = json.loads((PROJECT_ROOT / "integrations/agent-plugin/powercontext/mcp.json").read_text())
    entry = portable["mcpServers"]["powercontext"]
    transport = {"streamable-http": "http"}[entry["type"]]
    endpoint = url + urlsplit(entry["url"]).path
    result = subprocess.run(
        [node, "--experimental-strip-types", "--input-type=module", "--eval", PROVIDER_SCRIPT, str(provider)],
        input=json.dumps({
            "agentKind": agent_kind,
            "token": token,
            "config": {
                "id": "powercontext",
                "name": "PowerContext",
                "transport": transport,
                "url": endpoint,
                "headers": {},
            },
        }),
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    projected = json.loads(result.stdout)
    config = projected["config"]
    assert config["type"] == "http"
    if agent_kind == "claude-code":
        headers = config["headers"]
    else:
        headers = {"Authorization": "Bearer " + projected["env"][config["bearerTokenEnvVar"]]}
    return StreamableHttpTransport(config["url"], headers=headers)


@pytest.mark.parametrize("agent_kind", ["claude-code", "codex", "pi"])
def test_cindy_native_configuration_preserves_scoped_mcp_operations(
    cindy_provider: Path, cindy_server: tuple[str, str], agent_kind: str
) -> None:
    url, token = cindy_server
    with httpx.Client(base_url=url) as unauthenticated:
        assert unauthenticated.get("/mcp/").status_code == 401
    transport = _native_transport(cindy_provider, url, token, agent_kind)

    async def scenario() -> None:
        async with Client(transport) as client:
            catalog = {tool.name for tool in await client.list_tools()}
            assert {"search_memory", "remember_memory", "capture_content_source", "resolve_scope_binding"} <= catalog

            async def call(name: str, **arguments: Any) -> dict[str, Any]:
                result = await client.call_tool(name, arguments)
                assert not result.is_error
                assert result.structured_content is not None
                return result.structured_content

            missing = await client.call_tool("resolve_scope_binding", {"allow_default": False}, raise_on_error=False)
            assert missing.is_error
            selected = await call(
                "create_scope", title="Cindy project", summary="Selected project", idempotency_key="selected"
            )
            other = await call(
                "create_scope", title="Other project", summary="Independent project", idempotency_key="other"
            )
            scope_id = selected["scope_id"]
            resolved = await call("resolve_scope_binding", explicit_scope_id=scope_id, allow_default=False)
            assert resolved["scope_id"] == scope_id
            written = await call(
                "remember_memory", scope_id=scope_id, kind="decision", text="Cindy uses the shared MCP contract."
            )
            assert written["records"][0]["text"] == "Cindy uses the shared MCP contract."
            empty = await call("search_memory", scope_id=other["scope_id"], query="Cindy", mode="fts")
            assert empty["hits"] == []
            payload = {
                "scope_id": scope_id,
                "source_id": "cindy-explicit-source",
                "content": "Original user-selected task material.",
            }
            captured = await call("capture_content_source", **payload)
            retried = await call("capture_content_source", **payload)
            assert retried["source"] == captured["source"]
            assert retried["position"] == captured["position"]
            conflict = await client.call_tool(
                "capture_content_source", {**payload, "content": "Different material."}, raise_on_error=False
            )
            assert conflict.is_error

        async with Client(transport) as reconnected:
            result = await reconnected.call_tool(
                "search_memory", {"scope_id": scope_id, "query": "Cindy", "mode": "fts"}
            )
            assert result.structured_content is not None
            assert result.structured_content["hits"][0]["memory"]["text"] == "Cindy uses the shared MCP contract."

    asyncio.run(scenario())
