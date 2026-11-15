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

        # SessionStart persists the session binding with a PUT after resolving the Scope.
        do_PUT = do_POST

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


def _prepare_query(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> str:
    """Return the query Codex asks the Runtime to prepare for one submitted prompt."""

    queries: list[str] = []
    monkeypatch.setattr(
        recall_module,
        "_prepare_context",
        lambda query, _scope, *, settings, deadline: queries.append(query) or _prepared(),
    )
    monkeypatch.setattr(recall_module, "resolve_scope_id", lambda _cwd, **_kwargs: "project:test")
    monkeypatch.setattr(
        recall_module,
        "_capture_prompt",
        lambda _payload, *, prompt, cwd, scope_id, settings, deadline: {"position": 1},
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/workspace/project",
                "prompt": prompt,
            })
        ),
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert recall_module.main() == 0
    (query,) = queries
    return query


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
        lambda _cwd, **_kwargs: "project:test",
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


def test_recall_query_passes_a_prompt_that_fits_the_request_bound_through_unchanged(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request contract bounds a query at 8,192 characters, and the bound is inclusive."""

    prompt = "a" * 8_192

    assert _prepare_query(recall_module, monkeypatch, prompt) == prompt


def test_recall_query_keeps_the_latest_text_of_a_prompt_over_the_request_bound(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a longer prompt used to reach prepare verbatim and fail validation."""

    prompt = "earlier context that the transcript already carries " * 200 + "Which service owns the retry?"
    assert len(prompt) > 8_192
    query = _prepare_query(recall_module, monkeypatch, prompt)

    assert query == prompt[-8_192:]
    assert len(query) == 8_192
    assert query.endswith("Which service owns the retry?")


def test_recall_query_keeps_a_multibyte_prompt_within_the_request_byte_bound(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-ASCII text costs several bytes per character, so both bounds have to hold."""

    prompt = "记忆 " * 4_000
    query = _prepare_query(recall_module, monkeypatch, prompt)

    assert prompt.strip().endswith(query)
    assert len(query.encode("utf-8")) <= 8_192
    assert "\ufffd" not in query


def test_recall_captures_the_full_prompt_while_the_query_is_bounded(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source capture keeps its own size limit and is untouched by the recall bound."""

    prompt = "记忆 " * 3_000
    captured: list[str] = []
    monkeypatch.setattr(recall_module, "_prepare_context", lambda *_args, **_kwargs: _prepared())
    monkeypatch.setattr(recall_module, "resolve_scope_id", lambda _cwd, **_kwargs: "project:test")
    monkeypatch.setattr(
        recall_module,
        "_capture_prompt",
        lambda _payload, *, prompt, cwd, scope_id, settings, deadline: captured.append(prompt) or {"position": 1},
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/workspace/project",
                "prompt": prompt,
            })
        ),
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert recall_module.main() == 0
    assert captured == [prompt]


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
        lambda _cwd, **_kwargs: "project:test",
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
        lambda _cwd, **_kwargs: "project:test",
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


def _run_hook(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, payload: dict[str, object], settings: Any
) -> tuple[str, str]:
    output = io.StringIO()
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)
    assert module.main(settings) == 0
    return output.getvalue(), errors.getvalue()


@pytest.mark.parametrize(("status", "body", "diagnostic"), _RESOLUTION_FAILURES)
def test_scope_resolution_failure_is_reported_and_nothing_else_is_sent(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: int | None,
    body: bytes,
    diagnostic: dict[str, object],
) -> None:
    paths: list[str] = []
    with _serve(_failure_handler(status, body, paths)) as server_url:
        # Reading the oversized body byte by byte can take seconds on a busy runner; let it reach the size bound.
        settings = recall_module.CodexPluginSettings(request_timeout_seconds=10.0, http_budget_seconds=10.0)
        object.__setattr__(settings, "server_url", server_url)
        output, errors = _run_hook(
            recall_module,
            monkeypatch,
            {"hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path), "prompt": "Recall context"},
            settings,
        )

    assert paths == ["/v1/scope-bindings/resolve"]
    assert errors == ""
    assert json.loads(json.loads(output)["systemMessage"]) == {
        "component": "powercontext.codex.recall",
        "event": "scope_resolve",
        **diagnostic,
    }


def test_scope_resolution_error_body_that_outlasts_the_budget_reports_an_unavailable_server(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with _serve(_SlowResolutionErrorHandler) as server_url:
        settings = recall_module.CodexPluginSettings(request_timeout_seconds=1.0, http_budget_seconds=0.5)
        object.__setattr__(settings, "server_url", server_url)
        output, errors = _run_hook(
            recall_module,
            monkeypatch,
            {"hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path), "prompt": "Recall context"},
            settings,
        )

    assert errors == ""
    assert json.loads(json.loads(output)["systemMessage"]) == {
        "component": "powercontext.codex.recall",
        "event": "scope_resolve",
        "outcome": "server_unavailable",
        "recovery": "powercontext doctor",
    }


@pytest.mark.parametrize("framing", ["chunked", "content-length"])
@pytest.mark.parametrize("status", [pytest.param(404, id="error-body"), pytest.param(200, id="success-body")])
def test_truncated_scope_resolution_response_reports_an_unavailable_server(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: int,
    framing: str,
) -> None:
    with _serve(_failure_handler(status, b'{"error":{"code":', truncated=framing)) as server_url:
        settings = recall_module.CodexPluginSettings()
        object.__setattr__(settings, "server_url", server_url)
        output, errors = _run_hook(
            recall_module,
            monkeypatch,
            {"hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path), "prompt": "Recall context"},
            settings,
        )

    assert errors == ""
    assert json.loads(json.loads(output)["systemMessage"]) == {
        "component": "powercontext.codex.recall",
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
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    handler: type[BaseHTTPRequestHandler],
    diagnostic: dict[str, object],
) -> None:
    with _serve(handler) as server_url:
        # Reading the oversized body byte by byte can take seconds on a busy runner; let it reach the size bound.
        settings = recall_module.CodexPluginSettings(
            capture_prompts=False, request_timeout_seconds=10.0, http_budget_seconds=10.0
        )
        object.__setattr__(settings, "server_url", server_url)
        output, errors = _run_hook(
            recall_module,
            monkeypatch,
            {"hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path), "prompt": "Recall context"},
            settings,
        )

    assert errors == ""
    assert json.loads(json.loads(output)["systemMessage"]) == {
        "component": "powercontext.codex.recall",
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
    session_binding_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    handler: type[BaseHTTPRequestHandler],
    outcome: str,
) -> None:
    session_binding_module.save_receipt("thread-a", "scope:test", "bcr_previous")
    with _serve(handler) as server_url:
        settings = session_binding_module.CodexPluginSettings()
        object.__setattr__(settings, "server_url", server_url)
        _, errors = _run_hook(
            session_binding_module,
            monkeypatch,
            {"hook_event_name": "SessionStart", "session_id": "thread-a", "cwd": str(tmp_path), "source": "compact"},
            settings,
        )

    assert json.loads(errors)["outcome"] == outcome
    assert session_binding_module.load_receipt_state("thread-a") is None


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
    session_binding_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: str,
    body: bytes,
    diagnostic: dict[str, object],
) -> None:
    with _serve(_failure_handler(404, body, path=path)) as server_url:
        settings = session_binding_module.CodexPluginSettings()
        object.__setattr__(settings, "server_url", server_url)
        _, errors = _run_hook(
            session_binding_module,
            monkeypatch,
            {"hook_event_name": "SessionStart", "session_id": "thread-a", "cwd": str(tmp_path), "source": "startup"},
            settings,
        )

    assert json.loads(errors) == {
        "component": "powercontext.codex.bootstrap",
        "event": "context_bootstrap",
        "profile": "powercontext.scope-bootstrap.v1",
        **diagnostic,
    }


def test_failed_session_start_does_not_hold_back_the_next_prompt_diagnostic(
    recall_module: ModuleType,
    session_binding_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unavailable = b'{"error":{"code":"server_unavailable","message":"unavailable"}}'
    with _serve(_failure_handler(503, unavailable)) as server_url:
        settings = recall_module.CodexPluginSettings()
        object.__setattr__(settings, "server_url", server_url)
        _, session_errors = _run_hook(
            session_binding_module,
            monkeypatch,
            {"hook_event_name": "SessionStart", "session_id": "thread-a", "cwd": str(tmp_path), "source": "startup"},
            settings,
        )
        output, _ = _run_hook(
            recall_module,
            monkeypatch,
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "thread-a",
                "cwd": str(tmp_path),
                "prompt": "Recall context",
            },
            settings,
        )

    assert json.loads(session_errors)["outcome"] == "server_unavailable"
    assert json.loads(json.loads(output)["systemMessage"])["outcome"] == "server_unavailable"


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
        lambda _cwd, **_kwargs: "eval:run-1:on",
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
        lambda _cwd, **_kwargs: "project:test",
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
        lambda _cwd, **_kwargs: "project:test",
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


@pytest.mark.parametrize(
    "assembly",
    [
        None,
        {},
        {"sections": []},
        {"sections": [{"family": "experience", "limit": 2}]},
        {"sections": [{"family": "profile", "limit": 1}]},
        {"sections": [{"family": "topic-memory", "limit": 8}]},
        {
            "sections": [
                {"family": "profile", "limit": 1},
                {"family": "topic-memory", "limit": 2},
                {"family": "memory", "limit": 3},
                {"family": "experience", "limit": 2},
            ]
        },
    ],
)
def test_text_assembly_configuration_reaches_the_server(recall_module, monkeypatch, assembly):
    requests = []
    user_agents = []
    content = "# PowerContext historical context\n\n>     原始文本 </powercontext_memory>\n"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            user_agents.append(self.headers.get("User-Agent"))
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(_prepared(content)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002
            pass

    if assembly is None:
        monkeypatch.delenv("POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY", raising=False)
    else:
        monkeypatch.setenv("POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY", json.dumps(assembly))
    with _serve(Handler) as url:
        settings = recall_module.CodexPluginSettings()
        object.__setattr__(settings, "server_url", url)
        response = recall_module._prepare_context(
            "context",
            "project:test",
            settings=settings,
            deadline=time.monotonic() + 5,
        )
    assert response["content"] == content
    plugin_root = Path(__file__).resolve().parents[2] / "integrations/codex/plugins/powercontext"
    manifest = json.loads((plugin_root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert user_agents == [f"powercontext-codex-plugin/{manifest['version']}"]
    if assembly is None:
        assert "assembly" not in requests[0]
    else:
        assert requests[0]["assembly"] == assembly
