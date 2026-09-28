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

import json
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _stats(reduction: int) -> dict[str, object]:
    return {
        "recall": {
            "totals": {
                "preparations": 5,
                "ready_preparations": 4,
                "comparable_preparations": 3,
                "baseline_tokens": 3_000,
                "recalled_tokens": 1_800,
                "token_reduction": reduction,
            }
        }
    }


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


def test_formats_two_windows_with_honest_signed_wording(statusline_module: ModuleType) -> None:
    assert statusline_module.format_statusline(_stats(1_200), _stats(-250), color=False) == (
        "● PC online · saved 1.2k today · cost 250 in 30d"
    )
    assert statusline_module.format_statusline({}, {}, color=False) == ("● PC online · no data today · no data in 30d")
    assert statusline_module.compact_tokens(12_500) == "13k"
    assert statusline_module.compact_tokens(1_250) == "1.3k"


def test_render_uses_claude_workspace_and_both_periods(
    statusline_module: ModuleType,
    monkeypatch,
    tmp_path: Path,
) -> None:
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            requests.append({"path": self.path, "body": body})
            if self.path == "/v1/scope-bindings/resolve":
                self._respond({"scope_id": "project:test"})
                return
            assert self.path == "/v1/stats"
            self._respond(_stats(1_200 if body["period"] == "today" else -250))

        def _respond(self, payload: object) -> None:
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    monkeypatch.setattr(
        statusline_module.sys,
        "stdin",
        StringIO(json.dumps({"workspace": {"current_dir": str(tmp_path)}})),
    )
    with _serve(Handler) as server_url:
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_SERVER_URL", server_url)
        rendered = statusline_module.render("http://127.0.0.1:9000")

    assert "PC online" in rendered
    assert "saved 1.2k today" in rendered
    assert "cost 250 in 30d" in rendered
    resolve = [request for request in requests if request["path"] == "/v1/scope-bindings/resolve"]
    assert len(resolve) == 1
    assert resolve[0]["body"]["binding_keys"][-1]["kind"] == "workspace"
    stats = [request for request in requests if request["path"] == "/v1/stats"]
    assert len(stats) == 2
    for request in stats:
        assert request["body"]["selection"] == {"mode": "exact", "scope_ids": ["project:test"]}
    assert {request["body"]["period"] for request in stats} == {"today", "30d"}


def test_render_aborts_a_slow_drip_at_the_absolute_budget(
    statusline_module: ModuleType,
    monkeypatch,
    tmp_path: Path,
) -> None:
    periods: list[str] = []

    class SlowDripHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            if self.path == "/v1/scope-bindings/resolve":
                payload = json.dumps({"scope_id": "project:test"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            periods.append(body["period"])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            for _ in range(200):
                try:
                    self.wfile.write(b"x")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(0.05)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    monkeypatch.setattr(
        statusline_module.sys,
        "stdin",
        StringIO(json.dumps({"workspace": {"current_dir": str(tmp_path)}})),
    )
    monkeypatch.setattr(statusline_module, "resolve_scope_id", lambda *_args, **_kwargs: "project:test")
    with _serve(SlowDripHandler) as server_url:
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_SERVER_URL", server_url)
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_REQUEST_TIMEOUT_SECONDS", "5.0")
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_HTTP_BUDGET_SECONDS", "0.2")
        started = time.monotonic()
        rendered = statusline_module.render("http://127.0.0.1:9000")
        elapsed = time.monotonic() - started

    assert elapsed < 1.0
    # The first window consumes the shared budget, so the second window must
    # never be requested; per-call budgets would send both.
    assert periods == ["today"]
    assert "PC offline" in rendered


def test_render_aborts_a_slow_chunked_response_at_the_absolute_budget(
    statusline_module: ModuleType,
    monkeypatch,
    tmp_path: Path,
) -> None:
    periods: list[str] = []

    class ChunkedDripHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            if self.path == "/v1/scope-bindings/resolve":
                payload = json.dumps({"scope_id": "project:test"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            periods.append(body["period"])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            # One chunk-extension byte per interval keeps every socket read
            # inside its own timeout; only the absolute deadline can stop it.
            self.wfile.write(b"1")
            self.wfile.flush()
            for _ in range(200):
                try:
                    self.wfile.write(b";")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(0.05)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    monkeypatch.setattr(
        statusline_module.sys,
        "stdin",
        StringIO(json.dumps({"workspace": {"current_dir": str(tmp_path)}})),
    )
    monkeypatch.setattr(statusline_module, "resolve_scope_id", lambda *_args, **_kwargs: "project:test")
    with _serve(ChunkedDripHandler) as server_url:
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_SERVER_URL", server_url)
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_REQUEST_TIMEOUT_SECONDS", "5.0")
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_HTTP_BUDGET_SECONDS", "0.2")
        started = time.monotonic()
        rendered = statusline_module.render("http://127.0.0.1:9000")
        elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert periods == ["today"]
    assert "PC offline" in rendered


def test_render_fails_open_without_writing_diagnostics(statusline_module: ModuleType, monkeypatch) -> None:
    monkeypatch.setattr(statusline_module.sys, "stdin", StringIO("not-json"))

    assert "PC invalid response" in statusline_module.render("http://127.0.0.1:8000")


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "PC auth failed"),
        (404, "PC version mismatch"),
        (503, "PC offline · run powercontext doctor"),
        (500, "PC invalid response"),
    ],
)
def test_render_classifies_scope_failures(
    statusline_module: ModuleType,
    monkeypatch,
    tmp_path: Path,
    status: int,
    expected: str,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = json.dumps({"error": {"code": "failure"}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    monkeypatch.setattr(
        statusline_module.sys,
        "stdin",
        StringIO(json.dumps({"workspace": {"current_dir": str(tmp_path)}})),
    )
    with _serve(Handler) as server_url:
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_SERVER_URL", server_url)
        rendered = statusline_module.render("http://127.0.0.1:9000")

    assert expected in rendered


def test_render_classifies_a_malformed_scope_response(
    statusline_module: ModuleType,
    monkeypatch,
    tmp_path: Path,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = b"not-json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    monkeypatch.setattr(
        statusline_module.sys,
        "stdin",
        StringIO(json.dumps({"workspace": {"current_dir": str(tmp_path)}})),
    )
    with _serve(Handler) as server_url:
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_SERVER_URL", server_url)
        rendered = statusline_module.render("http://127.0.0.1:9000")

    assert "PC invalid response" in rendered


def _stats_handler(payload: object) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if self.path == "/v1/scope-bindings/resolve":
                self._respond(json.dumps({"scope_id": "project:test"}).encode())
                return
            assert self.path == "/v1/stats"
            self._respond(json.dumps(payload).encode())

        def _respond(self, data: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    return Handler


def test_render_keeps_legitimate_zero_stats_online(
    statusline_module: ModuleType,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        statusline_module.sys,
        "stdin",
        StringIO(json.dumps({"workspace": {"current_dir": str(tmp_path)}})),
    )
    with _serve(_stats_handler(_stats(0))) as server_url:
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_SERVER_URL", server_url)
        rendered = statusline_module.render("http://127.0.0.1:9000")

    assert "PC online" in rendered
    assert "saved 0 today" in rendered


@pytest.mark.parametrize(
    "payload",
    [{}, {"recall": {}}, {"recall": {"totals": {}}}],
)
def test_render_reports_stats_without_required_totals_as_invalid_response(
    statusline_module: ModuleType,
    monkeypatch,
    tmp_path: Path,
    payload: object,
) -> None:
    monkeypatch.setattr(
        statusline_module.sys,
        "stdin",
        StringIO(json.dumps({"workspace": {"current_dir": str(tmp_path)}})),
    )
    with _serve(_stats_handler(payload)) as server_url:
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_SERVER_URL", server_url)
        rendered = statusline_module.render("http://127.0.0.1:9000")

    assert "PC invalid response" in rendered


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "PC auth failed"),
        (503, "PC offline · run powercontext doctor"),
        (500, "PC invalid response"),
    ],
)
def test_render_classifies_stats_http_failures(
    statusline_module: ModuleType,
    monkeypatch,
    tmp_path: Path,
    status: int,
    expected: str,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if self.path == "/v1/scope-bindings/resolve":
                self._respond(200, {"scope_id": "project:test"})
                return
            assert self.path == "/v1/stats"
            self._respond(status, {"error": {"code": "failure"}})

        def _respond(self, code: int, payload: object) -> None:
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    monkeypatch.setattr(
        statusline_module.sys,
        "stdin",
        StringIO(json.dumps({"workspace": {"current_dir": str(tmp_path)}})),
    )
    with _serve(Handler) as server_url:
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_SERVER_URL", server_url)
        rendered = statusline_module.render("http://127.0.0.1:9000")

    assert expected in rendered


def test_render_aborts_a_slow_scope_response_at_the_absolute_budget(
    statusline_module: ModuleType,
    monkeypatch,
    tmp_path: Path,
) -> None:
    stats_requests = 0

    class SlowScopeHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal stats_requests
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if self.path == "/v1/stats":
                stats_requests += 1
                body = json.dumps(_stats(0)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            assert self.path == "/v1/scope-bindings/resolve"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            for _ in range(200):
                try:
                    self.wfile.write(b" ")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(0.05)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    monkeypatch.setattr(
        statusline_module.sys,
        "stdin",
        StringIO(json.dumps({"workspace": {"current_dir": str(tmp_path)}})),
    )
    with _serve(SlowScopeHandler) as server_url:
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_SERVER_URL", server_url)
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_REQUEST_TIMEOUT_SECONDS", "5.0")
        monkeypatch.setenv("POWERCONTEXT_CLAUDE_HTTP_BUDGET_SECONDS", "0.2")
        started = time.monotonic()
        rendered = statusline_module.render("http://127.0.0.1:9000")
        elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert stats_requests == 0
    assert "PC offline" in rendered


def _write_credentials(root: Path, *, server_url: str, authorization: str) -> None:
    credential = root / "powercontext" / "credentials.json"
    credential.parent.mkdir()
    credential.write_text(
        json.dumps({"version": 1, "server_url": server_url, "authorization": authorization}),
        encoding="utf-8",
    )
    credential.chmod(0o600)


def _isolate_authorization(
    monkeypatch,
    tmp_path: Path,
    *,
    configured_url: str | None,
) -> None:
    monkeypatch.delenv("POWERCONTEXT_CLAUDE_SERVER_URL", raising=False)
    monkeypatch.delenv("POWERCONTEXT_CLAUDE_AUTHORIZATION", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_SERVER_URL", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    if configured_url is None:
        monkeypatch.delenv("POWERCONTEXT_CLIENT_SERVER_URL", raising=False)
    else:
        monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", configured_url)


def _authorization_recorder() -> tuple[list[tuple[str, str | None]], type[BaseHTTPRequestHandler]]:
    received: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            received.append((self.path, self.headers.get("Authorization")))
            if self.path == "/v1/scope-bindings/resolve":
                self._respond({"scope_id": "project:test"})
                return
            self._respond(_stats(0))

        def _respond(self, payload: object) -> None:
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    return received, Handler


def test_render_binds_persisted_authorization_to_the_host_configured_server(
    statusline_module: ModuleType,
    monkeypatch,
    tmp_path: Path,
) -> None:
    received, handler = _authorization_recorder()
    monkeypatch.setattr(
        statusline_module.sys,
        "stdin",
        StringIO(json.dumps({"workspace": {"current_dir": str(tmp_path)}})),
    )
    _isolate_authorization(monkeypatch, tmp_path, configured_url=None)
    with _serve(handler) as server_url:
        _write_credentials(tmp_path, server_url=server_url, authorization="Bearer host-token")
        rendered = statusline_module.render(server_url)

    assert "PC online" in rendered
    assert received
    assert {authorization for _, authorization in received} == {"Bearer host-token"}


def test_render_does_not_pair_another_servers_authorization_with_the_host_server(
    statusline_module: ModuleType,
    monkeypatch,
    tmp_path: Path,
) -> None:
    received, handler = _authorization_recorder()
    monkeypatch.setattr(
        statusline_module.sys,
        "stdin",
        StringIO(json.dumps({"workspace": {"current_dir": str(tmp_path)}})),
    )
    _isolate_authorization(monkeypatch, tmp_path, configured_url=None)
    with _serve(handler) as server_url:
        _write_credentials(tmp_path, server_url="http://127.0.0.1:8000", authorization="Bearer loopback-token")
        rendered = statusline_module.render(server_url)

    assert "PC online" in rendered
    assert received
    assert {authorization for _, authorization in received} == {None}
