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
            if "delta" in normalized:
                # Close to gamma, but never an exact match.
                return (0.0, 0.1, 1.0)
            return (0.0, 1.0, 0.0)

        vectors = tuple(vector(text) for text in texts)
        return EmbeddingResult(vectors=vectors)


def test_sqlite_vec_supports_vector_and_hybrid_search(tmp_path) -> None:
    async def scenario() -> None:
        model = _KeywordEmbeddingModel()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
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


def test_sqlite_vec_keeps_one_embedding_per_live_entry_across_appends(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
        async with open_builtin_contexts(
            BuiltinConfig(database=config),
            embedding_model=_KeywordEmbeddingModel(),
        ) as contexts:
            memory_service = (await contexts.get("project")).artifacts.memory
            memory = await memory_service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Gamma semantic record."),),
                mode="append",
            )
            for step in range(4):
                memory = await memory_service.remember(
                    memory=memory,
                    entries=(MemoryEntryInput(kind="fact", text=f"Alpha record {step}."),),
                    mode="append",
                )

            async with contexts.database.transaction() as connection:
                metadata = await connection.exec_driver_sql("SELECT count(*) FROM pc_memory_vector_entries")
                vectors = await connection.exec_driver_sql("SELECT count(*) FROM pc_memory_entry_vec")
                assert (metadata.scalar(), vectors.scalar()) == (5, 5)

    asyncio.run(scenario())


def test_sqlite_vec_search_is_unaffected_by_writes_in_other_scopes(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
        async with open_builtin_contexts(
            BuiltinConfig(database=config),
            embedding_model=_KeywordEmbeddingModel(),
        ) as contexts:
            quiet = (await contexts.get("quiet")).artifacts.memory
            busy = (await contexts.get("busy")).artifacts.memory
            target = await quiet.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Delta semantic record."),),
                mode="append",
            )
            assert target is not None
            churned = await busy.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Gamma one."),
                    MemoryEntryInput(kind="fact", text="Gamma two."),
                ),
                mode="append",
            )
            for step in range(4):
                churned = await busy.remember(
                    memory=churned,
                    entries=(MemoryEntryInput(kind="fact", text=f"Alpha record {step}."),),
                    mode="append",
                )

            result = await quiet.search("gamma", memories=(target,), mode="vector")

            assert [hit.text for hit in result.hits] == ["Delta semantic record."]

    asyncio.run(scenario())
