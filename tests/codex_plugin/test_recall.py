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

import io
import json
import os
import stat
import sys
import threading
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

from powercontext.client.integration import prompt as prompt_operations


@contextmanager
def _serve(handler: type[BaseHTTPRequestHandler]) -> Generator[str, None, None]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


def _prepared(content: str | None = "prepared context", *, status: str = "ready") -> dict[str, object]:
    return {
        "schema": "powercontext.prepared-context.v1",
        "status": status,
        "content": content,
        "content_bytes": 0 if content is None else len(content.encode("utf-8")),
    }


def test_recall_records_exact_injected_context_only_when_eval_trace_is_enabled(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trace = tmp_path / "evaluation-injections.jsonl"
    monkeypatch.setenv("POWERCONTEXT_EVAL_TRACE_PATH", str(trace))
    prepared_context = "PowerContext recalled context: Refresh namespace after writes."
    monkeypatch.setattr(
        prompt_operations,
        "prepare",
        lambda *_args, **_kwargs: _prepared(prepared_context),
    )
    monkeypatch.setattr(
        recall_module,
        "resolve_scope_id",
        lambda _cwd, **_kwargs: "eval:run-1:on",
    )
    monkeypatch.setattr(prompt_operations, "capture", lambda *_args, **_kwargs: {"position": 1})
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/workspace",
                "prompt": "fix namespace refresh",
                "session_id": "session-1",
                "turn_id": "turn-2",
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert recall_module.main() == 0

    injected = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    event = json.loads(trace.read_text())
    assert event == {
        "event_type": "powercontext_injection",
        "observed_at": event["observed_at"],
        "query": "fix namespace refresh",
        "injected_text": injected,
        "hits": [],
        "scope_id": "eval:run-1:on",
        "session_id": "session-1",
        "turn_id": "turn-2",
    }
    assert injected.startswith('PowerContext Scope: "eval:run-1:on".')
    assert injected.endswith(prepared_context)
    assert event["observed_at"].endswith("Z")
    if os.name != "nt":
        assert stat.S_IMODE(trace.stat().st_mode) == 0o600


def test_recall_does_not_write_an_evaluation_trace_by_default(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("POWERCONTEXT_EVAL_TRACE_PATH", raising=False)
    monkeypatch.setattr(
        prompt_operations,
        "prepare",
        lambda *_args, **_kwargs: _prepared("PowerContext recalled context: Use memory."),
    )
    monkeypatch.setattr(
        recall_module,
        "resolve_scope_id",
        lambda _cwd, **_kwargs: "project:test",
    )
    monkeypatch.setattr(prompt_operations, "capture", lambda *_args, **_kwargs: {"position": 1})
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/workspace",
                "prompt": "Recall context",
            })
        ),
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert recall_module.main() == 0
    assert list(tmp_path.iterdir()) == []


def test_recall_uses_the_eval_home_when_codex_filters_the_trace_path(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("POWERCONTEXT_EVAL_TRACE_PATH", raising=False)
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path))
    monkeypatch.setattr(
        prompt_operations,
        "prepare",
        lambda *_args, **_kwargs: _prepared("PowerContext recalled context: Use the retained audit."),
    )
    monkeypatch.setattr(recall_module, "resolve_scope_id", lambda *_args, **_kwargs: "eval:run-1:on")
    monkeypatch.setattr(prompt_operations, "capture", lambda *_args, **_kwargs: {"position": 1})
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": "/workspace", "prompt": "audit injection"})
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert recall_module.main() == 0

    trace = tmp_path / "evaluation-injections.jsonl"
    event = json.loads(trace.read_text())
    assert event["event_type"] == "powercontext_injection"
    assert event["scope_id"] == "eval:run-1:on"
    assert event["injected_text"] == json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert event["injected_text"].endswith("PowerContext recalled context: Use the retained audit.")
