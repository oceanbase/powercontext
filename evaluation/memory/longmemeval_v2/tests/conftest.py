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
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest


@contextmanager
def _model_server() -> Iterator[SimpleNamespace]:
    state = SimpleNamespace(calls=[], redirect_url=None, redirect_status=302, text="answer")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.calls.append({"path": self.path, "authorization": self.headers.get("Authorization"), "body": payload})
            if state.redirect_url:
                self.send_response(state.redirect_status)
                self.send_header("Location", state.redirect_url)
                self.end_headers()
                return
            value = (
                {"content": [{"type": "text", "text": state.text}], "usage": {"input_tokens": 3, "output_tokens": 1}}
                if self.path.endswith("/messages")
                else {
                    "choices": [{"message": {"content": state.text}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                }
            )
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            state.calls.append({"path": self.path, "authorization": self.headers.get("Authorization")})
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}"
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def model_http_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[], SimpleNamespace]]:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    with ExitStack() as stack:
        yield lambda: stack.enter_context(_model_server())
