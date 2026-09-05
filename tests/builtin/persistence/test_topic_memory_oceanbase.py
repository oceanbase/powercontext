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
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.topic_memory import TopicMemorySearchRequest
from powercontext.builtin.persistence.oceanbase.topic_memory_index import (
    OceanBaseTopicMemoryFTSIndex,
    OceanBaseTopicMemoryVectorIndex,
)
from powercontext.builtin.persistence.tables import (
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
    TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE,
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
    active_topic = str(CreateTable(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE).compile(dialect=dialect))
    active_chunks = str(CreateTable(TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE).compile(dialect=dialect))
    vector_index = OceanBaseTopicMemoryVectorIndex(_embedding_profile())
    vector_topics = str(CreateTable(vector_index.topic_table).compile(dialect=dialect))
    vector_chunks = str(CreateTable(vector_index.chunk_table).compile(dialect=dialect))

    assert "published_at DATETIME NOT NULL" in publication
    assert "FOREIGN KEY(scope_id, family, artifact_id, revision)" in publication
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
        assert "MATCH (pc_topic_memory_active_chunks.searchable_text) AGAINST" in query_statements[1]
        assert "pc_topic_memory_active_chunks.scope_id" in query_statements[1]
        assert result.topic_fts == ()
        assert result.detail_fts == ()

    asyncio.run(scenario())
