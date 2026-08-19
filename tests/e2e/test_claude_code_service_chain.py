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
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import SecretStr
from pydantic_ai.models.test import TestModel

from powercontext.builtin.artifacts.handoff import HandoffDraft, HandoffGenerationRequest, HandoffStatement
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_PLUGIN = PROJECT_ROOT / "integrations" / "claude-code" / "plugins" / "powercontext"
CODEX_PLUGIN = PROJECT_ROOT / "integrations" / "codex" / "plugins" / "powercontext"
SCOPE_ID = "git:github.com/oceanbase/powercontext"
AUTH_TOKEN = "claude-code-e2e-token"  # noqa: S105 - non-secret test credential.
AUTHORIZATION = f"Bearer {AUTH_TOKEN}"


class _DeterministicHandoffPipeline:
    async def generate(self, request: HandoffGenerationRequest, /) -> HandoffDraft:
        citations = tuple(item.citation for item in request.evidence)
        return HandoffDraft(
            objective=request.objective,
            state=(
                HandoffStatement(text="Claude Code MCP exposes the explicit Handoff lifecycle.", citations=citations),
            ),
            disposition="continuable",
            next_action=HandoffStatement(text="Continue from the inspected Prepared Handoff.", citations=citations),
        )


@pytest.mark.parametrize("authentication_enabled", [False, True], ids=["public", "authenticated"])
def test_claude_sessions_and_codex_share_one_project_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authentication_enabled: bool,
) -> None:
    model_output = """
    {
      "candidates": [{
        "intent": "add",
        "kind": "decision",
        "text": "Use PowerContext as the shared project context service.",
        "evidence_ids": ["source:0"],
        "reason": "captured by the Claude Code hook"
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
        codex_plugin = tmp_path / "codex-plugin"
        shutil.copytree(CODEX_PLUGIN, codex_plugin, ignore=shutil.ignore_patterns("__pycache__", ".venv"))
        mcp_configuration = json.loads((codex_plugin / ".mcp.json").read_text())
        mcp_configuration["mcpServers"]["powercontext"]["url"] = f"{base_url}/mcp"
        (codex_plugin / ".mcp.json").write_text(json.dumps(mcp_configuration))

        captured = _run_claude_hook(
            prompt="Remember the shared project context service.",
            session_id="claude-session-a",
            prompt_id="prompt-1",
            base_url=base_url,
            authorization=AUTHORIZATION if authentication_enabled else None,
        )
        assert captured.stdout == ""
        assert AUTH_TOKEN not in captured.stderr

        recalled_by_claude = _run_claude_hook(
            prompt="Which shared project context service should we use?",
            session_id="claude-session-b",
            prompt_id="prompt-2",
            base_url=base_url,
            authorization=AUTHORIZATION if authentication_enabled else None,
        )
        claude_context = json.loads(recalled_by_claude.stdout)["hookSpecificOutput"]["additionalContext"]
        claude_envelope = json.loads(claude_context.splitlines()[-2])
        assert claude_envelope["items"][0]["content"] == ("Use PowerContext as the shared project context service.")
        assert AUTH_TOKEN not in recalled_by_claude.stderr

        recalled_by_codex = _run_codex_hook(
            codex_plugin,
            prompt="Which shared project context service should we use?",
            authorization=AUTHORIZATION if authentication_enabled else None,
        )
        codex_context = json.loads(recalled_by_codex.stdout)["hookSpecificOutput"]["additionalContext"]
        codex_envelope = json.loads(codex_context.splitlines()[-2])
        assert codex_envelope["items"][0]["content"] == claude_envelope["items"][0]["content"]
        assert AUTH_TOKEN not in recalled_by_codex.stderr
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()


@pytest.mark.parametrize("authentication_enabled", [False, True], ids=["public", "authenticated"])
def test_claude_plugin_mcp_supports_explicit_memory_and_handoff_workflows(
    tmp_path: Path,
    authentication_enabled: bool,
) -> None:
    app = create_server_app(
        settings=ServerSettings(
            auth=BearerAuthConfig(
                enabled=authentication_enabled,
                token=SecretStr(AUTH_TOKEN) if authentication_enabled else None,
            ),
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'mcp.db'}"),
            mcp=McpConfig(enabled=True),
        ),
        handoff_pipeline=_DeterministicHandoffPipeline(),
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
        endpoint, headers, helper_errors = _claude_mcp_connection(
            base_url,
            authorization=AUTHORIZATION if authentication_enabled else None,
        )
        result = asyncio.run(_exercise_explicit_mcp_workflows(endpoint, headers))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()

    assert AUTH_TOKEN not in helper_errors
    assert result == {
        "memory_state": "inactive",
        "memory_text": "Use the Claude Code plugin MCP transport for explicit operations.",
        "temporary_selection": "prepared",
        "committed_family": "handoff",
        "latest_matches_commit": True,
    }


def _claude_mcp_connection(base_url: str, *, authorization: str | None) -> tuple[str, dict[str, str], str]:
    configuration = json.loads((CLAUDE_PLUGIN / ".mcp.json").read_text(encoding="utf-8"))["powercontext"]
    endpoint = configuration["url"].replace("${user_config.server_url}", base_url)
    helper_command = configuration["headersHelper"].replace("${CLAUDE_PLUGIN_ROOT}", CLAUDE_PLUGIN.as_posix())
    environment = dict(os.environ)
    environment.pop("POWERCONTEXT_CLAUDE_AUTHORIZATION", None)
    if authorization is not None:
        environment["POWERCONTEXT_CLAUDE_AUTHORIZATION"] = authorization
    completed = subprocess.run(
        shlex.split(helper_command),
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    return endpoint, json.loads(completed.stdout), completed.stderr


async def _exercise_explicit_mcp_workflows(endpoint: str, headers: dict[str, str]) -> dict[str, object]:
    scope_id = "project:claude-code-mcp"
    async with Client(StreamableHttpTransport(endpoint, headers=headers)) as client:
        remembered_result = await client.call_tool(
            "remember_memory",
            {
                "scope_id": scope_id,
                "kind": "decision",
                "text": "Use Claude MCP for explicit operations.",
                "reason": "Phase 2 integration verification.",
            },
        )
        remembered = remembered_result.structured_content or {}
        revised_result = await client.call_tool(
            "revise_memory_entry",
            {
                "scope_id": scope_id,
                "citation": remembered["entry"]["citation"],
                "kind": "decision",
                "text": "Use the Claude Code plugin MCP transport for explicit operations.",
                "reason": "Clarify the integration boundary.",
            },
        )
        revised = revised_result.structured_content or {}
        retired_result = await client.call_tool(
            "retire_memory_entry",
            {
                "scope_id": scope_id,
                "citation": revised["entry"]["citation"],
                "reason": "Exercise the complete explicit maintenance lifecycle.",
            },
        )
        retired = retired_result.structured_content or {}

        captured_result = await client.call_tool(
            "capture_content_source",
            {
                "scope_id": scope_id,
                "source_id": "claude-mcp-handoff-boundary",
                "content": "The Claude Code MCP integration completed its explicit workflow checks.",
            },
        )
        captured = captured_result.structured_content or {}
        activation_result = await client.call_tool(
            "activate_handoff",
            {
                "scope_id": scope_id,
                "boundary_source": captured["source"],
                "objective": "Transfer the verified Claude Code MCP integration state.",
            },
        )
        activation = activation_result.structured_content or {}
        prepared_result = await client.call_tool(
            "finalize_handoff",
            {"scope_id": scope_id, "draft": activation["draft"]},
        )
        prepared = prepared_result.structured_content or {}
        temporary_result = await client.call_tool(
            "continue_handoff",
            {"scope_id": scope_id, "selection": "prepared", "prepared": prepared},
        )
        temporary = temporary_result.structured_content or {}
        committed_result = await client.call_tool(
            "commit_handoff",
            {"scope_id": scope_id, "handoff": prepared},
        )
        committed = committed_result.structured_content or {}
        latest_result = await client.call_tool(
            "continue_handoff",
            {"scope_id": scope_id, "selection": "latest"},
        )
        latest = latest_result.structured_content or {}

    return {
        "memory_state": retired["entry"]["state"],
        "memory_text": retired["entry"]["text"],
        "temporary_selection": temporary["selection"],
        "committed_family": committed["reference"]["family"],
        "latest_matches_commit": latest["selected_revision"] == committed["reference"],
    }


def _run_claude_hook(
    *,
    prompt: str,
    session_id: str,
    prompt_id: str,
    base_url: str,
    authorization: str | None,
) -> subprocess.CompletedProcess[str]:
    environment: dict[str, str] = {
        **os.environ,
        "POWERCONTEXT_CLAUDE_SERVER_URL": base_url,
        "POWERCONTEXT_CLAUDE_FLUSH_ON_CAPTURE": "true",
        "POWERCONTEXT_CLAUDE_HTTP_BUDGET_SECONDS": "10",
        "POWERCONTEXT_CLAUDE_REQUEST_TIMEOUT_SECONDS": "5",
        "POWERCONTEXT_CLAUDE_SCOPE_ID": SCOPE_ID,
    }
    environment.pop("POWERCONTEXT_CLAUDE_AUTHORIZATION", None)
    if authorization is not None:
        environment["POWERCONTEXT_CLAUDE_AUTHORIZATION"] = authorization
    return subprocess.run(
        [sys.executable, str(CLAUDE_PLUGIN / "hooks" / "user_prompt_submit.py")],
        cwd=PROJECT_ROOT,
        env=environment,
        input=json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(PROJECT_ROOT),
            "prompt": prompt,
            "session_id": session_id,
            "prompt_id": prompt_id,
        }),
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )


def _run_codex_hook(
    plugin: Path,
    *,
    prompt: str,
    authorization: str | None,
) -> subprocess.CompletedProcess[str]:
    environment: dict[str, str] = {
        **os.environ,
        "POWERCONTEXT_CODEX_SCOPE_ID": SCOPE_ID,
        "POWERCONTEXT_CODEX_CAPTURE_PROMPTS": "false",
        "POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS": "10",
        "POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS": "5",
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
            "session_id": "codex-session",
            "turn_id": "turn-1",
        }),
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
