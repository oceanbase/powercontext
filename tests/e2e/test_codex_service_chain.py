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
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import SecretStr
from pydantic_ai.models.test import TestModel

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.client import PowerContextClient
from powercontext.http import (
    ListMemoryEntriesRequest,
    PrepareContextRequest,
    RetireMemoryEntryRequest,
    SearchMemoryRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODEX_PLUGIN = PROJECT_ROOT / "integrations" / "codex" / "plugins" / "powercontext"
AUTH_TOKEN = "codex-e2e-token"  # noqa: S105 - non-secret test credential.
AUTHORIZATION = f"Bearer {AUTH_TOKEN}"


@pytest.mark.parametrize("authentication_enabled", [False, True], ids=["public", "authenticated"])
def test_codex_hook_http_sdk_and_mcp_share_one_composed_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authentication_enabled: bool,
) -> None:
    model_output = """
    {
      "candidates": [{
        "intent": "add",
        "kind": "decision",
        "text": "Use PowerContext as the composition root.",
        "evidence_ids": ["source:0"],
        "reason": "captured by the Codex hook"
      }]
    }
    """
    monkeypatch.setattr(
        "pydantic_ai.models.infer_model",
        lambda _: TestModel(custom_output_text=model_output),
    )
    app = create_server_app(
        settings=ServerSettings(
            auth=BearerAuthConfig(
                enabled=authentication_enabled,
                token=SecretStr(AUTH_TOKEN) if authentication_enabled else None,
            ),
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            inference=InferenceConfig(generation_model="test"),
            mcp=McpConfig(enabled=True),
        )
    )

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    host, port = listener.getsockname()
    base_url = f"http://{host}:{port}"
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="critical",
            lifespan="on",
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    try:
        _wait_until_started(server, thread)
        plugin = tmp_path / "plugin"
        shutil.copytree(
            CODEX_PLUGIN,
            plugin,
            ignore=shutil.ignore_patterns("__pycache__", ".venv"),
        )
        mcp_configuration = json.loads((plugin / ".mcp.json").read_text())
        mcp_configuration["mcpServers"]["powercontext"]["url"] = f"{base_url}/mcp"
        (plugin / ".mcp.json").write_text(json.dumps(mcp_configuration))
        scope_id = _create_scope(
            base_url,
            authorization=AUTHORIZATION if authentication_enabled else None,
        )

        first = _run_hook(
            plugin,
            prompt="Remember which object is the composition root.",
            turn_id="turn-1",
            authorization=AUTHORIZATION if authentication_enabled else None,
            scope_id=scope_id,
        )
        assert first.stdout == ""
        assert AUTH_TOKEN not in first.stderr

        recalled = _run_hook(
            plugin,
            prompt="Which composition root should this project use?",
            turn_id="turn-2",
            authorization=AUTHORIZATION if authentication_enabled else None,
            scope_id=scope_id,
        )
        context = json.loads(recalled.stdout)["hookSpecificOutput"]["additionalContext"]
        envelope = json.loads(context.splitlines()[-2])
        assert envelope["items"][0]["content"] == "Use PowerContext as the composition root."
        assert envelope["items"][0]["citation"]["memory_ref"]["family"] == "memory"
        assert AUTH_TOKEN not in recalled.stderr

        async def verify_transport_surfaces() -> None:
            async with PowerContextClient(base_url, token=AUTH_TOKEN if authentication_enabled else None) as sdk:
                found = await sdk.search_memory(
                    SearchMemoryRequest(
                        scope_id=scope_id,
                        query="PowerContext composition root",
                    )
                )
                prepared = await sdk.prepare_context(
                    PrepareContextRequest(
                        scope_id=scope_id,
                        query="PowerContext composition root",
                    )
                )
                entries = await sdk.list_memory_entries(
                    ListMemoryEntriesRequest(scope_id=scope_id),
                )
                assert found.hits
                assert {hit.text for hit in found.hits} == {"Use PowerContext as the composition root."}
                assert prepared.content is not None
                prepared_envelope = json.loads(prepared.content.splitlines()[-2])
                assert "Use PowerContext as the composition root." in {
                    item["content"] for item in prepared_envelope["items"]
                }
                assert entries.entries
                assert entries.entries[0].source_refs[0].name == "content"

                transport = StreamableHttpTransport(
                    f"{base_url}/mcp",
                    headers={"Authorization": AUTHORIZATION} if authentication_enabled else None,
                )
                async with Client(transport) as mcp:
                    result = await mcp.call_tool(
                        "search_memory",
                        {
                            "scope_id": scope_id,
                            "query": "PowerContext composition root",
                        },
                    )
                structured = result.structured_content or {}
                hits = structured.get("hits")
                assert isinstance(hits, list)
                assert hits[0]["text"] == "Use PowerContext as the composition root."

                retired_entry_ids: set[str] = set()
                current = entries
                while current.entries:
                    retired = await sdk.retire_memory_entry(
                        RetireMemoryEntryRequest(
                            scope_id=scope_id,
                            citation=current.entries[0].citation,
                            reason="superseded",
                        ),
                    )
                    assert retired.entry is not None
                    retired_entry_ids.add(retired.entry.citation.entry_id)
                    current = await sdk.list_memory_entries(
                        ListMemoryEntriesRequest(scope_id=scope_id),
                    )
                audited = await sdk.list_memory_entries(
                    ListMemoryEntriesRequest(scope_id=scope_id, include_inactive=True),
                )
                assert current.entries == []
                assert {entry.citation.entry_id for entry in audited.entries} == retired_entry_ids
                assert all(entry.state == "inactive" for entry in audited.entries)

        asyncio.run(verify_transport_surfaces())

        excluded = _run_hook(
            plugin,
            prompt="Which composition root should this project use?",
            turn_id="turn-3",
            authorization=AUTHORIZATION if authentication_enabled else None,
            scope_id=scope_id,
        )
        assert excluded.stdout == ""
        assert AUTH_TOKEN not in excluded.stderr
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()


def test_codex_session_binding_switch_resume_and_child_scope_flow(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'scope-flow.db'}"),
            mcp=McpConfig(enabled=True),
        )
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    host, port = listener.getsockname()
    base_url = f"http://{host}:{port}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        _wait_until_started(server, thread)
        plugin = _copy_plugin(tmp_path, base_url)
        root_scope_id = _create_named_scope(base_url, title="Feature", key="codex-feature")
        first_child_id = _create_named_scope(
            base_url,
            title="Research result",
            key="codex-research",
            parent_scope_id=root_scope_id,
        )
        second_child_id = _create_named_scope(
            base_url,
            title="Validation result",
            key="codex-validation",
            parent_scope_id=root_scope_id,
        )

        _run_codex_plugin_hook(plugin, "session_binding.py", session_id="session-a")
        default_resolution = _run_codex_plugin_hook(
            plugin,
            "bind_tools.py",
            session_id="session-a",
            tool_name="mcp__powercontext__resolve_scope_binding",
            tool_input={"binding_keys": []},
        )
        default_input = json.loads(default_resolution.stdout)["hookSpecificOutput"]["updatedInput"]
        default_scope_id = httpx.post(
            f"{base_url}/v1/scope-bindings/resolve",
            json=default_input,
            timeout=5,
        ).json()["scope_id"]

        _set_session_binding(base_url, "session-a", first_child_id)
        _run_codex_plugin_hook(plugin, "session_binding.py", session_id="session-a")
        first_tool = _run_codex_plugin_hook(
            plugin,
            "bind_tools.py",
            session_id="session-a",
            tool_name="mcp__powercontext__search_memory",
            tool_input={"scope_id": second_child_id, "query": "current state"},
        )
        first_input = json.loads(first_tool.stdout)["hookSpecificOutput"]["updatedInput"]
        assert first_input["scope_id"] == first_child_id

        _run_codex_plugin_hook(plugin, "session_binding.py", session_id="session-b")
        second_tool = _run_codex_plugin_hook(
            plugin,
            "bind_tools.py",
            session_id="session-b",
            tool_name="mcp__powercontext__search_memory",
            tool_input={"scope_id": first_child_id, "query": "current state"},
        )
        second_input = json.loads(second_tool.stdout)["hookSpecificOutput"]["updatedInput"]
        assert second_input["scope_id"] == default_scope_id

        _set_session_binding(base_url, "session-b", second_child_id)
        switched_tool = _run_codex_plugin_hook(
            plugin,
            "bind_tools.py",
            session_id="session-b",
            tool_name="mcp__powercontext__search_memory",
            tool_input={"query": "current state"},
        )
        switched_input = json.loads(switched_tool.stdout)["hookSpecificOutput"]["updatedInput"]
        assert switched_input["scope_id"] == second_child_id

        _set_session_binding(base_url, "session-a", second_child_id)
        _run_codex_plugin_hook(plugin, "session_binding.py", session_id="session-a")
        resumed_after_switch = _run_codex_plugin_hook(
            plugin,
            "bind_tools.py",
            session_id="session-a",
            tool_name="mcp__powercontext__search_memory",
            tool_input={"scope_id": first_child_id, "query": "current state"},
        )
        resumed_after_switch_input = json.loads(resumed_after_switch.stdout)["hookSpecificOutput"]["updatedInput"]
        assert resumed_after_switch_input["scope_id"] == second_child_id

        peer_agent = _run_codex_plugin_hook(
            plugin,
            "bind_tools.py",
            session_id="session-b",
            tool_name="mcp__powercontext__remember_memory",
            tool_input={"scope_id": first_child_id, "kind": "decision", "text": "Shared result"},
        )
        peer_agent_input = json.loads(peer_agent.stdout)["hookSpecificOutput"]["updatedInput"]
        assert peer_agent_input["scope_id"] == second_child_id

        scopes = {scope["scope_id"]: scope for scope in httpx.get(f"{base_url}/v1/scopes", timeout=5).json()["items"]}
        assert scopes[first_child_id]["parent_scope_id"] == root_scope_id
        assert scopes[second_child_id]["parent_scope_id"] == root_scope_id
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()


def _run_hook(
    plugin: Path,
    *,
    prompt: str,
    turn_id: str,
    authorization: str | None,
    scope_id: str,
) -> subprocess.CompletedProcess[str]:
    environment: dict[str, str] = {
        **os.environ,
        "POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE": "true",
        "POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS": "10",
        "POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS": "5",
        "POWERCONTEXT_CODEX_SCOPE_ID": scope_id,
    }
    environment.pop("POWERCONTEXT_CODEX_AUTHORIZATION", None)
    if authorization is not None:
        environment["POWERCONTEXT_CODEX_AUTHORIZATION"] = authorization
    return subprocess.run(
        [sys.executable, str(plugin / "hooks" / "recall.py")],
        cwd=PROJECT_ROOT,
        env=environment,
        input=json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(PROJECT_ROOT),
            "prompt": prompt,
            "session_id": "session-e2e",
            "turn_id": turn_id,
        }),
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )


def _create_scope(base_url: str, *, authorization: str | None) -> str:
    headers = {"Authorization": authorization} if authorization is not None else None
    response = httpx.post(
        f"{base_url}/v1/scopes",
        headers=headers,
        json={
            "title": "Codex end-to-end work",
            "summary": "Shared context for the Codex integration test.",
            "idempotency_key": "codex-e2e-work",
        },
        timeout=5,
    )
    response.raise_for_status()
    return response.json()["scope_id"]


def _copy_plugin(tmp_path: Path, base_url: str) -> Path:
    plugin = tmp_path / "scope-plugin"
    shutil.copytree(CODEX_PLUGIN, plugin, ignore=shutil.ignore_patterns("__pycache__", ".venv"))
    configuration = json.loads((plugin / ".mcp.json").read_text())
    configuration["mcpServers"]["powercontext"]["url"] = f"{base_url}/mcp"
    (plugin / ".mcp.json").write_text(json.dumps(configuration))
    return plugin


def _create_named_scope(
    base_url: str,
    *,
    title: str,
    key: str,
    parent_scope_id: str | None = None,
) -> str:
    response = httpx.post(
        f"{base_url}/v1/scopes",
        json={
            "title": title,
            "summary": f"Codex integration test Scope for {title}.",
            "parent_scope_id": parent_scope_id,
            "idempotency_key": key,
        },
        timeout=5,
    )
    response.raise_for_status()
    return response.json()["scope_id"]


def _set_session_binding(base_url: str, session_id: str, scope_id: str) -> None:
    response = httpx.put(
        f"{base_url}/v1/scope-bindings",
        json={
            "key": {"integration": "codex", "kind": "session", "external_id": session_id},
            "scope_id": scope_id,
        },
        timeout=5,
    )
    response.raise_for_status()


def _run_codex_plugin_hook(
    plugin: Path,
    script: str,
    *,
    session_id: str,
    tool_name: str | None = None,
    tool_input: dict[str, object] | None = None,
) -> subprocess.CompletedProcess[str]:
    payload: dict[str, object] = {
        "session_id": session_id,
        "cwd": str(PROJECT_ROOT),
    }
    if tool_name is not None:
        payload.update({"tool_name": tool_name, "tool_input": tool_input or {}})
    environment: dict[str, str] = {
        **os.environ,
        "POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS": "10",
        "POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS": "5",
    }
    environment.pop("POWERCONTEXT_CODEX_SCOPE_ID", None)
    environment.pop("POWERCONTEXT_CODEX_AUTHORIZATION", None)
    return subprocess.run(
        [sys.executable, str(plugin / "hooks" / script)],
        cwd=PROJECT_ROOT,
        env=environment,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )


def _wait_until_started(server: uvicorn.Server, thread: threading.Thread) -> None:
    deadline = time.monotonic() + 10
    while thread.is_alive() and not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
