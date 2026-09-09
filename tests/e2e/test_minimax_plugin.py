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

"""Exercise the packaged MCP workflow over a real loopback HTTP connection."""

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

import httpx
import pytest
import uvicorn
import yaml
from build_minimax_plugin import build_distribution, json_bytes, write_or_check
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastmcp import Client

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings


@pytest.fixture
def minimax_server(tmp_path: Path) -> Iterator[tuple[FastAPI, str]]:
    app = create_server_app(
        settings=ServerSettings(
            workspace=tmp_path,
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"),
            runtime=RuntimeConfig(),
            inference=InferenceConfig(),
            auth=BearerAuthConfig(),
            access=AccessControlConfig(mode="disabled"),
            mcp=McpConfig(enabled=True),
        ),
        scheduler_path=tmp_path / "scheduler.db",
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started

            yield app, f"http://127.0.0.1:{port}"

        finally:
            server.should_exit = True
            thread.join(timeout=15)
            assert not thread.is_alive()


def test_packaged_mcp_memory_and_handoff_over_http(minimax_server: tuple[FastAPI, str]) -> None:
    files = build_distribution()
    configuration = json.loads(files["powercontext.mcp.json"])
    examples = json.loads(files["skills/powercontext-project-context/references/examples.json"])
    _, base_url = minimax_server
    configuration["mcpServers"]["powercontext"]["url"] = f"{base_url}/mcp"

    async def exercise() -> None:
        async with Client(configuration) as client:
            tools = await client.list_tools()
            assert {"resolve_scope_binding", "remember_memory", "handoff_current_work"} <= {tool.name for tool in tools}
            created = await client.call_tool(
                "create_scope",
                {
                    "title": "MiniMax acceptance",
                    "summary": "Isolated package workflow verification",
                    "idempotency_key": "minimax-acceptance",
                },
            )
            scope_id = created.structured_content["scope_id"]
            binding = await client.call_tool("resolve_scope_binding", {"explicit_scope_id": scope_id})
            assert binding.structured_content["scope_id"] == scope_id
            search = {**examples["search_memory"], "scope_id": scope_id, "query": "rollback"}
            empty = await client.call_tool("search_memory", search)
            assert empty.structured_content["hits"] == []
            await client.call_tool("remember_memory", {**examples["remember_memory"], "scope_id": scope_id})
            found = await client.call_tool("search_memory", search)
            hit = found.structured_content["hits"][0]
            assert "rollback" in hit["text"]
            exact = await client.call_tool(
                "get_memory_entry",
                {
                    "scope_id": scope_id,
                    "citation": hit["citation"],
                },
            )
            assert exact.structured_content["text"] == examples["remember_memory"]["text"]
            prepared = await client.call_tool(
                "handoff_current_work",
                {
                    **examples["handoff_current_work"],
                    "scope_id": scope_id,
                },
            )
            handoff = prepared.structured_content["handoff"]
            temporary = await client.call_tool(
                "continue_handoff",
                {
                    "scope_id": scope_id,
                    "selection": "prepared",
                    "prepared": handoff,
                },
            )
            assert temporary.structured_content["trust"] == "untrusted_history"
            assert temporary.structured_content["status"] == "resolved"
            assert temporary.structured_content["content"]["objective"] == handoff["content"]["objective"]
            assert temporary.structured_content["selected_revision"] is None
            latest = await client.call_tool("continue_handoff", {"scope_id": scope_id, "selection": "latest"})
            assert latest.structured_content["status"] == "empty"
            committed = await client.call_tool("commit_handoff", {"scope_id": scope_id, "handoff": handoff})
            resumed = await client.call_tool(
                "continue_handoff",
                {
                    "scope_id": scope_id,
                    "selection": "exact",
                    "revision": committed.structured_content["reference"],
                },
            )
            assert resumed.structured_content["selected_revision"] == committed.structured_content["reference"]

    asyncio.run(exercise())


def test_native_minimax_exec_loads_skill_and_calls_mcp(tmp_path: Path, minimax_server: tuple[FastAPI, str]) -> None:
    """Script model outputs, but execute the real host, Skill loader, and MCP tools."""
    executable = shutil.which("mcode")
    if executable is None:
        pytest.skip("MiniMax Code is not installed")
    app, base_url = minimax_server
    files = build_distribution()
    examples = json.loads(files["skills/powercontext-project-context/references/examples.json"])
    scope = httpx.post(
        f"{base_url}/v1/scopes",
        json={
            "title": "Native MiniMax acceptance",
            "summary": "Synthetic native host verification",
            "idempotency_key": "native-minimax-acceptance",
        },
    )
    scope.raise_for_status()
    scope_id = scope.json()["scope_id"]
    actions = [
        ("skill", {"name": "powercontext:powercontext-project-context"}),
        ("mcp__powercontext__resolve_scope_binding", {"explicit_scope_id": scope_id}),
        ("mcp__powercontext__remember_memory", {**examples["remember_memory"], "scope_id": scope_id}),
        (
            "mcp__powercontext__search_memory",
            {
                **examples["search_memory"],
                "scope_id": scope_id,
                "query": "rollback",
            },
        ),
        ("mcp__powercontext__handoff_current_work", {**examples["handoff_current_work"], "scope_id": scope_id}),
    ]
    observed: dict[str, dict[str, Any]] = {}

    @app.post("/test-model/{route:path}")
    async def respond(request: Request, route: str):
        if route.endswith("count_tokens"):
            return {"input_tokens": 1000}
        body = await request.json()
        for message in body["messages"]:
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "tool_result" and block["tool_use_id"].startswith("probe-"):
                        observed[block["tool_use_id"]] = block
        tool_names = {tool["name"] for tool in body.get("tools", [])}
        step = len(observed)
        action = actions[step] if step < len(actions) and actions[step][0] in tool_names else None
        return StreamingResponse(_model_events(step, action), media_type="text/event-stream")

    data = tmp_path / "minimax"
    configuration = json.loads(files["powercontext.mcp.json"])
    configuration["mcpServers"]["powercontext"]["url"] = f"{base_url}/mcp"
    files["powercontext.mcp.json"] = json_bytes(configuration)
    write_or_check(data / "plugins/powercontext", files, check=False)
    config = {
        "defaultModel": "custom_provider:probe/probe",
        "custom_provider": {
            "probe": {
                "kind": "custom",
                "enabled": True,
                "options": {"baseURL": f"{base_url}/test-model", "apiKey": "local-test-only", "authMode": "api-key"},
                "models": {"probe": {"name": "probe", "limit": {"context": 128000, "output": 4096}, "tool_call": True}},
            }
        },
        "memory": {"enabled": False, "proactive": False},
        "skillEvolve": {"enabled": False},
    }
    (data / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    result = subprocess.run(
        [
            executable,
            "exec",
            "--cwd",
            str(tmp_path),
            "--permission",
            "full",
            "--timeout",
            "45s",
            "--max-steps",
            "8",
            "--output-format",
            "json",
            "Use the PowerContext Skill on the isolated test Scope. Save the test rollback decision, search for it, "
            "and prepare a temporary handoff. These test operations are authorized. Do not use other tools.",
        ],
        env={**os.environ, "MINIMAX_DATA_DIR": str(data)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=65,
    )
    assert result.returncode == 0, result.stderr
    assert "NATIVE_LINK_OK" in result.stdout
    assert len(observed) == len(actions)
    assert all(not block.get("is_error") for block in observed.values())
    assert "powercontext-project-context" in observed["probe-0"]["content"]
    found = json.loads(observed["probe-3"]["content"])
    assert found["hits"][0]["text"] == examples["remember_memory"]["text"]
    handoff = json.loads(observed["probe-4"]["content"])
    assert handoff["handoff"]["scope_id"] == scope_id
    stored = httpx.post(f"{base_url}/v1/memory/entries/list", json={"scope_id": scope_id})
    stored.raise_for_status()
    assert len(stored.json()["entries"]) == 1


def _model_events(step: int, action: tuple[str, dict[str, Any]] | None) -> Iterator[str]:
    """Serve the Anthropic wire format used by the host's local custom provider."""
    yield _event(
        "message_start",
        message={
            "id": f"message-{step}",
            "type": "message",
            "role": "assistant",
            "model": "probe",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 1000, "output_tokens": 0},
        },
    )
    if action is None:
        block = {"type": "text", "text": ""}
        delta = {"type": "text_delta", "text": "NATIVE_LINK_OK"}
        reason = "end_turn"
    else:
        name, arguments = action
        block = {"type": "tool_use", "id": f"probe-{step}", "name": name, "input": {}}
        delta = {"type": "input_json_delta", "partial_json": json.dumps(arguments)}
        reason = "tool_use"
    yield _event("content_block_start", index=0, content_block=block)
    yield _event("content_block_delta", index=0, delta=delta)
    yield _event("content_block_stop", index=0)
    yield _event("message_delta", delta={"stop_reason": reason, "stop_sequence": None}, usage={"output_tokens": 10})
    yield _event("message_stop")


def _event(kind: str, **fields: Any) -> str:
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **fields})}\n\n"
