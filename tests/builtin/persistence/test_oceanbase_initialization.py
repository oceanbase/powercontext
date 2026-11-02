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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from pydantic import SecretStr
from pyobvector import VECTOR
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table, bindparam, func, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexError
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.oceanbase.experience_index import OceanBaseExperienceFTSIndex
from powercontext.builtin.persistence.oceanbase.topic_memory_index import (
    OceanBaseTopicMemoryFTSIndex,
    OceanBaseTopicMemoryVectorIndex,
)
from powercontext.builtin.persistence.schema import create_tables
from powercontext.builtin.persistence.tables import BUILTIN_TABLES

LIVE_URL = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
pytestmark = pytest.mark.skipif(
    not LIVE_URL,
    reason="set POWERCONTEXT_TEST_OCEANBASE_URL to an OceanBase URL with database creation and deletion privileges",
)


@asynccontextmanager
async def _isolated_database(*, tables: tuple[Table, ...] = BUILTIN_TABLES) -> AsyncIterator[OceanBaseProfile]:
    assert LIVE_URL is not None
    name = f"pc_test_{uuid4().hex}"
    async with OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(LIVE_URL)), tables=()) as server:
        async with server.database.transaction() as connection:
            await connection.exec_driver_sql(f"CREATE DATABASE `{name}`")
        try:
            url = make_url(LIVE_URL).set(database=name).render_as_string(hide_password=False)
            async with OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(url)), tables=tables) as profile:
                yield profile
        finally:
            async with server.database.transaction() as connection:
                await connection.exec_driver_sql(f"DROP DATABASE `{name}`")


def test_live_fulltext_initialization_is_concurrent_and_repeatable() -> None:
    async def scenario() -> None:
        async with _isolated_database() as profile:
            start = asyncio.Event()
            connected = 0

            async def initialize() -> None:
                nonlocal connected
                async with profile.database.transaction() as connection:
                    connected += 1
                    if connected == 8:
                        start.set()
                    await start.wait()
                    for index in (
                        OceanBaseAtomicMemoryIndex(),
                        OceanBaseExperienceFTSIndex(),
                        OceanBaseTopicMemoryFTSIndex(),
                    ):
                        await index.initialize(connection)

            # All connections reach initialization before any of them can create the first index.
            await asyncio.wait_for(asyncio.gather(*(initialize() for _ in range(8))), timeout=120)
            await initialize()
            async with profile.database.transaction() as connection:
                rows = (
                    await connection.execute(
                        text(
                            "SELECT DISTINCT table_name, index_name, index_type "
                            "FROM information_schema.statistics WHERE table_schema = DATABASE() AND index_type = 'FULLTEXT'"
                        )
                    )
                ).all()
                assert set(rows) == {
                    ("pc_atomic_memory_current", "ix_pc_atomic_memory_current_fts", "FULLTEXT"),
                    ("pc_artifact_heads", "ix_pc_artifact_heads_fts", "FULLTEXT"),
                    (
                        "pc_topic_memory_active_topics",
                        "ix_pc_topic_memory_active_topics_fts",
                        "FULLTEXT",
                    ),
                    (
                        "pc_topic_memory_active_chunks",
                        "ix_pc_topic_memory_active_chunks_fts",
                        "FULLTEXT",
                    ),
                }

    asyncio.run(scenario())


@pytest.mark.parametrize("projection", ["memory", "chunk"])
def test_live_fulltext_initialization_rejects_an_unusable_existing_index(projection: str) -> None:
    async def scenario() -> None:
        async with _isolated_database() as profile, profile.database.transaction() as connection:
            if projection == "memory":
                index = OceanBaseAtomicMemoryIndex()
                await create_tables(connection, index.tables)
                await connection.exec_driver_sql(
                    "CREATE INDEX ix_pc_atomic_memory_current_fts ON pc_atomic_memory_current (artifact_id)"
                )
                index = OceanBaseAtomicMemoryIndex()
            else:
                await connection.exec_driver_sql(
                    "CREATE INDEX ix_pc_topic_memory_active_chunks_fts ON pc_topic_memory_active_chunks (artifact_id)"
                )
                index = OceanBaseTopicMemoryFTSIndex()
            # IF NOT EXISTS must not turn an unusable pre-existing index into successful startup.
            with pytest.raises((AtomicMemoryIndexError, DBAPIError)) as caught:
                await index.initialize(connection)
            underlying = caught.value.__cause__ if isinstance(caught.value, AtomicMemoryIndexError) else caught.value
            assert isinstance(underlying, DBAPIError)
            assert ("pc_atomic_memory_current" if projection == "memory" else "pc_topic_memory_active_chunks") in (
                underlying.statement or ""
            )

    asyncio.run(scenario())


def test_live_profile_table_creation_is_concurrent_and_preserves_existing_rows() -> None:
    async def scenario() -> None:
        metadata = MetaData()
        parent = Table("fts_test_parent", metadata, Column("id", Integer, primary_key=True))
        child = Table(
            "fts_test_child",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("parent_id", Integer, ForeignKey(parent.c.id), nullable=False, index=True),
        )
        async with _isolated_database(tables=()) as profile:

            async def open_profile() -> None:
                # Reverse caller order to exercise foreign-key dependency ordering.
                async with OceanBaseProfile.attach(profile.database.engine, tables=(child, parent)):
                    pass

            await asyncio.wait_for(asyncio.gather(*(open_profile() for _ in range(8))), timeout=120)
            async with profile.database.transaction() as connection:
                await connection.execute(insert(parent).values(id=1))
                await connection.execute(insert(child).values(id=2, parent_id=1))
            await open_profile()
            async with profile.database.transaction() as connection:
                assert await connection.scalar(select(child.c.id).join(parent)) == 2
                assert (
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() "
                            "AND table_name='fts_test_child' AND index_name='ix_fts_test_child_parent_id'"
                        )
                    )
                    == 1
                )

    asyncio.run(scenario())


def test_live_access_bootstrap_is_concurrent_and_idempotent() -> None:
    from powercontext.server.authz import AccessAction, AccessAuditContext, PrincipalRef, ResourceRef
    from powercontext.server.authz.composition import open_builtin_access_control

    async def scenario() -> None:
        async with _isolated_database(tables=()) as profile:
            config = OceanBaseConfig(url=SecretStr(profile.database.engine.url.render_as_string(hide_password=False)))
            principal = PrincipalRef(type="service", id="bootstrap-test")

            async def bootstrap() -> None:
                async with open_builtin_access_control(config, bootstrap_administrators=(principal,)) as service:
                    assert (
                        await service.require(
                            principal,
                            AccessAction.SERVER_ADMIN,
                            ResourceRef.server("powercontext"),
                            context=AccessAuditContext(transport="http", operation="test", request_id=str(uuid4())),
                        )
                    ).allowed

            await asyncio.wait_for(asyncio.gather(*(bootstrap() for _ in range(8))), timeout=120)
            await bootstrap()
            async with profile.database.transaction() as connection:
                assert (
                    await connection.scalar(
                        text("SELECT COUNT(*) FROM pc_access_relationships WHERE subject_id='bootstrap-test'")
                    )
                    == 2
                )

    asyncio.run(scenario())


def test_live_vector_initialization_is_concurrent_and_repeatable() -> None:
    async def scenario() -> None:
        embedding_profile = EmbeddingProfile(
            profile_id="startup-test", model="test", dimension=3, distance="l2", normalization="unit"
        )
        async with _isolated_database() as profile:
            start = asyncio.Event()
            connected = 0

            async def initialize() -> None:
                nonlocal connected
                atomic = OceanBaseAtomicMemoryIndex(embedding_profile)
                topics = OceanBaseTopicMemoryVectorIndex(embedding_profile)
                async with profile.database.transaction() as connection:
                    connected += 1
                    if connected == 8:
                        start.set()
                    await start.wait()
                    await create_tables(connection, topics.tables)
                    await atomic.initialize(connection)
                    await topics.initialize(connection)

            await asyncio.wait_for(asyncio.gather(*(initialize() for _ in range(8))), timeout=120)
            await initialize()
            async with profile.database.transaction() as connection:
                expected = {
                    ("pc_atomic_memory_current", "ix_pc_atomic_memory_current_embedding"),
                    ("pc_topic_memory_vector_topics", "ix_pc_topic_memory_vector_topics_embedding"),
                }
                rows = (
                    await connection.execute(
                        text(
                            "SELECT DISTINCT table_name, index_name FROM information_schema.statistics "
                            "WHERE table_schema = DATABASE() AND index_name IN "
                            "('ix_pc_atomic_memory_current_embedding', 'ix_pc_topic_memory_vector_topics_embedding', "
                            "'ix_pc_topic_memory_vector_chunks_embedding')"
                        )
                    )
                ).all()
                assert set(rows) == expected
                # The ANN executor needs a native index, not a regular index
                # accidentally created on a VECTOR column. No model is called.
                atomic = OceanBaseAtomicMemoryIndex(embedding_profile)
                topics = OceanBaseTopicMemoryVectorIndex(embedding_profile)
                for table in (*atomic.tables, *topics.tables):
                    probe = select(table.c.embedding).order_by(
                        func.l2_distance(table.c.embedding, bindparam("query_vector", type_=VECTOR(3)))
                    )
                    # Details use exact ranking; only Atomic Memory and title
                    # projections require native ANN indexes in the current schema.
                    probe = probe.limit(1) if table is topics.chunk_table else probe.suffix_with("APPROXIMATE LIMIT 1")
                    await connection.execute(probe, {"query_vector": (1.0, 0.0, 0.0)})

    asyncio.run(scenario())
