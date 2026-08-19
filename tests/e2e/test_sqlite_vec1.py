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
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.memory import (
    EmbeddingProfile,
    MemoryEntryInput,
)
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts

PROFILE = EmbeddingProfile(
    profile_id="test-v1",
    model="test",
    dimension=3,
    distance="l2",
    normalization="unit",
)


class _KeywordEmbeddingModel:
    profile = PROFILE

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        def vector(text: str) -> tuple[float, float, float]:
            normalized = text.casefold()
            if "alpha" in normalized:
                return (1.0, 0.0, 0.0)
            if "gamma" in normalized:
                return (0.0, 0.0, 1.0)
            return (0.0, 1.0, 0.0)

        vectors = tuple(vector(text) for text in texts)
        return EmbeddingResult(vectors=vectors)


def _vec1_extension() -> Path:
    configured = os.environ.get("POWERCONTEXT_VEC1_EXTENSION")
    if configured is None:
        pytest.skip("POWERCONTEXT_VEC1_EXTENSION is not configured")
    extension = Path(configured)
    if not extension.is_file():
        pytest.skip("POWERCONTEXT_VEC1_EXTENSION does not point to a file")
    return extension


def test_sqlite_vec1_supports_vector_and_hybrid_search(tmp_path) -> None:
    async def scenario() -> None:
        model = _KeywordEmbeddingModel()
        config = SQLiteConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}",
            vec1_extension=_vec1_extension(),
        )
        async with open_builtin_contexts(
            BuiltinConfig(database=config),
            embedding_model=model,
        ) as contexts:
            memory_service = (await contexts.get("project")).artifacts.memory
            memory = await memory_service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Alpha semantic record."),
                    MemoryEntryInput(kind="fact", text="Beta semantic record."),
                ),
                mode="append",
            )
            assert memory is not None
            revised = await memory_service.remember(
                memory=memory,
                entries=(MemoryEntryInput(kind="fact", text="Gamma semantic record."),),
                mode="append",
            )
            assert revised is not None
            vector = await memory_service.search("alpha", memories=(revised,), mode="vector")
            hybrid = await memory_service.search("alpha", memories=(revised,), mode="hybrid")
            gamma = await memory_service.search("gamma", memories=(revised,), mode="vector")

            assert vector.hits[0].text == "Alpha semantic record."
            assert hybrid.hits[0].matched_by == ("fts", "vector")
            assert gamma.hits[0].text == "Gamma semantic record."

    asyncio.run(scenario())
