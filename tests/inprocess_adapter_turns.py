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

"""Run one automatic turn through each in-process Python adapter against a recording Server.

Hermes, LangChain, LangGraph, and Pydantic AI run inside their host's process rather than as hooks. Each turn here goes
through the adapter's public host entry point and its real HTTP client, and the Server records what reached it: the
automatic recall query and any captured Source content.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from powercontext.http import (
    CaptureContentSourceResponse,
    CaptureStatus,
    FlushMemoryResponse,
    FlushStatus,
    PreparedContext,
    ScopeDescriptor,
    SourceReference,
)

ADAPTERS = ("hermes", "langchain", "langgraph", "pydantic-ai")
RECALLED = "safe historical context"
ANSWER = "Acknowledged."
_SCOPE_ID = "scp_00000000000000000000000000"
_HERMES_ROOT = Path(__file__).resolve().parents[1] / "integrations" / "hermes"
_ENVIRONMENT_PREFIXES = (
    "POWERCONTEXT_HERMES_",
    "POWERCONTEXT_LANGCHAIN_",
    "POWERCONTEXT_LANGGRAPH_",
    "POWERCONTEXT_PYDANTIC_AI_",
)


@dataclass
class Turn:
    """What one automatic turn sent to the Server."""

    queries: list[str] = field(default_factory=list)
    captures: list[str] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)


def _json(model: Any) -> bytes:
    return json.dumps(model.model_dump(mode="json", by_alias=True, exclude_none=False)).encode()


@contextmanager
def _recording_server(turn: Turn) -> Iterator[str]:
    position = 0

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal position
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            turn.requests.append(value)
            status = 200
            if self.path == "/v1/scope-bindings/resolve":
                body = _json(
                    ScopeDescriptor(
                        scope_id=value.get("explicit_scope_id") or _SCOPE_ID,
                        title="Recording Scope",
                        summary="Scope returned by the recording Server.",
                        context_references=[],
                        external_references=[],
                        version=1,
                    )
                )
            elif self.path == "/v1/context/prepare":
                turn.queries.append(value["query"])
                body = _json(
                    PreparedContext.model_validate({
                        "schema": "powercontext.prepared-context.v1",
                        "status": "ready",
                        "content": RECALLED,
                        "content_bytes": len(RECALLED),
                    })
                )
            elif self.path == "/v1/sources/content":
                turn.captures.append(value["content"])
                position += 1
                status = 202
                body = _json(
                    CaptureContentSourceResponse(
                        status=CaptureStatus.ACCEPTED,
                        source=SourceReference(name="content", source_id=value["source_id"]),
                        position=position,
                    )
                )
            elif self.path == "/v1/memory/flush":
                body = _json(
                    FlushMemoryResponse(
                        status=FlushStatus.PROCESSED,
                        previous_cursor=0,
                        current_cursor=position,
                        high_watermark=position,
                        processed_source_count=position,
                        memory=None,
                    )
                )
            else:
                self.send_error(404)
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *_args: object) -> None:  # noqa: A002 - BaseHTTPRequestHandler override.
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _hermes(url: str, prompt: str, *, token: str | None, capture: bool, home: Path) -> None:
    names = [name for name in sys.modules if name == "plugins" or name.startswith("plugins.")]
    previous = {name: sys.modules.pop(name) for name in names}
    sys.path.insert(0, str(_HERMES_ROOT))
    try:
        plugin = importlib.import_module("plugins.powercontext")
        config: dict[str, Any] = {"base_url": url, "capture_turns": capture}
        if token is not None:
            config["token"] = token
        provider = plugin.PowerContextMemoryProvider(config)
        provider.initialize("session-1", hermes_home=str(home), agent_identity="coder", user_id="user-1")
        try:
            assert RECALLED in provider.prefetch(prompt, session_id="session-1")
            provider.sync_turn(prompt, ANSWER, session_id="session-1")
        finally:
            provider.shutdown()
    finally:
        sys.path.remove(str(_HERMES_ROOT))
        for name in [name for name in sys.modules if name == "plugins" or name.startswith("plugins.")]:
            del sys.modules[name]
        sys.modules.update(previous)


def _chat_model() -> Any:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from pydantic import Field

    class Model(BaseChatModel):
        inputs: list[Any] = Field(default_factory=list)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # type: ignore[no-untyped-def]
            self.inputs.append(list(messages))
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=ANSWER))])

        @property
        def _llm_type(self) -> str:
            return "recording"

    return Model()


def _system_texts(messages: list[Any]) -> str:
    return "\n".join(message.text for message in messages if message.type == "system")


def _langchain(url: str, prompt: str, *, token: str | None, capture: bool, home: Path) -> None:
    del home
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage
    from powercontext_langchain import PowerContextMiddleware, PowerContextScope

    model = _chat_model()
    agent = create_agent(
        model,
        tools=[],
        middleware=[PowerContextMiddleware(auto_capture=capture)],
        context_schema=PowerContextScope,
    )
    asyncio.run(
        agent.ainvoke(
            {"messages": [HumanMessage(content=prompt)]}, context=PowerContextScope(base_url=url, token=token)
        )
    )
    assert RECALLED in _system_texts(model.inputs[-1])


def _langgraph(url: str, prompt: str, *, token: str | None, capture: bool, home: Path) -> None:
    del capture, home  # The LangGraph adapter captures only through explicit tools.
    from langchain_core.messages import HumanMessage
    from langgraph.prebuilt import create_react_agent  # ty: ignore[deprecated]
    from powercontext_langgraph import PowerContextRecall, PowerContextScope

    model = _chat_model()
    # The adapter documents create_react_agent's pre_model_hook, which langchain.agents.create_agent does not offer.
    agent: Any = create_react_agent(  # ty: ignore[deprecated]
        model, tools=[], pre_model_hook=PowerContextRecall(), context_schema=PowerContextScope
    )
    asyncio.run(
        agent.ainvoke(
            {"messages": [HumanMessage(content=prompt)]}, context=PowerContextScope(base_url=url, token=token)
        )
    )
    assert RECALLED in _system_texts(model.inputs[-1])


def _pydantic_ai(url: str, prompt: str, *, token: str | None, capture: bool, home: Path) -> None:
    del home
    from powercontext_pydantic_ai import PowerContext, PowerContextSettings
    from pydantic import SecretStr
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, SystemPromptPart, TextPart
    from pydantic_ai.models.function import FunctionModel

    seen: list[str] = []

    async def respond(messages: list[Any], _info: Any) -> ModelResponse:
        seen.extend(
            part.content for message in messages for part in message.parts if isinstance(part, SystemPromptPart)
        )
        return ModelResponse(parts=[TextPart(ANSWER)])

    secret = None if token is None else SecretStr(token)
    settings = PowerContextSettings(base_url=url, token=secret, capture_events=capture)
    agent = Agent(FunctionModel(respond), capabilities=[PowerContext(settings=settings)])
    asyncio.run(agent.run(prompt))
    assert any(RECALLED in text for text in seen)


_RUNNERS: dict[str, Callable[..., None]] = {
    "hermes": _hermes,
    "langchain": _langchain,
    "langgraph": _langgraph,
    "pydantic-ai": _pydantic_ai,
}


def run_turn(
    adapter: str,
    prompt: str,
    *,
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    token: str | None = None,
    capture: bool = False,
) -> Turn:
    """Submit ``prompt`` as one user turn and return what the adapter sent to the Server."""

    if adapter != "hermes":
        pytest.importorskip(f"powercontext_{adapter.replace('-', '_')}")
    for name in list(os.environ):
        if name.startswith(_ENVIRONMENT_PREFIXES):
            monkeypatch.delenv(name)
    turn = Turn()
    with _recording_server(turn) as url:
        _RUNNERS[adapter](url, prompt, token=token, capture=capture, home=home)
    return turn
