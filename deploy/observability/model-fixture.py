"""Isolated OpenAI-compatible no-op model for explicit split-role acceptance only."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from typing_extensions import override


class Handler(BaseHTTPRequestHandler):
    @override
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 4 * 1024 * 1024:
            self.send_error(413)
            return
        payload = self.rfile.read(length)
        # Match the two structured model boundaries without returning invented business data.
        content = {"probes": []} if b"probes" in payload else {"candidates": []}
        response = json.dumps({
            "id": "reference-noop",
            "object": "chat.completion",
            "created": 1,
            "model": "reference-noop",
            "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(content)}}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()  # noqa: S104 - unpublished fixture network.
