"""Approved Experience head projection and FTS index contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import (
    Experience,
    ExperienceContent,
    ExperienceSearchHit,
    experience_search_text,
    experience_searchable_text,
)
from powercontext.builtin.artifacts.search import admits_fts_text
from powercontext.builtin.persistence.codec import load_model, stored_bytes
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE

_SQLITE_SEARCHABLE_TEXT_EXISTS_SQL = text(
    """
    SELECT COUNT(*)
    FROM pragma_table_info('pc_artifact_heads')
    WHERE name = 'searchable_text'
    """
)
_MYSQL_SEARCHABLE_TEXT_EXISTS_SQL = text(
    """
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'pc_artifact_heads'
      AND column_name = 'searchable_text'
    """
)
_ADD_SQLITE_SEARCHABLE_TEXT_SQL = "ALTER TABLE pc_artifact_heads ADD COLUMN searchable_text TEXT NULL"
_ADD_MYSQL_SEARCHABLE_TEXT_SQL = "ALTER TABLE pc_artifact_heads ADD COLUMN searchable_text MEDIUMTEXT NULL"


class ExperienceIndex(Protocol):
    """A rebuildable query projection updated with approved Artifact heads."""

    async def initialize(self, connection: AsyncConnection, /) -> None: ...

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        experience: Experience,
        /,
    ) -> None: ...

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        query: str,
        limit: int,
        /,
    ) -> tuple[ExperienceSearchHit, ...]: ...


class NoExperienceIndex:
    """Allow exact Experience reads when no recall projection is configured."""

    async def initialize(self, _connection: AsyncConnection, /) -> None:
        pass

    async def replace(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _experience: Experience,
        /,
    ) -> None:
        pass

    async def search(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _query: str,
        _limit: int,
        /,
    ) -> tuple[ExperienceSearchHit, ...]:
        return ()


async def ensure_artifact_head_searchable_text(connection: AsyncConnection, /) -> None:
    """Upgrade a pre-Experience Artifact head table with its rebuildable search projection."""

    dialect = connection.dialect.name
    if dialect == "sqlite":
        exists_sql = _SQLITE_SEARCHABLE_TEXT_EXISTS_SQL
        migration_sql = _ADD_SQLITE_SEARCHABLE_TEXT_SQL
    elif dialect == "mysql":
        exists_sql = _MYSQL_SEARCHABLE_TEXT_EXISTS_SQL
        migration_sql = _ADD_MYSQL_SEARCHABLE_TEXT_SQL
    else:
        raise ValueError(f"unsupported Artifact head migration dialect: {dialect}")  # noqa: TRY003

    if int(await connection.scalar(exists_sql) or 0) == 0:
        await connection.exec_driver_sql(migration_sql)


async def rebuild_experience_projections(connection: AsyncConnection, /) -> None:
    """Rebuild searchable text on authoritative Experience Artifact heads."""

    rows = tuple(
        (
            await connection.execute(
                select(
                    ARTIFACT_HEADS_TABLE.c.scope_id,
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
                .where(ARTIFACT_HEADS_TABLE.c.family == Experience.family)
                .order_by(ARTIFACT_HEADS_TABLE.c.scope_id, ARTIFACT_HEADS_TABLE.c.artifact_id)
            )
        ).mappings()
    )
    for row in rows:
        await _update_searchable_text(
            connection,
            scope_id=str(row["scope_id"]),
            artifact_id=str(row["artifact_id"]),
            revision=int(row["revision"]),
            searchable_text=experience_searchable_text(_content(row["content"])),
        )


async def replace_experience_projection(
    connection: AsyncConnection,
    scope_id: str,
    experience: Experience,
    /,
) -> None:
    """Replace searchable text on one newly approved exact head."""

    await _update_searchable_text(
        connection,
        scope_id=scope_id,
        artifact_id=experience.artifact_id,
        revision=experience.revision,
        searchable_text=experience_searchable_text(experience.content),
    )


def experience_search_hits(
    rows: Iterable[Mapping[Any, Any]],
    query: str,
    limit: int,
    /,
) -> tuple[ExperienceSearchHit, ...]:
    """Decode backend-ordered rows and apply the shared lexical admission rule."""

    hits: list[ExperienceSearchHit] = []
    for row in rows:
        content = _content(row["content"])
        if not admits_fts_text(query, experience_search_text(content)):
            continue
        hits.append(
            ExperienceSearchHit(
                artifact_ref=ArtifactRef(
                    family=Experience.family,
                    artifact_id=str(row["artifact_id"]),
                    revision=int(row["revision"]),
                ),
                content=content,
            )
        )
        if len(hits) >= limit:
            break
    return tuple(hits)


async def _update_searchable_text(
    connection: AsyncConnection,
    *,
    scope_id: str,
    artifact_id: str,
    revision: int,
    searchable_text: str,
) -> None:
    result = await connection.execute(
        update(ARTIFACT_HEADS_TABLE)
        .where(
            ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
            ARTIFACT_HEADS_TABLE.c.family == Experience.family,
            ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
            ARTIFACT_HEADS_TABLE.c.revision == revision,
        )
        .values(searchable_text=searchable_text)
    )
    if result.rowcount != 1:
        raise RepositoryNotFoundError(  # noqa: TRY003
            "artifact head",
            ArtifactRef(family=Experience.family, artifact_id=artifact_id, revision=revision),
        )


def _content(value: object) -> ExperienceContent:
    return load_model(
        ExperienceContent,
        stored_bytes(value, column="content"),
        kind="artifact",
        name=Experience.family,
    )


__all__ = [
    "ExperienceIndex",
    "NoExperienceIndex",
    "ensure_artifact_head_searchable_text",
    "experience_search_hits",
    "rebuild_experience_projections",
    "replace_experience_projection",
]
