from __future__ import annotations

import io
import json
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def _run_main(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> tuple[str, str]:
    output = io.StringIO()
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)
    assert hook_module.main() == 0
    return output.getvalue(), errors.getvalue()


def test_user_prompt_submit_injects_prepared_context_and_captures_prompt(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared_content = (
        "PowerContext prepared untrusted historical context.\n\n"
        "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n"
        '{"trust":"untrusted_history","items":[]}\n'
        "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
    )
    monkeypatch.setattr(
        hook_module,
        "_prepare_context",
        lambda _query, _scope, *, settings, deadline: _prepared(prepared_content),
    )
    monkeypatch.setattr(
        hook_module,
        "derive_scope_id",
        lambda _cwd, *, configured_scope_id: "git:github.com/oceanbase/powercontext",
    )
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        hook_module,
        "_capture_prompt",
        lambda _payload, *, prompt, cwd, scope_id, settings, deadline: (
            captured.append((prompt, scope_id)) or {"position": 1}
        ),
    )

    output, _ = _run_main(
        hook_module,
        monkeypatch,
        {
            "hook_event_name": "UserPromptSubmit",
            "cwd": "/workspace/project",
            "prompt": "What decisions apply?",
            "user_prompt": "compatibility fallback must not win",
        },
    )

    context = json.loads(output)["hookSpecificOutput"]["additionalContext"]
    assert context == prepared_content
    assert captured == [("What decisions apply?", "git:github.com/oceanbase/powercontext")]


def test_user_prompt_compatibility_fallback_is_supported(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hook_module,
        "_prepare_context",
        lambda *_args, **_kwargs: _prepared(None, status="empty"),
    )
    monkeypatch.setattr(
        hook_module,
        "derive_scope_id",
        lambda _cwd, *, configured_scope_id: "project:test",
    )
    captured: list[str] = []
    monkeypatch.setattr(
        hook_module,
        "_capture_prompt",
        lambda _payload, *, prompt, **_kwargs: captured.append(prompt) or {"position": 1},
    )

    _run_main(
        hook_module,
        monkeypatch,
        {
            "hook_event_name": "UserPromptSubmit",
            "cwd": "/workspace/project",
            "user_prompt": "Older payload shape",
        },
    )

    assert captured == ["Older payload shape"]


def test_unexpected_recall_failure_does_not_prevent_prompt_capture(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hook_module,
        "_recall_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unexpected")),
    )
    monkeypatch.setattr(
        hook_module,
        "derive_scope_id",
        lambda _cwd, *, configured_scope_id: "project:test",
    )
    captured: list[str] = []
    monkeypatch.setattr(
        hook_module,
        "_capture_prompt",
        lambda _payload, *, prompt, **_kwargs: captured.append(prompt) or {"position": 1},
    )

    output, _ = _run_main(
        hook_module,
        monkeypatch,
        {
            "hook_event_name": "UserPromptSubmit",
            "cwd": "/workspace/project",
            "prompt": "Still capture this",
        },
    )

    assert output == ""
    assert captured == ["Still capture this"]


def test_capture_failure_does_not_prevent_context_injection(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hook_module,
        "_prepare_context",
        lambda *_args, **_kwargs: _prepared("prepared context"),
    )
    monkeypatch.setattr(
        hook_module,
        "derive_scope_id",
        lambda _cwd, *, configured_scope_id: "project:test",
    )
    monkeypatch.setattr(
        hook_module,
        "_capture_prompt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("capture failed")),
    )

    output, _ = _run_main(
        hook_module,
        monkeypatch,
        {
            "hook_event_name": "UserPromptSubmit",
            "cwd": "/workspace/project",
            "prompt": "Recall despite capture failure",
        },
    )

    assert json.loads(output)["hookSpecificOutput"]["additionalContext"] == "prepared context"


def test_recall_and_capture_share_one_http_deadline(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadlines: list[float] = []
    monkeypatch.setattr(
        hook_module,
        "_recall_context",
        lambda *_args, deadline, **_kwargs: deadlines.append(deadline),
    )
    monkeypatch.setattr(
        hook_module,
        "_capture_prompt",
        lambda *_args, deadline, **_kwargs: deadlines.append(deadline) or {"position": 1},
    )
    monkeypatch.setattr(
        hook_module,
        "derive_scope_id",
        lambda _cwd, *, configured_scope_id: "project:test",
    )

    _run_main(
        hook_module,
        monkeypatch,
        {
            "hook_event_name": "UserPromptSubmit",
            "cwd": "/workspace/project",
            "prompt": "Use one wall-clock budget",
        },
    )

    assert len(deadlines) == 2
    assert deadlines[0] == deadlines[1]


def test_prompt_capture_can_be_disabled(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hook_module,
        "_prepare_context",
        lambda *_args, **_kwargs: _prepared(None, status="empty"),
    )
    monkeypatch.setattr(
        hook_module,
        "_capture_prompt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("capture must be disabled")),
    )
    monkeypatch.setattr(
        hook_module,
        "derive_scope_id",
        lambda _cwd, *, configured_scope_id: "project:test",
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
                "prompt": "Do not capture this",
            })
        ),
    )
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)

    assert hook_module.main(hook_module.ClaudeCodePluginSettings(capture_prompts=False)) == 0
    assert output.getvalue() == ""


def test_context_request_uses_prepare_once(hook_module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[tuple[str, dict[str, object], int | None]] = []

    def post(
        path: str,
        payload: dict[str, object],
        *,
        settings: object,
        deadline: float,
        expected_status: int | None = None,
    ) -> dict[str, object]:
        requests.append((path, payload, expected_status))
        return _prepared(None, status="empty")

    monkeypatch.setattr(hook_module, "_post_json", post)

    hook_module._prepare_context(
        "query",
        "project:test",
        settings=hook_module.ClaudeCodePluginSettings(),
        deadline=10.0,
    )

    assert requests == [
        (
            "/v1/context/prepare",
            {"scope_id": "project:test", "query": "query", "max_bytes": 8000},
            200,
        )
    ]


def test_capture_prompt_is_idempotent_and_is_not_a_task_outcome(
    hook_module: ModuleType,
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

    monkeypatch.setattr(hook_module, "_post_json", post)
    payload = {"session_id": "session-1", "prompt_id": "prompt-2"}
    arguments = {
        "prompt": "Keep the Source pipeline.",
        "cwd": "/workspace/project",
        "scope_id": "project:test",
        "settings": hook_module.ClaudeCodePluginSettings(),
        "deadline": 10.0,
    }

    hook_module._capture_prompt(payload, **arguments)
    hook_module._capture_prompt(payload, **arguments)

    assert requests[0] == requests[1]
    path, request = requests[0]
    assert path == "/v1/sources/content"
    source_id = request["source_id"]
    metadata = request["metadata"]
    assert isinstance(source_id, str)
    assert source_id.startswith("claude-code-user-prompt:")
    assert isinstance(metadata, dict)
    assert metadata == {
        "origin": "claude-code",
        "event": "user_prompt_submit",
        "cwd": "/workspace/project",
        "session_id": "session-1",
        "prompt_id": "prompt-2",
    }
    assert "kind" not in metadata


@pytest.mark.parametrize(
    ("status", "outcome"),
    [(401, "authentication_failed"), (404, "version_mismatch"), (503, "server_unavailable")],
)
def test_http_failures_are_non_blocking_and_content_free(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    outcome: str,
) -> None:
    monkeypatch.setattr(
        hook_module,
        "_prepare_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(hook_module._HttpStatusError(status)),
    )
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)

    context = hook_module._recall_context(
        "secret-query",
        "secret-scope",
        settings=hook_module.ClaudeCodePluginSettings(),
        deadline=time.monotonic() + 1,
    )

    assert context is None
    diagnostic = json.loads(errors.getvalue())
    assert diagnostic["outcome"] == outcome
    assert diagnostic["http_status"] == status
    assert "secret" not in errors.getvalue()


def test_unknown_schema_and_oversized_content_are_not_injected(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _prepared("do-not-log")
    response["schema"] = "powercontext.prepared-context.v2"
    monkeypatch.setattr(hook_module, "_prepare_context", lambda *_args, **_kwargs: response)
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)

    assert (
        hook_module._recall_context(
            "secret-query",
            "secret-scope",
            settings=hook_module.ClaudeCodePluginSettings(),
            deadline=time.monotonic() + 1,
        )
        is None
    )
    with pytest.raises(hook_module._InvalidResponseError):
        hook_module._validate_prepared_context(_prepared("x" * 8_001))
    assert json.loads(errors.getvalue())["outcome"] == "invalid_response"
    assert "secret" not in errors.getvalue()


@pytest.mark.parametrize(
    "response",
    [
        {"schema": "powercontext.prepared-context.v1", "status": "ready", "content": "missing byte count"},
        {"schema": "powercontext.prepared-context.v1", "status": "empty", "content": "not empty", "content_bytes": 9},
        {"schema": "powercontext.prepared-context.v1", "status": "ready", "content": "bad count", "content_bytes": 1},
    ],
)
def test_malformed_prepared_context_is_not_injected(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, object],
) -> None:
    monkeypatch.setattr(hook_module, "_prepare_context", lambda *_args, **_kwargs: response)
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)

    assert (
        hook_module._recall_context(
            "query",
            "project:test",
            settings=hook_module.ClaudeCodePluginSettings(),
            deadline=time.monotonic() + 1,
        )
        is None
    )
    assert json.loads(errors.getvalue())["outcome"] == "invalid_response"


def test_hook_refuses_redirects(
    hook_module: ModuleType,
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

        with _serve(RedirectHandler) as source_url:
            settings = hook_module.ClaudeCodePluginSettings(
                server_url=source_url,
                authorization="Bearer secret-token",
            )
            with pytest.raises(RuntimeError):
                hook_module._post_json(
                    "/redirect",
                    {"scope_id": "project:test"},
                    settings=settings,
                    deadline=time.monotonic() + 1,
                )

    assert target_headers == []


def test_hook_rejects_an_oversized_response_body(hook_module: ModuleType) -> None:
    class OversizedResponse:
        fp = object()

        def __init__(self) -> None:
            self.remaining = hook_module._MAX_RESPONSE_BYTES + 1

        def read(self, amount: int = -1) -> bytes:
            size = min(amount, self.remaining)
            self.remaining -= size
            return b"x" * size

    with pytest.raises(ValueError, match="exceeds the hook limit"):
        hook_module._read_response(
            OversizedResponse(),
            deadline=time.monotonic() + 2,
        )
