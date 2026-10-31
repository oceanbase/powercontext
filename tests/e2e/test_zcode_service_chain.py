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

import asyncio
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

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.inference import GenerationResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from tests.e2e.dream_support import atomic_memory_pipeline, memory_source_text

HOOK = Path(__file__).resolve().parents[2] / "integrations/zcode/plugins/powercontext/hooks/user_prompt_submit.mjs"


class DeterministicSourcePipeline:
    def __init__(self, delay: float = 0) -> None:
        self.delay = delay

    async def generate(self, request: AtomicMemoryExtractionInput, /) -> GenerationResult[AtomicMemoryExtractionOutput]:
        await asyncio.sleep(self.delay)
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(kind="fact", text=text, evidence_ids=(evidence.evidence_id,))
                    for evidence in request.evidence
                    if (text := memory_source_text(evidence)) is not None
                )
            )
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


def _run_scope(node: str, environment: dict[str, str], action: str, *extra: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while True:
        result = subprocess.run(
            [
                node,
                str(HOOK.parent.parent / "scripts" / "scope.mjs"),
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
        assert result.stderr == "", result.stderr
        document = json.loads(result.stdout)
        assert document["schema"] == "powercontext.zcode.scope-result.v1"
        if result.returncode == 0:
            assert document["status"] == "resolved", document
            return document
        assert result.returncode == 1, document
        assert document["status"] == "failed" and document["code"] == "timeout", document
        if action != "resolve":
            # Delivery is uncertain; inspect committed state without replaying a mutation.
            assert document["binding"]["action"] == action, document
            allowed = {"unknown", "saved"} if action == "bind" else {"unknown", "cleared", "absent"}
            assert document["binding"]["status"] in allowed, document
            return document
        assert time.monotonic() < deadline, document
        time.sleep(0.05)


def _observe_binding_replies(app, binding_delay: float) -> list[tuple[str, dict[str, Any]]]:
    binding_mutations: list[tuple[str, dict[str, Any]]] = []

    @app.middleware("http")
    async def observe_binding_response(request, call_next):
        workspace_mutation = False
        if (request.method, request.url.path) in {
            ("PUT", "/v1/scope-bindings"),
            ("POST", "/v1/scope-bindings/clear"),
        }:
            payload = await request.json()
            workspace_mutation = payload["key"]["kind"] == "workspace"
            if workspace_mutation:
                binding_mutations.append((request.url.path, payload))
        response = await call_next(request)
        if workspace_mutation:
            # Hold a real committed binding reply past the unchanged CLI deadline.
            await asyncio.sleep(binding_delay)
        return response

    return binding_mutations


def _exercise_scope_bindings(
    client: httpx.Client,
    node: str,
    environment: dict[str, str],
    scope_id: str,
    binding_mutations: list[tuple[str, dict[str, Any]]],
    binding_delay: float,
) -> None:
    def wait_for_workspace_binding(expected: str | None) -> None:
        deadline = time.monotonic() + 10
        while True:
            if binding_mutations:
                selected = client.post(
                    "/v1/scope-bindings/resolve",
                    json={"binding_keys": [binding_mutations[0][1]["key"]], "allow_default": False},
                )
                if expected is None and selected.status_code == 404:
                    return
                if selected.status_code == 200 and selected.json()["scope_id"] == expected:
                    return
                assert selected.status_code in {200, 404}, selected.text
            assert time.monotonic() < deadline, (expected, binding_mutations)
            time.sleep(0.05)

    binding = _run_scope(node, environment, "bind", "--scope-id", scope_id)
    if binding_delay:
        assert binding["status"] == "failed" and binding["binding"]["status"] == "unknown", binding
    if binding["status"] == "resolved":
        assert binding["scope_id"] == scope_id, binding
    wait_for_workspace_binding(scope_id)
    assert len(binding_mutations) == 1
    assert binding_mutations[0][0] == "/v1/scope-bindings"
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
    assert _run_scope(node, environment, "resolve")["scope_id"] == session_scope
    probe = _invoke_hook(
        node=node,
        base_url=str(client.base_url),
        scope_id=None,
        session_id="binding-probe",
        turn_id="probe-1",
        prompt="Scope binding synthetic probe.",
    )
    assert session_scope in probe["hookSpecificOutput"]["additionalContext"]
    probe_sources = client.get(f"/v1/scopes/{session_scope}/sources")
    probe_sources.raise_for_status()
    assert len(probe_sources.json()["items"]) == 1
    unbinding = _run_scope(node, environment, "unbind")
    if binding_delay:
        assert unbinding["status"] == "failed" and unbinding["binding"]["status"] == "unknown", unbinding
    if unbinding["status"] == "resolved":
        assert unbinding["scope_id"] == session_scope, unbinding
    wait_for_workspace_binding(None)
    assert [path for path, _ in binding_mutations] == ["/v1/scope-bindings", "/v1/scope-bindings/clear"]
    assert binding_mutations[0][1]["key"] == binding_mutations[1][1]["key"]


@pytest.mark.parametrize(
    ("processing_delay", "binding_delay"),
    [(0, 0), (1.2, 0), (0, 1.2)],
    ids=["fast", "beyond-stop-budget", "late-binding-replies"],
)
def test_zcode_source_is_processed_and_recalled_in_a_new_session(
    tmp_path: Path, monkeypatch, processing_delay: float, binding_delay: float
) -> None:
    monkeypatch.setenv("ZCODE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the ZCode Hook")
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'zcode.db'}"), mcp=McpConfig(enabled=False)
        ),
        candidate_pipeline=atomic_memory_pipeline(DeterministicSourcePipeline(processing_delay)),
    )
    binding_mutations = _observe_binding_replies(app, binding_delay)
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
            environment = dict(os.environ)
            environment.pop("POWERCONTEXT_ZCODE_SCOPE_ID", None)
            environment.pop("ZCODE_SESSION_ID", None)
            environment["POWERCONTEXT_ZCODE_SERVER_URL"] = base_url

            _exercise_scope_bindings(client, node, environment, scope_id, binding_mutations, binding_delay)
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
            flush = result["observation"]["stages"]["flush"]
            assert result["observation"]["config"]["request_timeout_ms"] == 800
            assert result["observation"]["config"]["hook_budget_ms"] == 1000
            if processing_delay:
                assert flush["state"] == "unknown", result
            if flush["state"] == "unknown":
                # The bounded Stop can time out while real Server processing continues.
                # Preserve the durable pause; reading results must never replay the flush.
                assert flush["code"] == "timeout", result
                assert flush["source_position"] > 0
                assert result["pending"]["scopes"] == [
                    {
                        "scope_id": scope_id,
                        "receipt_count": 1,
                        "target_position": flush["source_position"],
                        "tracking_incomplete": False,
                        "unknown_flush_paused": True,
                    }
                ], result
            else:
                assert flush["state"] == "cursor_reached", result
                assert flush["current_cursor"] >= flush["source_position"]
                assert result["pending"]["scopes"] == []
            # Server completion has its own budget, independent of the host Hook deadline.
            deadline = time.monotonic() + 15
            while True:
                entries = client.post("/v1/memory/entries/list", json={"scope_id": scope_id})
                entries.raise_for_status()
                generated = next((entry for entry in entries.json()["entries"] if "teal-731" in entry["text"]), None)
                if generated is not None:
                    break
                assert time.monotonic() < deadline, entries.json()
                time.sleep(0.05)
            assert all(entry["artifact"]["family"] == "atomic-memory" for entry in entries.json()["entries"])
            captured = client.get(f"/v1/scopes/{scope_id}/sources").json()["items"]
            reference = generated["artifact"]
            revision = client.get(
                f"/v1/scopes/{scope_id}/artifacts/{reference['family']}/{reference['artifact_id']}"
                f"/revisions/{reference['revision']}"
            )
            revision.raise_for_status()
            assert revision.json()["sources"] == [{"source_type": "content", "source_id": captured[0]["source_id"]}]

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
