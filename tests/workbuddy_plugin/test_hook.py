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
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCOPE_BODY = json.dumps({"scope_id": "scope-1"}).encode()
_DRIP_INTERVAL_SECONDS = 0.05
_DRIP_WRITES = 60


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


def test_prompt_hook_fails_open_within_the_http_budget_when_recall_reads_drip(
    hook_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The recall/capture reads must stay inside the hook's own HTTP budget, not the host hook timeout."""

    paths: list[str] = []

    class DripRecallHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if self.path == "/v1/scope-bindings/resolve":
                self.send_header("Content-Length", str(len(_SCOPE_BODY)))
                self.end_headers()
                self.wfile.write(_SCOPE_BODY)
                self.wfile.flush()
                return
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

    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "prompt": "what did we decide about the recall budget?",
                "cwd": str(tmp_path),
                "session_id": "session-1",
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    with _serve(DripRecallHandler) as server_url:
        settings = hook_module.WorkBuddyPluginSettings(
            server_url=server_url,
            request_timeout_seconds=0.3,
            http_budget_seconds=1.0,
        )
        started = time.monotonic()
        assert hook_module.main(settings) == 0
        elapsed = time.monotonic() - started

    assert elapsed < 1.5
    assert "/v1/scope-bindings/resolve" in paths
    assert "/v1/context/prepare" in paths
    assert json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"] == ""


def test_prompt_hook_refuses_redirects(
    hook_module: ModuleType,
) -> None:
    """Sharing the bounded opener must not let a redirect carry the hook's credentials elsewhere."""

    target_headers: list[dict[str, str]] = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            target_headers.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    with _serve(TargetHandler) as target_url:

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(302)
                self.send_header("Location", f"{target_url}/stolen")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass

        with _serve(RedirectHandler) as source_url:
            settings = hook_module.WorkBuddyPluginSettings(
                server_url=source_url,
                authorization="Bearer secret-token",
            )
            with pytest.raises(RuntimeError):
                hook_module._post_json(
                    "/v1/context/prepare",
                    {"scope_id": "scope-1", "query": "what did we decide?"},
                    settings=settings,
                    deadline=time.monotonic() + 1.0,
                )

    assert target_headers == []
