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

"""Public managed Skill search over the existing approved-head lexical index."""

from typing import Protocol, cast

from sqlalchemy import select

from powercontext.artifacts import ArtifactRef
from powercontext.artifacts.search import (
    ArtifactSearchContractError,
    ArtifactSearchExecutionContext,
    ArtifactSearchMatch,
    ArtifactSearchUnsupported,
)
from powercontext.builtin.artifacts.skill import Skill, SkillSearchHit, SkillSearchOutcome, SkillSearchRequest
from powercontext.builtin.persistence.artifact_governance import (
    ArtifactGovernance,
    ArtifactLifecycleState,
    decode_artifact_governance,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository, with_artifact_content
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.experience_index import ExperienceIndex, skill_search_hits
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE
from powercontext.builtin.runtime.models import GetSkillRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.errors import ArtifactNotFoundError


class _SkillLibrary(Protocol):
    async def search(self, query: str, limit: int, /) -> tuple[SkillSearchHit, ...]: ...
    async def get(self, request: GetSkillRequest, /) -> Skill: ...
    async def governance(self, artifact_id: str, /) -> ArtifactGovernance: ...
    async def list(
        self, *, include_deprecated: bool = False, limit: int = 100
    ) -> tuple[tuple[Skill, ArtifactGovernance], ...]: ...


async def search_skill_library(
    scoped: _SkillLibrary, query: str, limit: int, /, *, include_deprecated: bool = False
) -> tuple[tuple[Skill, ArtifactGovernance], ...]:
    """Apply the legacy Library rule through existing scoped Skill operations."""

    query = query.strip()
    if not query:
        return (await scoped.list(include_deprecated=include_deprecated, limit=limit))[:limit]
    values: list[tuple[Skill, ArtifactGovernance]] = []
    for hit in await scoped.search(query, limit):
        skill = await scoped.get(GetSkillRequest(artifact=hit.artifact_ref))
        values.append((skill, await scoped.governance(skill.artifact_id)))
    listed = await scoped.list(include_deprecated=True, limit=limit) if include_deprecated else ()
    return _library_entries(query, values, listed, limit)


def _library_entries(
    query: str,
    values: list[tuple[Skill, ArtifactGovernance]],
    listed: tuple[tuple[Skill, ArtifactGovernance], ...],
    limit: int,
) -> tuple[tuple[Skill, ArtifactGovernance], ...]:
    if not query:
        return listed[:limit]
    seen = {skill.artifact_id for skill, _governance in values}
    for skill, governance in listed:
        search_text = "\n".join((
            skill.content.name,
            skill.content.description,
            skill.content.instructions,
            *skill.content.metadata.values(),
        ))
        if (
            governance.lifecycle_state is ArtifactLifecycleState.DEPRECATED
            and skill.artifact_id not in seen
            and query.casefold() in search_text.casefold()
        ):
            values.append((skill, governance))
    return tuple(values[:limit])


class SkillArtifactSearcher:
    """Own Skill request controls and exact final-result reads."""

    family = Skill.family
    request_type = SkillSearchRequest

    def __init__(self, database: AsyncDatabase, artifacts: ArtifactRepository, index: ExperienceIndex) -> None:
        self._database = database
        self._artifacts = artifacts
        self._index = index

    async def __call__(
        self, scope_id: str, include_deprecated: bool, limit: int, /, *, query: str = ""
    ) -> tuple[tuple[Skill, ArtifactGovernance], ...]:
        """Read the legacy Library window in one snapshot, preserving exact search hits."""

        query, scope = query.strip(), validate_scope_id(scope_id)
        if limit < 1:
            raise ValueError("Skill Library limit must be positive")  # noqa: TRY003
        states = (
            (ArtifactLifecycleState.ACTIVE.value, ArtifactLifecycleState.DEPRECATED.value)
            if include_deprecated
            else (ArtifactLifecycleState.ACTIVE.value,)
        )
        async with self._database.transaction(consistent_snapshot=bool(query)) as connection:
            values: list[tuple[Skill, ArtifactGovernance]] = []
            if query:
                rows = await self._index.search(connection, scope, "skill", query, limit)
                hits = skill_search_hits(rows, query, limit)
                refs = tuple(hit.artifact_ref for hit in hits)
                invalid_ref = next((ref for ref in refs if ref.family != Skill.family), None)
                if invalid_ref is not None:
                    raise ArtifactNotFoundError(invalid_ref)
                try:
                    by_ref = {
                        (str(row["artifact_id"]), int(row["revision"])): row
                        for row in rows
                        if not row.get("is_rejected")
                    }
                    skills = await self._artifacts.decode_many(
                        connection, scope, tuple(by_ref[(ref.artifact_id, ref.revision)] for ref in refs)
                    )
                except RepositoryNotFoundError as error:
                    raise ArtifactNotFoundError(cast(tuple[str, ArtifactRef], error.identity)[1]) from None
                governance = {}
                table = ARTIFACT_HEADS_TABLE
                for offset in range(0, len(refs), 100):
                    rows = (
                        await connection.execute(
                            select(table).where(
                                table.c.scope_id == scope,
                                table.c.family == Skill.family,
                                table.c.artifact_id.in_(tuple(ref.artifact_id for ref in refs[offset : offset + 100])),
                            )
                        )
                    ).mappings()
                    governance.update({row["artifact_id"]: decode_artifact_governance(row) for row in rows})
                for skill in skills:
                    if type(skill) is not Skill:
                        raise ArtifactNotFoundError(skill.as_ref())
                    if skill.artifact_id not in governance:
                        raise RepositoryNotFoundError("artifact-head", (scope, Skill.family, skill.artifact_id))
                    values.append((skill, governance[skill.artifact_id]))
                if not include_deprecated:
                    return tuple(values[:limit])
            heads = (
                select(ARTIFACT_HEADS_TABLE)
                .where(
                    ARTIFACT_HEADS_TABLE.c.scope_id == scope,
                    ARTIFACT_HEADS_TABLE.c.family == Skill.family,
                    ARTIFACT_HEADS_TABLE.c.lifecycle_state.in_(states),
                    ARTIFACT_HEADS_TABLE.c.merged_into_id.is_(None),
                )
                .order_by(ARTIFACT_HEADS_TABLE.c.artifact_id)
                .limit(limit)
                .subquery("selected_skill_heads")
            )
            rows = tuple(
                (
                    await connection.execute(with_artifact_content(select(heads), heads).order_by(heads.c.artifact_id))
                ).mappings()
            )
            artifacts = await self._artifacts.decode_many(connection, scope, rows)
            listed = tuple(
                (cast(Skill, skill), decode_artifact_governance(row))
                for skill, row in zip(artifacts, rows, strict=True)
            )
            return _library_entries(query, values, listed, limit)

    async def search(
        self,
        scope_id: str,
        request: SkillSearchRequest,
        /,
        *,
        execution_context: ArtifactSearchExecutionContext | None = None,
    ) -> SkillSearchOutcome:
        if self.family not in self._index.families:
            raise ArtifactSearchUnsupported(self.family, field="mode")
        scope = validate_scope_id(scope_id)
        async with self._database.transaction(consistent_snapshot=True) as connection:
            rows = await self._index.search(
                connection, scope, "skill", request.query, request.limit, admission=request.admission.as_floor()
            )
            try:
                hits = skill_search_hits(
                    rows,
                    request.query,
                    request.limit,
                    admission=request.admission.as_floor(),
                    min_score=request.min_score,
                    require_scores=True,
                )
                by_ref = {
                    (str(row["artifact_id"]), int(row["revision"])): row for row in rows if not row.get("is_rejected")
                }
                artifacts = await self._artifacts.decode_many(
                    connection,
                    scope,
                    tuple(by_ref[(hit.artifact_ref.artifact_id, hit.artifact_ref.revision)] for hit in hits),
                )
            except RepositoryNotFoundError as error:
                raise ArtifactSearchContractError(self.family, "selected exact Artifact revision is missing") from error
            matches = tuple(
                ArtifactSearchMatch(hit.artifact_ref, cast(float, hit.retrieval_score), hit.channel_scores)
                for hit in hits
            )
            return SkillSearchOutcome(hits=hits, matches=matches, artifacts=cast(tuple[Skill, ...], artifacts))


__all__ = ["SkillArtifactSearcher", "search_skill_library"]
