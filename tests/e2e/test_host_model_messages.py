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

"""The Messages API host model fake that the real-host Claude Code cases drive."""

from __future__ import annotations

from anthropic import Anthropic
from anthropic.types import MessageParam, ToolParam
from fastapi.testclient import TestClient

from tests.e2e.host_models.messages import MessagesModelFixture
from tests.e2e.host_models.script import FINAL_ANSWER

TOOL: ToolParam = {"name": "mcp__plugin_powercontext_powercontext__remember_memory", "input_schema": {"type": "object"}}
ARGUMENTS = {"kind": "decision", "text": "Synthetic decision."}


def test_scripted_tool_use_streams_to_the_sdk_and_side_requests_do_not_advance_the_script() -> None:
    model = MessagesModelFixture("fixture-main")
    model.plan([("remember_memory", ARGUMENTS)])
    with TestClient(model.app) as http:
        client = Anthropic(api_key="synthetic-only", base_url="http://model", http_client=http)
        prompt: MessageParam = {"role": "user", "content": "Save the decision."}

        with client.messages.stream(model="fixture-main", max_tokens=64, messages=[prompt], tools=[TOOL]) as stream:
            called = stream.get_final_message()
        # A host's request to another model, such as a title, answers without consuming the pending call.
        side = client.messages.create(model="fixture-small", max_tokens=64, messages=[prompt])
        (call,) = called.content
        assert call.type == "tool_use"
        assert (called.stop_reason, call.name, call.input) == ("tool_use", TOOL["name"], ARGUMENTS)
        assert [block.type for block in side.content] == ["text"]

        messages: list[MessageParam] = [
            prompt,
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call.id, "content": "saved"}]},
        ]
        with client.messages.stream(model="fixture-main", max_tokens=64, messages=messages, tools=[TOOL]) as stream:
            answered = stream.get_final_message()
        assert (answered.stop_reason, answered.content[0].type) == ("end_turn", "text")
        assert stream.get_final_text() == FINAL_ANSWER
        assert (len(model.requests), len(model.side_requests)) == (2, 1)
