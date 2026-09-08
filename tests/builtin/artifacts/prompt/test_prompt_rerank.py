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

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from typing_extensions import override

from powercontext.builtin.artifacts.memory import (
    LLMMemoryReranker,
    MemoryEntryInput,
    MemoryRerankInput,
    MemoryRerankOutput,
    MemorySearchChannels,
    MemorySearchRequest,
    MemoryService,
)
from powercontext.builtin.artifacts.prompt import Prompt, PromptContent, PromptRegistry
from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions
from powercontext.builtin.artifacts.prompt.service import PromptService, ScopedPrompts
from powercontext.builtin.inference.pydantic_ai import PydanticAIStructuredGenerator
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


def test_search_freezes_the_rerank_prompt_before_reading_coarse_candidates() -> None:
    async def scenario() -> None:
        def prompt(revision: int) -> Prompt:
            return Prompt(
                artifact_id="memory.rerank",
                revision=revision,
                content=PromptContent(
                    schema_version="powercontext.prompt.v1",
                    mode="custom",
                    instructions=f"RERANK_REVISION_{revision}: Select the most relevant supplied memories.",
                    demonstrations=(),
                ),
            )

        head = prompt(1)
        observed: list[str] = []

        async def read(scope: str, key: str) -> Prompt:
            return head

        async def respond(messages, info) -> ModelResponse:
            observed.append(info.instructions)
            return ModelResponse(parts=[TextPart('{"selected_ranks":[1]}')])

        class AdvancingBackend(RelationalMemoryBackend):
            @override
            async def search(self, request: MemorySearchRequest, /) -> MemorySearchChannels:
                nonlocal head
                # Simulate another operator saving while this search is reading its candidate pool.
                head = prompt(2)
                return await super().search(request)

        registry = PromptRegistry(builtin_prompt_definitions(), supported=frozenset({"memory.rerank"}))
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(respond),
            instructions=registry.get("memory.rerank").default_instructions,
            input_type=MemoryRerankInput,
            output_type=MemoryRerankOutput,
            prompt_key="memory.rerank",
        )
        async with open_builtin_contexts(BuiltinConfig()) as contexts:
            existing = (await contexts.get("rerank-freeze")).artifacts.memory
            memory = await existing.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Project uses SQLite."),),
                mode="append",
            )
            assert memory is not None
            service = MemoryService(
                backend=AdvancingBackend(
                    database=contexts.database,
                    scope_id="rerank-freeze",
                    artifacts=contexts.repositories.artifacts,
                    index=contexts.index,
                ),
                reranker=LLMMemoryReranker(generator),
                prompt_context=ScopedPrompts(PromptService(registry, read), "rerank-freeze"),
            )
            for _ in range(2):
                assert (await service.search("project", memories=(memory,), mode="fts", limit=1)).hits

        assert "RERANK_REVISION_1" in observed[0] and "RERANK_REVISION_2" not in observed[0]
        assert "RERANK_REVISION_2" in observed[1]

    asyncio.run(scenario())
