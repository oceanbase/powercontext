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

"""Real HTTP/SSE boundary acceptance with controlled synthetic upstream replies.

These tests exercise sockets and the production protected transport. They do
not establish behavior or token savings of a real external model provider.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr

from powercontext.builtin.inference.errors import InferencePrivacyError
from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.inference.protected_transport import ProtectedAsyncTransport

_SECRET = "synthetic-rfc2086-forbidden"  # noqa: S105 - synthetic forbidden-content fixture
_MODEL = "rfc2086-controlled-model"


class RecordingServer(ThreadingHTTPServer):
    def __init__(self, body: bytes, *, content_type: str = "application/json", status: int = 200) -> None:
        super().__init__(("127.0.0.1", 0), RecordingHandler)
        self.body = body
        self.content_type = content_type
        self.status = status
        self.received: list[bytes] = []


class RecordingHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server = cast(RecordingServer, self.server)
        server.received.append(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(server.status)
        self.send_header("Content-Type", server.content_type)
        self.send_header("Content-Length", str(len(server.body)))
        self.end_headers()
        self.wfile.write(server.body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - standard-library signature
        pass


@contextmanager
def upstream(body: bytes, **kwargs: Any) -> Iterator[RecordingServer]:
    server = RecordingServer(body, **kwargs)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(content: str = "Return a safe answer.", *, stream: bool = False) -> dict[str, Any]:
    return {"model": _MODEL, "messages": [{"role": "user", "content": content}], "stream": stream, "max_tokens": 1024}


def completion(content: str = "safe") -> bytes:
    return json.dumps({
        "model": _MODEL,
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }).encode()


def protected_client(server: RecordingServer) -> httpx.AsyncClient:
    target = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    config = InferenceProtectionConfig(
        mode="enforcing",
        forbidden_values=(SecretStr(_SECRET),),
        targets={
            "controlled": UpstreamProfile(
                endpoint=target,
                model=_MODEL,
                protocol="openai-chat",
                allow_streaming=True,
                context_window_tokens=32768,
                max_output_tokens=1024,
                validated_utf8_token_bound=True,
            )
        },
    )
    return httpx.AsyncClient(
        transport=ProtectedAsyncTransport(config), base_url=f"http://127.0.0.1:{server.server_port}", timeout=5
    )


@pytest.mark.parametrize(
    "carrier", ("message", "tool_result", "duplicate_key", "unicode_escape", "literal_placeholder")
)
def test_forbidden_input_does_not_reach_a_real_http_listener(carrier: str) -> None:
    payload = request(_SECRET)
    if carrier == "tool_result":
        payload["messages"] = [{"role": "tool", "tool_call_id": "synthetic-tool", "content": _SECRET}]
    elif carrier == "literal_placeholder":
        payload = request("{{TOKEN_ABCDEFGH}} " + _SECRET)
    raw = json.dumps(payload).encode()
    if carrier == "duplicate_key":
        raw = raw.replace(json.dumps(_SECRET).encode(), f'"{_SECRET}", "content": "safe"'.encode())
    elif carrier == "unicode_escape":
        raw = raw.replace(_SECRET.encode(), "".join(f"\\u{ord(char):04x}" for char in _SECRET).encode())

    async def scenario(server: RecordingServer) -> None:
        async with protected_client(server) as client:
            with pytest.raises(InferencePrivacyError) as rejected:
                await client.post("/v1/chat/completions", content=raw, headers={"Content-Type": "application/json"})
        assert rejected.value.phase == "before_send"
        assert rejected.value.decision == "block"
        assert _SECRET not in str(rejected.value)
        assert server.received == []

    with upstream(completion()) as server:
        asyncio.run(scenario(server))


@pytest.mark.parametrize(
    "content",
    ("Return a safe answer.", "Keep {{USERNAME_AAAAAAAA}} and {{IP_BBBBBBBB}} unchanged; 路径 /home/example."),
    ids=("plain_text", "literal_placeholders"),
)
def test_safe_request_and_exact_response_cross_the_real_http_boundary(content: str) -> None:
    raw_request = json.dumps(request(content), ensure_ascii=False, indent=2).encode()
    raw_response = completion(content)

    async def scenario(server: RecordingServer) -> None:
        async with protected_client(server) as client:
            response = await client.post(
                "/v1/chat/completions", content=raw_request, headers={"Content-Type": "application/json"}
            )
        assert response.content == raw_response
        assert server.received == [raw_request]

    with upstream(raw_response) as server:
        asyncio.run(scenario(server))


@pytest.mark.parametrize("provider_error", (False, True))
def test_forbidden_raw_response_never_reaches_host_and_known_usage_survives(provider_error: bool) -> None:
    body = completion(_SECRET)
    if provider_error:
        body = json.dumps({"error": {"message": _SECRET}, "usage": {"prompt_tokens": 12}}).encode()

    async def scenario(server: RecordingServer) -> None:
        delivered: list[bytes] = []
        async with protected_client(server) as client:
            with pytest.raises(InferencePrivacyError) as rejected:
                async with client.stream("POST", "/v1/chat/completions", json=request()) as response:
                    async for chunk in response.aiter_bytes():
                        delivered.append(chunk)
        assert len(server.received) == 1
        assert delivered == []
        assert rejected.value.phase == "before_release"
        assert rejected.value.decision == "block"
        assert rejected.value.usage is not None
        assert rejected.value.usage.input_tokens == 12
        assert rejected.value.usage.output_tokens == (None if provider_error else 3)

    with upstream(body, status=400 if provider_error else 200) as server:
        asyncio.run(scenario(server))


def test_secret_split_across_sse_events_is_withheld_before_first_host_chunk() -> None:
    events = []
    for part in (_SECRET[:12], _SECRET[12:]):
        events.append({"model": _MODEL, "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}]})
    events.append({"model": _MODEL, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    raw = ("".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n").encode()
    assert _SECRET.encode() not in raw

    async def scenario(server: RecordingServer) -> None:
        delivered: list[bytes] = []
        async with protected_client(server) as client:
            with pytest.raises(InferencePrivacyError) as rejected:
                async with client.stream("POST", "/v1/chat/completions", json=request(stream=True)) as response:
                    async for chunk in response.aiter_bytes():
                        delivered.append(chunk)
        assert len(server.received) == 1
        assert delivered == []
        assert rejected.value.phase == "before_release"
        assert rejected.value.decision == "block"
        assert rejected.value.usage is not None
        assert rejected.value.usage.input_tokens is None
        assert rejected.value.usage.incomplete is True

    with upstream(raw, content_type="text/event-stream") as server:
        asyncio.run(scenario(server))
