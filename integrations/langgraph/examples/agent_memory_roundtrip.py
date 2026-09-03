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

"""Drive a real agent through the write-then-recall memory loop against a live PowerContext Server.

Turn 1 asks the agent to persist a decision, which it does by calling the ``powercontext_remember`` tool.
Turn 2 is a fresh question; ``PowerContextRecall`` supplies the remembered decision as an untrusted-history
system message on the ephemeral ``llm_input_messages`` channel before the model step, and the agent answers
from it. Because that context never enters the persisted history, the example verifies recall through the
agent's answer rather than by inspecting messages. The connection uses bearer auth, and the example checks
that the token never reaches the agent-visible messages.

The model is any OpenAI-compatible endpoint; the defaults target DeepSeek. Requires ``langchain-openai``
and an API key::

    DEEPSEEK_API_KEY=sk-... uv run --with langchain-openai python \\
        integrations/langgraph/examples/agent_memory_roundtrip.py

In production you would point the adapter at a separately managed Server through
``POWERCONTEXT_LANGGRAPH_BASE_URL`` rather than starting the local Server this example spins up.
"""

from __future__ import annotations

import asyncio
import os
import sys

from _local_server import local_powercontext_server
from powercontext_langgraph import PowerContextRecall, PowerContextScope, powercontext_tools
from pydantic import SecretStr

AUTH_TOKEN = "example-token"  # noqa: S105 - local throwaway Server credential, not a real secret.
MEMORY_FACT = "hexagonal architecture for the payment gateway"


def _require_api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        sys.exit("Set DEEPSEEK_API_KEY (or adapt this example to another OpenAI-compatible endpoint).")
    return key


def _build_model(api_key: str):
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        sys.exit("Install langchain-openai, e.g. `uv run --with langchain-openai python <this file>`.")
    return ChatOpenAI(
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        api_key=SecretStr(api_key),
        temperature=0,
    )


def _print_turn(title: str, messages: list) -> None:
    print(f"\n===== {title} =====")
    for message in messages:
        kind = getattr(message, "type", "?")
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            print(f"[{kind}] tool call -> {', '.join(call['name'] for call in tool_calls)}")
        text = (message.text or "").strip()
        if text:
            print(f"[{kind}] {text if len(text) < 300 else text[:300] + ' …'}")


async def main(base_url: str) -> int:
    from langgraph.prebuilt import create_react_agent

    agent = create_react_agent(
        _build_model(_require_api_key()),
        tools=powercontext_tools(),
        pre_model_hook=PowerContextRecall(),
        context_schema=PowerContextScope,
    )
    scope = PowerContextScope(base_url=base_url, token=AUTH_TOKEN)

    turn1 = await agent.ainvoke(
        {"messages": [("user", f"Persist this decision to long-term memory verbatim: adopt {MEMORY_FACT}.")]},
        context=scope,
    )
    _print_turn("turn 1 — write via powercontext_remember", turn1["messages"])

    turn2 = await agent.ainvoke(
        {"messages": [("user", "What architecture did we choose for the payment gateway? Check memory first.")]},
        context=scope,
    )
    _print_turn("turn 2 — recall via PowerContextRecall", turn2["messages"])

    all_messages = turn1["messages"] + turn2["messages"]
    answers = [m.text for m in turn2["messages"] if getattr(m, "type", None) == "ai" and (m.text or "").strip()]
    checks = {
        # Recall is ephemeral, so it is observed through the agent's answer rather than in the persisted history.
        "final answer used the recalled fact": bool(answers) and "hexagonal" in answers[-1].lower(),
        "recall context stayed out of the persisted history": all(
            getattr(m, "type", None) != "system" for m in all_messages
        ),
        "auth token never leaked into messages": all(AUTH_TOKEN not in (m.text or "") for m in all_messages),
    }
    print("\n===== checks =====")
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    with local_powercontext_server(token=AUTH_TOKEN) as base_url:
        raise SystemExit(asyncio.run(main(base_url)))
