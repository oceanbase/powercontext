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

"""Unified Artifact search adapter over the current Atomic Memory projection."""

from typing import cast

from powercontext.artifacts.search import (
    ArtifactSearchContractError,
    ArtifactSearchExecutionContext,
    ArtifactSearchMatch,
    ArtifactSearchUnsupported,
)
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemory
from powercontext.builtin.artifacts.atomic_memory.search import (
    AtomicMemoryArtifactSearchOutcome,
    AtomicMemoryArtifactSearchRequest,
)
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.runtime.atomic_memory import AtomicMemoryApplication
from powercontext.builtin.sources import validate_scope_id


class AtomicMemoryArtifactSearcher:
    """Preserve Family authorization and ranking while returning exact Artifacts."""

    family = AtomicMemory.family
    request_type = AtomicMemoryArtifactSearchRequest

    def __init__(self, application: AtomicMemoryApplication) -> None:
        self._application = application

    async def search(
        self,
        scope_id: str,
        request: AtomicMemoryArtifactSearchRequest,
        /,
        *,
        execution_context: ArtifactSearchExecutionContext | None = None,
    ) -> AtomicMemoryArtifactSearchOutcome:
        if request.include_scores:
            # The existing page retains fused RRF ranks, not every native channel score.
            raise ArtifactSearchUnsupported(self.family, field="include_scores")
        scope = validate_scope_id(scope_id)
        page = await self._application.for_scope(scope).search(
            request.query,
            mode=request.mode,
            limit=request.limit,
            kind=request.filters.kind,
            tag_filter=request.filters.tag_filter,
            context=execution_context,
        )
        matches = tuple(ArtifactSearchMatch(item.hit.artifact_ref, item.hit.score) for item in page.hits)
        artifacts: list[AtomicMemory] = []
        async with self._application.database.transaction(consistent_snapshot=True) as connection:
            for match in matches:
                try:
                    artifact = await self._application.artifacts.get(connection, scope, match.artifact_ref)
                except RepositoryNotFoundError as error:
                    raise ArtifactSearchContractError(self.family, "selected exact Artifact is missing") from error
                artifacts.append(cast(AtomicMemory, artifact))
        return AtomicMemoryArtifactSearchOutcome(matches, tuple(artifacts))


__all__ = ["AtomicMemoryArtifactSearcher"]
