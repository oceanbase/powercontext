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
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from pymysql.err import OperationalError as MySQLOperationalError
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.search import AdmissionFloor
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryContent,
    TopicMemoryDraft,
    TopicMemorySearchRequest,
    chunk_topic_memory_detail,
    prepare_topic_memory_projection,
)
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.oceanbase.topic_memory_index import (
    OceanBaseTopicMemoryFTSIndex,
    OceanBaseTopicMemoryVectorIndex,
)
from powercontext.builtin.persistence.tables import (
    BUILTIN_TABLES,
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
    TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE,
    TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE,
)
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.persistence.topic_memory_index import (
    CompositeTopicMemoryIndex,
)


def _embedding_profile() -> EmbeddingProfile:
    return EmbeddingProfile(
        profile_id="topic-test-v1",
        model="test",
        dimension=3,
        distance="l2",
        normalization="unit",
    )


def test_oceanbase_topic_schema_compiles_native_text_and_vector_storage() -> None:
    dialect = mysql.dialect()
    publication = str(CreateTable(TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE).compile(dialect=dialect))
    retrieval_shape = str(CreateTable(TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE).compile(dialect=dialect))
    active_topic = str(CreateTable(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE).compile(dialect=dialect))
    active_chunks = str(CreateTable(TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE).compile(dialect=dialect))
    vector_index = OceanBaseTopicMemoryVectorIndex(_embedding_profile())
    vector_topics = str(CreateTable(vector_index.topic_table).compile(dialect=dialect))
    vector_chunks = str(CreateTable(vector_index.chunk_table).compile(dialect=dialect))

    assert "published_at DATETIME(6) NOT NULL" in publication
    assert "FOREIGN KEY(scope_id, family, artifact_id, revision)" in publication
    assert "shape VARCHAR(16)" in retrieval_shape
    assert "profile_fingerprint VARCHAR(64)" in retrieval_shape
    assert "AUTO_INCREMENT" not in retrieval_shape
    assert "title MEDIUMTEXT NOT NULL" in active_topic
    assert "summary MEDIUMTEXT NOT NULL" in active_topic
    assert "chunk_text MEDIUMTEXT NOT NULL" in active_chunks
    assert "embedding VECTOR(3) NOT NULL" in vector_topics
    assert "embedding VECTOR(3) NOT NULL" in vector_chunks
    assert "UNIQUE (scope_id, artifact_id, chunk_ordinal)" in vector_chunks


@pytest.mark.parametrize("mode", ["fts", "hybrid"])
@pytest.mark.parametrize("floor", [4, 2**63, 10**100])
def test_oceanbase_impossible_topic_lexical_floor_returns_empty_before_database_binding(mode, floor) -> None:
    async def scenario() -> None:
        connection = AsyncMock(spec=AsyncConnection)
        # This checks the adapter's input handling without claiming native OceanBase execution.
        connection.scalar.side_effect = AssertionError("an impossible lexical floor needs no SQL query")
        connection.execute.side_effect = AssertionError("an impossible lexical floor needs no SQL query")
        result = await OceanBaseTopicMemoryFTSIndex().search(
            cast(AsyncConnection, connection),
            "scope-a",
            TopicMemorySearchRequest(
                query="alpha beta gamma",
                analyzed_query="alpha beta gamma",
                candidate_limit=20,
                mode=mode,
                admission=AdmissionFloor(lexical_min_matched_terms=floor),
            ),
        )
        assert result.topic_fts == result.detail_fts == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("conflict_on", ["topics", "chunks"])
@pytest.mark.parametrize("created_by_peer", [False, True])
def test_oceanbase_fts_startup_recovers_from_ddl_lock_conflict(conflict_on, created_by_peer) -> None:
    async def scenario() -> None:
        connection = AsyncMock(spec=AsyncConnection)
        connection.dialect = mysql.dialect()
        created: set[str] = set()
        conflicted = False

        async def exists(_statement, parameters):
            return int(parameters["index_name"] in created)

        async def create(statement):
            nonlocal conflicted
            index_name = statement.split()[3]
            # A duplicate CREATE is an error even if another initializer won.
            assert index_name not in created
            if conflict_on in index_name and not conflicted:
                conflicted = True
                if created_by_peer:
                    created.add(index_name)
                raise OperationalError(statement, None, MySQLOperationalError(1205, "DDL lock conflict"))
            created.add(index_name)

        connection.scalar.side_effect = exists
        connection.exec_driver_sql.side_effect = create
        await OceanBaseTopicMemoryFTSIndex().initialize(cast(AsyncConnection, connection))

        assert created == {"ix_pc_topic_memory_active_topics_fts", "ix_pc_topic_memory_active_chunks_fts"}
        assert conflicted
        # The adapter must not finish its caller's transaction while recovering.
        connection.commit.assert_not_awaited()
        connection.rollback.assert_not_awaited()

    asyncio.run(scenario())


def test_oceanbase_fts_startup_stops_retrying_a_persistent_ddl_lock_conflict() -> None:
    async def scenario() -> None:
        connection = AsyncMock(spec=AsyncConnection)
        connection.dialect = mysql.dialect()
        connection.scalar.return_value = 0
        error = OperationalError("CREATE FULLTEXT INDEX", None, MySQLOperationalError(1205, "DDL lock conflict"))
        connection.exec_driver_sql.side_effect = error

        # Repeated NOWAIT conflicts must fail startup within a bounded budget.
        with pytest.raises(OperationalError) as raised:
            await asyncio.wait_for(
                OceanBaseTopicMemoryFTSIndex().initialize(cast(AsyncConnection, connection)), timeout=5
            )
        assert raised.value is error

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [1142, 1213, "cancelled"])
def test_oceanbase_fts_startup_preserves_other_errors_and_cancellation(failure) -> None:
    async def scenario() -> None:
        connection = AsyncMock(spec=AsyncConnection)
        connection.dialect = mysql.dialect()
        connection.scalar.return_value = 0
        error = (
            asyncio.CancelledError()
            if failure == "cancelled"
            else OperationalError("CREATE FULLTEXT INDEX", None, MySQLOperationalError(failure, "DDL failed"))
        )
        connection.exec_driver_sql.side_effect = [error, None]

        with pytest.raises(type(error)) as raised:
            await OceanBaseTopicMemoryFTSIndex().initialize(cast(AsyncConnection, connection))
        assert raised.value is error

    asyncio.run(scenario())


def test_oceanbase_vector_startup_recovers_from_ddl_lock_conflict() -> None:
    async def scenario() -> None:
        connection = AsyncMock(spec=AsyncConnection)
        connection.dialect = mysql.dialect()
        connection.scalar.return_value = "VECTOR(3)"
        connection.run_sync.side_effect = [
            OperationalError("CREATE VECTOR INDEX", None, MySQLOperationalError(1205, "DDL lock conflict")),
            None,
        ]
        profile = EmbeddingProfile(
            profile_id="topic-test", model="test", dimension=3, distance="l2", normalization="unit"
        )
        await OceanBaseTopicMemoryVectorIndex(profile).initialize(cast(AsyncConnection, connection))
        connection.commit.assert_not_awaited()
        connection.rollback.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [1142, 1213, "cancelled", "persistent-lock"])
def test_oceanbase_vector_startup_preserves_failures_and_bounds_lock_retries(failure) -> None:
    async def scenario() -> None:
        connection = AsyncMock(spec=AsyncConnection)
        connection.dialect = mysql.dialect()
        connection.scalar.return_value = "VECTOR(3)"
        error = (
            asyncio.CancelledError()
            if failure == "cancelled"
            else OperationalError(
                "CREATE VECTOR INDEX",
                None,
                MySQLOperationalError(1205 if failure == "persistent-lock" else failure, "DDL failed"),
            )
        )
        connection.run_sync.side_effect = error if failure == "persistent-lock" else [error, None]
        profile = EmbeddingProfile(
            profile_id="topic-test", model="test", dimension=3, distance="l2", normalization="unit"
        )
        with pytest.raises(type(error)) as raised:
            await asyncio.wait_for(
                OceanBaseTopicMemoryVectorIndex(profile).initialize(cast(AsyncConnection, connection)), timeout=5
            )
        assert raised.value is error

    asyncio.run(scenario())


def test_oceanbase_fts_initializes_and_queries_both_current_projection_channels() -> None:
    async def scenario() -> None:
        connection = AsyncMock(spec=AsyncConnection)
        connection.dialect = mysql.dialect()
        connection.scalar.side_effect = (0, 0, 0, 0, 0, 0)
        connection.execute.return_value.mappings = MagicMock(return_value=())
        index = OceanBaseTopicMemoryFTSIndex()

        await index.initialize(cast(AsyncConnection, connection))
        result = await index.search(
            cast(AsyncConnection, connection),
            "scope-a",
            TopicMemorySearchRequest(
                query="lease recovery",
                analyzed_query="lease recovery",
                candidate_limit=20,
                mode="fts",
            ),
        )

        create_statements = [call.args[0] for call in connection.exec_driver_sql.await_args_list]
        query_statements = [
            str(call.args[0].compile(dialect=mysql.dialect())) for call in connection.execute.await_args_list[1:]
        ]
        assert create_statements == [
            "CREATE FULLTEXT INDEX ix_pc_topic_memory_active_topics_fts "
            "ON pc_topic_memory_active_topics (searchable_text) WITH PARSER SPACE",
            "CREATE FULLTEXT INDEX ix_pc_topic_memory_active_chunks_fts "
            "ON pc_topic_memory_active_chunks (searchable_text) WITH PARSER SPACE",
        ]
        assert "MATCH (pc_topic_memory_active_topics.searchable_text) AGAINST" in query_statements[0]
        assert " AS raw_score" in query_statements[0]
        assert "pc_topic_memory_active_topics.scope_id" in query_statements[0]
        assert "MATCH (pc_topic_memory_active_chunks.searchable_text) AGAINST" in query_statements[1]
        assert " AS raw_score" in query_statements[1]
        assert "pc_topic_memory_active_chunks.scope_id" in query_statements[1]
        assert "row_number() OVER" in query_statements[1]
        assert "anon_1.topic_rank" in query_statements[1]
        assert result.topic_fts == ()
        assert result.detail_fts == ()
        assert result.topic_fts_retrieved == 0
        assert result.detail_fts_retrieved == 0
        assert result.topic_fts_eligible == 0
        assert result.detail_fts_eligible == 0

    asyncio.run(scenario())


LIVE_URL = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")


@pytest.mark.skipif(
    not LIVE_URL,
    reason="set POWERCONTEXT_TEST_OCEANBASE_URL to an OceanBase MySQL-mode URL with database creation and deletion privileges",
)
def test_live_oceanbase_topic_vector_hybrid_and_detail_search() -> None:
    """Exercise the ANN executor with canned vectors, never a model/provider AK."""

    async def scenario() -> None:
        assert LIVE_URL is not None
        embedding_profile = _embedding_profile()
        vector = (1.0, 0.0, 0.0)
        vector_index = OceanBaseTopicMemoryVectorIndex(embedding_profile)
        index = CompositeTopicMemoryIndex(OceanBaseTopicMemoryFTSIndex(), vector_index)
        repository = TopicMemoryRepository(index=index)
        database_name = f"pc_test_{uuid4().hex}"
        database_created = False
        async with OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(LIVE_URL)), tables=()) as server_profile:
            try:
                async with server_profile.database.transaction() as connection:
                    await connection.exec_driver_sql(f"CREATE DATABASE `{database_name}`")
                    database_created = True
                test_url = make_url(LIVE_URL).set(database=database_name).render_as_string(hide_password=False)
                async with OceanBaseProfile.open(
                    OceanBaseConfig(url=SecretStr(test_url)), tables=BUILTIN_TABLES + index.tables
                ) as profile:
                    async with profile.database.transaction() as connection:
                        await repository.initialize(connection)
                    # Equal distances across multi-chunk Topics retain ordering and Scope isolation.
                    for scope, label in (("scope-a", "zulu"), ("scope-a", "alpha"), ("scope-b", "hidden")):
                        content = TopicMemoryContent(
                            title=f"{label} recovery",
                            summary=f"{label} evidence",
                            detail=f"{label} evidence " * 200,
                        )
                        chunks = chunk_topic_memory_detail(content.detail)
                        assert len(chunks) > 1
                        projection = prepare_topic_memory_projection(
                            content,
                            topic_embedding=vector,
                            chunk_embeddings=(vector,) * len(chunks),
                            embedding_profile=embedding_profile,
                        )
                        async with profile.database.transaction() as connection:
                            await repository.publish_create(
                                connection, scope, f"topic-{label}", TopicMemoryDraft(content=content), projection
                            )
                    async with profile.database.transaction() as connection:
                        for mode in ("vector", "hybrid"):
                            result = await repository.search(
                                connection,
                                "scope-a",
                                "alpha",
                                limit=2,
                                mode=mode,
                                query_vector=vector,
                                embedding_profile=embedding_profile,
                            )
                            assert result.mode == mode
                            assert [hit.artifact_ref.artifact_id for hit in result.hits] == [
                                "topic-alpha",
                                "topic-zulu",
                            ]
                        channels = await vector_index.search(
                            connection,
                            "scope-a",
                            TopicMemorySearchRequest(
                                query="alpha",
                                candidate_limit=2,
                                mode="vector",
                                query_vector=vector,
                                embedding_profile=embedding_profile,
                            ),
                        )
                        for hits in (channels.topic_vector, channels.detail_vector):
                            assert [hit.artifact_ref.artifact_id for hit in hits] == ["topic-alpha", "topic-zulu"]
                        assert all(
                            hit.chunk_ordinal is not None and hit.distance == 0 for hit in channels.detail_vector
                        )
            finally:
                if database_created:
                    async with server_profile.database.transaction() as connection:
                        await connection.exec_driver_sql(f"DROP DATABASE `{database_name}`")

    asyncio.run(scenario())
