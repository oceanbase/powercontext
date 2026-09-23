# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import io
import json
import sys
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest


def _ready(content: str = "curated bootstrap context") -> dict[str, object]:
    return {
        "schema": "powercontext.bootstrap-context.v1",
        "status": "ready",
        "reason": None,
        "profile": "powercontext.scope-bootstrap.v1",
        "content": content,
        "content_bytes": len(content.encode()),
        "package_digest": "sha256:" + sha256(content.encode()).hexdigest(),
        "items": [
            {
                "kind": "memory_entry",
                "scope_id": "scope:test",
                "artifact": {"family": "memory", "artifact_id": "memory", "revision": 3},
                "entry_id": "entry-1",
                "entry_version_id": "entry-version-1",
                "content_digest": "sha256:" + "a" * 64,
                "truncated": False,
            }
        ],
        "truncated": False,
        "receipt": {"receipt_id": "bcr_test", "state": "pending"},
    }


def _skipped(reason: str = "disabled") -> dict[str, object]:
    return {
        "schema": "powercontext.bootstrap-context.v1",
        "status": "skipped",
        "reason": reason,
        "profile": "powercontext.scope-bootstrap.v1",
        "content": None,
        "content_bytes": 0,
        "package_digest": None,
        "items": [],
        "truncated": False,
        "receipt": {"receipt_id": "bcr_skipped", "state": "skipped"},
    }


def _empty() -> dict[str, object]:
    value = _skipped("no_eligible_context")
    value["status"] = "empty"
    return value


def _already_delivered() -> dict[str, object]:
    value = _skipped("already_delivered")
    value["receipt"] = {"receipt_id": "bcr_test", "state": "injected"}
    return value


def test_session_start_marks_delivery_before_injecting_and_first_query_sends_receipt(
    session_start_module: ModuleType,
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def post(path, payload, **_kwargs):
        requests.append((path, payload))
        if path.endswith("/receipts"):
            return {"receipt_id": "bcr_test", "state": "injected"}
        return _ready()

    monkeypatch.setattr(session_start_module, "_post_json", post)
    monkeypatch.setattr(session_start_module, "resolve_scope_id", lambda *_args, **_kwargs: "scope:test")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "SessionStart",
                "source": "startup",
                "session_id": "session-1",
                "cwd": "/workspace",
                "event_id": "event-1",
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    settings = session_start_module.ClaudeCodePluginSettings(bootstrap_context=True)

    assert session_start_module.main(settings) == 0
    assert json.loads(output.getvalue()) == {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "curated bootstrap context",
        }
    }
    assert requests[0] == (
        "/v1/context/bootstrap",
        {
            "scope_id": "scope:test",
            "enabled": True,
            "profile": "powercontext.scope-bootstrap.v1",
            "lifecycle": "startup",
            "integration": "claude-code",
            "max_bytes": 4096,
            "event_id": "event-1",
        },
    )
    assert requests[1] == (
        "/v1/context/bootstrap/receipts",
        {"scope_id": "scope:test", "receipt_id": "bcr_test", "outcome": "injected"},
    )

    state_files = list((tmp_path / "plugin-data" / "powercontext-bootstrap").glob("*.json"))
    assert len(state_files) == 1
    state = state_files[0].read_text()
    assert "curated bootstrap context" not in state
    assert "event-1" not in state
    assert "session-1" not in state

    prepared_receipts: list[str | None] = []

    def prepare(_query, _scope, *, settings, deadline, bootstrap_receipt_id=None):
        prepared_receipts.append(bootstrap_receipt_id)
        return {"schema": "powercontext.prepared-context.v1", "status": "empty", "content": None, "content_bytes": 0}

    monkeypatch.setattr(hook_module, "_prepare_context", prepare)
    monkeypatch.setattr(hook_module, "resolve_scope_id", lambda *_args, **_kwargs: "scope:test")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "session_id": "session-1",
                "cwd": "/workspace",
                "prompt": "What applies?",
            })
        ),
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    assert hook_module.main(hook_module.ClaudeCodePluginSettings(capture_prompts=False)) == 0
    assert prepared_receipts == ["bcr_test"]
    assert not list((tmp_path / "plugin-data" / "powercontext-bootstrap").glob("*.json"))

    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "session_id": "session-1",
                "cwd": "/workspace",
                "prompt": "What applies next?",
            })
        ),
    )
    assert hook_module.main(hook_module.ClaudeCodePluginSettings(capture_prompts=False)) == 0
    assert prepared_receipts == ["bcr_test", None]


def test_repeated_injected_event_preserves_receipt_for_first_query(
    session_start_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_start_module.save_receipt("session-1", "scope:test", "bcr_test")
    monkeypatch.setattr(session_start_module, "resolve_scope_id", lambda *_args, **_kwargs: "scope:test")
    monkeypatch.setattr(session_start_module, "_post_json", lambda *_args, **_kwargs: _already_delivered())
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "source": "startup",
                "session_id": "session-1",
                "cwd": "/workspace",
                "event_id": "event-1",
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert session_start_module.main(session_start_module.ClaudeCodePluginSettings(bootstrap_context=True)) == 0
    assert output.getvalue() == ""
    state_files = list((tmp_path / "plugin-data" / "powercontext-bootstrap").glob("*.json"))
    assert len(state_files) == 1
    assert json.loads(state_files[0].read_text())["receipt_id"] == "bcr_test"


def test_invalid_settings_clear_a_stale_receipt(
    session_start_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_start_module.save_receipt("session-1", "scope:test", "bcr_previous")

    def invalid_settings():
        raise ValueError

    monkeypatch.setattr(
        session_start_module.ClaudeCodePluginSettings,
        "from_environment",
        staticmethod(invalid_settings),
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"source": "compact", "session_id": "session-1", "cwd": "/workspace"})),
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert session_start_module.main() == 0
    assert not list((tmp_path / "plugin-data" / "powercontext-bootstrap").glob("*.json"))


@pytest.mark.parametrize("invalid_field", ["receipt", "artifact"])
def test_bootstrap_response_rejects_identifiers_containing_spaces(
    invalid_field: str,
    session_start_module: ModuleType,
) -> None:
    response = _ready()
    if invalid_field == "receipt":
        response["receipt"] = {"receipt_id": "bcr test", "state": "pending"}
    else:
        items = cast(list[dict[str, object]], response["items"])
        artifact = cast(dict[str, object], items[0]["artifact"])
        artifact["artifact_id"] = "memory id"

    with pytest.raises(session_start_module.InvalidBootstrapResponse):
        session_start_module.validate_bootstrap_context(response, max_bytes=4096)


@pytest.mark.parametrize("lifecycle", ["startup", "resume", "clear", "compact", "fork"])
def test_session_start_preserves_distinct_lifecycle_sources(
    lifecycle: str,
    session_start_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []
    monkeypatch.setattr(session_start_module, "resolve_scope_id", lambda *_args, **_kwargs: "scope:test")
    monkeypatch.setattr(
        session_start_module,
        "_post_json",
        lambda _path, payload, **_kwargs: requests.append(payload) or _skipped(),
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"source": lifecycle, "session_id": "session-1", "cwd": "/workspace"})),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert session_start_module.main(session_start_module.ClaudeCodePluginSettings()) == 0
    assert output.getvalue() == ""
    assert requests[0]["lifecycle"] == lifecycle
    assert requests[0]["enabled"] is False


def test_session_start_fails_open_and_does_not_inject_when_delivery_cannot_be_recorded(
    session_start_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def post(path, _payload, **_kwargs):
        nonlocal calls
        calls += 1
        if path == "/v1/context/bootstrap":
            return _ready()
        raise OSError

    monkeypatch.setattr(session_start_module, "_post_json", post)
    monkeypatch.setattr(session_start_module, "resolve_scope_id", lambda *_args, **_kwargs: "scope:test")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"source": "resume", "session_id": "session-1", "cwd": "/workspace"})),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)

    assert session_start_module.main(session_start_module.ClaudeCodePluginSettings(bootstrap_context=True)) == 0
    assert output.getvalue() == ""
    assert calls == 3
    assert json.loads(errors.getvalue())["outcome"] == "delivery_failed"


def test_session_start_output_failure_clears_the_deduplication_receipt(
    session_start_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class BrokenOutput:
        def write(self, _value):
            raise OSError

    def post(path, _payload, **_kwargs):
        return {"receipt_id": "bcr_test", "state": "injected"} if path.endswith("/receipts") else _ready()

    monkeypatch.setattr(session_start_module, "_post_json", post)
    monkeypatch.setattr(session_start_module, "resolve_scope_id", lambda *_args, **_kwargs: "scope:test")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"source": "startup", "session_id": "session-1", "cwd": "/workspace"})),
    )
    monkeypatch.setattr(sys, "stdout", BrokenOutput())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    assert session_start_module.main(session_start_module.ClaudeCodePluginSettings(bootstrap_context=True)) == 0
    assert not list((tmp_path / "plugin-data" / "powercontext-bootstrap").glob("*.json"))

    monkeypatch.setattr(session_start_module, "_post_json", lambda *_args, **_kwargs: _already_delivered())
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"source": "startup", "session_id": "session-1", "cwd": "/workspace"})),
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert session_start_module.main(session_start_module.ClaudeCodePluginSettings(bootstrap_context=True)) == 0
    assert not list((tmp_path / "plugin-data" / "powercontext-bootstrap").glob("*.json"))


def test_session_start_rejects_malformed_bootstrap_without_blocking(
    session_start_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_start_module, "_post_json", lambda *_args, **_kwargs: {"status": "ready"})
    monkeypatch.setattr(session_start_module, "resolve_scope_id", lambda *_args, **_kwargs: "scope:test")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"source": "startup", "session_id": "session-1", "cwd": "/workspace"})),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)

    assert session_start_module.main(session_start_module.ClaudeCodePluginSettings(bootstrap_context=True)) == 0
    assert output.getvalue() == ""
    assert json.loads(errors.getvalue())["outcome"] == "invalid_response"


@pytest.mark.parametrize("result", [_empty(), TimeoutError()])
def test_session_start_empty_or_timeout_fails_open(
    result: dict[str, object] | TimeoutError,
    session_start_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def post(*_args, **_kwargs):
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(session_start_module, "_post_json", post)
    monkeypatch.setattr(session_start_module, "resolve_scope_id", lambda *_args, **_kwargs: "scope:test")
    session_start_module.save_receipt("session-1", "scope:test", "bcr_previous")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"source": "startup", "session_id": "session-1", "cwd": "/workspace"})),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stderr", errors)

    assert session_start_module.main(session_start_module.ClaudeCodePluginSettings(bootstrap_context=True)) == 0
    assert output.getvalue() == ""
    diagnostic = json.loads(errors.getvalue())
    assert diagnostic["outcome"] == ("server_unavailable" if isinstance(result, BaseException) else "empty")
    assert diagnostic["profile"] == "powercontext.scope-bootstrap.v1"
    assert "content" not in diagnostic
    assert not list((tmp_path / "plugin-data" / "powercontext-bootstrap").glob("*.json"))


@pytest.mark.parametrize(
    ("http_status", "outcome"),
    [
        (401, "authentication_failed"),
        (403, "authorization_denied"),
        (404, "version_mismatch"),
        (503, "server_unavailable"),
        (422, "invalid_response"),
    ],
)
def test_session_start_classifies_http_failures_without_blocking(
    http_status: int,
    outcome: str,
    session_start_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status = http_status

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(session_start_module, "open_bounded", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(session_start_module, "resolve_scope_id", lambda *_args, **_kwargs: "scope:test")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"source": "startup", "session_id": "session-1", "cwd": "/workspace"})),
    )
    output = io.StringIO()
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)

    assert session_start_module.main(session_start_module.ClaudeCodePluginSettings(bootstrap_context=True)) == 0
    assert output.getvalue() == ""
    diagnostic = json.loads(errors.getvalue())
    assert diagnostic["outcome"] == outcome
    assert diagnostic["http_status"] == http_status
    assert "content" not in diagnostic
