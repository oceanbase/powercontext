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
import os
from uuid import uuid4

import pytest
from pydantic import SecretStr

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts

OCEANBASE_URL = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
PROFILE = EmbeddingProfile(
    profile_id="vector-round-trip-test-v1",
    model="test",
    dimension=3,
    distance="l2",
    normalization="unit",
)


class _DenseEmbeddingModel:
    profile = PROFILE

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        vector = (0.2407121489724894, -0.9705965492093231, 0.123456789)
        return EmbeddingResult(vectors=(vector,) * len(texts))


@pytest.mark.skipif(
    not OCEANBASE_URL,
    reason="set POWERCONTEXT_TEST_OCEANBASE_URL to a dedicated OceanBase MySQL-mode test database",
)
def test_oceanbase_memory_append_reuses_a_hydrated_dense_embedding() -> None:
    async def scenario() -> None:
        assert OCEANBASE_URL is not None
        config = BuiltinConfig(database=OceanBaseConfig(url=SecretStr(OCEANBASE_URL)))
        scope_id = f"vector-round-trip-{uuid4()}"
        model = _DenseEmbeddingModel()
        async with open_builtin_contexts(config, embedding_model=model) as contexts:
            service = (await contexts.get(scope_id)).artifacts.memory
            first = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="OceanBase stores dense embeddings."),),
                mode="append",
            )
            assert first is not None

        async with open_builtin_contexts(config, embedding_model=model) as contexts:
            service = (await contexts.get(scope_id)).artifacts.memory
            second = await service.remember(
                memory=first,
                entries=(MemoryEntryInput(kind="fact", text="Hydrated embeddings remain reusable."),),
                mode="append",
            )

            assert second is not None
            assert second.revision == 2
            assert {entry.text for entry in await service.entries(second)} == {
                "OceanBase stores dense embeddings.",
                "Hydrated embeddings remain reusable.",
            }

    asyncio.run(scenario())
