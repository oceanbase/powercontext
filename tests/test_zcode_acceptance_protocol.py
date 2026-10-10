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

"""The acceptance fault withholds an actual app receipt, independently of client timer scheduling."""

import asyncio
import json

import pytest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .e2e.zcode_acceptance.protocol import CaptureResponseGate, ObserveServer, WireEvidence


def capture_app(writes: list[str], *, reject: bool = False) -> ASGIApp:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["path"] == "/health/ready":
            status, body = 200, {"ready": True}
        else:
            request = json.loads((await receive())["body"])
            if reject:
                status, body = 422, {"error": "invalid_request"}
            else:
                writes.append(request["content"])
                status, body = (
                    202,
                    {
                        "status": "accepted",
                        "source": {"name": "content", "source_id": "source-" + str(len(writes))},
                        "position": len(writes),
                    },
                )
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": json.dumps(body).encode()})

    return app


async def exchange(
    observer: ObserveServer, content: str, sent: list[Message], *, path: str = "/v1/sources/content"
) -> None:
    async def receive() -> Message:
        return {"type": "http.request", "body": json.dumps({"content": content}).encode()}

    async def send(message: Message) -> None:
        sent.append(message)

    await observer({"type": "http", "path": path}, receive, send)


def test_capture_gate_holds_only_target_accepted_response_until_release():
    async def check():
        writes = []
        wire = WireEvidence()
        observer = ObserveServer(capture_app(writes), wire)
        gate = CaptureResponseGate("target capture")
        observer.capture_response_gate = gate
        target_sent = []
        target = asyncio.create_task(exchange(observer, gate.content, target_sent))
        try:
            assert await asyncio.to_thread(gate.accepted.wait, 2)
            assert writes == [gate.content]
            assert wire.records[0]["status"] == 202
            assert target_sent == []
            assert not target.done()

            health_sent, other_sent = [], []
            await asyncio.wait_for(exchange(observer, "", health_sent, path="/health/ready"), 2)
            await asyncio.wait_for(exchange(observer, "other capture", other_sent), 2)
            assert health_sent[0]["status"] == 200
            assert other_sent[0]["status"] == 202
            assert writes == [gate.content, "other capture"]
            assert target_sent == []
            assert gate.evidence()["accepted_receipt"] == {
                "source": {"name": "content", "source_id": "source-1"},
                "position": 1,
            }
        finally:
            gate.release()
            await asyncio.wait_for(target, 2)
        assert target_sent[0]["status"] == 202
        assert json.loads(target_sent[1]["body"])["source"]["source_id"] == "source-1"
        assert not gate.release_timed_out

    asyncio.run(check())


def test_capture_gate_does_not_hold_a_rejected_target_request():
    gate = CaptureResponseGate("target capture")
    observer = ObserveServer(capture_app([], reject=True), WireEvidence())
    observer.capture_response_gate = gate
    sent = []
    asyncio.run(asyncio.wait_for(exchange(observer, gate.content, sent), 2))
    assert sent[0]["status"] == 422
    assert not gate.accepted.is_set()


def test_capture_gate_has_bounded_failure_without_release():
    gate = CaptureResponseGate("target capture", release_timeout=0.01)
    observer = ObserveServer(capture_app([]), WireEvidence())
    observer.capture_response_gate = gate
    sent = []
    with pytest.raises(AssertionError, match="capture_response_gate_release_timeout"):
        asyncio.run(exchange(observer, gate.content, sent))
    assert gate.accepted.is_set()
    assert gate.release_timed_out
    assert sent == []
