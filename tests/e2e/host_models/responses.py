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

"""A streamed OpenAI Responses API fake for hosts such as Codex."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from tests.e2e.zcode_acceptance.protocol import ToolAction, strings

from .script import FINAL_ANSWER, ScriptedTurns, ToolCall


class ResponsesModelFixture:
    """Serve `POST /v1/responses` as server-sent events from a `ScriptedTurns` script.

    Every request body is kept in `requests`. A script names a tool offered inside a namespace as
    `<namespace>__<tool>`, or by the tool's own name; Codex offers MCP tools in a `mcp__<server>` namespace.
    """

    def __init__(self) -> None:
        self.script = ScriptedTurns()
        self.requests: list[dict[str, Any]] = []
        # A repeated call keeps the address it was first issued with, even in a request that offers no tools.
        self._addresses: dict[str, dict[str, str]] = {}
        self.app = FastAPI()
        self.app.post("/v1/responses")(self.respond)

    def plan(self, actions: list[ToolAction] | None = None) -> None:
        self.script.plan(actions)

    def texts(self, request: dict[str, Any]) -> str:
        """All text a request carries in its input items, joined by newlines."""
        return "\n".join(strings(request.get("input", [])))

    def function_outputs(self, request: dict[str, Any]) -> dict[str, str]:
        """Tool results the host returned in a request, by call id."""
        return {
            item["call_id"]: item["output"] if isinstance(item["output"], str) else json.dumps(item["output"])
            for item in request.get("input", [])
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        }

    async def respond(self, request: Request) -> Response:
        body = await request.json()
        self.requests.append(body)
        offered = _offered_tools(body.get("tools", []))
        try:
            call = self.script.next(offered, self.function_outputs(body))
        except AssertionError as error:
            # A missing tool is terminal for the turn; a 400 is not retried as a provider outage.
            return JSONResponse({"error": {"message": str(error), "type": "invalid_request_error"}}, status_code=400)
        number = len(self.requests)
        if call is None:
            item = {
                "type": "message",
                "id": f"msg-{number}",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": FINAL_ANSWER, "annotations": []}],
            }
        else:
            if call.call_id not in self._addresses:
                self._addresses[call.call_id] = offered[call.name]
            item = _function_call(call, self._addresses[call.call_id])
        return StreamingResponse(_events(f"resp-{number}", item), media_type="text/event-stream")


def _offered_tools(tools: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Map each callable tool name to the function-call fields that address it."""
    offered: dict[str, dict[str, str]] = {}
    for tool in tools:
        if tool.get("type") == "function":
            offered[tool["name"]] = {"name": tool["name"]}
        elif tool.get("type") == "namespace":
            for child in tool.get("tools", []):
                if child.get("type") == "function":
                    offered[f"{tool['name']}__{child['name']}"] = {"namespace": tool["name"], "name": child["name"]}
    return offered


def _function_call(call: ToolCall, address: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": f"fc-{call.call_id}",
        "call_id": call.call_id,
        "arguments": call.arguments,
        "status": "completed",
        **address,
    }


def _events(response_id: str, item: dict[str, Any]) -> Iterator[str]:
    def event(kind: str, **fields: Any) -> str:
        return f"event: {kind}\ndata: {json.dumps({'type': kind, **fields})}\n\n"

    yield event("response.created", response={"id": response_id, "status": "in_progress", "output": []})
    yield event("response.output_item.added", output_index=0, item=item)
    if item["type"] == "message":
        text = item["content"][0]["text"]
        yield event("response.output_text.delta", item_id=item["id"], output_index=0, content_index=0, delta=text)
    yield event("response.output_item.done", output_index=0, item=item)
    usage = {
        "input_tokens": 100,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 20,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 120,
    }
    yield event("response.completed", response={"id": response_id, "status": "completed", "usage": usage})
