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
import re
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from powercontext.builtin.artifacts.memory import EmbeddingProfile
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
from powercontext.builtin.persistence.topic_memory_index import CompositeTopicMemoryIndex


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

    assert "published_at DATETIME NOT NULL" in publication
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


def test_oceanbase_fts_initializes_and_queries_both_current_projection_channels() -> None:
    async def scenario() -> None:
        connection = AsyncMock(spec=AsyncConnection)
        connection.dialect = mysql.dialect()
        connection.scalar.side_effect = (0, 0)
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
        assert "pc_topic_memory_active_topics.scope_id" in query_statements[0]
        assert "instr(concat(" in query_statements[0].casefold()
        assert "MATCH (pc_topic_memory_active_chunks.searchable_text) AGAINST" in query_statements[1]
        assert "pc_topic_memory_active_chunks.scope_id" in query_statements[1]
        assert "instr(concat(" in query_statements[1].casefold()
        assert "row_number() OVER" in query_statements[1]
        assert "anon_1.topic_rank" in query_statements[1]
        assert result.topic_fts == ()
        assert result.detail_fts == ()

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
                    # Reverse insertion order, equal distances, multiple chunks:
                    # tie-break and collapse must occur outside the bounded ANN.
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
                        assert [hit.chunk_ordinal for hit in channels.detail_vector] == [0, 0]
            finally:
                if database_created:
                    async with server_profile.database.transaction() as connection:
                        await connection.exec_driver_sql(f"DROP DATABASE `{database_name}`")

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_oceanbase_detail_vector_collapses_topics_before_the_channel_limit(mode) -> None:
    async def scenario() -> None:
        connection = AsyncMock(spec=AsyncConnection)
        connection.execute.return_value.mappings = MagicMock(return_value=())
        index = OceanBaseTopicMemoryVectorIndex(_embedding_profile())

        result = await index.search(
            cast(AsyncConnection, connection),
            "scope-a",
            TopicMemorySearchRequest(
                query="semantic evidence",
                analyzed_query="semantic evidence",
                candidate_limit=2,
                mode=mode,
                query_vector=(1.0, 0.0, 0.0),
                embedding_profile=_embedding_profile(),
            ),
        )

        statements = [str(call.args[0]) for call in connection.execute.await_args_list]
        # OceanBase's grammar is `order_by opt_approx limit_clause`: the
        # modifier follows the ENTIRE sort list, never an individual key.
        for statement in statements:
            assert re.search(r"\bAPPROXIMATE\s+LIMIT\s+:(?:candidate|neighbor)_limit\b", statement)
            assert not re.search(r"\bAPPROXIMATE\s*,", statement)
            # The vector executor (not just the parser) rejects secondary
            # sort keys on ANN queries. Tie-break only AFTER bounded KNN.
            assert re.search(
                r"ORDER BY\s+l2_distance\([^()]+\)\s+APPROXIMATE\s+LIMIT\s+:(?:candidate|neighbor)_limit",
                statement,
            )
            # CE 4.3.5.6 loses same-scope neighbors if ANN itself joins the
            # active tables. Hydrate only AFTER bounded, join-free selection.
            ann_selection = statement[: statement.index("APPROXIMATE")]
            assert "JOIN" not in ann_selection
            assert "WHERE v.scope_id = :scope_id" in ann_selection
            assert statement.index("JOIN pc_topic_memory_active_topics") > statement.index("APPROXIMATE")
            assert statement.rfind("ORDER BY distance, artifact_id, revision DESC") > statement.index("APPROXIMATE")
        assert statements[0].index("LIMIT :candidate_limit") < statements[0].rfind("ORDER BY distance")
        assert "row_number() OVER" in statements[1]
        assert "WHERE topic_rank = 1" in statements[1]
        assert statements[1].find("LIMIT :neighbor_limit") < statements[1].find("row_number() OVER")
        assert statements[1].rfind("LIMIT :candidate_limit") > statements[1].rfind("WHERE topic_rank = 1")
        assert connection.execute.await_args_list[1].args[1]["neighbor_limit"] == 400
        assert result.topic_vector == ()
        assert result.detail_vector == ()

    asyncio.run(scenario())
