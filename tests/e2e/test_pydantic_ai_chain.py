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

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import powercontext_pydantic_ai.toolset as toolset_module
import pytest
from powercontext_pydantic_ai import PowerContext, PowerContextSettings
from powercontext_pydantic_ai.capability import CONTEXT_MARKER
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelResponse, SystemPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.builtin.sources import ContentSource
from powercontext.client import PowerContextClient
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


class ToolResultCandidatePipeline:
    """Activate only completed tool results so the chain proves that capture path."""

    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="agent-trajectory",
                text=source.content,
                sources=(source,),
                reason="captured Pydantic AI tool result",
            )
            for source in request.sources
            if isinstance(source, ContentSource) and source.metadata.get("event") == "tool_result"
        )


def test_pydantic_ai_capture_checkpoint_recall_and_search_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope_id = "pydantic-ai-chain"
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pydantic-ai.db'}"),
            inference=InferenceConfig(),
            mcp=McpConfig(enabled=False),
        ),
        candidate_pipeline=ToolResultCandidatePipeline(),
    )
    recalled_contexts: list[str] = []
    search_results: list[dict[str, Any]] = []

    async def produce_evidence(ctx: RunContext[object]) -> dict[str, str]:
        del ctx
        return {"finding": "checkpoint-evidence is available after the tool completes"}

    async def respond(messages, _info):
        latest_returns = [part for part in messages[-1].parts if isinstance(part, ToolReturnPart)]
        if not latest_returns:
            return ModelResponse(parts=[ToolCallPart("produce_evidence", {}, "produce-1")])
        latest_return = latest_returns[-1]
        if latest_return.tool_name == "produce_evidence":
            contexts = [
                part.content
                for message in messages
                for part in message.parts
                if isinstance(part, SystemPromptPart) and CONTEXT_MARKER in part.content
            ]
            assert contexts and "checkpoint-evidence" in contexts[-1]
            recalled_contexts.append(contexts[-1])
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "powercontext_search",
                        {"query": "checkpoint-evidence", "mode": "fts"},
                        "search-1",
                    )
                ]
            )
        assert latest_return.tool_name == "powercontext_search"
        assert isinstance(latest_return.content, dict)
        search_results.append(latest_return.content)
        return ModelResponse(parts=[TextPart("capture, checkpoint, recall, and search completed")])

    async def scenario() -> str:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):

            class AsgiPowerContextClient(PowerContextClient):
                def __init__(
                    self,
                    base_url: str,
                    *,
                    token: str | None = None,
                    timeout: float = 10,
                ) -> None:
                    del timeout
                    super().__init__(base_url, token=token, http_client=transport)

            monkeypatch.setattr(toolset_module, "PowerContextClient", AsgiPowerContextClient)
            settings = PowerContextSettings(
                base_url="http://testserver",
                capture_events=True,
                capture_checkpoint_every=3,
            )
            agent: Agent[object, str] = Agent(
                FunctionModel(respond),
                output_type=str,
                deps_type=object,
                tools=[produce_evidence],
                capabilities=[PowerContext[object](settings=settings, scope_id=scope_id)],
            )
            return (await agent.run("Find checkpoint-evidence with a tool, then recall and search it.")).output

    assert asyncio.run(scenario()) == "capture, checkpoint, recall, and search completed"
    assert recalled_contexts
    assert len(search_results) == 1
    assert search_results[0]["mode"] == "fts"
    assert search_results[0]["hits"]
    assert "checkpoint-evidence" in search_results[0]["hits"][0]["text"]
    assert search_results[0]["hits"][0]["citation"]["memory_ref"]["revision"] >= 1
