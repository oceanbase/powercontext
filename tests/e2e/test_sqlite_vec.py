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
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import Engine, event, select, update

from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError, EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.sqlite.memory_index import SQLITE_MEMORY_VECTOR_ENTRIES_TABLE
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


@contextmanager
def _sql_counter() -> Iterator[list[str]]:
    statements: list[str] = []

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", record)


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


def test_sqlite_vector_completeness_uses_a_fixed_sql_budget(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'sql-budget.db'}")
        async with open_builtin_contexts(
            BuiltinConfig(database=config), embedding_model=_KeywordEmbeddingModel()
        ) as contexts:
            memory_service = (await contexts.get("project")).artifacts.memory
            memory = await memory_service.remember(
                memory=None,
                entries=tuple(MemoryEntryInput(kind="fact", text=f"Alpha record {index}.") for index in range(100)),
                mode="append",
            )
            assert memory is not None

            with _sql_counter() as statements:
                result = await memory_service.search("alpha", memories=(memory,), mode="vector")

            assert result.hits
            assert len(statements) < 40

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "corruption",
    ["missing-vector", "wrong-entry-content-hash", "wrong-embedding-hash", "wrong-revision"],
)
def test_sqlite_vector_completeness_rejects_corrupt_projection(tmp_path, corruption: str) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / f'{corruption}.db'}")
        async with open_builtin_contexts(
            BuiltinConfig(database=config), embedding_model=_KeywordEmbeddingModel()
        ) as contexts:
            memory_service = (await contexts.get("project")).artifacts.memory
            memory = await memory_service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Alpha semantic record."),),
                mode="append",
            )
            assert memory is not None
            async with contexts.database.transaction() as connection:
                vector_id = (
                    await connection.execute(select(SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.vector_id))
                ).scalar_one()
                if corruption == "missing-vector":
                    await connection.exec_driver_sql("DELETE FROM pc_memory_entry_vec WHERE rowid = ?", (vector_id,))
                else:
                    values = (
                        {"entry_content_hash": "0" * 64}
                        if corruption == "wrong-entry-content-hash"
                        else (
                            {"embedding_content_hash": "0" * 64}
                            if corruption == "wrong-embedding-hash"
                            else {"head_revision": memory.revision + 1}
                        )
                    )
                    await connection.execute(update(SQLITE_MEMORY_VECTOR_ENTRIES_TABLE).values(**values))

            with pytest.raises(CapabilityNotSupportedError):
                await memory_service.search("alpha", memories=(memory,), mode="vector")

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
