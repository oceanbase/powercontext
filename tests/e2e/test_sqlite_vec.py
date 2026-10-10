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
from sqlalchemy import Engine, event, func, select, update

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexError
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
        async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=model) as contexts:
            await contexts.get("project")
            memory = contexts.atomic_memory.for_scope("project")
            await contexts.records.create_atomic_memories(
                "project",
                (
                    {"kind": "fact", "text": "Alpha semantic record."},
                    {"kind": "fact", "text": "Beta semantic record."},
                ),
            )
            await contexts.records.create_atomic_memories(
                "project", ({"kind": "fact", "text": "Gamma semantic record."},)
            )
            vector = await memory.search("alpha", mode="vector")
            hybrid = await memory.search("alpha", mode="hybrid")
            gamma = await memory.search("gamma", mode="vector")
            assert vector.hits[0].text == "Alpha semantic record."
            assert hybrid.hits[0].matched_by == ("text", "vector")
            assert gamma.hits[0].text == "Gamma semantic record."

    asyncio.run(scenario())


def test_sqlite_vector_completeness_uses_a_fixed_sql_budget(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'sql-budget.db'}")
        async with open_builtin_contexts(
            BuiltinConfig(database=config), embedding_model=_KeywordEmbeddingModel()
        ) as contexts:
            await contexts.get("project")
            memory = contexts.atomic_memory.for_scope("project")
            await contexts.records.create_atomic_memories(
                "project", tuple({"kind": "fact", "text": f"Alpha record {index}."} for index in range(100))
            )
            with _sql_counter() as statements:
                result = await memory.search("alpha", mode="vector")
            assert result.hits
            assert len(statements) < 40

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "corruption",
    ["missing-vector", "wrong-content-hash", "wrong-embedding-hash", "wrong-revision"],
)
def test_sqlite_vector_completeness_rejects_corrupt_projection(tmp_path, corruption: str) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / f'{corruption}.db'}")
        async with open_builtin_contexts(
            BuiltinConfig(database=config), embedding_model=_KeywordEmbeddingModel()
        ) as contexts:
            await contexts.get("project")
            memory = contexts.atomic_memory.for_scope("project")
            (created,) = await contexts.records.create_atomic_memories(
                "project", ({"kind": "fact", "text": "Alpha semantic record."},)
            )
            table = contexts.atomic_memory.index.table
            async with contexts.database.transaction() as connection:
                values = (
                    {"embedding": None, "profile_fingerprint": None, "embedding_input_hash": None}
                    if corruption == "missing-vector"
                    else {"content_hash": "0" * 64}
                    if corruption == "wrong-content-hash"
                    else {"embedding_input_hash": "0" * 64}
                    if corruption == "wrong-embedding-hash"
                    else {"revision": created.revision + 1}
                )
                await connection.execute(update(table).values(**values))
            with pytest.raises(AtomicMemoryIndexError):
                await memory.search("alpha", mode="vector")

    asyncio.run(scenario())


def test_sqlite_vec_keeps_one_embedding_per_live_artifact_across_creates_and_revisions(tmp_path) -> None:
    from powercontext.builtin.records import ArtifactWrite

    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
        async with open_builtin_contexts(
            BuiltinConfig(database=config), embedding_model=_KeywordEmbeddingModel()
        ) as contexts:
            await contexts.get("project")
            (initial,) = await contexts.records.create_atomic_memories(
                "project", ({"kind": "fact", "text": "Gamma semantic record."},)
            )
            for step in range(4):
                await contexts.records.create_atomic_memories(
                    "project", ({"kind": "fact", "text": f"Alpha record {step}."},)
                )
            await contexts.records.replace_artifact(
                "project",
                "atomic-memory",
                initial.artifact_id,
                '"revision:1"',
                ArtifactWrite(content={"kind": "fact", "text": "Gamma revised semantic record."}),
            )
            table = contexts.atomic_memory.index.table
            async with contexts.database.transaction() as connection:
                metadata = await connection.scalar(select(func.count()).select_from(table))
                vectors = await connection.scalar(select(func.count(table.c.embedding)))
                assert (metadata, vectors) == (5, 5)
                rows = (await connection.execute(select(table))).mappings().all()
                assert all(len(row["embedding"]) == PROFILE.dimension * 4 for row in rows)
                assert {row["artifact_id"]: row["revision"] for row in rows}[initial.artifact_id] == 2

    asyncio.run(scenario())


def test_sqlite_vec_search_is_unaffected_by_writes_in_other_scopes(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
        async with open_builtin_contexts(
            BuiltinConfig(database=config), embedding_model=_KeywordEmbeddingModel()
        ) as contexts:
            await contexts.get("quiet")
            await contexts.get("busy")
            quiet = contexts.atomic_memory.for_scope("quiet")
            await contexts.records.create_atomic_memories(
                "quiet", ({"kind": "fact", "text": "Delta semantic record."},)
            )
            await contexts.records.create_atomic_memories(
                "busy",
                (
                    {"kind": "fact", "text": "Gamma one."},
                    {"kind": "fact", "text": "Gamma two."},
                ),
            )
            for step in range(4):
                await contexts.records.create_atomic_memories(
                    "busy", ({"kind": "fact", "text": f"Alpha record {step}."},)
                )
            result = await quiet.search("gamma", mode="vector")
            assert [hit.text for hit in result.hits] == ["Delta semantic record."]

    asyncio.run(scenario())
