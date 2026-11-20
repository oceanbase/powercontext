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

"""Minimal Deep Agents + PowerContext example against a running Server and model endpoint.

    export POWERCONTEXT_SERVER_URL=http://127.0.0.1:8123
    export OPENAI_BASE_URL=http://127.0.0.1:8000/v1   # any OpenAI-compatible endpoint
    export OPENAI_MODEL=your-model-id
    export OPENAI_API_KEY=dummy
    python integrations/deepagents/examples/quickstart.py

The example needs `powercontext server run` on POWERCONTEXT_SERVER_URL and `langchain-openai` installed.
With `auto_capture=True`, the completed turn is stored as a Source; the Server's normal extraction turns it into
Memory later. The next run recalls it through the same Scope.
"""

from __future__ import annotations

import asyncio
import os

from langchain_openai import ChatOpenAI
from powercontext_deepagents import create_powercontext_deep_agent
from powercontext_langchain import PowerContextScope

from powercontext.client import PowerContextClient
from powercontext.http import RememberMemoryRequest, ResolveScopeBindingRequest

SERVER = os.environ.get("POWERCONTEXT_SERVER_URL", "http://127.0.0.1:8123")
QUESTION = "What should we do before deploying the application?"


async def main() -> None:
    client = PowerContextClient(SERVER, trust_transport_security=True)
    scope_id = (await client.resolve_scope_binding(ResolveScopeBindingRequest())).scope_id
    await client.remember_memory(
        RememberMemoryRequest(
            scope_id=scope_id,
            kind="decision",
            text="Always run database migrations before deploying the application.",
            description="Deployment rule",
        )
    )

    model = ChatOpenAI(
        base_url=os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1"),
        api_key=os.environ.get("OPENAI_API_KEY", "dummy"),
        model=os.environ["OPENAI_MODEL"],
        temperature=0,
        max_tokens=128,
    )
    agent = create_powercontext_deep_agent(
        model,
        tools=[],
        system_prompt="You are a deployment assistant. Answer in one sentence.",
        auto_capture=True,
    )
    scope = PowerContextScope(base_url=SERVER, scope_id=scope_id, allow_insecure_http=True)

    before = await client.list_sources(scope_id)
    result = await agent.ainvoke({"messages": [{"role": "user", "content": QUESTION}]}, context=scope)
    after = await client.list_sources(scope_id)

    print("answer:", result["messages"][-1].text.strip())
    print(f"sources: {len(before.items)} -> {len(after.items)}")


if __name__ == "__main__":
    asyncio.run(main())
