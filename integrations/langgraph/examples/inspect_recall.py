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

"""Print the exact system message ``PowerContextRecall`` supplies. No model or API key required.

The example remembers one decision, then runs a one-node graph whose only node is ``PowerContextRecall``.
``PowerContextRecall`` writes the model input onto the ephemeral ``llm_input_messages`` channel, so the state
carries that channel and the example prints the leading system message from it, showing the untrusted-history
framing and the citations the Server attaches. Run it with::

    uv run python integrations/langgraph/examples/inspect_recall.py
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from _local_server import local_powercontext_server
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from powercontext_langgraph import PowerContextRecall, PowerContextScope
from typing_extensions import TypedDict

from powercontext.client import PowerContextClient
from powercontext.http import RememberMemoryRequest

SCOPE_ID = "project:langgraph-inspect-recall"
SEED_TEXT = "Adopt hexagonal architecture for the payment gateway."


class RecallState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    llm_input_messages: list[BaseMessage]


def _build_graph():
    builder = StateGraph(RecallState, context_schema=PowerContextScope)
    builder.add_node("recall", PowerContextRecall())
    builder.add_edge(START, "recall")
    builder.add_edge("recall", END)
    return builder.compile()


async def main(base_url: str) -> None:
    scope = PowerContextScope(scope_id=SCOPE_ID, base_url=base_url)

    async with PowerContextClient(base_url) as client:
        await client.remember_memory(
            RememberMemoryRequest(
                scope_id=SCOPE_ID,
                kind="decision",
                text=SEED_TEXT,
                reason="example seed",
            )
        )

    graph = _build_graph()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="What architecture did we choose for the payment gateway?")]},
        context=scope,
    )

    model_input = result.get("llm_input_messages") or []
    system_messages = [message for message in model_input if getattr(message, "type", None) == "system"]
    if not system_messages:
        print("Recall supplied nothing — the Server returned no relevant context.")
        return
    print("---- supplied system message ----")
    print(system_messages[0].text)
    print("---- end ----")


if __name__ == "__main__":
    with local_powercontext_server() as base_url:
        asyncio.run(main(base_url))
