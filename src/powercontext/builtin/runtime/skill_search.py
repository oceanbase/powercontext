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

from powercontext.artifacts.search import (
    ArtifactSearchContractError,
    ArtifactSearchExecutionContext,
    ArtifactSearchMatch,
    ArtifactSearchUnsupported,
)
from powercontext.builtin.artifacts.search import InvalidSearchScore
from powercontext.builtin.artifacts.skill import Skill, SkillSearchHit, SkillSearchOutcome, SkillSearchRequest
from powercontext.builtin.persistence.artifact_governance import ArtifactGovernance, ArtifactLifecycleState
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.experience_index import ExperienceIndex, NoExperienceIndex
from powercontext.builtin.runtime.models import GetSkillRequest
from powercontext.builtin.sources import validate_scope_id


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
    if include_deprecated:
        seen = {skill.artifact_id for skill, _governance in values}
        for skill, governance in await scoped.list(include_deprecated=True, limit=limit):
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

    async def search(
        self,
        scope_id: str,
        request: SkillSearchRequest,
        /,
        *,
        execution_context: ArtifactSearchExecutionContext | None = None,
    ) -> SkillSearchOutcome:
        if isinstance(self._index, NoExperienceIndex):
            raise ArtifactSearchUnsupported(self.family, field="mode")
        scope = validate_scope_id(scope_id)
        async with self._database.transaction() as connection:
            hits = await self._index.search_skills(
                connection,
                scope,
                request.query,
                request.limit,
                admission=request.admission.as_floor(),
                min_score=request.min_score,
                require_scores=True,
            )
            matches: list[ArtifactSearchMatch] = []
            artifacts: list[Skill] = []
            for hit in hits:
                if hit.retrieval_score is None or hit.channel_scores is None or "text" not in hit.channel_scores:
                    raise InvalidSearchScore("public Skill search requires true text scoring metadata")  # noqa: TRY003
                matches.append(ArtifactSearchMatch(hit.artifact_ref, hit.retrieval_score, hit.channel_scores))
                try:
                    artifact = await self._artifacts.get(connection, scope, hit.artifact_ref)
                except RepositoryNotFoundError as error:
                    raise ArtifactSearchContractError(
                        self.family, "selected exact Artifact revision is missing"
                    ) from error
                artifacts.append(cast(Skill, artifact))
            return SkillSearchOutcome(hits=hits, matches=tuple(matches), artifacts=tuple(artifacts))


__all__ = ["SkillArtifactSearcher", "search_skill_library"]
