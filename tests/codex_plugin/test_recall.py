from __future__ import annotations

import io
import json
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
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


def test_recall_emits_bounded_untrusted_context(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        recall_module,
        "_search",
        lambda _query, _scope, *, settings, deadline: {
            "hits": [
                {"text": "Use the public API.\nDo not duplicate it."},
                {"text": "Run make test."},
            ]
        },
    )
    monkeypatch.setattr(
        recall_module,
        "derive_scope_id",
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
    assert "untrusted historical data" in context
    assert "[memory] Use the public API. Do not duplicate it." in context
    assert len(context) <= 8_000
    assert captured == [("What decisions apply?", "project:test")]


def test_recall_failure_is_non_blocking(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        recall_module,
        "_search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()),
    )
    monkeypatch.setattr(
        recall_module,
        "derive_scope_id",
        lambda _cwd, *, configured_scope_id: "project:test",
    )
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
    monkeypatch.setattr(sys, "stdout", output)

    assert recall_module.main() == 0
    assert output.getvalue() == ""


@pytest.mark.parametrize("event_name", ["UserPromptSubmit", "user_prompt_submit"])
def test_hook_accepts_codex_event_name_variants(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    event_name: str,
) -> None:
    monkeypatch.setattr(recall_module, "_search", lambda *_args, **_kwargs: {"hits": []})
    captured: list[str] = []
    monkeypatch.setattr(
        recall_module,
        "_capture_prompt",
        lambda _payload, *, prompt, cwd, scope_id, settings, deadline: captured.append(prompt) or {"position": 1},
    )
    monkeypatch.setattr(
        recall_module,
        "derive_scope_id",
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

    assert recall_module.main() == 0
    assert captured == ["Capture this input."]


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


def test_prompt_capture_can_be_disabled(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_CODEX_CAPTURE_PROMPTS", "false")

    assert recall_module.CodexPluginSettings().capture_prompts is False
