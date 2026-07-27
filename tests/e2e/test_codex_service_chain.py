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

import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai.models.test import TestModel

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.client import PowerContextClient
from powercontext.http import ListMemoryEntriesRequest, SearchMemoryRequest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODEX_PLUGIN = PROJECT_ROOT / "integrations" / "codex" / "plugins" / "powercontext"
SCOPE_ID = "project:codex-e2e"


def test_codex_hook_http_sdk_and_mcp_share_one_composed_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

        first = _run_hook(
            plugin,
            prompt="Remember which object is the composition root.",
            turn_id="turn-1",
        )
        assert first.stdout == ""

        recalled = _run_hook(
            plugin,
            prompt="Which composition root should this project use?",
            turn_id="turn-2",
        )
        context = json.loads(recalled.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "[memory] Use PowerContext as the composition root." in context

        async def verify_public_surfaces() -> None:
            async with PowerContextClient(base_url) as sdk:
                found = await sdk.search_memory(
                    SearchMemoryRequest(
                        scope_id=SCOPE_ID,
                        query="PowerContext composition root",
                    )
                )
                entries = await sdk.list_memory_entries(
                    ListMemoryEntriesRequest(scope_id=SCOPE_ID),
                )
            assert found.hits
            assert {hit.text for hit in found.hits} == {"Use PowerContext as the composition root."}
            assert entries.entries
            assert entries.entries[0].source_refs[0].name == "content"

            transport = StreamableHttpTransport(f"{base_url}/mcp")
            async with Client(transport) as mcp:
                result = await mcp.call_tool(
                    "search_memory",
                    {
                        "scope_id": SCOPE_ID,
                        "query": "PowerContext composition root",
                    },
                )
            structured = result.structured_content or {}
            hits = structured.get("hits")
            assert isinstance(hits, list)
            assert hits[0]["text"] == "Use PowerContext as the composition root."

        asyncio.run(verify_public_surfaces())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()


def _run_hook(plugin: Path, *, prompt: str, turn_id: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE": "true",
        "POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS": "10",
        "POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS": "5",
        "POWERCONTEXT_CODEX_SCOPE_ID": SCOPE_ID,
    }
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


def _wait_until_started(server: uvicorn.Server, thread: threading.Thread) -> None:
    deadline = time.monotonic() + 10
    while thread.is_alive() and not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
