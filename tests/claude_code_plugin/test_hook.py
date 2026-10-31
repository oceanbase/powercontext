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
import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


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


_RESOLUTION_FAILURES = [
    pytest.param(
        None, b"", {"outcome": "server_unavailable", "recovery": "powercontext doctor"}, id="dropped-connection"
    ),
    pytest.param(
        503,
        b'{"error":{"code":"server_unavailable","message":"unavailable"}}',
        {"outcome": "server_unavailable", "http_status": 503, "recovery": "powercontext doctor"},
        id="unavailable",
    ),
    pytest.param(
        401,
        b'{"error":{"code":"authentication_required","message":"missing credential"}}',
        {"outcome": "authentication_failed", "http_status": 401},
        id="rejected-credential",
    ),
    pytest.param(
        404, b'{"detail":"Not Found"}', {"outcome": "version_mismatch", "http_status": 404}, id="missing-route"
    ),
    pytest.param(
        404,
        b'{"error":{"code":"scope_not_found","message":"no such Scope"}}',
        {"outcome": "invalid_response", "http_status": 404, "error_code": "scope_not_found"},
        id="unknown-scope",
    ),
    pytest.param(
        409,
        b'{"error":{"code":"scope_binding_target_missing","message":"binding target missing"}}',
        {"outcome": "invalid_response", "http_status": 409, "error_code": "scope_binding_target_missing"},
        id="missing-binding-target",
    ),
    pytest.param(
        404,
        b'{"error":{"code":"scope_not_found","message":"' + b"x" * 1_048_576 + b'"}}',
        {"outcome": "invalid_response"},
        id="oversized-error-body",
    ),
    pytest.param(200, b"<html><body>maintenance</body></html>", {"outcome": "invalid_response"}, id="non-json"),
]


_RESOLUTION_PATH = "/v1/scope-bindings/resolve"
_BOOTSTRAP_PATH = "/v1/context/bootstrap"
_PREPARE_PATH = "/v1/context/prepare"


def _failure_handler(
    status: int | None,
    body: bytes,
    paths: list[str] | None = None,
    *,
    path: str = _RESOLUTION_PATH,
    truncated: str | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Answer requests to path with one response, and resolve the Scope for every other request.

    A status of None closes the connection without a response. A truncated response closes the connection inside its
    body: with "chunked" framing inside a chunk longer than its data, with "content-length" framing before the
    announced length.
    """

    class FailureHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if paths is not None:
                paths.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            if self.path != path:
                self._send(200, b'{"scope_id":"scope:test"}')
            elif status is not None and truncated is not None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if truncated == "chunked":
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b"%x\r\n" % (len(body) + 32) + body)
                else:
                    self.send_header("Content-Length", str(len(body) + 32))
                    self.end_headers()
                    self.wfile.write(body)
            elif status is not None:
                self._send(status, body)

        def _send(self, response_status: int, content: bytes) -> None:
            self.send_response(response_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(content)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    return FailureHandler


class _SlowResolutionErrorHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["Content-Length"]))
        body = b'{"error":{"code":"scope_not_found","message":"a slowly sent error body"}}'
        self.send_response(404)
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
    *,
    settings: object | None = None,
) -> tuple[str, str]:
    output = io.StringIO()
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)
    assert hook_module.main(settings=settings) == 0
    return output.getvalue(), errors.getvalue()


def _prepare_query(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> str:
    """Return the query Claude Code asks the Runtime to prepare for one submitted prompt."""

    queries: list[str] = []
    monkeypatch.setattr(
        hook_module,
        "_prepare_context",
        lambda query, _scope, *, settings, deadline: queries.append(query) or _prepared(),
    )
    monkeypatch.setattr(hook_module, "resolve_scope_id", lambda _cwd, **_kwargs: "project:test")
    monkeypatch.setattr(
        hook_module,
        "_capture_prompt",
        lambda _payload, *, prompt, cwd, scope_id, settings, deadline: {"position": 1},
    )

    _run_main(
        hook_module,
        monkeypatch,
        {
            "hook_event_name": "UserPromptSubmit",
            "cwd": "/workspace/project",
            "prompt": prompt,
        },
    )

    (query,) = queries
    return query


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
        "resolve_scope_id",
        lambda _cwd, **_kwargs: "git:github.com/oceanbase/powercontext",
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


def test_user_prompt_submit_passes_a_prompt_that_fits_the_request_bound_through_unchanged(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request contract bounds a query at 8,192 characters, and the bound is inclusive."""

    prompt = "a" * 8_192

    assert _prepare_query(hook_module, monkeypatch, prompt) == prompt


def test_user_prompt_submit_keeps_the_latest_text_of_a_prompt_over_the_request_bound(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a longer prompt used to reach prepare verbatim and fail validation."""

    prompt = "earlier context that the transcript already carries " * 200 + "Which service owns the retry?"
    assert len(prompt) > 8_192
    query = _prepare_query(hook_module, monkeypatch, prompt)

    assert query == prompt[-8_192:]
    assert len(query) == 8_192
    assert query.endswith("Which service owns the retry?")


def test_user_prompt_submit_keeps_a_multibyte_prompt_within_the_request_byte_bound(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-ASCII text costs several bytes per character, so both bounds have to hold."""

    prompt = "记忆 " * 4_000
    query = _prepare_query(hook_module, monkeypatch, prompt)

    assert prompt.strip().endswith(query)
    assert len(query.encode("utf-8")) <= 8_192
    assert "\ufffd" not in query


def test_user_prompt_submit_captures_the_full_prompt_while_the_query_is_bounded(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source capture keeps its own size limit and is untouched by the recall bound."""

    prompt = "记忆 " * 3_000
    captured: list[str] = []
    monkeypatch.setattr(hook_module, "_prepare_context", lambda *_args, **_kwargs: _prepared())
    monkeypatch.setattr(hook_module, "resolve_scope_id", lambda _cwd, **_kwargs: "project:test")
    monkeypatch.setattr(
        hook_module,
        "_capture_prompt",
        lambda _payload, *, prompt, cwd, scope_id, settings, deadline: captured.append(prompt) or {"position": 1},
    )

    _run_main(
        hook_module,
        monkeypatch,
        {
            "hook_event_name": "UserPromptSubmit",
            "cwd": "/workspace/project",
            "prompt": prompt,
        },
    )

    assert captured == [prompt]


def test_user_prompt_submit_reads_utf8_stdin_on_windows_encodings(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared_content = "prepared context"
    captured: list[str] = []
    monkeypatch.setattr(
        hook_module,
        "_prepare_context",
        lambda _query, _scope, *, settings, deadline: _prepared(prepared_content),
    )
    monkeypatch.setattr(
        hook_module,
        "resolve_scope_id",
        lambda _cwd, **_kwargs: "git:github.com/oceanbase/powercontext",
    )
    monkeypatch.setattr(
        hook_module,
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

    assert hook_module.main() == 0
    assert captured == ["查看当前记忆"]
    assert json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"] == prepared_content


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
        "resolve_scope_id",
        lambda _cwd, **_kwargs: "project:test",
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
        "resolve_scope_id",
        lambda _cwd, **_kwargs: "project:test",
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
        "resolve_scope_id",
        lambda _cwd, **_kwargs: "project:test",
    )
    monkeypatch.setattr(
        hook_module,
        "_capture_prompt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("capture failed")),
    )

    output, errors = _run_main(
        hook_module,
        monkeypatch,
        {
            "hook_event_name": "UserPromptSubmit",
            "cwd": "/workspace/project",
            "prompt": "Recall despite capture failure",
        },
    )

    result = json.loads(output)
    assert result["hookSpecificOutput"]["additionalContext"] == "prepared context"
    assert json.loads(result["systemMessage"]) == {
        "component": "powercontext.claude_code.recall",
        "event": "capture_source",
        "outcome": "invalid_response",
    }
    assert errors == ""


def test_host_diagnostic_is_throttled_across_hook_invocations(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hook_module,
        "_prepare_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(hook_module._ServerUnavailableError()),
    )
    monkeypatch.setattr(hook_module, "resolve_scope_id", lambda *_args, **_kwargs: "project:test")
    monkeypatch.setattr(hook_module, "_capture_prompt", lambda *_args, **_kwargs: {"position": 1})

    payload: dict[str, object] = {
        "hook_event_name": "UserPromptSubmit",
        "cwd": "/workspace/project",
        "prompt": "Recall context",
    }
    outputs: list[str] = []
    for _ in range(2):
        output, errors = _run_main(hook_module, monkeypatch, payload)
        assert errors == ""
        outputs.append(output)

    assert json.loads(outputs[0])["systemMessage"]
    assert outputs[1] == ""


@pytest.mark.parametrize(("status", "body", "diagnostic"), _RESOLUTION_FAILURES)
def test_scope_resolution_failure_is_reported_and_nothing_else_is_sent(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: int | None,
    body: bytes,
    diagnostic: dict[str, object],
) -> None:
    paths: list[str] = []
    with _serve(_failure_handler(status, body, paths)) as server_url:
        output, errors = _run_main(
            hook_module,
            monkeypatch,
            {"hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path), "prompt": "Recall context"},
            # Reading the oversized body byte by byte can take seconds on a busy runner; let it reach the size bound.
            settings=hook_module.ClaudeCodePluginSettings(
                server_url=server_url, request_timeout_seconds=10.0, http_budget_seconds=10.0
            ),
        )

    assert paths == ["/v1/scope-bindings/resolve"]
    assert errors == ""
    assert json.loads(json.loads(output)["systemMessage"]) == {
        "component": "powercontext.claude_code.recall",
        "event": "scope_resolve",
        **diagnostic,
    }


def test_scope_resolution_error_body_that_outlasts_the_budget_reports_an_unavailable_server(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with _serve(_SlowResolutionErrorHandler) as server_url:
        output, errors = _run_main(
            hook_module,
            monkeypatch,
            {"hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path), "prompt": "Recall context"},
            settings=hook_module.ClaudeCodePluginSettings(
                server_url=server_url,
                request_timeout_seconds=1.0,
                http_budget_seconds=0.5,
            ),
        )

    assert errors == ""
    assert json.loads(json.loads(output)["systemMessage"]) == {
        "component": "powercontext.claude_code.recall",
        "event": "scope_resolve",
        "outcome": "server_unavailable",
        "recovery": "powercontext doctor",
    }


@pytest.mark.parametrize("framing", ["chunked", "content-length"])
@pytest.mark.parametrize("status", [pytest.param(404, id="error-body"), pytest.param(200, id="success-body")])
def test_truncated_scope_resolution_response_reports_an_unavailable_server(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: int,
    framing: str,
) -> None:
    with _serve(_failure_handler(status, b'{"error":{"code":', truncated=framing)) as server_url:
        output, errors = _run_main(
            hook_module,
            monkeypatch,
            {"hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path), "prompt": "Recall context"},
            settings=hook_module.ClaudeCodePluginSettings(server_url=server_url),
        )

    assert errors == ""
    assert json.loads(json.loads(output)["systemMessage"]) == {
        "component": "powercontext.claude_code.recall",
        "event": "scope_resolve",
        "outcome": "server_unavailable",
        "recovery": "powercontext doctor",
    }


@pytest.mark.parametrize(
    ("handler", "diagnostic"),
    [
        pytest.param(
            _failure_handler(200, b'{"schema":', path=_PREPARE_PATH, truncated="chunked"),
            {"outcome": "server_unavailable", "recovery": "powercontext doctor"},
            id="truncated-chunked-body",
        ),
        pytest.param(
            _failure_handler(200, b'{"schema":', path=_PREPARE_PATH, truncated="content-length"),
            {"outcome": "server_unavailable", "recovery": "powercontext doctor"},
            id="truncated-content-length-body",
        ),
        pytest.param(
            _failure_handler(422, b'{"error":{"code":', path=_PREPARE_PATH, truncated="chunked"),
            {"outcome": "server_unavailable", "recovery": "powercontext doctor"},
            id="truncated-chunked-error-body",
        ),
        pytest.param(
            _failure_handler(422, b'{"error":{"code":', path=_PREPARE_PATH, truncated="content-length"),
            {"outcome": "server_unavailable", "recovery": "powercontext doctor"},
            id="truncated-content-length-error-body",
        ),
        pytest.param(
            _failure_handler(200, b"[" * 100_000, path=_PREPARE_PATH),
            {"outcome": "invalid_response"},
            id="deeply-nested-body",
        ),
        pytest.param(
            _failure_handler(422, b"[" * 100_000, path=_PREPARE_PATH),
            {"outcome": "invalid_response", "http_status": 422},
            id="deeply-nested-error-body",
        ),
        pytest.param(
            _failure_handler(422, b"1" * 5_000, path=_PREPARE_PATH),
            {"outcome": "invalid_response", "http_status": 422},
            id="long-number-error-body",
        ),
        pytest.param(
            _failure_handler(422, b"x" * 1_048_577, path=_PREPARE_PATH),
            {"outcome": "invalid_response"},
            id="oversized-error-body",
        ),
    ],
)
def test_prepared_context_response_that_cannot_be_read_or_decoded_is_reported(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    handler: type[BaseHTTPRequestHandler],
    diagnostic: dict[str, object],
) -> None:
    with _serve(handler) as server_url:
        output, errors = _run_main(
            hook_module,
            monkeypatch,
            {"hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path), "prompt": "Recall context"},
            # Reading the oversized body byte by byte can take seconds on a busy runner; let it reach the size bound.
            settings=hook_module.ClaudeCodePluginSettings(
                server_url=server_url,
                capture_prompts=False,
                request_timeout_seconds=10.0,
                http_budget_seconds=10.0,
            ),
        )

    assert errors == ""
    assert json.loads(json.loads(output)["systemMessage"]) == {
        "component": "powercontext.claude_code.recall",
        "event": "context_prepare",
        **diagnostic,
    }


@pytest.mark.parametrize(
    ("handler", "outcome"),
    [
        pytest.param(
            _failure_handler(404, b'{"error":{"code":', truncated="chunked"),
            "server_unavailable",
            id="truncated-chunked-resolution-error-body",
        ),
        pytest.param(
            _failure_handler(404, b'{"error":{"code":', truncated="content-length"),
            "server_unavailable",
            id="truncated-content-length-resolution-error-body",
        ),
        pytest.param(
            _failure_handler(404, b"[" * 100_000), "version_mismatch", id="deeply-nested-resolution-error-body"
        ),
        pytest.param(_failure_handler(404, b"1" * 5_000), "version_mismatch", id="long-number-resolution-error-body"),
        pytest.param(_failure_handler(200, b"[" * 100_000), "invalid_response", id="deeply-nested-resolution-body"),
        pytest.param(_failure_handler(200, b"1" * 5_000), "invalid_response", id="long-number-resolution-body"),
        pytest.param(
            _failure_handler(404, b'{"error":{"code":', path=_BOOTSTRAP_PATH, truncated="chunked"),
            "server_unavailable",
            id="truncated-chunked-bootstrap-error-body",
        ),
        pytest.param(
            _failure_handler(404, b'{"error":{"code":', path=_BOOTSTRAP_PATH, truncated="content-length"),
            "server_unavailable",
            id="truncated-content-length-bootstrap-error-body",
        ),
        pytest.param(
            _failure_handler(200, b'{"schema":', path=_BOOTSTRAP_PATH, truncated="chunked"),
            "server_unavailable",
            id="truncated-chunked-bootstrap-body",
        ),
        pytest.param(
            _failure_handler(200, b'{"schema":', path=_BOOTSTRAP_PATH, truncated="content-length"),
            "server_unavailable",
            id="truncated-content-length-bootstrap-body",
        ),
        pytest.param(
            _failure_handler(200, b"[" * 100_000, path=_BOOTSTRAP_PATH),
            "invalid_response",
            id="deeply-nested-bootstrap-body",
        ),
        pytest.param(
            _failure_handler(200, b"1" * 5_000, path=_BOOTSTRAP_PATH),
            "invalid_response",
            id="long-number-bootstrap-body",
        ),
    ],
)
def test_session_start_clears_the_kept_receipt_when_a_response_cannot_be_read_or_decoded(
    session_start_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    handler: type[BaseHTTPRequestHandler],
    outcome: str,
) -> None:
    session_start_module.save_receipt("session-a", "scope:test", "bcr_previous")
    with _serve(handler) as server_url:
        _, errors = _run_main(
            session_start_module,
            monkeypatch,
            {"hook_event_name": "SessionStart", "session_id": "session-a", "cwd": str(tmp_path), "source": "compact"},
            settings=session_start_module.ClaudeCodePluginSettings(server_url=server_url),
        )

    assert json.loads(errors)["outcome"] == outcome
    assert session_start_module.load_receipt_state("session-a") is None


@pytest.mark.parametrize(
    ("body", "diagnostic"),
    [
        pytest.param(
            b'{"detail":"Not Found"}', {"outcome": "version_mismatch", "http_status": 404}, id="missing-route"
        ),
        pytest.param(
            b'{"error":{"code":"scope_not_found","message":"no such Scope"}}',
            {"outcome": "invalid_response", "http_status": 404, "error_code": "scope_not_found"},
            id="unknown-scope",
        ),
    ],
)
@pytest.mark.parametrize("path", [_RESOLUTION_PATH, _BOOTSTRAP_PATH], ids=["resolution", "bootstrap"])
def test_session_start_classifies_a_404_by_its_error_code(
    session_start_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: str,
    body: bytes,
    diagnostic: dict[str, object],
) -> None:
    with _serve(_failure_handler(404, body, path=path)) as server_url:
        _, errors = _run_main(
            session_start_module,
            monkeypatch,
            {"hook_event_name": "SessionStart", "session_id": "session-a", "cwd": str(tmp_path), "source": "startup"},
            settings=session_start_module.ClaudeCodePluginSettings(server_url=server_url),
        )

    assert json.loads(errors) == {
        "component": "powercontext.claude_code.bootstrap",
        "event": "context_bootstrap",
        "profile": "powercontext.scope-bootstrap.v1",
        **diagnostic,
    }


def test_failed_session_start_does_not_hold_back_the_next_prompt_diagnostic(
    hook_module: ModuleType,
    session_start_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unavailable = b'{"error":{"code":"server_unavailable","message":"unavailable"}}'
    with _serve(_failure_handler(503, unavailable)) as server_url:
        settings = hook_module.ClaudeCodePluginSettings(server_url=server_url)
        _, session_errors = _run_main(
            session_start_module,
            monkeypatch,
            {"hook_event_name": "SessionStart", "session_id": "session-a", "cwd": str(tmp_path), "source": "startup"},
            settings=settings,
        )
        output, _ = _run_main(
            hook_module,
            monkeypatch,
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "session-a",
                "cwd": str(tmp_path),
                "prompt": "Recall context",
            },
            settings=settings,
        )

    assert json.loads(session_errors)["outcome"] == "server_unavailable"
    assert json.loads(json.loads(output)["systemMessage"])["outcome"] == "server_unavailable"


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
        "resolve_scope_id",
        lambda _cwd, **_kwargs: "project:test",
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
        "resolve_scope_id",
        lambda _cwd, **_kwargs: "project:test",
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
    if outcome == "server_unavailable":
        assert diagnostic["recovery"] == "powercontext doctor"
    assert "secret" not in errors.getvalue()


@pytest.mark.parametrize(
    ("status", "code"),
    [(404, "invalid_request"), (409, "scope_conflict"), (422, "invalid_request")],
)
def test_context_prepare_domain_errors_remain_visible(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    code: str,
) -> None:
    monkeypatch.setattr(
        hook_module,
        "_prepare_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hook_module._HttpStatusError(status, "/v1/context/prepare", code)
        ),
    )
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
    assert json.loads(errors.getvalue()) == {
        "component": "powercontext.claude_code.recall",
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
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    code: str,
) -> None:
    monkeypatch.setattr(hook_module, "_prepare_context", lambda *_args, **_kwargs: _prepared("prepared context"))
    monkeypatch.setattr(hook_module, "resolve_scope_id", lambda *_args, **_kwargs: "project:test")
    monkeypatch.setattr(
        hook_module,
        "_capture_prompt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hook_module._HttpStatusError(status, "/v1/sources/content", code)
        ),
    )

    output, errors = _run_main(
        hook_module,
        monkeypatch,
        {
            "hook_event_name": "UserPromptSubmit",
            "cwd": "/workspace/project",
            "prompt": "Recall despite a domain error",
        },
    )

    result = json.loads(output)
    assert result["hookSpecificOutput"]["additionalContext"] == "prepared context"
    assert json.loads(result["systemMessage"]) == {
        "component": "powercontext.claude_code.recall",
        "event": "capture_source",
        "outcome": "invalid_response",
        "http_status": status,
        "error_code": code,
    }
    assert errors == ""


def test_flush_domain_error_remains_visible_as_an_automatic_failure(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook_module, "_prepare_context", lambda *_args, **_kwargs: _prepared("prepared context"))
    monkeypatch.setattr(hook_module, "resolve_scope_id", lambda *_args, **_kwargs: "project:test")
    monkeypatch.setattr(hook_module, "_capture_prompt", lambda *_args, **_kwargs: {"position": 1})
    monkeypatch.setattr(
        hook_module,
        "_flush_through",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hook_module._HttpStatusError(422, "/v1/memory/flush", "invalid_request")
        ),
    )

    output, errors = _run_main(
        hook_module,
        monkeypatch,
        {
            "hook_event_name": "UserPromptSubmit",
            "cwd": "/workspace/project",
            "prompt": "Recall before flushing",
        },
        settings=hook_module.ClaudeCodePluginSettings(
            server_url="http://127.0.0.1:8000",
            flush_on_capture=True,
        ),
    )

    result = json.loads(output)
    assert json.loads(result["systemMessage"]) == {
        "component": "powercontext.claude_code.recall",
        "event": "flush_memory",
        "outcome": "invalid_response",
        "http_status": 422,
        "error_code": "invalid_request",
    }
    assert errors == ""


def test_unknown_schema_is_not_injected(
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
    assert json.loads(errors.getvalue())["outcome"] == "invalid_response"
    assert "secret" not in errors.getvalue()


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


def test_http_error_preserves_structured_error_code(hook_module: ModuleType) -> None:
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
        settings = hook_module.ClaudeCodePluginSettings(server_url=server_url)
        with pytest.raises(hook_module._HttpStatusError) as caught:
            hook_module._post_json(
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
    hook_module: ModuleType,
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
        settings = hook_module.ClaudeCodePluginSettings(
            server_url=server_url,
            request_timeout_seconds=1.0,
            http_budget_seconds=0.1,
        )
        with pytest.raises(hook_module._ServerUnavailableError):
            hook_module._post_json(
                "/v1/context/prepare",
                {},
                settings=settings,
                deadline=started + 0.1,
                expected_status=200,
            )
        elapsed = time.monotonic() - started

    assert elapsed < 0.5
