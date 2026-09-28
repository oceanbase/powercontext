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
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "integrations" / "codex" / "plugins" / "powercontext"


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


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _settings(module: ModuleType, server_url: str, **overrides: object) -> Any:
    settings = module.CodexPluginSettings(**overrides)
    object.__setattr__(settings, "server_url", server_url)
    return settings


def _run_main(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
    payload: str | None = None,
) -> io.StringIO:
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            payload
            if payload is not None
            else json.dumps({
                "hook_event_name": "Stop",
                "cwd": "/workspace/project",
                "session_id": "session-1",
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    assert module.main(settings) == 0
    return output


def _diagnostics(output: io.StringIO) -> list[dict[str, object]]:
    value = json.loads(output.getvalue())
    return [json.loads(line) for line in value["systemMessage"].splitlines()]


def test_formats_two_windows_with_honest_signed_wording(token_savings_module: ModuleType) -> None:
    assert token_savings_module.format_message(_stats(1_200), _stats(-250)) == (
        "PowerContext · saved 1.2k today · cost 250 in 30d"
    )
    assert token_savings_module.format_message(_stats(0), _stats(0)) == (
        "PowerContext · saved 0 today · saved 0 in 30d"
    )
    assert token_savings_module.compact_tokens(12_500) == "13k"
    assert token_savings_module.compact_tokens(1_250) == "1.3k"


def test_stop_reports_savings_from_the_contract_stats_endpoint(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
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

    with _serve(Handler) as server_url:
        output = _run_main(token_savings_module, monkeypatch, _settings(token_savings_module, server_url))

    assert json.loads(output.getvalue()) == {
        "continue": True,
        "systemMessage": "PowerContext · saved 1.2k today · cost 250 in 30d",
    }
    stats = [request for request in requests if request["path"] == "/v1/stats"]
    assert len(stats) == 2
    for request in stats:
        assert request["body"]["selection"] == {"mode": "exact", "scope_ids": ["project:test"]}
    assert {request["body"]["period"] for request in stats} == {"today", "30d"}


def test_transport_failure_reports_server_unavailable(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _run_main(
        token_savings_module, monkeypatch, _settings(token_savings_module, f"http://127.0.0.1:{_closed_port()}")
    )

    events = _diagnostics(output)
    assert len(events) == 1
    assert events[0] == {
        "component": "powercontext.codex.token_savings",
        "event": "status",
        "outcome": "server_unavailable",
        "recovery": "powercontext doctor",
    }


def test_http_401_reports_authentication_failed_without_request_details(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if self.path == "/v1/scope-bindings/resolve":
                body = json.dumps({"scope_id": "project:test"}).encode()
                self.send_response(200)
            else:
                body = json.dumps({
                    "error": {"code": "unauthorized", "message": "bad credential /workspace/project"}
                }).encode()
                self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(Handler) as server_url:
        output = _run_main(token_savings_module, monkeypatch, _settings(token_savings_module, server_url))

    events = _diagnostics(output)
    assert len(events) == 1
    assert events[0]["outcome"] == "authentication_failed"
    assert events[0]["http_status"] == 401
    serialized = json.dumps(events)
    assert "http://" not in serialized
    assert "/workspace/project" not in serialized
    assert "bad credential" not in serialized


def test_http_503_reports_server_unavailable_with_status(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if self.path == "/v1/scope-bindings/resolve":
                body = json.dumps({"scope_id": "project:test"}).encode()
                status = 200
            else:
                body = json.dumps({"error": {"code": "overloaded"}}).encode()
                status = 503
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(Handler) as server_url:
        output = _run_main(token_savings_module, monkeypatch, _settings(token_savings_module, server_url))

    events = _diagnostics(output)
    assert events == [
        {
            "component": "powercontext.codex.token_savings",
            "event": "status",
            "outcome": "server_unavailable",
            "http_status": 503,
            "error_code": "overloaded",
            "recovery": "powercontext doctor",
        }
    ]


def test_malformed_stats_response_reports_invalid_response(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if self.path == "/v1/scope-bindings/resolve":
                body = json.dumps({"scope_id": "project:test"}).encode()
            else:
                body = b"not-json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(Handler) as server_url:
        output = _run_main(token_savings_module, monkeypatch, _settings(token_savings_module, server_url))

    events = _diagnostics(output)
    assert events == [
        {
            "component": "powercontext.codex.token_savings",
            "event": "status",
            "outcome": "invalid_response",
        }
    ]


def test_diagnostics_are_throttled_across_hook_invocations(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(token_savings_module, f"http://127.0.0.1:{_closed_port()}")

    first = _run_main(token_savings_module, monkeypatch, settings)
    second = _run_main(token_savings_module, monkeypatch, settings)

    assert len(_diagnostics(first)) == 1
    assert second.getvalue() == ""


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


def test_legitimate_empty_stats_stay_a_normal_message(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _serve(_stats_handler(_stats(0))) as server_url:
        output = _run_main(token_savings_module, monkeypatch, _settings(token_savings_module, server_url))

    assert json.loads(output.getvalue()) == {
        "continue": True,
        "systemMessage": "PowerContext · saved 0 today · saved 0 in 30d",
    }


@pytest.mark.parametrize(
    "payload",
    [{}, {"recall": {}}, {"recall": {"totals": {}}}],
)
def test_stats_response_without_required_totals_reports_invalid_response(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    with _serve(_stats_handler(payload)) as server_url:
        output = _run_main(token_savings_module, monkeypatch, _settings(token_savings_module, server_url))

    events = _diagnostics(output)
    assert events == [
        {
            "component": "powercontext.codex.token_savings",
            "event": "status",
            "outcome": "invalid_response",
        }
    ]


def test_two_stat_windows_share_one_absolute_budget(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    periods: list[str] = []

    class SlowDripHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            if self.path == "/v1/scope-bindings/resolve":
                body = json.dumps({"scope_id": "project:test"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
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

    with _serve(SlowDripHandler) as server_url:
        settings = _settings(
            token_savings_module,
            server_url,
            request_timeout_seconds=5.0,
            http_budget_seconds=0.2,
        )
        started = time.monotonic()
        output = _run_main(token_savings_module, monkeypatch, settings)
        elapsed = time.monotonic() - started

    assert elapsed < 1.0
    # The first window consumes the shared deadline, so the second window must
    # never be requested; per-window budgets would send both.
    assert periods == ["today"]
    events = _diagnostics(output)
    assert [event["outcome"] for event in events] == ["server_unavailable"]
    assert events[0]["recovery"] == "powercontext doctor"


def test_chunked_drip_respects_the_absolute_budget(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
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

    with _serve(ChunkedDripHandler) as server_url:
        settings = _settings(
            token_savings_module,
            server_url,
            request_timeout_seconds=5.0,
            http_budget_seconds=0.2,
        )
        started = time.monotonic()
        output = _run_main(token_savings_module, monkeypatch, settings)
        elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert periods == ["today"]
    events = _diagnostics(output)
    assert [event["outcome"] for event in events] == ["server_unavailable"]
    assert events[0]["recovery"] == "powercontext doctor"


def test_chunked_http_error_body_respects_the_absolute_budget(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    periods: list[str] = []

    class ChunkedErrorHandler(BaseHTTPRequestHandler):
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
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            # HTTPError wraps the response and must still use the same deadline.
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

    with _serve(ChunkedErrorHandler) as server_url:
        settings = _settings(
            token_savings_module,
            server_url,
            request_timeout_seconds=5.0,
            http_budget_seconds=0.2,
        )
        started = time.monotonic()
        output = _run_main(token_savings_module, monkeypatch, settings)
        elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert periods == ["today"]
    events = _diagnostics(output)
    assert [event["outcome"] for event in events] == ["server_unavailable"]
    assert events[0]["recovery"] == "powercontext doctor"


def test_non_stop_payloads_stay_silent(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _run_main(
        token_savings_module,
        monkeypatch,
        _settings(token_savings_module, "http://127.0.0.1:9"),
        payload=json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": "/workspace/project"}),
    )

    assert output.getvalue() == ""


def test_slow_scope_response_respects_the_absolute_budget(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowScopeHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
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

    with _serve(SlowScopeHandler) as server_url:
        settings = _settings(
            token_savings_module,
            server_url,
            request_timeout_seconds=5.0,
            http_budget_seconds=0.2,
        )
        started = time.monotonic()
        output = _run_main(token_savings_module, monkeypatch, settings)
        elapsed = time.monotonic() - started

    assert elapsed < 1.0
    events = _diagnostics(output)
    assert [event["outcome"] for event in events] == ["server_unavailable"]
    assert events[0]["recovery"] == "powercontext doctor"


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (401, "authentication_failed"),
        (404, "version_mismatch"),
        (500, "invalid_response"),
        (503, "server_unavailable"),
    ],
)
def test_scope_binding_failures_are_classified(
    token_savings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    outcome: str,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            assert self.path == "/v1/scope-bindings/resolve"
            body = json.dumps({"error": {"code": "failure"}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(Handler) as server_url:
        output = _run_main(token_savings_module, monkeypatch, _settings(token_savings_module, server_url))

    events = _diagnostics(output)
    assert [event["outcome"] for event in events] == [outcome]


def test_scope_response_body_respects_the_per_request_timeout(
    scope_module: ModuleType,
    tmp_path: Path,
) -> None:
    class SlowBodyHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
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

    with _serve(SlowBodyHandler) as server_url:
        settings = _settings(scope_module, server_url, request_timeout_seconds=0.3)
        started = time.monotonic()
        with pytest.raises(scope_module.ScopeBindingUnavailableError):
            scope_module.resolve_scope_id(
                str(tmp_path),
                session_id=None,
                settings=settings,
                deadline=time.monotonic() + 2.0,
            )
        elapsed = time.monotonic() - started

    assert elapsed < 1.0


def test_stop_budget_stays_below_the_host_deadline(token_savings_module: ModuleType) -> None:
    configuration = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    host_timeout = configuration["hooks"]["Stop"][0]["hooks"][0]["timeout"]

    assert token_savings_module.stop_http_budget(float(host_timeout)) == host_timeout - 2.0
    assert token_savings_module.stop_http_budget(4.0) < host_timeout
    assert token_savings_module.stop_http_budget(0.2) == 0.2


def test_stop_process_exits_within_the_host_deadline_against_a_slow_server(tmp_path: Path) -> None:
    configuration = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    declared = configuration["hooks"]["Stop"][0]["hooks"][0]
    host_timeout = declared["timeout"]
    assert "hooks/token_savings.py" in declared["command"]

    class SlowScopeHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
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
                time.sleep(0.15)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_", "CLAUDE_PLUGIN_OPTION_"))
    }
    env.update(
        CODEX_HOME=str(tmp_path / "codex"),
        POWERCONTEXT_CLIENT_CONFIG_FILE=str(tmp_path / "absent.json"),
        POWERCONTEXT_DIAGNOSTIC_STATE_FILE=str(tmp_path / "diagnostics.json"),
    )
    runner = tmp_path / "run_stop.py"
    with _serve(SlowScopeHandler) as server_url:
        config = tmp_path / "mcp.json"
        config.write_text(
            json.dumps({
                "mcpServers": {
                    "powercontext": {
                        "type": "http",
                        "required": False,
                        "env_http_headers": {"Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"},
                        "url": f"{server_url}/mcp",
                    }
                }
            })
        )
        runner.write_text(
            "\n".join((
                "import sys",
                "from pathlib import Path",
                f"root = Path({str(PLUGIN_ROOT)!r})",
                "sys.path[:0] = [str(root), str(root / 'scripts')]",
                "import settings",
                f"settings._MCP_CONFIGURATION_PATH = Path({str(config)!r})",
                "from hooks.token_savings import main",
                "raise SystemExit(main())",
            ))
        )
        payload = json.dumps({"hook_event_name": "Stop", "cwd": str(tmp_path), "session_id": "review"})
        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, str(runner)],
            input=payload,
            text=True,
            capture_output=True,
            env=env,
            timeout=host_timeout,
        )
        elapsed = time.monotonic() - started

    assert elapsed < host_timeout
    assert completed.returncode == 0
    events = _diagnostics(io.StringIO(completed.stdout))
    assert [event["outcome"] for event in events] == ["server_unavailable"]
