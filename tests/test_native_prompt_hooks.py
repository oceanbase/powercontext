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

"""Native host events exercise the installed client's shared prompt pipeline."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTENT = "Historical evidence: use the established service boundary."
SCOPE_CONTEXT = 'PowerContext Scope: "project:test". Use this exact scope_id for PowerContext tools.\n'
RECALLED_CONTEXT = SCOPE_CONTEXT + CONTENT


@dataclass
class Hook:
    module: Any
    settings: Any
    host: str
    monkeypatch: Any
    responses: dict[str, Any] = field(default_factory=dict)
    requests: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    slow: str | None = None

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content)
        self.requests.append((path, body))
        if self.slow == path:
            await asyncio.sleep(1.1)
        if path in self.responses:
            response = self.responses[path]
            return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)
        if path == "/v1/context/prepare":
            value = {
                "schema": "powercontext.prepared-context.v1",
                "status": "ready",
                "content": CONTENT,
                "content_bytes": len(CONTENT.encode()),
            }
        elif path == "/v1/sources/content":
            value = {"status": "accepted", "source": {"name": "content", "source_id": body["source_id"]}, "position": 3}
        elif path == "/v1/memory/flush":
            value = {
                "status": "processed",
                "previous_cursor": 0,
                "current_cursor": 3,
                "high_watermark": 3,
                "processed_source_count": 3,
            }
        else:
            raise AssertionError(path)
        return httpx.Response(202 if path == "/v1/sources/content" else 200, json=value)

    def run(self, **payload: Any) -> tuple[dict[str, Any], str]:
        event = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Recall this project.",
            "cwd": "/workspace",
            "session_id": "session-1",
            "prompt_id": "event-1",
            "turn_id": "event-1",
            **payload,
        }
        # A non-UTF-8 host text wrapper must not corrupt UTF-8 hook input.
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(event, ensure_ascii=False).encode()), encoding="ascii")
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.monkeypatch.context() as patch:
            patch.setattr(sys, "stdin", stdin)
            patch.setattr(sys, "stdout", stdout)
            patch.setattr(sys, "stderr", stderr)
            assert self.module.main(self.settings) == 0
        return json.loads(stdout.getvalue() or "{}"), stderr.getvalue()


@pytest.fixture(params=["codex", "claude-code", "workbuddy"])
def hook(request, monkeypatch, tmp_path):
    host = request.param
    filename, settings_name = {
        "codex": ("recall.py", "CodexPluginSettings"),
        "claude-code": ("user_prompt_submit.py", "ClaudeCodePluginSettings"),
        "workbuddy": ("workbuddy_powercontext_hook.py", "WorkBuddyPluginSettings"),
    }[host]
    path = ROOT / "integrations" / host / "plugins/powercontext/hooks" / filename
    spec = importlib.util.spec_from_file_location(f"native_{host}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous_path = sys.path[:]
    # The native hosts intentionally use the same script name in separate processes.
    monkeypatch.delitem(sys.modules, "workspace_scope", raising=False)
    monkeypatch.delitem(sys.modules, "settings", raising=False)
    spec.loader.exec_module(module)
    settings = getattr(module, settings_name)(
        capture_prompts=True, flush_on_capture=True, request_timeout_seconds=0.1, http_budget_seconds=0.5
    )
    value = Hook(module, settings, host, monkeypatch)
    monkeypatch.setattr(module, "resolve_scope_id", lambda *_args, **_kwargs: "project:test")
    monkeypatch.setenv("POWERCONTEXT_DIAGNOSTIC_STATE_FILE", str(tmp_path / "diagnostics.json"))
    monkeypatch.delenv("POWERCONTEXT_EVAL_TRACE_PATH", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(value.handle)))
    try:
        yield value
    finally:
        sys.path[:] = previous_path


def context(output):
    return output.get("hookSpecificOutput", {}).get("additionalContext")


def test_default_budget_allows_recall_taking_more_than_one_second(hook):
    hook.settings = type(hook.settings)(capture_prompts=False)
    hook.slow = "/v1/context/prepare"

    assert context(hook.run()[0]) == RECALLED_CONTEXT


def test_recall_precedes_capture_and_flush_preserves_native_identity(hook):
    output, _ = hook.run(prompt="Recall café context.")
    assert context(output) == RECALLED_CONTEXT
    assert [path for path, _ in hook.requests] == ["/v1/context/prepare", "/v1/sources/content", "/v1/memory/flush"]
    prepared, captured = hook.requests[0][1], hook.requests[1][1]
    assert prepared["query"] == captured["content"] == "Recall café context."
    assert prepared["scope_id"] == captured["scope_id"] == "project:test"
    assert captured["metadata"] == {
        "origin": hook.host,
        "event": "user_prompt_submit",
        "cwd": "/workspace",
        "session_id": "session-1",
        "turn_id" if hook.host == "codex" else "prompt_id": "event-1",
    }
    hook.run(prompt="Recall café context.")
    assert captured["source_id"] == hook.requests[4][1]["source_id"]


@pytest.mark.parametrize("event", ["UserPromptSubmit", "user_prompt_submit"])
def test_native_event_spellings_are_accepted(hook, event):
    assert context(hook.run(hook_event_name=event)[0]) == RECALLED_CONTEXT


@pytest.mark.parametrize("payload", [{"hook_event_name": "Stop"}, {"prompt": ""}, {"cwd": None}])
def test_irrelevant_or_incomplete_events_do_not_send_content(hook, payload):
    hook.run(**payload)
    assert hook.requests == []


def test_legacy_prompt_field_is_a_host_mapping(hook):
    output, _ = hook.run(prompt=None, user_prompt="Recall the legacy event.")
    if hook.host == "codex":
        assert hook.requests == []
    else:
        assert context(output) == RECALLED_CONTEXT


def test_scope_failure_never_dispatches_a_domain_operation(hook):
    def unavailable(*args, **kwargs):
        raise TimeoutError

    hook.monkeypatch.setattr(hook.module, "resolve_scope_id", unavailable)
    output, _ = hook.run()
    assert context(output) == ("" if hook.host == "workbuddy" else None)
    assert hook.requests == []


@pytest.mark.parametrize("capture,flush,expected", [(False, True, 1), (True, False, 2)])
def test_capture_and_flush_require_configuration_consent(hook, capture, flush, expected):
    hook.settings = type(hook.settings)(capture_prompts=capture, flush_on_capture=flush)
    assert context(hook.run()[0]) == RECALLED_CONTEXT
    assert len(hook.requests) == expected


def test_oversized_prompt_is_recalled_without_capture(hook):
    hook.run(prompt="x" * 200_001)
    assert not any(path != "/v1/context/prepare" for path, _ in hook.requests)


@pytest.mark.parametrize("path", ["/v1/context/prepare", "/v1/sources/content", "/v1/memory/flush"])
@pytest.mark.parametrize("status", [401, 404, 409, 422, 503])
def test_failures_are_content_free_and_do_not_replay_writes(hook, path, status):
    hook.responses[path] = httpx.Response(
        status, json={"error": {"code": "scope_not_found", "message": "private-body"}}
    )
    output, stderr = hook.run()
    assert "private-body" not in json.dumps(output) + stderr
    diagnostic = json.loads(output["systemMessage"])
    assert diagnostic["http_status"] == status
    assert (
        diagnostic["event"]
        == {
            "/v1/context/prepare": "context_prepare",
            "/v1/sources/content": "capture_source",
            "/v1/memory/flush": "flush_memory",
        }[path]
    )
    assert context(output) == (SCOPE_CONTEXT if path == "/v1/context/prepare" else RECALLED_CONTEXT)
    paths = [requested for requested, _ in hook.requests]
    assert paths.count(path) == 1
    if path == "/v1/sources/content":
        assert "/v1/memory/flush" not in paths


@pytest.mark.parametrize(
    "response",
    [
        {"schema": "unknown", "status": "ready", "content": "private-body", "content_bytes": 12},
        {
            "schema": "powercontext.prepared-context.v1",
            "status": "ready",
            "content": "private-body",
            "content_bytes": 1,
        },
        {
            "schema": "powercontext.prepared-context.v1",
            "status": "empty",
            "content": "private-body",
            "content_bytes": 12,
        },
    ],
)
def test_invalid_core_responses_are_never_injected(hook, response):
    hook.responses["/v1/context/prepare"] = response
    output, stderr = hook.run()
    assert context(output) == SCOPE_CONTEXT
    assert "private-body" not in json.dumps(output) + stderr
    assert "/v1/sources/content" in [path for path, _ in hook.requests]


def test_empty_context_is_success_and_capture_continues(hook):
    hook.responses["/v1/context/prepare"] = {
        "schema": "powercontext.prepared-context.v1",
        "status": "empty",
        "content": None,
        "content_bytes": 0,
    }
    output, stderr = hook.run()
    assert context(output) == SCOPE_CONTEXT
    assert "systemMessage" not in output
    assert json.loads(stderr)["outcome"] == "empty"
    assert len(hook.requests) == 3


def test_diagnostics_are_throttled_across_invocations(hook):
    hook.responses["/v1/context/prepare"] = httpx.Response(401, json={})
    assert "systemMessage" in hook.run()[0]
    assert "systemMessage" not in hook.run()[0]


def test_unknown_capture_never_starts_a_flush(hook):
    hook.responses["/v1/sources/content"] = {"position": 3}
    assert context(hook.run()[0]) == RECALLED_CONTEXT
    assert "/v1/memory/flush" not in [path for path, _ in hook.requests]


def test_timeout_does_not_block_the_remaining_prompt_flow(hook):
    hook.slow = "/v1/context/prepare"
    output, _ = hook.run()
    assert context(output) == SCOPE_CONTEXT
    assert json.loads(output["systemMessage"])["outcome"] == "server_unavailable"
    assert len(hook.requests) == 3


def test_flush_stops_when_the_cursor_stops_advancing(hook):
    hook.responses["/v1/memory/flush"] = {
        "status": "idle",
        "previous_cursor": 0,
        "current_cursor": 0,
        "high_watermark": 3,
        "processed_source_count": 0,
    }
    assert context(hook.run()[0]) == RECALLED_CONTEXT
    assert len([path for path, _ in hook.requests if path == "/v1/memory/flush"]) == 2
