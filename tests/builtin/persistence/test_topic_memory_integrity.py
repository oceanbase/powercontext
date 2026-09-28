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

"""Statement-consistent integrity checks on SQLite and opt-in OceanBase/seekdb."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemory,
    TopicMemoryContent,
    TopicMemoryDraft,
    TopicMemoryStorageInvariantError,
    prepare_topic_memory_projection,
)
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.oceanbase.topic_memory_index import (
    OceanBaseTopicMemoryFTSIndex,
    OceanBaseTopicMemoryVectorIndex,
)
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.topic_memory_index import (
    SQLiteTopicMemoryFTSIndex,
    SQLiteTopicMemoryVectorIndex,
)
from powercontext.builtin.persistence.tables import BUILTIN_TABLES, TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.persistence.topic_memory_index import CompositeTopicMemoryIndex

_LIVE_URL = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
_OB = pytest.param("oceanbase", marks=pytest.mark.skipif(not _LIVE_URL, reason="requires test OceanBase URL"))
_SEEKDB = pytest.param(
    "seekdb",
    marks=pytest.mark.skipif(
        os.environ.get("POWERCONTEXT_TEST_SEEKDB") != "1", reason="requires POWERCONTEXT_TEST_SEEKDB=1"
    ),
)
_PROFILE = EmbeddingProfile(profile_id="integrity", model="test", dimension=3, distance="l2", normalization="unit")
_VECTOR = (1.0, 0.0, 0.0)


@dataclass
class _Store:
    database: AsyncDatabase
    reader: AsyncDatabase
    repository: TopicMemoryRepository
    first: TopicMemory
    index: CompositeTopicMemoryIndex
    seekdb_config: SeekDBConfig | None = None

    async def publish(self, connection: AsyncConnection, previous: TopicMemory | None, scope: str) -> TopicMemory:
        content = TopicMemoryContent(title="Recovery", summary="Durable evidence", detail="Evidence " * 400)
        projection = prepare_topic_memory_projection(content)
        if self.index.capabilities.vector:
            projection = prepare_topic_memory_projection(
                content,
                topic_embedding=_VECTOR,
                chunk_embeddings=(_VECTOR,) * len(projection.chunks),
                embedding_profile=_PROFILE,
            )
        draft = TopicMemoryDraft(content=content)
        if previous is None:
            result = await self.repository.publish_create(connection, scope, "shared-id", draft, projection)
        else:
            result = await self.repository.publish_revision(connection, scope, previous, draft, projection)
        return result.topic


@asynccontextmanager
async def _open_store(tmp_path: Path, backend: str, *, vector: bool) -> AsyncIterator[_Store]:
    async with AsyncExitStack() as stack:
        seekdb_config = None
        if backend == "sqlite":
            index = CompositeTopicMemoryIndex(
                SQLiteTopicMemoryFTSIndex(), *((SQLiteTopicMemoryVectorIndex(_PROFILE),) if vector else ())
            )
            config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'integrity.db'}")
            profile = await stack.enter_async_context(
                SQLiteProfile.open(config, tables=BUILTIN_TABLES + index.tables, load_vector_extension=vector)
            )
            reader = await stack.enter_async_context(
                SQLiteProfile.open(config, tables=BUILTIN_TABLES + index.tables, load_vector_extension=vector)
            )
        elif backend == "seekdb":
            pytest.importorskip("pylibseekdb")
            # Unix socket paths must fit sockaddr_un even in deeply nested CI
            # workspaces. Both profiles own separate engines and native handles.
            directory = stack.enter_context(TemporaryDirectory(prefix="pc-integrity-", dir="/tmp"))
            seekdb_config = SeekDBConfig(path=Path(directory) / "db")
            index = CompositeTopicMemoryIndex(
                OceanBaseTopicMemoryFTSIndex(), *((OceanBaseTopicMemoryVectorIndex(_PROFILE),) if vector else ())
            )
            profile = await stack.enter_async_context(
                SeekDBProfile.open(seekdb_config, tables=BUILTIN_TABLES + index.tables)
            )
            reader = await stack.enter_async_context(
                SeekDBProfile.open(seekdb_config, tables=BUILTIN_TABLES + index.tables)
            )
        else:
            assert _LIVE_URL is not None
            server = await stack.enter_async_context(
                OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(_LIVE_URL)), tables=())
            )
            database_name = f"pc_p2_{uuid4().hex}"
            async with server.database.transaction() as connection:
                await connection.exec_driver_sql(f"CREATE DATABASE `{database_name}`")

            async def drop_database() -> None:
                async with server.database.transaction() as connection:
                    await connection.exec_driver_sql(f"DROP DATABASE `{database_name}`")

            stack.push_async_callback(drop_database)
            url = make_url(_LIVE_URL).set(database=database_name).render_as_string(hide_password=False)
            index = CompositeTopicMemoryIndex(
                OceanBaseTopicMemoryFTSIndex(), *((OceanBaseTopicMemoryVectorIndex(_PROFILE),) if vector else ())
            )
            ob_config = OceanBaseConfig(url=SecretStr(url))
            profile = await stack.enter_async_context(
                OceanBaseProfile.open(ob_config, tables=BUILTIN_TABLES + index.tables)
            )
            reader = await stack.enter_async_context(
                OceanBaseProfile.open(ob_config, tables=BUILTIN_TABLES + index.tables)
            )
        repository = TopicMemoryRepository(index=index)
        assert profile.database.engine is not reader.database.engine
        store = _Store(profile.database, reader.database, repository, cast(TopicMemory, None), index, seekdb_config)
        async with profile.database.transaction() as connection:
            await repository.initialize(connection)
        async with profile.database.transaction() as connection:
            store.first = await store.publish(connection, None, "scope-a")
            await store.publish(connection, None, "scope-b")
        yield store


class _ReadBarrier:
    """Control commit timing, without mocking any SQL result or database write."""

    def __init__(self, connection: AsyncConnection, marker: str, timing: str) -> None:
        self.connection = connection
        self.marker = marker
        self.timing = timing
        self.reached = asyncio.Event()
        self.committed = asyncio.Event()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        pause = not self.reached.is_set() and self.marker in str(statement.compile(dialect=self.connection.dialect))
        if pause and self.timing == "before":
            self.reached.set()
            await self.committed.wait()
        result = await self.connection.execute(statement, *args, **kwargs)
        if pause and self.timing == "after":
            self.reached.set()
            await self.committed.wait()
        return result


async def _concurrent_revision(store: _Store, *, timing: str, vectors_only: bool = False) -> None:
    async with store.reader.transaction() as connection:
        if connection.dialect.name == "mysql":
            await connection.exec_driver_sql("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
            assert await connection.scalar(select(1)) == 1
            assert (await connection.exec_driver_sql("SELECT @@tx_isolation, @@autocommit")).one() == (
                "READ-COMMITTED",
                0,
            )
        else:
            await connection.exec_driver_sql("PRAGMA query_only = ON")
        marker = "FROM pc_topic_memory_active_topics AS active" if vectors_only else "AS active_revision"
        barrier = _ReadBarrier(connection, marker, timing)

        async def publish() -> None:
            await barrier.reached.wait()
            async with store.database.transaction() as writer:
                await store.publish(writer, store.first, "scope-a")
            barrier.committed.set()

        async def check() -> None:
            wrapped = cast(AsyncConnection, barrier)
            if vectors_only:
                await store.index.validate_current(wrapped)
            else:
                await store.repository.initialize(wrapped, configure_retrieval_shape=False)

        # SQLite's vector initialization performs probe writes and therefore
        # serializes publication. Direct validation exercises the unlocked
        # vector read boundary; full FTS-only initialization covers bootstrap.
        async with asyncio.timeout(20), asyncio.TaskGroup() as tasks:
            tasks.create_task(publish())
            tasks.create_task(check())
        assert barrier.committed.is_set()
        if connection.dialect.name == "sqlite":
            await connection.exec_driver_sql("PRAGMA query_only = OFF")
    async with store.reader.transaction() as connection:
        await store.repository.initialize(connection, configure_retrieval_shape=False)
        a = await store.repository.browse_current(connection, "scope-a", limit=10)
        b = await store.repository.browse_current(connection, "scope-b", limit=10)
        assert [item.artifact_ref.revision for item in a] == [2]
        assert [item.artifact_ref.revision for item in b] == [1]


@pytest.mark.parametrize("backend", ["sqlite", _OB, _SEEKDB])
@pytest.mark.parametrize("timing", ["before", "after"])
def test_worker_integrity_accepts_another_scopes_concurrent_revision(tmp_path: Path, backend: str, timing: str) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path, backend, vector=False) as store:
            await _concurrent_revision(store, timing=timing)

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["sqlite", _OB, _SEEKDB])
@pytest.mark.parametrize("timing", ["before", "after"])
def test_current_vector_integrity_uses_one_revision(tmp_path: Path, backend: str, timing: str) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path, backend, vector=True) as store:
            await _concurrent_revision(store, timing=timing, vectors_only=True)

    asyncio.run(scenario())


@pytest.mark.skipif(not _LIVE_URL, reason="requires test OceanBase URL")
@pytest.mark.parametrize("timing", ["before", "after"])
def test_oceanbase_hybrid_bootstrap_accepts_concurrent_revision(tmp_path: Path, timing: str) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path, "oceanbase", vector=True) as store:
            await _concurrent_revision(store, timing=timing)

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["sqlite", _OB, _SEEKDB])
def test_worker_integrity_still_rejects_missing_active_chunks(tmp_path: Path, backend: str) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path, backend, vector=False) as store:
            async with store.database.transaction() as connection:
                await connection.execute(
                    delete(TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE).where(
                        TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.scope_id == "scope-a"
                    )
                )
            async with store.reader.transaction() as connection:
                with pytest.raises(TopicMemoryStorageInvariantError, match="missing-active-chunks"):
                    await store.repository.initialize(connection, configure_retrieval_shape=False)

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["sqlite", _OB, _SEEKDB])
@pytest.mark.parametrize(
    "corruption",
    ["topic-missing", "chunk-missing", "extra", "ordinal", "topic-profile", "chunk-profile", "extra-profile"],
)
def test_current_vector_integrity_rejects_real_corruption(tmp_path: Path, backend: str, corruption: str) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path, backend, vector=True) as store:
            topics, chunks = store.index.indexes[1].tables
            async with store.database.transaction() as connection:
                table = topics if corruption.startswith("topic") else chunks
                target = table.c.scope_id == "scope-a"
                if "missing" in corruption:
                    await connection.execute(delete(table).where(target))
                elif corruption in {"topic-profile", "chunk-profile"}:
                    await connection.execute(update(table).where(target).values(profile_fingerprint="invalid"))
                elif corruption == "ordinal":
                    await connection.execute(
                        update(chunks).where(target, chunks.c.chunk_ordinal == 0).values(chunk_ordinal=99)
                    )
                else:
                    row = dict((await connection.execute(select(chunks).where(target).limit(1))).mappings().one())
                    row.pop("vector_id", None)
                    row["chunk_ordinal"] = 99
                    if corruption == "extra-profile":
                        row["profile_fingerprint"] = "invalid"
                    if "embedding" in row:
                        row["embedding"] = _VECTOR
                    await connection.execute(insert(chunks).values(**row))
            async with store.reader.transaction() as connection:
                with pytest.raises(TopicMemoryStorageInvariantError, match="incomplete-vector") as caught:
                    await store.repository.initialize(connection, configure_retrieval_shape=False)
                assert caught.value.identity == ("scope-a", store.first.as_ref())

    asyncio.run(scenario())


@pytest.mark.parametrize("channel", ["topic", "chunk"])
def test_sqlite_integrity_rejects_missing_physical_vectors(tmp_path: Path, channel: str) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path, "sqlite", vector=True) as store:
            async with store.database.transaction() as connection:
                statements = {
                    "topic": "DELETE FROM pc_topic_memory_topic_vec WHERE scope_id = 'scope-a'",
                    "chunk": "DELETE FROM pc_topic_memory_chunk_vec WHERE scope_id = 'scope-a'",
                }
                await connection.exec_driver_sql(statements[channel])
            async with store.reader.transaction() as connection:
                with pytest.raises(TopicMemoryStorageInvariantError, match="incomplete-vector"):
                    await store.repository.initialize(connection, configure_retrieval_shape=False)

    asyncio.run(scenario())
