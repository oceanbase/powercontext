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
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


@contextmanager
def _serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
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


def test_recall_emits_bounded_untrusted_context(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared_content = (
        "PowerContext prepared untrusted historical context.\n\n"
        "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
        '{"trust":"untrusted_history","items":[{"content":"Use the public API."}]}\n'
        "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
    )
    monkeypatch.setattr(
        recall_module,
        "_prepare_context",
        lambda _query, _scope, *, settings, deadline: _prepared(prepared_content),
    )
    monkeypatch.setattr(
        recall_module,
        "resolve_scope_id",
        lambda _cwd, *, configured_scope_id: "project:test",
    )
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        recall_module,
        "_capture_prompt",
        lambda _payload, *, prompt, cwd, scope_id, settings, deadline: (
            captured.append((prompt, scope_id)) or {"position": 1}
        ),
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/workspace/project",
                "prompt": "What decisions apply?",
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert recall_module.main() == 0
    context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert context == prepared_content
    assert len(context.encode("utf-8")) <= 8_000
    assert captured == [("What decisions apply?", "project:test")]


def test_recall_reads_utf8_stdin_on_windows_encodings(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared_content = "prepared context"
    captured: list[str] = []
    monkeypatch.setattr(
        recall_module,
        "_prepare_context",
        lambda _query, _scope, *, settings, deadline: _prepared(prepared_content),
    )
    monkeypatch.setattr(
        recall_module,
        "resolve_scope_id",
        lambda _cwd, *, configured_scope_id: "project:test",
    )
    monkeypatch.setattr(
        recall_module,
        "_capture_prompt",
        lambda _payload, *, prompt, cwd, scope_id, settings, deadline: captured.append(prompt) or {"position": 1},
    )

    payload = {
        "hook_event_name": "UserPromptSubmit",
        "cwd": "/workspace/project",
        "prompt": "查看当前记忆",
    }
    stdin = io.TextIOWrapper(
        io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
        encoding="cp1252",
    )
    monkeypatch.setattr(sys, "stdin", stdin)
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert recall_module.main() == 0
    assert captured == ["查看当前记忆"]
    assert json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"] == prepared_content


def test_recall_failure_is_non_blocking(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        recall_module,
        "_prepare_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(recall_module._ServerUnavailableError()),
    )
    monkeypatch.setattr(
        recall_module,
        "resolve_scope_id",
        lambda _cwd, *, configured_scope_id: "project:test",
    )
    monkeypatch.setattr(recall_module, "_capture_prompt", lambda *_args, **_kwargs: {"position": 1})
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/workspace/project",
                "prompt": "Recall context",
            })
        ),
    )
    output = io.StringIO()
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)

    assert recall_module.main() == 0
    assert errors.getvalue() == ""
    result = json.loads(output.getvalue())
    diagnostic = json.loads(result["systemMessage"])
    assert diagnostic == {
        "component": "powercontext.codex.recall",
        "event": "context_prepare",
        "outcome": "server_unavailable",
        "recovery": "powercontext doctor",
    }


def test_host_output_keeps_context_when_capture_diagnostic_is_emitted(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recall_module, "_prepare_context", lambda *_args, **_kwargs: _prepared("prepared context"))
    monkeypatch.setattr(recall_module, "resolve_scope_id", lambda *_args, **_kwargs: "project:test")
    monkeypatch.setattr(
        recall_module,
        "_capture_prompt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(recall_module._HttpStatusError(503)),
    )

    output = io.StringIO()
    errors = io.StringIO()
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/workspace/project",
                "prompt": "Recall despite capture failure",
            })
        ),
    )
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)

    assert recall_module.main() == 0

    result = json.loads(output.getvalue())
    assert result["hookSpecificOutput"]["additionalContext"] == "prepared context"
    assert json.loads(result["systemMessage"]) == {
        "component": "powercontext.codex.recall",
        "event": "capture_source",
        "outcome": "server_unavailable",
        "http_status": 503,
        "recovery": "powercontext doctor",
    }
    assert errors.getvalue() == ""


def test_host_diagnostic_is_throttled_across_hook_invocations(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        recall_module,
        "_prepare_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(recall_module._ServerUnavailableError()),
    )
    monkeypatch.setattr(recall_module, "resolve_scope_id", lambda *_args, **_kwargs: "project:test")
    monkeypatch.setattr(recall_module, "_capture_prompt", lambda *_args, **_kwargs: {"position": 1})

    payload = {
        "hook_event_name": "UserPromptSubmit",
        "cwd": "/workspace/project",
        "prompt": "Recall context",
    }
    outputs: list[str] = []
    for _ in range(2):
        output = io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        monkeypatch.setattr(sys, "stdout", output)
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        assert recall_module.main() == 0
        outputs.append(output.getvalue())

    assert json.loads(outputs[0])["systemMessage"]
    assert outputs[1] == ""


def test_recall_authentication_failure_is_non_blocking_and_content_free(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        recall_module,
        "_prepare_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(recall_module._HttpStatusError(401)),
    )
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)

    assert (
        recall_module._recall_context(
            "secret query",
            "secret scope",
            settings=recall_module.CodexPluginSettings(),
            deadline=time.monotonic() + 1,
        )
        is None
    )
    assert json.loads(errors.getvalue()) == {
        "component": "powercontext.codex.recall",
        "event": "context_prepare",
        "outcome": "authentication_failed",
        "http_status": 401,
    }
    assert "secret" not in errors.getvalue()


def test_recall_records_exact_injected_context_only_when_eval_trace_is_enabled(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trace = tmp_path / "evaluation-injections.jsonl"
    monkeypatch.setenv("POWERCONTEXT_EVAL_TRACE_PATH", str(trace))
    prepared_context = "PowerContext recalled context: Refresh namespace after writes."
    monkeypatch.setattr(
        recall_module,
        "_prepare_context",
        lambda *_args, **_kwargs: _prepared(prepared_context),
    )
    monkeypatch.setattr(
        recall_module,
        "resolve_scope_id",
        lambda _cwd, *, configured_scope_id: "eval:run-1:on",
    )
    monkeypatch.setattr(recall_module, "_capture_prompt", lambda *_args, **_kwargs: {"position": 1})
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
    assert event["injected_text"] == prepared_context
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
        recall_module,
        "_prepare_context",
        lambda *_args, **_kwargs: _prepared("PowerContext recalled context: Use memory."),
    )
    monkeypatch.setattr(
        recall_module,
        "resolve_scope_id",
        lambda _cwd, *, configured_scope_id: "project:test",
    )
    monkeypatch.setattr(recall_module, "_capture_prompt", lambda *_args, **_kwargs: {"position": 1})
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
        recall_module,
        "_prepare_context",
        lambda *_args, **_kwargs: _prepared("PowerContext recalled context: Use the retained audit."),
    )
    monkeypatch.setattr(recall_module, "resolve_scope_id", lambda *_args, **_kwargs: "eval:run-1:on")
    monkeypatch.setattr(recall_module, "_capture_prompt", lambda *_args, **_kwargs: {"position": 1})
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": "/workspace", "prompt": "audit injection"})
        ),
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert recall_module.main() == 0

    trace = tmp_path / "evaluation-injections.jsonl"
    event = json.loads(trace.read_text())
    assert event["event_type"] == "powercontext_injection"
    assert event["scope_id"] == "eval:run-1:on"
    assert event["injected_text"] == "PowerContext recalled context: Use the retained audit."


@pytest.mark.parametrize("event_name", ["UserPromptSubmit", "user_prompt_submit"])
def test_hook_accepts_codex_event_name_variants(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    event_name: str,
) -> None:
    monkeypatch.setattr(
        recall_module,
        "_prepare_context",
        lambda *_args, **_kwargs: _prepared(None, status="empty"),
    )
    captured: list[str] = []
    monkeypatch.setattr(
        recall_module,
        "_capture_prompt",
        lambda _payload, *, prompt, cwd, scope_id, settings, deadline: captured.append(prompt) or {"position": 1},
    )
    monkeypatch.setattr(
        recall_module,
        "resolve_scope_id",
        lambda _cwd, *, configured_scope_id: "project:test",
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": event_name,
                "cwd": "/workspace/project",
                "prompt": "Capture this input.",
            })
        ),
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    assert recall_module.main() == 0
    assert captured == ["Capture this input."]


def test_normal_empty_context_emits_a_generic_diagnostic(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        recall_module,
        "_prepare_context",
        lambda *_args, **_kwargs: _prepared(None, status="empty"),
    )
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)

    context = recall_module._recall_context(
        "query",
        "project:test",
        settings=recall_module.CodexPluginSettings(),
        deadline=time.monotonic() + 1,
    )

    assert context is None
    diagnostic = json.loads(errors.getvalue())
    assert diagnostic == {
        "component": "powercontext.codex.recall",
        "event": "context_prepare",
        "outcome": "empty",
        "http_status": 200,
        "context_status": "empty",
        "content_bytes": 0,
    }


def test_unknown_prepared_context_schema_fails_open_without_exposing_response(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _prepared("do-not-log")
    response["schema"] = "powercontext.prepared-context.v2"
    monkeypatch.setattr(recall_module, "_prepare_context", lambda *_args, **_kwargs: response)
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)

    context = recall_module._recall_context(
        "secret-query",
        "secret-scope",
        settings=recall_module.CodexPluginSettings(),
        deadline=time.monotonic() + 1,
    )

    assert context is None
    assert json.loads(errors.getvalue())["outcome"] == "invalid_response"
    assert "secret" not in errors.getvalue()


def test_hook_injects_runtime_content_without_a_second_selection(
    recall_module: ModuleType,
) -> None:
    content = "x" * 8_000

    prepared = recall_module._validate_prepared_context(_prepared(content))

    assert prepared["content"] == content


def test_hook_rejects_runtime_content_over_the_requested_budget(recall_module: ModuleType) -> None:
    with pytest.raises(recall_module._InvalidResponseError):
        recall_module._validate_prepared_context(_prepared("x" * 8_001))


def test_context_prepare_404_is_reported_as_a_version_mismatch(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        recall_module,
        "_prepare_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(recall_module._HttpStatusError(404)),
    )
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)

    assert (
        recall_module._recall_context(
            "query",
            "project:test",
            settings=recall_module.CodexPluginSettings(),
            deadline=time.monotonic() + 1,
        )
        is None
    )
    assert json.loads(errors.getvalue())["outcome"] == "version_mismatch"


@pytest.mark.parametrize(
    ("status", "code"),
    [(404, "invalid_request"), (409, "scope_conflict"), (422, "invalid_request")],
)
def test_context_prepare_domain_errors_remain_visible(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    code: str,
) -> None:
    monkeypatch.setattr(
        recall_module,
        "_prepare_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            recall_module._HttpStatusError(status, "/v1/context/prepare", code)
        ),
    )
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)

    assert (
        recall_module._recall_context(
            "query",
            "project:test",
            settings=recall_module.CodexPluginSettings(),
            deadline=time.monotonic() + 1,
        )
        is None
    )
    assert json.loads(errors.getvalue()) == {
        "component": "powercontext.codex.recall",
        "event": "context_prepare",
        "outcome": "invalid_response",
        "http_status": status,
        "error_code": code,
    }


@pytest.mark.parametrize(
    ("status", "code"),
    [(404, "source_not_found"), (409, "source_conflict"), (422, "invalid_request")],
)
def test_capture_domain_errors_remain_visible_as_automatic_failures(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    code: str,
) -> None:
    monkeypatch.setattr(recall_module, "_prepare_context", lambda *_args, **_kwargs: _prepared("prepared context"))
    monkeypatch.setattr(recall_module, "resolve_scope_id", lambda *_args, **_kwargs: "project:test")
    monkeypatch.setattr(
        recall_module,
        "_capture_prompt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            recall_module._HttpStatusError(status, "/v1/sources/content", code)
        ),
    )

    output = io.StringIO()
    errors = io.StringIO()
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/workspace/project",
                "prompt": "Recall despite a domain error",
            })
        ),
    )
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)

    assert recall_module.main() == 0
    result = json.loads(output.getvalue())
    assert result["hookSpecificOutput"]["additionalContext"] == "prepared context"
    assert json.loads(result["systemMessage"]) == {
        "component": "powercontext.codex.recall",
        "event": "capture_source",
        "outcome": "invalid_response",
        "http_status": status,
        "error_code": code,
    }
    assert errors.getvalue() == ""


def test_flush_domain_error_remains_visible_as_an_automatic_failure(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recall_module, "_prepare_context", lambda *_args, **_kwargs: _prepared("prepared context"))
    monkeypatch.setattr(recall_module, "resolve_scope_id", lambda *_args, **_kwargs: "project:test")
    monkeypatch.setattr(recall_module, "_capture_prompt", lambda *_args, **_kwargs: {"position": 1})
    monkeypatch.setattr(
        recall_module,
        "_flush_through",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            recall_module._HttpStatusError(422, "/v1/memory/flush", "invalid_request")
        ),
    )

    output = io.StringIO()
    errors = io.StringIO()
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/workspace/project",
                "prompt": "Recall before flushing",
            })
        ),
    )
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)

    settings = recall_module.CodexPluginSettings(flush_on_capture=True)
    assert recall_module.main(settings=settings) == 0

    result = json.loads(output.getvalue())
    assert json.loads(result["systemMessage"]) == {
        "component": "powercontext.codex.recall",
        "event": "flush_memory",
        "outcome": "invalid_response",
        "http_status": 422,
        "error_code": "invalid_request",
    }
    assert errors.getvalue() == ""


def test_capture_prompt_is_idempotent_and_preserves_provenance(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def post(
        path: str,
        payload: dict[str, object],
        *,
        settings: object,
        deadline: float,
    ) -> dict[str, object]:
        requests.append((path, payload))
        return {"position": 1}

    monkeypatch.setattr(recall_module, "_post_json", post)
    hook_payload = {
        "session_id": "session-1",
        "turn_id": "turn-2",
    }

    first = recall_module._capture_prompt(
        hook_payload,
        prompt="Keep the Source pipeline.",
        cwd="/workspace/project",
        scope_id="project:test",
        settings=recall_module.CodexPluginSettings(),
        deadline=10.0,
    )
    second = recall_module._capture_prompt(
        hook_payload,
        prompt="Keep the Source pipeline.",
        cwd="/workspace/project",
        scope_id="project:test",
        settings=recall_module.CodexPluginSettings(),
        deadline=10.0,
    )

    assert first == second == {"position": 1}
    assert requests[0] == requests[1]
    path, payload = requests[0]
    assert path == "/v1/sources/content"
    source_id = payload["source_id"]
    assert isinstance(source_id, str)
    assert source_id.startswith("codex-user-prompt:")
    assert payload["content"] == "Keep the Source pipeline."
    assert payload["metadata"] == {
        "origin": "codex",
        "event": "user_prompt_submit",
        "cwd": "/workspace/project",
        "session_id": "session-1",
        "turn_id": "turn-2",
    }


def test_flush_reaches_the_captured_source_position(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursors = iter((1, 3))
    requests: list[tuple[str, dict[str, object]]] = []

    def post(
        path: str,
        payload: dict[str, object],
        *,
        settings: object,
        deadline: float,
    ) -> dict[str, object]:
        requests.append((path, payload))
        return {"current_cursor": next(cursors)}

    monkeypatch.setattr(recall_module, "_post_json", post)

    recall_module._flush_through(
        "project:test",
        3,
        settings=recall_module.CodexPluginSettings(),
        deadline=10.0,
    )

    assert requests == [
        ("/v1/memory/flush", {"scope_id": "project:test"}),
        ("/v1/memory/flush", {"scope_id": "project:test"}),
    ]


def test_flush_is_bounded_by_settings(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def post(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"current_cursor": 0}

    monkeypatch.setattr(recall_module, "_post_json", post)

    with pytest.raises(RuntimeError):
        recall_module._flush_through(
            "project:test",
            3,
            settings=recall_module.CodexPluginSettings(flush_max_calls=2),
            deadline=10.0,
        )

    assert calls == 2


def test_hook_refuses_redirects(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_headers: list[dict[str, str]] = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            target_headers.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(TargetHandler) as target_url:

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.send_response(302)
                self.send_header("Location", f"{target_url}/stolen")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass

        monkeypatch.setenv("POWERCONTEXT_CODEX_AUTHORIZATION", "Bearer secret-token")
        with _serve(RedirectHandler) as source_url:
            settings = recall_module.CodexPluginSettings()
            object.__setattr__(settings, "server_url", source_url)
            with pytest.raises(RuntimeError):
                recall_module._post_json(
                    "/redirect",
                    {"scope_id": "project:test"},
                    settings=settings,
                    deadline=time.monotonic() + 1,
                )

    assert target_headers == []


def test_http_error_preserves_structured_error_code(recall_module: ModuleType) -> None:
    class ErrorHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = b'{"error":{"code":"invalid_request","message":"bad request"}}'
            self.send_response(422)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(ErrorHandler) as server_url:
        settings = recall_module.CodexPluginSettings()
        object.__setattr__(settings, "server_url", server_url)
        with pytest.raises(recall_module._HttpStatusError) as caught:
            recall_module._post_json(
                "/v1/context/prepare",
                {},
                settings=settings,
                deadline=time.monotonic() + 1,
                expected_status=200,
            )

    assert caught.value.status == 422
    assert caught.value.path == "/v1/context/prepare"
    assert caught.value.code == "invalid_request"


def test_hook_aborts_a_slow_error_response_at_the_shared_deadline(
    recall_module: ModuleType,
) -> None:
    class SlowErrorHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = b'{"error":{"code":"invalid_request","message":"slow"}}'
            self.send_response(422)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for byte in body:
                try:
                    self.wfile.write(bytes((byte,)))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(0.02)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(SlowErrorHandler) as server_url:
        started = time.monotonic()
        settings = recall_module.CodexPluginSettings(
            request_timeout_seconds=1.0,
            http_budget_seconds=0.1,
        )
        object.__setattr__(settings, "server_url", server_url)
        with pytest.raises(recall_module._ServerUnavailableError):
            recall_module._post_json(
                "/v1/context/prepare",
                {},
                settings=settings,
                deadline=started + 0.1,
                expected_status=200,
            )
        elapsed = time.monotonic() - started

    assert elapsed < 0.5


def test_hook_aborts_a_slow_response_at_the_request_deadline(
    recall_module: ModuleType,
) -> None:
    class SlowHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{")
            self.wfile.flush()
            time.sleep(0.2)
            with suppress(BrokenPipeError):
                self.wfile.write(b"}")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(SlowHandler) as server_url:
        started = time.monotonic()
        settings = recall_module.CodexPluginSettings(
            request_timeout_seconds=0.05,
            http_budget_seconds=0.1,
        )
        object.__setattr__(settings, "server_url", server_url)
        with pytest.raises(RuntimeError):
            recall_module._post_json(
                "/slow",
                {},
                settings=settings,
                deadline=started + 0.1,
            )

    assert time.monotonic() - started < 0.6


def test_expired_request_deadline_is_reported_as_server_unavailable(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recall_module, "_remaining_time", lambda _deadline: (_ for _ in ()).throw(TimeoutError))

    with pytest.raises(recall_module._ServerUnavailableError):
        recall_module._post_json(
            "/v1/context/prepare",
            {},
            settings=recall_module.CodexPluginSettings(),
            deadline=time.monotonic() + 1,
        )


def test_prompt_capture_can_be_disabled(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_CODEX_CAPTURE_PROMPTS", "false")

    assert recall_module.CodexPluginSettings().capture_prompts is False
