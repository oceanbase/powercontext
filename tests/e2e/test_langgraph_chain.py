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

"""End-to-end chain for the PowerContext LangGraph adapter over a real HTTP server.

A compiled graph writes a memory through the ``powercontext_remember`` tool and reads it back through
``PowerContextRecall``, both over a live uvicorn server. The connection is carried entirely by the run scope
(``context_schema``), which proves the tool and node pick up the scope from the graph runtime and that the bare
token is sent as ``Authorization: Bearer`` without leaking into agent-visible message content.
"""

from __future__ import annotations

import asyncio
import importlib
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings

pytest.importorskip("powercontext_langgraph")

from powercontext_langgraph import PowerContextRecall, PowerContextScope, powercontext_remember

SCOPE_ID = "project:langgraph-e2e"
AUTH_TOKEN = "langgraph-e2e-token"  # noqa: S105 - non-secret test credential.
MEMORY_TEXT = "Adopt hexagonal architecture for the payment gateway."
UNTRUSTED_LABEL = "untrusted historical evidence"
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "integrations" / "langgraph" / "examples"


async def _remember_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
    # The tool resolves its scope and connection from the graph runtime, so no client is threaded through state.
    saved = await powercontext_remember.ainvoke({"text": MEMORY_TEXT, "kind": "decision", "reason": "e2e seed"})
    return {"messages": [AIMessage(content=saved)]}


def _echo_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
    return {"messages": [AIMessage(content="acknowledged")]}


def _build_graph():
    builder = StateGraph(MessagesState, context_schema=PowerContextScope)
    builder.add_node("remember", _remember_node)
    builder.add_node("recall", PowerContextRecall())
    builder.add_node("model", _echo_node)
    builder.add_edge(START, "remember")
    builder.add_edge("remember", "recall")
    builder.add_edge("recall", "model")
    builder.add_edge("model", END)
    return builder.compile()


@pytest.mark.parametrize("authentication_enabled", [False, True], ids=["public", "authenticated"])
def test_langgraph_write_then_recall_over_real_http(tmp_path: Path, authentication_enabled: bool) -> None:
    app = create_server_app(
        settings=ServerSettings(
            auth=BearerAuthConfig(
                enabled=authentication_enabled,
                token=SecretStr(AUTH_TOKEN) if authentication_enabled else None,
            ),
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            inference=InferenceConfig(generation_model="test"),
            mcp=McpConfig(enabled=False),
        )
    )

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    host, port = listener.getsockname()
    base_url = f"http://{host}:{port}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        _wait_until_started(server, thread)
        graph = _build_graph()
        scope = PowerContextScope(
            scope_id=SCOPE_ID,
            base_url=base_url,
            token=AUTH_TOKEN if authentication_enabled else None,
        )

        result = asyncio.run(
            graph.ainvoke(
                {"messages": [HumanMessage(content="What architecture should the payment gateway use?")]},
                context=scope,
            )
        )

        messages = result["messages"]
        system_texts = [message.text for message in messages if message.type == "system"]
        assert len(system_texts) == 1
        assert UNTRUSTED_LABEL in system_texts[0]
        assert MEMORY_TEXT in system_texts[0]

        # The bare token authenticates the write and read, but must never surface in agent-visible message content.
        for message in messages:
            assert AUTH_TOKEN not in message.text
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()


def _wait_until_started(server: uvicorn.Server, thread: threading.Thread) -> None:
    deadline = time.monotonic() + 10
    while thread.is_alive() and not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started


def test_inspect_recall_example_runs(capsys: pytest.CaptureFixture[str]) -> None:
    """Run the shipped key-free example end-to-end so it cannot rot as the adapter evolves.

    The example is documentation, kept beside the library and excluded from the wheel, so it has no unit-test
    home. Executing its own ``main`` against its own throwaway Server here guarantees the documented
    write-then-recall path keeps working without duplicating the example's logic.
    """

    sys.path.insert(0, str(EXAMPLES_DIR))
    try:
        example = importlib.import_module("inspect_recall")
        seed_text = example.SEED_TEXT
        with example.local_powercontext_server() as base_url:
            asyncio.run(example.main(base_url))
    finally:
        sys.path.remove(str(EXAMPLES_DIR))
        sys.modules.pop("inspect_recall", None)
        sys.modules.pop("_local_server", None)

    printed = capsys.readouterr().out
    assert UNTRUSTED_LABEL in printed
    assert seed_text in printed
