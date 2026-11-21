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

"""End-to-end example: capture a turn, extract Memory, then recall it in a new session.

    export POWERCONTEXT_SERVER_URL=http://127.0.0.1:8129
    export OPENAI_BASE_URL=http://127.0.0.1:12346/v1
    export OPENAI_MODEL=/path/to/chat-model
    export OPENAI_API_KEY=dummy
    python integrations/deepagents/examples/full_flow.py

The Server needs a generation model for Source-to-Memory extraction and an embedding model for paraphrased recall;
a 4B-class chat model plus a small embedding model is enough. Without an embedding model the Server falls back to
lexical retrieval and a paraphrased question can return an empty context.

The script takes four observable steps:

1. run one agent turn with `auto_capture=True`, which stores the turn as a Source;
2. `flush_memory` turns the Source into Memory;
3. poll `search_memory` until the extracted entry is visible;
4. ask a paraphrased question in a new agent turn and print the answer.
"""

from __future__ import annotations

import asyncio
import os
import time

from langchain_openai import ChatOpenAI
from powercontext_deepagents import create_powercontext_deep_agent
from powercontext_langchain import PowerContextScope

from powercontext.client import PowerContextClient
from powercontext.http import FlushMemoryRequest, ResolveScopeBindingRequest, SearchMemoryRequest

SERVER = os.environ.get("POWERCONTEXT_SERVER_URL", "http://127.0.0.1:8129")
TEACHING_TURN = (
    "Remember this deployment rule for this repository: always run `make migrate` before `make ship-it`, "
    "because the database schema must match the new application code before startup. "
    "The deploy command itself is `make ship-it`."
)
PARAPHRASED_QUESTION = "What deployment command should we use?"
EXTRACTION_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 4


def build_model() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:12346/v1"),
        api_key=os.environ.get("OPENAI_API_KEY", "dummy"),
        model=os.environ["OPENAI_MODEL"],
        temperature=0,
        max_tokens=256,
    )


async def main() -> None:
    client = PowerContextClient(SERVER, trust_transport_security=True, timeout=180)
    scope_id = (await client.resolve_scope_binding(ResolveScopeBindingRequest())).scope_id
    scope = PowerContextScope(base_url=SERVER, scope_id=scope_id, allow_insecure_http=True)
    agent = create_powercontext_deep_agent(
        build_model(),
        tools=[],
        system_prompt="You are a deployment assistant. Answer briefly.",
        auto_capture=True,
    )
    print(f"scope: {scope_id}")

    # 1. Capture: the completed turn becomes one Source.
    before = len((await client.list_sources(scope_id)).items)
    await agent.ainvoke({"messages": [{"role": "user", "content": TEACHING_TURN}]}, context=scope)
    after = len((await client.list_sources(scope_id)).items)
    print(f"1. capture: sources {before} -> {after}")

    # 2. Extraction: flush turns pending Sources into Memory.
    flushed = await client.flush_memory(FlushMemoryRequest(scope_id=scope_id))
    print(f"2. flush: status={flushed.status} processed={flushed.processed_source_count}")

    # 3. Wait for the extracted Memory to be searchable.
    hits = []
    deadline = time.monotonic() + EXTRACTION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        result = await client.search_memory(
            SearchMemoryRequest(scope_id=scope_id, query="deploy command migrate", limit=5)
        )
        hits = getattr(result, "hits", [])
        if hits:
            break
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    print(f"3. memory: {len(hits)} hit(s)")
    for hit in hits[:3]:
        print(f"   - {hit.memory.text[:120]}")

    # 4. Recall: a paraphrased question in a new session.
    recalled = await agent.ainvoke({"messages": [{"role": "user", "content": PARAPHRASED_QUESTION}]}, context=scope)
    print(f"4. answer: {recalled['messages'][-1].text.strip()[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
