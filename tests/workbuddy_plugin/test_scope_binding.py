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
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCOPE_BODY = json.dumps({"scope_id": "scope-1"}).encode()
_DRIP_INTERVAL_SECONDS = 0.05
_DRIP_WRITES = 60
_DRIP_HEADER_LINES = 60
_LARGE_PAYLOAD_BYTES = 1_000_000


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


def _settings(scope_module: ModuleType, server_url: str, **overrides: Any) -> Any:
    return scope_module.WorkBuddyPluginSettings(server_url=server_url, **overrides)


def _drain_request(request: BaseHTTPRequestHandler) -> None:
    request.rfile.read(int(request.headers.get("Content-Length", "0")))


def test_response_body_drip_aborts_at_the_request_timeout(
    scope_module: ModuleType,
    tmp_path: Path,
) -> None:
    """A server that trickles one byte faster than the socket timeout must not outlive the deadline."""

    class DripBodyHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            _drain_request(self)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            for _ in range(_DRIP_WRITES):
                try:
                    self.wfile.write(b" ")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(_DRIP_INTERVAL_SECONDS)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(DripBodyHandler) as server_url:
        started = time.monotonic()
        with pytest.raises(scope_module.ScopeBindingError):
            scope_module.resolve_scope_id(
                str(tmp_path),
                session_id=None,
                settings=_settings(scope_module, server_url, request_timeout_seconds=0.3),
                deadline=time.monotonic() + 2.0,
            )
        elapsed = time.monotonic() - started

    assert elapsed < 1.0


def test_response_header_drip_aborts_at_the_request_timeout(
    scope_module: ModuleType,
    tmp_path: Path,
) -> None:
    """A server that trickles response headers inside the socket timeout must not outlive the deadline."""

    class DripHeaderHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            _drain_request(self)
            try:
                self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                self.wfile.flush()
                for index in range(_DRIP_HEADER_LINES):
                    self.wfile.write(f"X-Pad-{index:02d}: {'p' * 16}\r\n".encode())
                    self.wfile.flush()
                    time.sleep(_DRIP_INTERVAL_SECONDS)
                self.wfile.write(b"Content-Type: application/json\r\n")
                self.wfile.write(f"Content-Length: {len(_SCOPE_BODY)}\r\n".encode())
                self.wfile.write(b"\r\n")
                self.wfile.write(_SCOPE_BODY)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(DripHeaderHandler) as server_url:
        started = time.monotonic()
        with pytest.raises(scope_module.ScopeBindingError):
            scope_module.resolve_scope_id(
                str(tmp_path),
                session_id=None,
                settings=_settings(scope_module, server_url, request_timeout_seconds=0.3),
                deadline=time.monotonic() + 2.0,
            )
        elapsed = time.monotonic() - started

    assert elapsed < 1.0


def test_large_response_body_within_the_request_timeout_succeeds(
    scope_module: ModuleType,
    tmp_path: Path,
) -> None:
    """Bounding the deadline must not turn a large but healthy response into a failure."""

    payload = json.dumps({"scope_id": "scope-1", "pad": "x" * _LARGE_PAYLOAD_BYTES}).encode()

    class LargeBodyHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            _drain_request(self)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(LargeBodyHandler) as server_url:
        started = time.monotonic()
        scope_id = scope_module.resolve_scope_id(
            str(tmp_path),
            session_id=None,
            settings=_settings(scope_module, server_url, request_timeout_seconds=2.0),
            deadline=time.monotonic() + 2.0,
        )
        elapsed = time.monotonic() - started

    assert scope_id == "scope-1"
    assert elapsed < 2.0


def test_expired_deadline_fails_without_contacting_the_server(
    scope_module: ModuleType,
    tmp_path: Path,
) -> None:
    requests: list[str] = []

    class RecordingHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            _drain_request(self)
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_SCOPE_BODY)))
            self.end_headers()
            self.wfile.write(_SCOPE_BODY)
            self.wfile.flush()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(RecordingHandler) as server_url:
        started = time.monotonic()
        with pytest.raises(scope_module.ScopeBindingError):
            scope_module.resolve_scope_id(
                str(tmp_path),
                session_id=None,
                settings=_settings(scope_module, server_url, request_timeout_seconds=0.3),
                deadline=time.monotonic() - 1.0,
            )
        elapsed = time.monotonic() - started

    assert requests == []
    assert elapsed < 1.0
