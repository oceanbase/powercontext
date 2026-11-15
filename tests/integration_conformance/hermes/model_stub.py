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

"""Loopback Chat Completions protocol stub for Hermes' real summary client."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

SUMMARY_MARKER = "COBALT_COMPACTION_SUMMARY_FIXTURE"
MODEL_KEY = "synthetic-conformance-no-model-key"
MODEL = "fixture/cobalt-model"
SUMMARY = (
    "## Historical Task Snapshot\nThe synthetic cobalt conversation has been recorded.\n\n"
    "## Completed Actions\n1. Preserved the public API and synthetic project decisions.\n\n"
    f"## Active State\n{SUMMARY_MARKER}\n\n## In Progress\nNone.\n"
)


@contextmanager
def summary_model_stub():
    # Only bounded counts are retained; prompts and responses are never written to an artifact.
    observed = SimpleNamespace(calls=0, base_url="")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *_args):  # noqa: A002 - preserve the stdlib keyword argument.
            pass

        def _reply(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            body = json.dumps({"object": "list", "data": [{"id": MODEL, "object": "model"}]}).encode()
            self._reply(200, "application/json", body)

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self._reply(404, "application/json", b"{}")
                return
            if self.headers.get("Authorization") != f"Bearer {MODEL_KEY}":
                self._reply(401, "application/json", b"{}")
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.calls += 1
            common = {"id": "chatcmpl-conformance", "created": 0, "model": MODEL}
            usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
            if payload.get("stream"):
                chunks = [
                    {
                        **common,
                        "object": "chat.completion.chunk",
                        "choices": [
                            {"index": 0, "delta": {"role": "assistant", "content": SUMMARY}, "finish_reason": None}
                        ],
                    },
                    {
                        **common,
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "usage": usage,
                    },
                ]
                body = ("".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
                self._reply(200, "text/event-stream", body)
            else:
                body = json.dumps({
                    **common,
                    "object": "chat.completion",
                    "usage": usage,
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": SUMMARY}, "finish_reason": "stop"}
                    ],
                }).encode()
                self._reply(200, "application/json", body)

    serving = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    observed.base_url = f"http://127.0.0.1:{serving.server_port}/v1"
    thread = threading.Thread(target=serving.serve_forever, daemon=True)
    thread.start()
    try:
        yield observed
    finally:
        serving.shutdown()
        serving.server_close()
        thread.join(10)
        assert not thread.is_alive(), "Summary model stub did not stop"
