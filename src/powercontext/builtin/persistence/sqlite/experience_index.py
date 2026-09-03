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

"""SQLite FTS5 projection for approved Experience heads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.experience import Experience, ExperienceSearchHit, experience_searchable_text
from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError
from powercontext.builtin.artifacts.search import fts_match_query
from powercontext.builtin.artifacts.skill import Skill, SkillPackageSnapshot, SkillSearchHit, skill_searchable_text
from powercontext.builtin.persistence.experience_index import (
    ensure_artifact_head_searchable_text,
    experience_search_hits,
    rebuild_experience_projections,
    rebuild_skill_projections,
    replace_experience_projection,
    replace_skill_projection,
    skill_search_hits,
)
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE

_CREATE_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS pc_artifact_fts USING fts5(
    scope_id UNINDEXED,
    family UNINDEXED,
    artifact_id UNINDEXED,
    revision UNINDEXED,
    searchable_text,
    tokenize='unicode61'
)
"""
_DELETE_ALL_FTS_SQL = "DELETE FROM pc_artifact_fts"
_PROBE_FTS_SQL = "SELECT rowid FROM pc_artifact_fts WHERE pc_artifact_fts MATCH 'powercontext'"
_DELETE_FTS_SQL = text(
    "DELETE FROM pc_artifact_fts WHERE scope_id = :scope_id AND family = :family AND artifact_id = :artifact_id"
)
_INSERT_FTS_SQL = text(
    """
    INSERT INTO pc_artifact_fts (scope_id, family, artifact_id, revision, searchable_text)
    VALUES (:scope_id, :family, :artifact_id, :revision, :searchable_text)
    """
)
_SEARCH_FTS_SQL = text(
    """
    SELECT f.artifact_id, f.revision, f.searchable_text, a.content
    FROM pc_artifact_fts AS f
    JOIN pc_artifacts AS a
      ON a.scope_id = f.scope_id
     AND a.family = f.family
     AND a.artifact_id = f.artifact_id
     AND a.revision = f.revision
    JOIN pc_artifact_heads AS h
      ON h.scope_id = f.scope_id
     AND h.family = f.family
     AND h.artifact_id = f.artifact_id
     AND h.revision = f.revision
    WHERE pc_artifact_fts MATCH :query
      AND f.scope_id = :scope_id
      AND f.family = :family
      AND h.lifecycle_state = 'active'
    ORDER BY bm25(pc_artifact_fts), f.artifact_id, f.revision
    LIMIT :candidate_limit
    """
)


class SQLiteExperienceFTSIndex:
    """Maintain and query approved Experience heads with SQLite FTS5."""

    async def initialize(self, connection: AsyncConnection, /) -> None:
        if connection.dialect.name != "sqlite":
            raise CapabilityNotSupportedError("sqlite-experience-fts")
        await ensure_artifact_head_searchable_text(connection)
        await rebuild_experience_projections(connection)
        await rebuild_skill_projections(connection)
        await connection.exec_driver_sql(_CREATE_FTS_SQL)
        await connection.exec_driver_sql(_DELETE_ALL_FTS_SQL)
        rows = (
            await connection.execute(
                select(
                    ARTIFACT_HEADS_TABLE.c.scope_id,
                    ARTIFACT_HEADS_TABLE.c.family,
                    ARTIFACT_HEADS_TABLE.c.artifact_id,
                    ARTIFACT_HEADS_TABLE.c.revision,
                    ARTIFACT_HEADS_TABLE.c.searchable_text,
                ).where(
                    ARTIFACT_HEADS_TABLE.c.family.in_((Experience.family, Skill.family)),
                    ARTIFACT_HEADS_TABLE.c.lifecycle_state == "active",
                    ARTIFACT_HEADS_TABLE.c.searchable_text.is_not(None),
                )
            )
        ).mappings()
        for row in rows:
            await self._insert_row(connection, row)
        await connection.exec_driver_sql(_PROBE_FTS_SQL)

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        experience: Experience,
        /,
    ) -> None:
        await replace_experience_projection(connection, scope_id, experience)
        await connection.execute(
            _DELETE_FTS_SQL,
            {"scope_id": scope_id, "family": Experience.family, "artifact_id": experience.artifact_id},
        )
        await self._insert_row(
            connection,
            {
                "scope_id": scope_id,
                "family": Experience.family,
                "artifact_id": experience.artifact_id,
                "revision": experience.revision,
                "searchable_text": experience_searchable_text(experience.content),
            },
        )

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        query: str,
        limit: int,
        /,
    ) -> tuple[ExperienceSearchHit, ...]:
        match_query = fts_match_query(query)
        if match_query is None:
            return ()
        rows = (
            await connection.execute(
                _SEARCH_FTS_SQL,
                {
                    "query": match_query,
                    "scope_id": scope_id,
                    "family": Experience.family,
                    "candidate_limit": limit * 4,
                },
            )
        ).mappings()
        return experience_search_hits(rows, query, limit)

    async def replace_skill(
        self,
        connection: AsyncConnection,
        scope_id: str,
        skill: Skill,
        package: SkillPackageSnapshot,
        /,
    ) -> None:
        await replace_skill_projection(connection, scope_id, skill, package)
        await connection.execute(
            _DELETE_FTS_SQL,
            {"scope_id": scope_id, "family": Skill.family, "artifact_id": skill.artifact_id},
        )
        await self._insert_row(
            connection,
            {
                "scope_id": scope_id,
                "family": Skill.family,
                "artifact_id": skill.artifact_id,
                "revision": skill.revision,
                "searchable_text": skill_searchable_text(skill.content, package),
            },
        )

    async def search_skills(
        self,
        connection: AsyncConnection,
        scope_id: str,
        query: str,
        limit: int,
        /,
    ) -> tuple[SkillSearchHit, ...]:
        match_query = fts_match_query(query)
        if match_query is None:
            return ()
        rows = (
            await connection.execute(
                _SEARCH_FTS_SQL,
                {
                    "query": match_query,
                    "scope_id": scope_id,
                    "family": Skill.family,
                    "candidate_limit": limit * 4,
                },
            )
        ).mappings()
        return skill_search_hits(rows, query, limit)

    @staticmethod
    async def _insert_row(connection: AsyncConnection, row: Mapping[Any, Any]) -> None:
        await connection.execute(
            _INSERT_FTS_SQL,
            {field: row[field] for field in ("scope_id", "family", "artifact_id", "revision", "searchable_text")},
        )


__all__ = ["SQLiteExperienceFTSIndex"]
