"""SQLite FTS5 projection for approved Experience heads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.experience import Experience, ExperienceSearchHit, experience_searchable_text
from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError
from powercontext.builtin.artifacts.search import fts_match_query
from powercontext.builtin.persistence.experience_index import (
    ensure_artifact_head_searchable_text,
    experience_search_hits,
    rebuild_experience_projections,
    replace_experience_projection,
)
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE

_CREATE_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS pc_experience_fts USING fts5(
    scope_id UNINDEXED,
    artifact_id UNINDEXED,
    revision UNINDEXED,
    searchable_text,
    tokenize='unicode61'
)
"""
_DELETE_ALL_FTS_SQL = "DELETE FROM pc_experience_fts"
_PROBE_FTS_SQL = "SELECT rowid FROM pc_experience_fts WHERE pc_experience_fts MATCH 'powercontext'"
_DELETE_FTS_SQL = text("DELETE FROM pc_experience_fts WHERE scope_id = :scope_id AND artifact_id = :artifact_id")
_INSERT_FTS_SQL = text(
    """
    INSERT INTO pc_experience_fts (scope_id, artifact_id, revision, searchable_text)
    VALUES (:scope_id, :artifact_id, :revision, :searchable_text)
    """
)
_SEARCH_FTS_SQL = text(
    """
    SELECT f.artifact_id, f.revision, a.content
    FROM pc_experience_fts AS f
    JOIN pc_artifacts AS a
      ON a.scope_id = f.scope_id
     AND a.family = 'experience'
     AND a.artifact_id = f.artifact_id
     AND a.revision = f.revision
    WHERE pc_experience_fts MATCH :query
      AND f.scope_id = :scope_id
    ORDER BY bm25(pc_experience_fts), f.artifact_id, f.revision
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
        await connection.exec_driver_sql(_CREATE_FTS_SQL)
        await connection.exec_driver_sql(_DELETE_ALL_FTS_SQL)
        rows = (
            await connection.execute(
                select(
                    ARTIFACT_HEADS_TABLE.c.scope_id,
                    ARTIFACT_HEADS_TABLE.c.artifact_id,
                    ARTIFACT_HEADS_TABLE.c.revision,
                    ARTIFACT_HEADS_TABLE.c.searchable_text,
                ).where(
                    ARTIFACT_HEADS_TABLE.c.family == Experience.family,
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
            {"scope_id": scope_id, "artifact_id": experience.artifact_id},
        )
        await self._insert_row(
            connection,
            {
                "scope_id": scope_id,
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
                    "candidate_limit": limit * 4,
                },
            )
        ).mappings()
        return experience_search_hits(rows, query, limit)

    @staticmethod
    async def _insert_row(connection: AsyncConnection, row: Mapping[Any, Any]) -> None:
        await connection.execute(
            _INSERT_FTS_SQL,
            {field: row[field] for field in ("scope_id", "artifact_id", "revision", "searchable_text")},
        )


__all__ = ["SQLiteExperienceFTSIndex"]
