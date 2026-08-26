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
import shlex
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import SecretStr
from pydantic_ai.models.test import TestModel

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.cli.workbuddy import install_workbuddy_plugin
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKBUDDY_PLUGIN = PROJECT_ROOT / "integrations" / "workbuddy" / "plugins" / "powercontext"
SCOPE_ID = "project:workbuddy-e2e"
AUTH_TOKEN = "workbuddy-e2e-token"  # noqa: S105 - non-secret test credential.
AUTHORIZATION = f"Bearer {AUTH_TOKEN}"
_SERVER_URL_TEMPLATE = "${POWERCONTEXT_WORKBUDDY_SERVER_URL:-http://127.0.0.1:8000}/mcp"
_AUTHORIZATION_TEMPLATE = "${POWERCONTEXT_WORKBUDDY_AUTHORIZATION:-}"


@pytest.mark.parametrize("authentication_enabled", [False, True], ids=["public", "authenticated"])
def test_workbuddy_hook_and_mcp_share_one_service_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authentication_enabled: bool,
) -> None:
    model_output = """
    {
      "candidates": [{
        "intent": "add",
        "kind": "decision",
        "text": "Use the WorkBuddy service chain for project context.",
        "evidence_ids": ["source:0"],
        "reason": "captured by the WorkBuddy hook"
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
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        _wait_until_started(server, thread)
        home = tmp_path / "WorkBuddy Home"
        monkeypatch.setenv("WORKBUDDY_HOME", str(home))
        monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "PowerContext Data"))
        install_workbuddy_plugin(source=str(PROJECT_ROOT), ref="master")

        settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
        command = _powercontext_hook_command(settings)
        authorization = AUTHORIZATION if authentication_enabled else None
        environment = _workbuddy_environment(base_url, authorization=authorization)

        captured = _run_hook(
            command,
            environment=environment,
            prompt="Remember the WorkBuddy project context service.",
            prompt_id="prompt-1",
        )
        captured_context = json.loads(captured.stdout)["hookSpecificOutput"]["additionalContext"]
        assert captured_context == ""
        assert AUTH_TOKEN not in captured.stderr

        recalled = _run_hook(
            command,
            environment=environment,
            prompt="Which service chain should WorkBuddy use?",
            prompt_id="prompt-2",
        )
        context = json.loads(recalled.stdout)["hookSpecificOutput"]["additionalContext"]
        envelope = json.loads(context.splitlines()[-2])
        assert envelope["items"][0]["content"] == "Use the WorkBuddy service chain for project context."
        assert AUTH_TOKEN not in recalled.stderr

        mcp_entry = json.loads((home / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["powercontext"]
        plugin_mcp_entry = json.loads((WORKBUDDY_PLUGIN / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"][
            "powercontext"
        ]
        assert mcp_entry == plugin_mcp_entry
        endpoint, headers = _workbuddy_mcp_connection(mcp_entry, environment)

        async def verify_mcp() -> None:
            async with Client(StreamableHttpTransport(endpoint, headers=headers)) as client:
                result = await client.call_tool(
                    "search_memory",
                    {
                        "scope_id": SCOPE_ID,
                        "query": "WorkBuddy service chain",
                    },
                )
            structured = result.structured_content or {}
            hits = structured.get("hits")
            assert isinstance(hits, list)
            assert hits[0]["text"] == "Use the WorkBuddy service chain for project context."

        asyncio.run(verify_mcp())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()


def _powercontext_hook_command(settings: dict[str, Any]) -> list[str]:
    hooks = settings["hooks"]
    assert isinstance(hooks, dict)
    matchers = hooks["UserPromptSubmit"]
    assert isinstance(matchers, list)
    for matcher in matchers:
        assert isinstance(matcher, dict)
        entries = matcher["hooks"]
        assert isinstance(entries, list)
        for entry in entries:
            assert isinstance(entry, dict)
            command = entry.get("command")
            if isinstance(command, str) and "workbuddy_powercontext_hook.py" in command:
                return shlex.split(command)
    raise AssertionError


def _workbuddy_environment(base_url: str, *, authorization: str | None) -> dict[str, str]:
    environment: dict[str, str] = {
        **os.environ,
        "POWERCONTEXT_WORKBUDDY_FLUSH_ON_CAPTURE": "true",
        "POWERCONTEXT_WORKBUDDY_HTTP_BUDGET_SECONDS": "10",
        "POWERCONTEXT_WORKBUDDY_REQUEST_TIMEOUT_SECONDS": "5",
        "POWERCONTEXT_WORKBUDDY_SCOPE_ID": SCOPE_ID,
        "POWERCONTEXT_WORKBUDDY_SERVER_URL": base_url,
    }
    environment.pop("POWERCONTEXT_WORKBUDDY_AUTHORIZATION", None)
    if authorization is not None:
        environment["POWERCONTEXT_WORKBUDDY_AUTHORIZATION"] = authorization
    return environment


def _run_hook(
    command: list[str],
    *,
    environment: dict[str, str],
    prompt: str,
    prompt_id: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        input=json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(PROJECT_ROOT),
            "prompt": prompt,
            "session_id": "workbuddy-e2e-session",
            "prompt_id": prompt_id,
        }),
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )


def _workbuddy_mcp_connection(
    entry: dict[str, object],
    environment: dict[str, str],
) -> tuple[str, dict[str, str]]:
    assert entry["url"] == _SERVER_URL_TEMPLATE
    assert entry["headers"] == {"Authorization": _AUTHORIZATION_TEMPLATE}
    endpoint = _SERVER_URL_TEMPLATE.replace(
        "${POWERCONTEXT_WORKBUDDY_SERVER_URL:-http://127.0.0.1:8000}",
        environment.get("POWERCONTEXT_WORKBUDDY_SERVER_URL", "http://127.0.0.1:8000"),
    )
    authorization = environment.get("POWERCONTEXT_WORKBUDDY_AUTHORIZATION", "")
    return endpoint, {"Authorization": authorization}


def _wait_until_started(server: uvicorn.Server, thread: threading.Thread) -> None:
    deadline = time.monotonic() + 10
    while thread.is_alive() and not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
