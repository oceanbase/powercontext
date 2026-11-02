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

"""Approved Experience head projection and FTS index contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, Protocol

from sqlalchemy import select, text, tuple_, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.artifacts.search import ChannelScore
from powercontext.builtin.artifacts.experience import (
    Experience,
    ExperienceContent,
    ExperienceSearchHit,
    ExperienceSearchOutcome,
    experience_search_text,
    experience_searchable_text,
)
from powercontext.builtin.artifacts.search import (
    AdmissionCounts,
    AdmissionFloor,
    InvalidSearchScore,
    admits_fts_projection,
    admits_fts_text,
    analyze_fts_query,
    analyze_text,
    fts_match_query,
    fts_query_requirements,
    lexical_search_score,
)
from powercontext.builtin.artifacts.skill import (
    Skill,
    SkillContent,
    SkillPackageSnapshot,
    SkillSearchHit,
    skill_search_text,
    skill_searchable_text,
)
from powercontext.builtin.artifacts.skill.package import capture_skill_archive
from powercontext.builtin.persistence.atomic_memory_index import oceanbase_fts_coverage_sql, sqlite_fts_coverage_sql
from powercontext.builtin.persistence.codec import load_model, stored_bytes
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.lexical_projection import LEXICAL_ANALYZER_VERSION
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE, SKILL_PACKAGES_TABLE

# Bump the suffix when Family searchable fields or package evidence rules change.
APPROVED_ARTIFACT_PROJECTION_VERSION = f"{LEXICAL_ANALYZER_VERSION}:approved-v1"

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
_GOVERNANCE_COLUMNS = {
    "lifecycle_state": (
        "VARCHAR(16) NOT NULL DEFAULT 'active'",
        "VARCHAR(16) NOT NULL DEFAULT 'active'",
    ),
    "replacement_artifact_id": ("VARCHAR(128) NULL", "VARCHAR(128) NULL"),
    "governance_generation": ("BIGINT NOT NULL DEFAULT 0", "BIGINT NOT NULL DEFAULT 0"),
    "merged_into_id": ("VARCHAR(128) NULL", "VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL"),
}


class ExperienceIndex(Protocol):
    """Shared approved Experience/Skill projection with Family-owned result decoding."""

    families: frozenset[str]

    async def initialize(self, connection: AsyncConnection, /) -> None: ...

    async def rebuild(self, connection: AsyncConnection, /) -> None:
        """Repair initialized projections from authority with writers stopped."""
        ...

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        experience: Experience,
        /,
    ) -> None: ...

    async def remove(self, connection: AsyncConnection, scope_id: str, artifact_id: str, /) -> None: ...

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        family: Literal["experience", "skill"],
        query: str,
        limit: int,
        /,
        *,
        admission: AdmissionFloor | None = None,
    ) -> tuple[Mapping[Any, Any], ...]:
        """Return ranked qualified rows with exact body and revision metadata.

        Rows may include a bounded rejection witness. Family services apply
        score controls and materialize selected rows with their lineage.
        """
        ...

    async def replace_skill(
        self,
        connection: AsyncConnection,
        scope_id: str,
        skill: Skill,
        package: SkillPackageSnapshot,
        /,
    ) -> None: ...


class NoExperienceIndex:
    """Retain exact Artifact reads when approved-head search is disabled."""

    families: frozenset[str] = frozenset()

    async def initialize(self, _connection: AsyncConnection, /) -> None:
        pass

    async def rebuild(self, _connection: AsyncConnection, /) -> None:
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
        _family: Literal["experience", "skill"],
        _query: str,
        _limit: int,
        /,
        *,
        admission: AdmissionFloor | None = None,
    ) -> tuple[Mapping[Any, Any], ...]:
        return ()

    async def remove(self, _connection: AsyncConnection, _scope_id: str, _artifact_id: str, /) -> None:
        pass

    async def replace_skill(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _skill: Skill,
        _package: SkillPackageSnapshot,
        /,
    ) -> None:
        pass


async def ensure_artifact_head_searchable_text(connection: AsyncConnection, /) -> None:
    """Upgrade existing shared heads and lineage with their current persisted columns."""

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
    for column, definitions in _GOVERNANCE_COLUMNS.items():
        column_sql = text(
            "SELECT COUNT(*) FROM pragma_table_info('pc_artifact_heads') WHERE name = :column"
            if dialect == "sqlite"
            else "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'pc_artifact_heads' "
            "AND column_name = :column"
        )
        exists = await connection.scalar(column_sql, {"column": column})
        if int(exists or 0) == 0:
            definition = definitions[0 if dialect == "sqlite" else 1]
            await connection.exec_driver_sql(f"ALTER TABLE pc_artifact_heads ADD COLUMN {column} {definition}")
    lineage_column_sql = text(
        "SELECT COUNT(*) FROM pragma_table_info('pc_artifact_lineage_artifacts') WHERE name = 'is_merge_input'"
        if dialect == "sqlite"
        else "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'pc_artifact_lineage_artifacts' "
        "AND column_name = 'is_merge_input'"
    )
    if int(await connection.scalar(lineage_column_sql) or 0) == 0:
        await connection.exec_driver_sql(
            "ALTER TABLE pc_artifact_lineage_artifacts ADD COLUMN is_merge_input BOOLEAN NOT NULL DEFAULT 0"
        )


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
                    ARTIFACT_HEADS_TABLE.c.searchable_text,
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
        searchable = experience_searchable_text(_content(row["content"]))
        if row["searchable_text"] == searchable:
            continue
        await _update_searchable_text(
            connection,
            scope_id=str(row["scope_id"]),
            artifact_id=str(row["artifact_id"]),
            revision=int(row["revision"]),
            searchable_text=searchable,
        )


async def rebuild_skill_projections(connection: AsyncConnection, /) -> None:
    """Verify package authority and recover searchable text in bounded head pages."""
    heads, artifacts = ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE
    after = ("", "")
    while True:
        statement = (
            select(
                heads.c.scope_id, heads.c.artifact_id, heads.c.revision, artifacts.c.content, heads.c.searchable_text
            )
            .join(
                artifacts,
                (artifacts.c.scope_id == heads.c.scope_id)
                & (artifacts.c.family == heads.c.family)
                & (artifacts.c.artifact_id == heads.c.artifact_id)
                & (artifacts.c.revision == heads.c.revision),
            )
            .where(heads.c.family == Skill.family, tuple_(heads.c.scope_id, heads.c.artifact_id) > after)
            .order_by(heads.c.scope_id, heads.c.artifact_id)
            .limit(32)
        )
        rows = tuple((await connection.execute(statement)).mappings())
        if not rows:
            return
        searchable = await _skill_projection_texts(connection, rows)
        for row in rows:
            value = searchable[row["scope_id"], row["artifact_id"]]
            if row["searchable_text"] != value:
                await _update_searchable_text(
                    connection,
                    scope_id=row["scope_id"],
                    family=Skill.family,
                    artifact_id=row["artifact_id"],
                    revision=row["revision"],
                    searchable_text=value,
                )
        after = (rows[-1]["scope_id"], rows[-1]["artifact_id"])


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


async def remove_experience_projection(connection: AsyncConnection, scope_id: str, artifact_id: str, /) -> None:
    """Remove current searchable content without changing exact Artifact history."""

    await connection.execute(
        update(ARTIFACT_HEADS_TABLE)
        .where(
            ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
            ARTIFACT_HEADS_TABLE.c.family == Experience.family,
            ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
        )
        .values(searchable_text=None)
    )


async def replace_skill_projection(
    connection: AsyncConnection,
    scope_id: str,
    skill: Skill,
    package: SkillPackageSnapshot,
    /,
) -> None:
    """Replace searchable text on one newly approved exact Skill head."""

    await _update_searchable_text(
        connection,
        scope_id=scope_id,
        family=Skill.family,
        artifact_id=skill.artifact_id,
        revision=skill.revision,
        searchable_text=skill_searchable_text(skill.content, package),
    )


async def search_approved_artifact_rows(
    connection: AsyncConnection,
    scope_id: str,
    family: str,
    query: str,
    limit: int,
    *,
    admission: AdmissionFloor | None,
) -> tuple[Mapping[Any, Any], ...]:
    """Qualify active heads before truncation; hydrate bounded candidates and decode selected rows.

    Admission observes the ranked prefix, not rejected rows beyond that prefix.
    """
    terms, required = fts_query_requirements(query, floor=admission)
    if not terms:
        return ()
    parameters: dict[str, Any] = {
        "scope_id": scope_id,
        "family": family,
        "result_limit": limit,
        "required": min(required, len(terms) + 1),
    }
    if connection.dialect.name == "sqlite":
        coverage, values = sqlite_fts_coverage_sql(terms) if required > 1 else ("1", {})
        parameters["query"] = fts_match_query(query)
        source = (
            "pc_artifact_fts AS f JOIN pc_artifact_heads AS h ON "
            "h.scope_id=f.scope_id AND h.family=f.family AND h.artifact_id=f.artifact_id "
            "AND h.revision=f.revision"
        )
        coverage = coverage.replace("searchable_text", "f.searchable_text")
        score, predicate, direction, metric = (
            "bm25(pc_artifact_fts)",
            "pc_artifact_fts MATCH :query",
            "ASC",
            "sqlite_bm25",
        )
        identity = "f.rowid AS projection_id, "
        projection_join = "JOIN pc_artifact_fts AS p ON p.rowid=c.projection_id "
    else:
        coverage, values = oceanbase_fts_coverage_sql(terms) if required > 1 else ("1", {})
        parameters["query"] = analyze_fts_query(query)
        source = "pc_artifact_heads AS h"
        score = "MATCH(h.searchable_text) AGAINST (:query)"
        predicate, direction, metric = score, "DESC", "oceanbase_match"
        identity = ""
        projection_join = (
            "JOIN pc_artifact_heads AS p ON p.scope_id=:scope_id AND p.family=:family "
            "AND p.artifact_id=c.artifact_id AND p.revision=c.revision "
        )
    parameters.update(values)
    eligibility = f"WHERE h.scope_id=:scope_id AND h.family=:family AND h.lifecycle_state='active' AND {predicate}"
    matches = f"FROM {source} {eligibility}"
    witness: tuple[Mapping[Any, Any], ...] = ()
    if connection.dialect.name != "sqlite" and required > 1:
        parameters["probe_limit"] = limit * 8
        probe = text(
            f"WITH raw_probe AS (SELECT h.artifact_id, h.revision, h.searchable_text, {score} AS raw_score "  # noqa: S608
            f"{matches} ORDER BY raw_score DESC, h.artifact_id, h.revision LIMIT :probe_limit) "
            "SELECT r.*, a.content, a.created_at, a.created_by, a.restored_from_revision, "
            "a.rollback_reason, 0 AS is_rejected FROM raw_probe AS r "
            "LEFT JOIN pc_artifacts AS a ON a.scope_id=:scope_id AND a.family=:family "
            "AND a.artifact_id=r.artifact_id AND a.revision=r.revision "
            "ORDER BY raw_score DESC, artifact_id, revision"
        )
        observed = tuple((await connection.execute(probe, parameters)).mappings())
        qualified = tuple(
            row for row in observed if admits_fts_projection(query, str(row["searchable_text"]), floor=admission)
        )
        witness = (
            ({"is_rejected": 1, "score_metric": metric},)
            if family == Experience.family and len(qualified) < len(observed)
            else ()
        )
        if len(qualified) >= limit or len(observed) < parameters["probe_limit"]:
            return witness + tuple({**row, "family": family, "score_metric": metric} for row in qualified[:limit])
    prefix = ""
    candidate_source = source
    if connection.dialect.name == "sqlite" and required > 1:
        parameters["probe_limit"] = limit * 4
        prefix = (
            f"raw_probe AS MATERIALIZED (SELECT {identity}h.artifact_id, h.revision, "  # noqa: S608
            f"{score} AS raw_score "
            f"{matches} ORDER BY raw_score, h.artifact_id, h.revision LIMIT :probe_limit), "
            "qualified_probe AS MATERIALIZED (SELECT r.* FROM raw_probe AS r "
            "CROSS JOIN pc_artifact_fts AS f ON f.rowid=r.projection_id "
            f"WHERE {coverage} >= :required), "
            "fallback_gate AS MATERIALIZED (SELECT 1 "
            "WHERE (SELECT COUNT(*) FROM raw_probe)=:probe_limit "
            "AND (SELECT COUNT(*) FROM qualified_probe)<:result_limit), "
        )
        # Keep the bounded prefix and the zero-or-one-row fallback gate outside FTS loops.
        candidate_source = "fallback_gate CROSS JOIN " + source.replace(" JOIN ", " CROSS JOIN ", 1)
    candidates = (
        f"SELECT {identity}h.artifact_id, h.revision, {score} AS raw_score "  # noqa: S608
        f"FROM {candidate_source} {eligibility} AND {coverage} >= :required "
        f"ORDER BY raw_score {direction}, h.artifact_id, h.revision LIMIT :result_limit"
    )
    if prefix:
        # An exact sorted prefix proves Top-K when enough qualify; otherwise retain the full query.
        candidates = (
            "SELECT * FROM (SELECT * FROM qualified_probe "  # noqa: S608
            "WHERE NOT EXISTS (SELECT 1 FROM fallback_gate) "
            "ORDER BY raw_score, artifact_id, revision LIMIT :result_limit) "
            f"UNION ALL SELECT * FROM ({candidates})"
        )
    rejected = (
        (
            " UNION ALL SELECT NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 1 FROM "
            "(SELECT 1 WHERE (SELECT COUNT(*) FROM raw_probe) > "
            "(SELECT COUNT(*) FROM qualified_probe)) AS rejected"
        )
        if family == Experience.family and prefix
        else ""
    )
    statement = text(
        f"WITH {prefix}candidates AS ({candidates}) "  # noqa: S608
        "SELECT c.artifact_id AS artifact_id, c.revision AS revision, p.searchable_text AS searchable_text, "
        "a.content, a.created_at, a.created_by, a.restored_from_revision, a.rollback_reason, "
        f"c.raw_score AS raw_score, 0 AS is_rejected FROM candidates AS c {projection_join}"
        "LEFT JOIN pc_artifacts AS a ON a.scope_id=:scope_id AND a.family=:family "
        f"AND a.artifact_id=c.artifact_id AND a.revision=c.revision{rejected} "
        f"ORDER BY is_rejected DESC, raw_score {direction}, artifact_id, revision"
    )
    rows = (await connection.execute(statement, parameters)).mappings()
    result = tuple({**row, "family": family, "score_metric": metric} for row in rows)
    return witness + result


def experience_search_hits(
    rows: Iterable[Mapping[Any, Any]],
    query: str,
    limit: int,
    scope_id: str,
    /,
    *,
    admission: AdmissionFloor | None = None,
    min_score: float | None = None,
    require_scores: bool = False,
) -> ExperienceSearchOutcome:
    """Decode backend-ordered rows and apply the shared lexical admission rule.

    ``admission=None`` applies the historical lexical floor bit for bit. Raw score metadata
    is optional for old custom indexes; public searches set ``require_scores=True``.

    ``retrieved`` counts the rows *examined*, not the rows kept: the loop stops as soon as
    ``limit`` final hits survive, so a backend that returned many rows for a narrow query is
    only visible through that count. ``scope_id`` is supplied by the caller because the
    decoder is the only place that knows both the rows and the Scope they were read from.
    """

    hits: list[ExperienceSearchHit] = []
    examined = 0
    admitted = 0
    for row in rows:
        examined += 1
        if row.get("is_rejected"):
            continue
        if row["content"] is None:
            raise RepositoryNotFoundError(
                "artifact",
                (
                    scope_id,
                    ArtifactRef(
                        family=Experience.family, artifact_id=str(row["artifact_id"]), revision=int(row["revision"])
                    ),
                ),
            )
        content = _content(row["content"])
        if not admits_fts_text(query, experience_search_text(content), floor=admission):
            continue
        admitted += 1
        retrieval_score, channel_scores = _search_scores(row, required=require_scores or min_score is not None)
        if min_score is not None and (retrieval_score is None or retrieval_score < min_score):
            continue
        hits.append(
            ExperienceSearchHit(
                artifact_ref=ArtifactRef(
                    family=Experience.family,
                    artifact_id=str(row["artifact_id"]),
                    revision=int(row["revision"]),
                ),
                content=content,
                retrieval_score=retrieval_score,
                channel_scores=channel_scores,
            )
        )
        if len(hits) >= limit:
            break
    return ExperienceSearchOutcome(
        hits=tuple(hits),
        mode="fts",
        admission=AdmissionCounts(
            family=Experience.family,
            scope_id=scope_id,
            retrieved=examined,
            admitted=admitted,
        ),
    )


def skill_search_hits(
    rows: Iterable[Mapping[Any, Any]],
    query: str,
    limit: int,
    /,
    *,
    admission: AdmissionFloor | None = None,
    min_score: float | None = None,
    require_scores: bool = False,
) -> tuple[SkillSearchHit, ...]:
    """Decode backend-ordered Skill rows and apply shared lexical admission."""

    hits: list[SkillSearchHit] = []
    for row in rows:
        if row["content"] is None:
            raise RepositoryNotFoundError(
                "artifact",
                ArtifactRef(family=Skill.family, artifact_id=str(row["artifact_id"]), revision=int(row["revision"])),
            )
        content = _skill_content(row["content"])
        searchable = row.get("searchable_text") or analyze_text(skill_search_text(content))
        if not admits_fts_projection(query, str(searchable), floor=admission):
            continue
        retrieval_score, channel_scores = _search_scores(row, required=require_scores or min_score is not None)
        if min_score is not None and (retrieval_score is None or retrieval_score < min_score):
            continue
        hits.append(
            SkillSearchHit(
                artifact_ref=ArtifactRef(
                    family=Skill.family,
                    artifact_id=str(row["artifact_id"]),
                    revision=int(row["revision"]),
                ),
                content=content,
                retrieval_score=retrieval_score,
                channel_scores=channel_scores,
            )
        )
        if len(hits) >= limit:
            break
    return tuple(hits)


def _search_scores(row: Mapping[Any, Any], /, *, required: bool) -> tuple[float | None, dict[str, ChannelScore] | None]:
    if "raw_score" not in row or "score_metric" not in row:
        if required or "raw_score" in row or "score_metric" in row:
            raise InvalidSearchScore("lexical raw score and metric are required")  # noqa: TRY003
        return None, None
    retrieval_score, channel = lexical_search_score(row["raw_score"], row["score_metric"])
    return retrieval_score, {"text": channel}


async def _update_searchable_text(
    connection: AsyncConnection,
    *,
    scope_id: str,
    family: str = Experience.family,
    artifact_id: str,
    revision: int,
    searchable_text: str,
) -> None:
    result = await connection.execute(
        update(ARTIFACT_HEADS_TABLE)
        .where(
            ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
            ARTIFACT_HEADS_TABLE.c.family == family,
            ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
            ARTIFACT_HEADS_TABLE.c.revision == revision,
        )
        .values(searchable_text=searchable_text)
    )
    if result.rowcount != 1:
        raise RepositoryNotFoundError(  # noqa: TRY003
            "artifact head",
            ArtifactRef(family=family, artifact_id=artifact_id, revision=revision),
        )


def _content(value: object) -> ExperienceContent:
    return load_model(
        ExperienceContent,
        stored_bytes(value, column="content"),
        kind="artifact",
        name=Experience.family,
    )


def _skill_content(value: object) -> SkillContent:
    return load_model(
        SkillContent,
        stored_bytes(value, column="content"),
        kind="artifact",
        name=Skill.family,
    )


async def _skill_projection_texts(connection, rows):
    references = {}
    searchable = {}
    for row in rows:
        content = _skill_content(row["content"])
        identity = (row["scope_id"], row["artifact_id"])
        if content.package is None:
            searchable[identity] = skill_searchable_text(content, None)
        else:
            key = (row["scope_id"], content.package.tree_digest)
            references.setdefault(key, []).append((identity, content))
    if not references:
        return searchable
    packages = SKILL_PACKAGES_TABLE
    statement = select(packages.c.scope_id, packages.c.tree_digest, packages.c.archive_bytes).where(
        tuple_(packages.c.scope_id, packages.c.tree_digest).in_(tuple(references))
    )
    async with connection.stream(statement.execution_options(yield_per=1)) as result:
        async for package in result.mappings():
            key = (package["scope_id"], package["tree_digest"])
            snapshot = capture_skill_archive(stored_bytes(package["archive_bytes"], column="archive_bytes"))
            for identity, content in references.pop(key):
                if snapshot.reference != content.package:
                    raise RepositoryNotFoundError("skill-package", key)
                searchable[identity] = skill_searchable_text(content, snapshot)
    if references:
        raise RepositoryNotFoundError("skill-package", next(iter(references)))
    return searchable


__all__ = [
    "ExperienceIndex",
    "NoExperienceIndex",
    "ensure_artifact_head_searchable_text",
    "experience_search_hits",
    "rebuild_experience_projections",
    "rebuild_skill_projections",
    "remove_experience_projection",
    "replace_experience_projection",
    "replace_skill_projection",
    "search_approved_artifact_rows",
    "skill_search_hits",
]
