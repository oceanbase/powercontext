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


"""Synthetic HTTP gateway drives the real Datus/SDK loop; never live model evidence."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

TABLE = {"columns": ["value"], "rows": [[7], [7], [None]]}
SQL = "SELECT value FROM sample ORDER BY rowid"


@contextmanager
def gateway(*, skill=False, sql_tool=False, wrong_answer=False, fail_sql=False, on_request=None):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - stdlib override signature
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(request)
            if on_request is not None:
                on_request(request)
            tool_returns = sum(m["role"] == "tool" for m in request["messages"])
            steps = []
            if skill:
                steps.append(("load_skill", {"skill_name": "fixture"}))
            if fail_sql:
                steps.append(("execute_sql", {"sql": "SELECT missing FROM sample"}))
            if sql_tool:
                steps.append(("execute_sql", {"sql": SQL}))
            delta: dict[str, Any] = {"role": "assistant"}
            if tool_returns < len(steps):
                name, args = steps[tool_returns]
                # Supply all native optional parameters when SDK strict schemas
                # require them. This is an API fixture, not a fake tool.
                schema = next(t["function"]["parameters"] for t in request["tools"] if t["function"]["name"] == name)
                arguments = dict.fromkeys(schema.get("required", []))
                arguments.update(args)
                delta["tool_calls"] = [
                    {
                        "index": 0,
                        "id": "call_" + str(tool_returns),
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ]
                finish = "tool_calls"
            else:
                answer = {"columns": ["value"], "rows": [[999]]} if wrong_answer else TABLE
                delta["content"] = json.dumps({"sql": SQL, "output": json.dumps(answer)})
                finish = "stop"
            chunks = [
                {
                    "id": "fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "gpt-4o-mini",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                },
                {
                    "id": "fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "gpt-4o-mini",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    "usage": {
                        "prompt_tokens": 50,
                        "completion_tokens": 20,
                        "total_tokens": 70,
                        "prompt_tokens_details": {"cached_tokens": 10},
                    },
                },
            ]
            data = "".join("data: " + json.dumps(v) + "\n\n" for v in chunks) + "data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data.encode())))
            self.end_headers()
            self.wfile.write(data.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port) + "/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
