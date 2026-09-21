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

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import PowerContextClient
from powercontext.http import (
    CreateScopeRequest,
    RememberMemoryRequest,
    ReplaceArtifactTagsRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, MetricsConfig, ServerSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOTS = {
    "codex": PROJECT_ROOT / "integrations" / "codex" / "plugins" / "powercontext",
    "claude-code": PROJECT_ROOT / "integrations" / "claude-code" / "plugins" / "powercontext",
}
BOOTSTRAP_TEXT = "HOST-REAL-BOOTSTRAP: use the exact packaged startup context chain."


@pytest.mark.parametrize("host", ["codex", "claude-code"])
def test_packaged_host_hooks_complete_the_live_bootstrap_chain(tmp_path: Path, host: str) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / f'{host}.db'}"),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
        )
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    address, port = listener.getsockname()
    base_url = f"http://{address}:{port}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        _wait_until_started(server, thread)
        scope_id = asyncio.run(_seed_bootstrap_memory(base_url, host))
        plugin = _copy_plugin(tmp_path, host, base_url)
        environment = _hook_environment(tmp_path, host, base_url, scope_id)
        session_id = f"{host}-bootstrap-session"

        started = _run_hook(
            plugin,
            _session_start_script(host),
            environment,
            {
                "hook_event_name": "SessionStart",
                "source": "startup",
                "session_id": session_id,
                "cwd": str(PROJECT_ROOT),
            },
        )
        bootstrap = json.loads(started.stdout)["hookSpecificOutput"]
        assert bootstrap["hookEventName"] == "SessionStart"
        assert BOOTSTRAP_TEXT in bootstrap["additionalContext"]

        first_prompt = _run_hook(
            plugin,
            _user_prompt_script(host),
            environment,
            _user_prompt_payload(host, session_id),
        )
        assert first_prompt.stdout == ""
        assert '"outcome":"empty"' in first_prompt.stderr

        ordinary_recall = _run_hook(
            plugin,
            _user_prompt_script(host),
            environment,
            _user_prompt_payload(host, f"{session_id}-without-bootstrap"),
        )
        recalled = json.loads(ordinary_recall.stdout)["hookSpecificOutput"]
        assert recalled["hookEventName"] == "UserPromptSubmit"
        assert BOOTSTRAP_TEXT in recalled["additionalContext"]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()


async def _seed_bootstrap_memory(base_url: str, host: str) -> str:
    async with (
        httpx.AsyncClient(trust_env=False) as transport,
        PowerContextClient(base_url, http_client=transport, trust_transport_security=True) as client,
    ):
        scope = await client.create_scope(
            CreateScopeRequest(
                title=f"{host} bootstrap acceptance",
                summary="Live packaged hook acceptance for lifecycle bootstrap context.",
                idempotency_key=f"{host}-bootstrap-host-service-chain",
            )
        )
        remembered = await client.remember_memory(
            RememberMemoryRequest(scope_id=scope.scope_id, kind="decision", text=BOOTSTRAP_TEXT)
        )
        assert remembered.entry is not None
        citation = remembered.entry.citation
        tags = await client.get_memory_entry_tags(
            scope.scope_id,
            citation.memory_ref.artifact_id,
            citation.entry_id,
        )
        assert tags is not None
        await client.replace_memory_entry_tags(
            scope.scope_id,
            citation.memory_ref.artifact_id,
            citation.entry_id,
            ReplaceArtifactTagsRequest.model_validate({"tags": ["bootstrap-context"]}),
            expected_etag=tags.etag,
        )
        return scope.scope_id


def _copy_plugin(tmp_path: Path, host: str, base_url: str) -> Path:
    plugin = tmp_path / f"{host}-plugin"
    shutil.copytree(PLUGIN_ROOTS[host], plugin, ignore=shutil.ignore_patterns("__pycache__", ".venv"))
    if host == "codex":
        configuration_path = plugin / ".mcp.json"
        configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
        configuration["mcpServers"]["powercontext"]["url"] = f"{base_url}/mcp"
        configuration_path.write_text(json.dumps(configuration), encoding="utf-8")
    return plugin


def _hook_environment(tmp_path: Path, host: str, base_url: str, scope_id: str) -> dict[str, str]:
    environment: dict[str, str] = dict(os.environ)
    environment.update({"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    if host == "codex":
        environment.pop("POWERCONTEXT_CODEX_AUTHORIZATION", None)
        environment.update({
            "POWERCONTEXT_CODEX_SCOPE_ID": scope_id,
            "POWERCONTEXT_CODEX_BOOTSTRAP_CONTEXT": "true",
            "POWERCONTEXT_CODEX_CAPTURE_PROMPTS": "false",
            "POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS": "10",
            "POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS": "5",
            "POWERCONTEXT_BOOTSTRAP_STATE_DIR": str(tmp_path / "codex-state"),
        })
    else:
        environment.pop("POWERCONTEXT_CLAUDE_AUTHORIZATION", None)
        environment.update({
            "POWERCONTEXT_CLAUDE_SERVER_URL": base_url,
            "POWERCONTEXT_CLAUDE_SCOPE_ID": scope_id,
            "POWERCONTEXT_CLAUDE_BOOTSTRAP_CONTEXT": "true",
            "POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS": "false",
            "POWERCONTEXT_CLAUDE_HTTP_BUDGET_SECONDS": "10",
            "POWERCONTEXT_CLAUDE_REQUEST_TIMEOUT_SECONDS": "5",
            "POWERCONTEXT_CLAUDE_BOOTSTRAP_STATE_DIR": str(tmp_path / "claude-state"),
        })
    return environment


def _session_start_script(host: str) -> str:
    return "session_binding.py" if host == "codex" else "session_start.py"


def _user_prompt_script(host: str) -> str:
    return "recall.py" if host == "codex" else "user_prompt_submit.py"


def _user_prompt_payload(host: str, session_id: str) -> dict[str, str]:
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "cwd": str(PROJECT_ROOT),
        "prompt": BOOTSTRAP_TEXT,
        "session_id": session_id,
    }
    payload["turn_id" if host == "codex" else "prompt_id"] = "first-prompt"
    return payload


def _run_hook(
    plugin: Path,
    script: str,
    environment: dict[str, str],
    payload: dict[str, str],
) -> subprocess.CompletedProcess[str]:
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
