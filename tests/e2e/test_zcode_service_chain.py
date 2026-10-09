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
    *, node: str, base_url: str, scope_id: str | None, session_id: str, turn_id: str, prompt: str
) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.pop("POWERCONTEXT_ZCODE_SCOPE_ID", None)
    environment.pop("ZCODE_SESSION_ID", None)
    environment.update(POWERCONTEXT_ZCODE_SERVER_URL=base_url, ZCODE_PLUGIN_DATA=os.environ["ZCODE_PLUGIN_DATA"])
    if scope_id is not None:
        environment["POWERCONTEXT_ZCODE_SCOPE_ID"] = scope_id
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


def test_zcode_source_is_processed_and_recalled_in_a_new_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZCODE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
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
            scope_script = HOOK.parent.parent / "scripts" / "scope.mjs"
            environment = dict(os.environ)
            environment.pop("POWERCONTEXT_ZCODE_SCOPE_ID", None)
            environment.pop("ZCODE_SESSION_ID", None)
            environment["POWERCONTEXT_ZCODE_SERVER_URL"] = base_url

            def run_scope(action: str, *extra: str) -> dict[str, Any]:
                result = subprocess.run(
                    [
                        node,
                        str(scope_script),
                        action,
                        "--cwd",
                        str(HOOK.parents[5]),
                        "--session-id",
                        "binding-probe",
                        *extra,
                    ],
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                assert result.returncode == 0, result.stdout + result.stderr
                return json.loads(result.stdout)

            assert run_scope("bind", "--scope-id", scope_id)["scope_id"] == scope_id
            other = client.post(
                "/v1/scopes",
                json={
                    "title": "Session binding probe",
                    "summary": "isolated",
                    "idempotency_key": "zcode-session-binding-probe",
                },
            )
            other.raise_for_status()
            session_scope = other.json()["scope_id"]
            bound = client.put(
                "/v1/scope-bindings",
                json={
                    "key": {"integration": "zcode", "kind": "session", "external_id": "binding-probe"},
                    "scope_id": session_scope,
                },
            )
            bound.raise_for_status()
            assert run_scope("resolve")["scope_id"] == session_scope
            probe = _invoke_hook(
                node=node,
                base_url=base_url,
                scope_id=None,
                session_id="binding-probe",
                turn_id="probe-1",
                prompt="Scope binding synthetic probe.",
            )
            assert session_scope in probe["hookSpecificOutput"]["additionalContext"]
            probe_sources = client.get(f"/v1/scopes/{session_scope}/sources")
            probe_sources.raise_for_status()
            assert len(probe_sources.json()["items"]) == 1
            assert run_scope("unbind")["scope_id"] == session_scope
            first = _invoke_hook(
                node=node,
                base_url=base_url,
                scope_id=scope_id,
                session_id="old-session",
                turn_id="turn-1",
                prompt="The staging deployment color is teal-731.",
            )
            assert scope_id in first["hookSpecificOutput"]["additionalContext"]
            assert "teal-731" not in first["hookSpecificOutput"]["additionalContext"]
            remaining = 5 - time.time() % 5
            if remaining < 1.5:
                time.sleep(remaining + 0.05)
            stopped = subprocess.run(
                [node, str(HOOK.parent / "stop.mjs")],
                input=json.dumps({
                    "hookEventName": "Stop",
                    "cwd": str(HOOK.parents[5]),
                    "sessionId": "old-session",
                    "stopHookActive": False,
                }),
                env={
                    **environment,
                    "POWERCONTEXT_ZCODE_SCOPE_ID": scope_id,
                    "POWERCONTEXT_ZCODE_BOUNDARY_FLUSH": "true",
                },
                text=True,
                capture_output=True,
                check=False,
                timeout=2,
            )
            assert stopped.returncode == 0, stopped.stderr
            assert json.loads(stopped.stdout) == {}
            status = subprocess.run(
                [
                    node,
                    str(HOOK.parent.parent / "scripts/status.mjs"),
                    "--cwd",
                    str(HOOK.parents[5]),
                    "--session-id",
                    "old-session",
                ],
                env={
                    **environment,
                    "POWERCONTEXT_ZCODE_SCOPE_ID": scope_id,
                    "POWERCONTEXT_ZCODE_BOUNDARY_FLUSH": "true",
                },
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            assert status.returncode == 0, status.stdout + status.stderr
            result = json.loads(status.stdout)
            assert result["observation"]["stages"]["flush"]["state"] == "cursor_reached"
            assert result["pending"]["scopes"] == []
            entries = client.post("/v1/memory/entries/list", json={"scope_id": scope_id})
            entries.raise_for_status()
            assert entries.json()["memory"] is not None
            generated = next(entry for entry in entries.json()["entries"] if "teal-731" in entry["text"])
            captured = client.get(f"/v1/scopes/{scope_id}/sources").json()["items"]
            assert generated["source_refs"] == [{"name": "content", "source_id": captured[0]["source_id"]}]

            second = _invoke_hook(
                node=node,
                base_url=base_url,
                scope_id=scope_id,
                session_id="new-session",
                turn_id="turn-1",
                prompt="What is the staging deployment color?",
            )
            assert "teal-731" in second["hookSpecificOutput"]["additionalContext"]
            status = subprocess.run(
                [
                    node,
                    str(HOOK.parent.parent / "scripts/status.mjs"),
                    "--cwd",
                    str(HOOK.parents[5]),
                    "--session-id",
                    "new-session",
                ],
                env={**environment, "POWERCONTEXT_ZCODE_SCOPE_ID": scope_id},
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            assert status.returncode == 0, status.stderr + status.stdout
            observation = json.loads(status.stdout)["observation"]
            assert observation["scope_id"] == scope_id
            assert observation["stages"]["prepare"]["state"] == "ready"
            assert observation["stages"]["capture"]["state"] == "accepted"
            accepted_position = observation["stages"]["capture"]["source_position"]
            persisted = client.get(f"/v1/scopes/{scope_id}/sources").json()["items"]
            assert any(source["position"] == accepted_position for source in persisted)
            assert "teal-731" not in status.stdout
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
