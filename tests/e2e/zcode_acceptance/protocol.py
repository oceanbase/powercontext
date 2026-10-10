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

"""Observe the real Server wire and provide a deterministic host model protocol."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Callable
from hashlib import sha256
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in strings(item)]
    return []


def decode_response(raw: bytes) -> dict[str, Any]:
    """Decode JSON or the MCP streamable-HTTP SSE result."""
    try:
        return json.loads(raw)
    except ValueError:
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if line.startswith("data: "):
                try:
                    value = json.loads(line[6:])
                except ValueError:
                    continue
                if isinstance(value, dict) and "result" in value:
                    return value
    return {}


class WireEvidence:
    """Keep request/response bodies in memory only; never record auth headers."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.received_paths: list[str] = []
        self.arrivals: list[dict[str, Any]] = []

    def calls(self, name: str, start: int = 0) -> list[dict[str, Any]]:
        return [
            item
            for item in self.records[start:]
            if item["request"].get("method") == "tools/call" and item["request"].get("params", {}).get("name") == name
        ]

    def result(self, name: str, start: int = 0) -> dict[str, Any]:
        calls = self.calls(name, start)
        assert calls, f"native_mcp_call_missing_{name}"
        result = calls[-1]["response"].get("result", {})
        assert not result.get("isError", False), f"native_mcp_tool_error_{name}"
        content = result.get("structuredContent")
        if isinstance(content, dict):
            return content
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    value = json.loads(block["text"])
                except ValueError:
                    continue
                if isinstance(value, dict):
                    return value
        message = f"native_mcp_object_result_missing_{name}"
        raise AssertionError(message)


class CaptureResponseGate:
    """Withhold one accepted capture receipt until its owning host turn has completed."""

    def __init__(self, content: str, *, release_timeout: float = 185) -> None:
        self.content = content
        self.release_timeout = release_timeout
        self.accepted = threading.Event()
        self._released = threading.Event()
        self.receipt: dict[str, Any] | None = None
        self.release_timed_out = False

    def release(self) -> None:
        self._released.set()

    async def hold(self, response: dict[str, Any]) -> None:
        source = response.get("source", {})
        assert response.get("status") == "accepted" and source.get("name") == "content", (
            "capture_gate_accepted_receipt_missing"
        )
        assert isinstance(source.get("source_id"), str) and source["source_id"], "capture_gate_source_identity_missing"
        assert isinstance(response.get("position"), int) and response["position"] > 0, (
            "capture_gate_source_position_missing"
        )
        self.receipt = {
            "source": {"name": "content", "source_id": source["source_id"]},
            "position": response["position"],
        }
        self.accepted.set()
        if not await asyncio.to_thread(self._released.wait, self.release_timeout):
            self.release_timed_out = True
            raise AssertionError("capture_response_gate_release_timeout")

    def evidence(self) -> dict[str, Any]:
        return {
            "target_content_digest": "sha256:" + sha256(self.content.encode()).hexdigest(),
            "accepted": self.accepted.is_set(),
            "accepted_receipt": self.receipt,
            "explicitly_released": self._released.is_set(),
            "release_timed_out": self.release_timed_out,
        }


class ObserveServer:
    def __init__(self, app: ASGIApp, evidence: WireEvidence) -> None:
        self.app = app
        self.evidence = evidence
        self.capture_response_gate: CaptureResponseGate | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:  # noqa: C901
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        self.evidence.received_paths.append(scope["path"])
        arrived = {"path": scope["path"], "request": {}}
        self.evidence.arrivals.append(arrived)
        request_body = bytearray()
        response_body = bytearray()
        status = 0
        held_messages: list[Message] = []
        gate = self.capture_response_gate if scope["path"] == "/v1/sources/content" else None
        hold_response = False

        async def receive_observed() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                request_body.extend(message.get("body", b""))
                assert len(request_body) <= 1_048_576, "acceptance request exceeded evidence limit"
                if not message.get("more_body", False):
                    arrived["request"] = decode_response(bytes(request_body)) if request_body else {}
            return message

        async def send_observed(message: Message) -> None:
            nonlocal status, hold_response
            if message["type"] == "http.response.start":
                status = message["status"]
                hold_response = bool(gate and status == 202 and arrived["request"].get("content") == gate.content)
            elif message["type"] == "http.response.body":
                response_body.extend(message.get("body", b""))
                assert len(response_body) <= 1_048_576, "acceptance response exceeded evidence limit"
                if not message.get("more_body", False):
                    self.evidence.records.append({
                        "path": scope["path"],
                        "status": status,
                        "request": decode_response(bytes(request_body)) if request_body else {},
                        "response": decode_response(bytes(response_body)),
                    })
            if hold_response:
                held_messages.append(message)
            else:
                await send(message)

        await self.app(scope, receive_observed, send_observed)
        if hold_response:
            assert gate is not None
            await gate.hold(decode_response(bytes(response_body)))
            for message in held_messages:
                await send(message)


ToolAction = tuple[str, dict[str, Any] | Callable[[], dict[str, Any]]]


class HostModelFixture:
    """Return native function calls; never execute a PowerContext operation itself."""

    def __init__(self) -> None:
        self.actions: list[ToolAction] = []
        self.step = 0
        self.requests: list[dict[str, Any]] = []
        self.generation_requests: list[dict[str, Any]] = []
        self.answer_from_context = False
        self.app = FastAPI()
        self.app.post("/v1/chat/completions")(self.complete)

    def plan(self, actions: list[ToolAction] | None = None, *, recall: bool = False) -> None:
        self.actions = actions or []
        self.step = 0
        self.answer_from_context = recall

    async def complete(self, request: Request) -> Response:
        body = await request.json()
        if body["model"] == "memory-fixture":
            return self.generate_memory(body)
        self.requests.append(body)
        if self.step < len(self.actions):
            operation, arguments = self.actions[self.step]
            tool = next(
                (
                    item["function"]["name"]
                    for item in body.get("tools", [])
                    if item["function"]["name"].endswith("__" + operation)
                ),
                None,
            )
            assert tool is not None, f"host catalog lacks PowerContext {operation}"
            encoded_arguments = json.dumps(arguments if isinstance(arguments, dict) else arguments())
            self.step += 1
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"call-{self.step}",
                        "type": "function",
                        "function": {
                            "name": tool,
                            "arguments": encoded_arguments,
                        },
                    }
                ],
            }
            finish = "tool_calls"
        else:
            answer = "Synthetic operation complete."
            if self.answer_from_context:
                contexts = [
                    text
                    for text in strings(body.get("messages", []))
                    if "PowerContext context for this request." in text
                ]
                match = re.search(r"verification color is (\w+-\d+)", "\n".join(contexts))
                answer = match[1] if match else "No prepared evidence received."
            delta = {"role": "assistant", "content": answer}
            finish = "stop"

        if not body.get("stream"):
            return JSONResponse({
                "id": "synthetic-completion",
                "object": "chat.completion",
                "created": 1,
                "model": "zcode-protocol-fixture",
                "choices": [{"index": 0, "message": delta, "finish_reason": finish}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            })

        def chunks():
            for value, reason in ((delta, None), ({}, finish)):
                yield (
                    "data: "
                    + json.dumps({
                        "id": "synthetic-completion",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "zcode-protocol-fixture",
                        "choices": [{"index": 0, "delta": value, "finish_reason": reason}],
                    })
                    + "\n\n"
                )
            yield "data: [DONE]\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    def generate_memory(self, body: dict[str, Any]) -> JSONResponse:
        """Public inference protocol for real scheduler worker processes, not an injected Runtime pipeline."""
        self.generation_requests.append(body)
        payload = None
        for text in strings(body.get("messages", [])):
            try:
                value = json.loads(text)
            except ValueError:
                continue
            if isinstance(value, dict) and "evidence" in value and "current_entries" in value:
                payload = value
                break
        assert payload is not None or not body.get("tools"), "generation_input_missing"
        payload = payload or {"evidence": [], "current_entries": []}
        candidates = []
        existing = {entry["text"] for entry in payload["current_entries"]}
        for item in payload["evidence"]:
            match = re.search(
                r"In the synthetic (\w+) project, the verification color is (\w+-\d+)\.",
                "\n".join(strings(item["content"])),
            )
            if match and match[0] not in existing:
                candidates.append({
                    "intent": "add",
                    "kind": "fact",
                    "text": match[0],
                    "evidence_ids": [item["evidence_id"]],
                    "entry_id": None,
                    "reason": "Synthetic source evidence",
                })
        output = json.dumps({"candidates": candidates})
        tools = body.get("tools", [])
        message = {"role": "assistant", "content": output}
        if tools:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "generation-result",
                        "type": "function",
                        "function": {"name": tools[0]["function"]["name"], "arguments": output},
                    }
                ],
            }
        return JSONResponse({
            "id": "synthetic-generation",
            "object": "chat.completion",
            "created": 1,
            "model": "memory-fixture",
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tools else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
        })
