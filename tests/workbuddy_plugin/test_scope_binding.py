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

import hashlib
import json
import os
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

from tests.git_worktree_support import ScopeBindings, check_worktree_fallback, make_git_layout

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


@pytest.mark.parametrize("operation", ["resolve", "bind", "clear"])
def test_workspace_discovery_obeys_the_operation_deadline(
    scope_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    run = subprocess.run

    def slow_git(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return run(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=kwargs["timeout"],
            check=True,
            capture_output=True,
            text=True,
        )

    git_worktree = sys.modules[scope_module.workspace_paths.__module__]
    monkeypatch.setattr(git_worktree, "which", lambda _name: sys.executable)
    monkeypatch.setattr(git_worktree.subprocess, "run", slow_git)
    settings = _settings(scope_module, "http://127.0.0.1:1")
    started = time.monotonic()
    deadline = started + 0.15
    with pytest.raises(scope_module.ScopeBindingError):
        if operation == "resolve":
            scope_module.resolve_scope_id(str(tmp_path), session_id=None, settings=settings, deadline=deadline)
        elif operation == "bind":
            scope_module.bind_scope(str(tmp_path), "scope-1", settings=settings, deadline=deadline)
        else:
            scope_module.clear_scope_binding(str(tmp_path), settings=settings, deadline=deadline)

    assert time.monotonic() - started < 0.8


def test_a_linked_worktree_resolves_to_its_checkouts_binding_until_it_has_its_own(
    scope_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    layout = make_git_layout(tmp_path, monkeypatch)
    bindings = ScopeBindings()

    def request(path, payload, *, settings, deadline, method="POST"):
        if path == "/v1/scope-bindings/resolve":
            return {"scope_id": bindings.resolve(payload["binding_keys"])}
        if path == "/v1/scope-bindings":
            bindings.set(payload["key"], payload["scope_id"])
            return {"scope_id": payload["scope_id"]}
        return {"cleared": bindings.clear(payload["key"])}

    monkeypatch.setattr(scope_module, "_request_json", request)
    options = {"settings": _settings(scope_module, "http://127.0.0.1:1"), "deadline": float("inf")}

    check_worktree_fallback(
        checkout=layout.checkout,
        worktree=layout.worktree_sub,
        bare_worktree=layout.bare_worktree,
        resolve=lambda cwd: scope_module.resolve_scope_id(str(cwd), session_id=None, **options),
        bind=lambda cwd, scope_id: scope_module.bind_scope(str(cwd), scope_id, **options),
        clear=lambda cwd: scope_module.clear_scope_binding(str(cwd), **options),
    )
    # The first workspace key is still the hash of the resolved Git top level, so existing bindings keep matching.
    keys = scope_module.binding_keys(str(layout.worktree_sub), session_id=None)
    assert [key["external_id"] for key in keys] == [
        hashlib.sha256(os.fsencode(layout.worktree)).hexdigest(),
        hashlib.sha256(os.fsencode(layout.checkout)).hexdigest(),
    ]
