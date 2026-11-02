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

"""Public Experience search over the existing approved-head lexical index."""

from dataclasses import replace
from typing import cast

from powercontext.artifacts.search import (
    ArtifactSearchContractError,
    ArtifactSearchExecutionContext,
    ArtifactSearchMatch,
    ArtifactSearchUnsupported,
)
from powercontext.builtin.artifacts.experience import Experience, ExperienceSearchOutcome, ExperienceSearchRequest
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.experience_index import ExperienceIndex, experience_search_hits
from powercontext.builtin.sources import validate_scope_id


class ExperienceArtifactSearcher:
    """Own Experience request controls and exact final-result reads."""

    family = Experience.family
    request_type = ExperienceSearchRequest

    def __init__(self, database: AsyncDatabase, artifacts: ArtifactRepository, index: ExperienceIndex) -> None:
        self._database = database
        self._artifacts = artifacts
        self._index = index

    async def search(
        self,
        scope_id: str,
        request: ExperienceSearchRequest,
        /,
        *,
        execution_context: ArtifactSearchExecutionContext | None = None,
    ) -> ExperienceSearchOutcome:
        if self.family not in self._index.families:
            raise ArtifactSearchUnsupported(self.family, field="mode")
        scope = validate_scope_id(scope_id)
        async with self._database.transaction(consistent_snapshot=True) as connection:
            rows = await self._index.search(
                connection, scope, "experience", request.query, request.limit, admission=request.admission.as_floor()
            )
            try:
                outcome = experience_search_hits(
                    rows,
                    request.query,
                    request.limit,
                    scope,
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
                    tuple(by_ref[(hit.artifact_ref.artifact_id, hit.artifact_ref.revision)] for hit in outcome.hits),
                )
            except RepositoryNotFoundError as error:
                raise ArtifactSearchContractError(self.family, "selected exact Artifact revision is missing") from error
            matches = tuple(
                ArtifactSearchMatch(hit.artifact_ref, cast(float, hit.retrieval_score), hit.channel_scores)
                for hit in outcome.hits
            )
            return replace(outcome, matches=matches, artifacts=cast(tuple[Experience, ...], artifacts))


__all__ = ["ExperienceArtifactSearcher"]
