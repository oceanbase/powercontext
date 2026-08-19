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

"""Relational persistence and evidence resolution for Handoffs."""

from __future__ import annotations

from typing import cast

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff import (
    Handoff,
    HandoffArtifactCitation,
    HandoffArtifactDraft,
    HandoffArtifactEvidence,
    HandoffCitation,
    HandoffEvidenceUnavailableError,
    HandoffGenerationEvidence,
    HandoffMemoryCitation,
    HandoffMemoryEvidence,
    HandoffSourceCitation,
    HandoffSourceEvidence,
)
from powercontext.builtin.artifacts.memory import (
    InvalidMemoryCitationError,
    MemoryEntryNotFoundError,
    MemoryService,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.errors import ArtifactNotFoundError


class RelationalHandoffBackend:
    """Store one Handoff lifecycle in the shared Artifact tables."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        artifacts: ArtifactRepository,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._artifacts = artifacts

    async def create(self, artifact_id: str, draft: HandoffArtifactDraft, /) -> Handoff:
        async with self._database.transaction() as connection:
            artifact = await self._artifacts.create(
                connection,
                self._scope_id,
                artifact_id,
                draft,
            )
        return cast(Handoff, artifact)

    async def revise(self, base: Handoff, draft: HandoffArtifactDraft, /) -> Handoff:
        async with self._database.transaction() as connection:
            artifact = await self._artifacts.revise(
                connection,
                self._scope_id,
                base,
                draft,
            )
        return cast(Handoff, artifact)

    async def get(self, reference: ArtifactRef, /) -> Handoff:
        try:
            async with self._database.transaction() as connection:
                artifact = await self._artifacts.get(connection, self._scope_id, reference)
        except RepositoryNotFoundError:
            raise ArtifactNotFoundError(reference) from None
        return cast(Handoff, artifact)

    async def latest(self, artifact_id: str, /) -> Handoff | None:
        try:
            async with self._database.transaction() as connection:
                artifact = await self._artifacts.latest(
                    connection,
                    self._scope_id,
                    Handoff.family,
                    artifact_id,
                )
        except RepositoryNotFoundError:
            return None
        return cast(Handoff, artifact)

    async def revisions(self, artifact_id: str, /) -> tuple[Handoff, ...]:
        async with self._database.transaction() as connection:
            artifacts = await self._artifacts.revisions(
                connection,
                self._scope_id,
                Handoff.family,
                artifact_id,
            )
        return cast(tuple[Handoff, ...], artifacts)


class RelationalHandoffEvidenceResolver:
    """Resolve Handoff citations against immutable records in one scope."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        memory: MemoryService,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._sources = sources
        self._artifacts = artifacts
        self._memory = memory

    async def resolve(self, citation: HandoffCitation, /) -> HandoffGenerationEvidence:
        try:
            if isinstance(citation, HandoffSourceCitation):
                async with self._database.transaction() as connection:
                    source = await self._sources.get(connection, self._scope_id, citation.source_ref)
                return HandoffSourceEvidence(citation=citation, source=source.value)
            if isinstance(citation, HandoffArtifactCitation):
                async with self._database.transaction() as connection:
                    artifact = await self._artifacts.get(connection, self._scope_id, citation.artifact_ref)
                return HandoffArtifactEvidence(citation=citation, artifact=artifact)
            if isinstance(citation, HandoffMemoryCitation):
                entry = await self._memory.validate_citation(citation.memory_citation)
                return HandoffMemoryEvidence(citation=citation, entry=entry)
        except (
            ArtifactNotFoundError,
            InvalidMemoryCitationError,
            MemoryEntryNotFoundError,
            RepositoryNotFoundError,
        ) as error:
            raise HandoffEvidenceUnavailableError(citation) from error
        raise TypeError(f"unsupported Handoff citation: {type(citation).__name__}")  # noqa: TRY003

    async def validate(self, citation: HandoffCitation, /) -> None:
        await self.resolve(citation)


__all__ = ["RelationalHandoffBackend", "RelationalHandoffEvidenceResolver"]
