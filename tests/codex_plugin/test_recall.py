from __future__ import annotations

import io
import json
import sys
from types import ModuleType

import pytest


def test_recall_emits_bounded_untrusted_context(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        recall_module,
        "_search",
        lambda _query, _scope: {
            "hits": [
                {"text": "Use the public API.\nDo not duplicate it."},
                {"text": "Run make test."},
            ]
        },
    )
    monkeypatch.setattr(recall_module, "derive_scope_id", lambda _cwd: "project:test")
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        recall_module,
        "_capture_prompt",
        lambda _payload, *, prompt, cwd, scope_id: captured.append((prompt, scope_id)) or {"position": 1},
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
    monkeypatch.setattr(recall_module, "_search", lambda *_args: (_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr(recall_module, "derive_scope_id", lambda _cwd: "project:test")
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
    monkeypatch.setattr(recall_module, "_search", lambda *_args: {"hits": []})
    captured: list[str] = []
    monkeypatch.setattr(
        recall_module,
        "_capture_prompt",
        lambda _payload, *, prompt, cwd, scope_id: captured.append(prompt) or {"position": 1},
    )
    monkeypatch.setattr(recall_module, "derive_scope_id", lambda _cwd: "project:test")
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

    def post(path: str, payload: dict[str, object]) -> dict[str, object]:
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
    )
    second = recall_module._capture_prompt(
        hook_payload,
        prompt="Keep the Source pipeline.",
        cwd="/workspace/project",
        scope_id="project:test",
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

    def post(path: str, payload: dict[str, object]) -> dict[str, object]:
        requests.append((path, payload))
        return {"current_cursor": next(cursors)}

    monkeypatch.setattr(recall_module, "_post_json", post)

    recall_module._flush_through("project:test", 3)

    assert requests == [
        ("/v1/memory/flush", {"scope_id": "project:test"}),
        ("/v1/memory/flush", {"scope_id": "project:test"}),
    ]


def test_prompt_capture_can_be_disabled(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_CAPTURE_PROMPTS", "false")

    assert recall_module._capture_enabled() is False


@pytest.mark.parametrize(
    "value",
    [
        "http://memory.example.com",
        "https://user:password@memory.example.com",
        "file:///tmp/socket",
    ],
)
def test_recall_rejects_unsafe_http_urls(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_HTTP_URL", value)

    with pytest.raises(ValueError):
        recall_module._http_url()
