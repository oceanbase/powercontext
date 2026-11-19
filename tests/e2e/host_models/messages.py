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

"""A streamed Anthropic Messages API fake for hosts such as Claude Code."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from tests.e2e.zcode_acceptance.protocol import ToolAction, strings

from .script import FINAL_ANSWER, ScriptedTurns, ToolCall

USAGE = {"input_tokens": 100, "output_tokens": 20, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


class MessagesModelFixture:
    """Serve `POST /v1/messages` from a `ScriptedTurns` script, streamed or not as the request asks.

    Only requests for `model` follow the script and are kept in `requests`; a host's side requests to another model,
    such as a title or a classifier, get the final answer and are kept in `side_requests`.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self.script = ScriptedTurns()
        self.requests: list[dict[str, Any]] = []
        self.side_requests: list[dict[str, Any]] = []
        self.app = FastAPI()
        self.app.post("/v1/messages")(self.respond)

    def plan(self, actions: list[ToolAction] | None = None) -> None:
        self.script.plan(actions)

    def texts(self, request: dict[str, Any]) -> str:
        """All text a request carries in its messages, joined by newlines."""
        return "\n".join(strings(request.get("messages", [])))

    def tool_names(self, request: dict[str, Any]) -> list[str]:
        return [tool["name"] for tool in request.get("tools", []) if isinstance(tool, dict) and "name" in tool]

    def tool_results(self, request: dict[str, Any]) -> dict[str, str]:
        """Tool results the host returned in a request, by tool use id."""
        results: dict[str, str] = {}
        for message in request.get("messages", []):
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    results[block["tool_use_id"]] = "\n".join(strings(block.get("content", "")))
        return results

    async def respond(self, request: Request) -> Response:
        body = await request.json()
        number = len(self.requests) + len(self.side_requests) + 1
        call: ToolCall | None = None
        if body.get("model") == self.model:
            self.requests.append(body)
            try:
                call = self.script.next(self.tool_names(body), self.tool_results(body))
            except AssertionError as error:
                # A missing tool is terminal for the turn; a 400 is not retried as a provider outage.
                return JSONResponse(
                    {"type": "error", "error": {"type": "invalid_request_error", "message": str(error)}},
                    status_code=400,
                )
        else:
            self.side_requests.append(body)
        if call is None:
            block: dict[str, Any] = {"type": "text", "text": FINAL_ANSWER}
        else:
            block = {"type": "tool_use", "id": call.call_id, "name": call.name, "input": json.loads(call.arguments)}
        message = {
            "id": f"msg_{number}",
            "type": "message",
            "role": "assistant",
            "model": body.get("model", self.model),
            "content": [block],
            "stop_reason": "end_turn" if call is None else "tool_use",
            "stop_sequence": None,
            "usage": USAGE,
        }
        if not body.get("stream"):
            return JSONResponse(message)
        return StreamingResponse(_events(message), media_type="text/event-stream")


def _events(message: dict[str, Any]) -> Iterator[str]:
    def event(kind: str, **fields: Any) -> str:
        return f"event: {kind}\ndata: {json.dumps({'type': kind, **fields})}\n\n"

    (block,) = message["content"]
    yield event(
        "message_start", message={**message, "content": [], "stop_reason": None, "usage": {**USAGE, "output_tokens": 1}}
    )
    if block["type"] == "text":
        yield event("content_block_start", index=0, content_block={"type": "text", "text": ""})
        yield event("content_block_delta", index=0, delta={"type": "text_delta", "text": block["text"]})
    else:
        yield event("content_block_start", index=0, content_block={**block, "input": {}})
        delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
        yield event("content_block_delta", index=0, delta=delta)
    yield event("content_block_stop", index=0)
    stop = {"stop_reason": message["stop_reason"], "stop_sequence": None}
    yield event("message_delta", delta=stop, usage={"output_tokens": USAGE["output_tokens"]})
    yield event("message_stop")
