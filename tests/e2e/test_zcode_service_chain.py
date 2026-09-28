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

"""Exercise the ZCode Hook against a real HTTP Server and SQLite persistence."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.sources import ContentSource
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

HOOK = Path(__file__).resolve().parents[2] / "integrations/zcode/plugins/powercontext/hooks/user_prompt_submit.mjs"


class DeterministicSourcePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="fact", text=source.content, sources=(source,), reason="captured")
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def _invoke_hook(
    *, node: str, base_url: str, scope_id: str, session_id: str, turn_id: str, prompt: str
) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.update(POWERCONTEXT_ZCODE_SERVER_URL=base_url, POWERCONTEXT_ZCODE_SCOPE_ID=scope_id)
    result = subprocess.run(
        [node, str(HOOK)],
        input=json.dumps({
            "hookEventName": "UserPromptSubmit",
            "sessionId": session_id,
            "turnId": turn_id,
            "cwd": str(HOOK.parents[5]),
            "prompt": prompt,
        }),
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return json.loads(result.stdout)


def test_zcode_source_is_processed_and_recalled_in_a_new_session(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the ZCode Hook")
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'zcode.db'}"), mcp=McpConfig(enabled=False)
        ),
        candidate_pipeline=DeterministicSourcePipeline(),
    )
    with socket.socket() as socket_probe:
        socket_probe.bind(("127.0.0.1", 0))
        port = socket_probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        base_url = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base_url, timeout=10) as client:
            created = client.post(
                "/v1/scopes",
                json={
                    "title": "ZCode source acceptance",
                    "summary": "Isolated Hook and Server chain",
                    "idempotency_key": "zcode-source-acceptance",
                },
            )
            created.raise_for_status()
            scope_id = created.json()["scope_id"]
            first = _invoke_hook(
                node=node,
                base_url=base_url,
                scope_id=scope_id,
                session_id="old-session",
                turn_id="turn-1",
                prompt="The staging deployment color is teal-731.",
            )
            assert first["hookSpecificOutput"]["additionalContext"] == ""
            flushed = client.post("/v1/memory/flush", json={"scope_id": scope_id})
            flushed.raise_for_status()
            assert flushed.json()["processed_source_count"] == 1
            assert flushed.json()["memory"] is not None

            second = _invoke_hook(
                node=node,
                base_url=base_url,
                scope_id=scope_id,
                session_id="new-session",
                turn_id="turn-1",
                prompt="What is the staging deployment color?",
            )
            assert "teal-731" in second["hookSpecificOutput"]["additionalContext"]
            _invoke_hook(
                node=node,
                base_url=base_url,
                scope_id=scope_id,
                session_id="old-session",
                turn_id="turn-1",
                prompt="The staging deployment color is teal-731.",
            )
            sources = client.get(f"/v1/scopes/{scope_id}/sources")
            sources.raise_for_status()
            assert len(sources.json()["items"]) == 2
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
