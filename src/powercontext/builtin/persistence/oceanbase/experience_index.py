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

"""OceanBase FULLTEXT projection for approved Experience heads."""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.dialects.mysql import match
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.experience import Experience, ExperienceSearchHit
from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.persistence.experience_index import (
    ensure_artifact_head_searchable_text,
    experience_search_hits,
    rebuild_experience_projections,
    replace_experience_projection,
)
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE

_OCEANBASE_FTS_INDEX_NAME = "ix_pc_artifact_heads_fts"
_OCEANBASE_CREATE_FTS_SQL = f"""
CREATE FULLTEXT INDEX {_OCEANBASE_FTS_INDEX_NAME}
ON pc_artifact_heads (searchable_text) WITH PARSER SPACE
"""
_OCEANBASE_FTS_INDEX_EXISTS_SQL = text(
    """
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'pc_artifact_heads'
      AND index_name = :index_name
    """
)


class OceanBaseExperienceFTSIndex:
    """Maintain and query approved Experience heads with OceanBase FULLTEXT."""

    async def initialize(self, connection: AsyncConnection, /) -> None:
        if connection.dialect.name != "mysql":
            raise CapabilityNotSupportedError("oceanbase-experience-fts")
        await ensure_artifact_head_searchable_text(connection)
        await rebuild_experience_projections(connection)
        count = await connection.scalar(
            _OCEANBASE_FTS_INDEX_EXISTS_SQL,
            {"index_name": _OCEANBASE_FTS_INDEX_NAME},
        )
        if count == 0:
            await connection.exec_driver_sql(_OCEANBASE_CREATE_FTS_SQL)
        probe = match(ARTIFACT_HEADS_TABLE.c.searchable_text, against="powercontext")
        await connection.execute(select(ARTIFACT_HEADS_TABLE.c.artifact_id).where(probe).limit(1))

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        experience: Experience,
        /,
    ) -> None:
        await replace_experience_projection(connection, scope_id, experience)

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        query: str,
        limit: int,
        /,
    ) -> tuple[ExperienceSearchHit, ...]:
        analyzed = analyze_text(query)
        if not analyzed:
            return ()
        score = match(ARTIFACT_HEADS_TABLE.c.searchable_text, against=analyzed)
        rows = (
            await connection.execute(
                select(
                    ARTIFACT_HEADS_TABLE.c.artifact_id,
                    ARTIFACT_HEADS_TABLE.c.revision,
                    ARTIFACTS_TABLE.c.content,
                )
                .join(
                    ARTIFACTS_TABLE,
                    (ARTIFACTS_TABLE.c.scope_id == ARTIFACT_HEADS_TABLE.c.scope_id)
                    & (ARTIFACTS_TABLE.c.family == ARTIFACT_HEADS_TABLE.c.family)
                    & (ARTIFACTS_TABLE.c.artifact_id == ARTIFACT_HEADS_TABLE.c.artifact_id)
                    & (ARTIFACTS_TABLE.c.revision == ARTIFACT_HEADS_TABLE.c.revision),
                )
                .where(
                    ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                    ARTIFACT_HEADS_TABLE.c.family == Experience.family,
                    score,
                )
                .order_by(score.desc(), ARTIFACT_HEADS_TABLE.c.artifact_id, ARTIFACT_HEADS_TABLE.c.revision)
                .limit(limit * 4)
            )
        ).mappings()
        return experience_search_hits(rows, query, limit)


__all__ = ["OceanBaseExperienceFTSIndex"]
