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

from collections.abc import Sequence
from typing import cast

from sqlalchemy.ext.asyncio import AsyncConnection

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
from powercontext.builtin.persistence.generation_sources import GenerationSourceAccess
from powercontext.errors import ArtifactNotFoundError


class RelationalHandoffBackend:
    """Store one Handoff lifecycle in the shared Artifact tables."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        artifacts: ArtifactRepository,
        connection: AsyncConnection | None = None,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._artifacts = artifacts
        self._bound_connection = connection

    async def create(self, artifact_id: str, draft: HandoffArtifactDraft, /) -> Handoff:
        async with self._database.connection(self._bound_connection) as connection:
            artifact = await self._artifacts.create(
                connection,
                self._scope_id,
                artifact_id,
                draft,
            )
        return cast(Handoff, artifact)

    async def revise(self, base: Handoff, draft: HandoffArtifactDraft, /) -> Handoff:
        async with self._database.connection(self._bound_connection) as connection:
            artifact = await self._artifacts.revise(
                connection,
                self._scope_id,
                base,
                draft,
            )
        return cast(Handoff, artifact)

    async def get(self, reference: ArtifactRef, /) -> Handoff:
        try:
            async with self._database.connection(self._bound_connection) as connection:
                artifact = await self._artifacts.get(connection, self._scope_id, reference)
        except RepositoryNotFoundError:
            raise ArtifactNotFoundError(reference) from None
        return cast(Handoff, artifact)

    async def latest(self, artifact_id: str, /) -> Handoff | None:
        try:
            async with self._database.connection(self._bound_connection) as connection:
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
        async with self._database.connection(self._bound_connection) as connection:
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
        sources: GenerationSourceAccess,
        artifacts: ArtifactRepository,
        memory: MemoryService,
        connection: AsyncConnection | None = None,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._sources = sources
        self._artifacts = artifacts
        self._memory = memory
        self._bound_connection = connection

    async def resolve(self, citation: HandoffCitation, /) -> HandoffGenerationEvidence:
        return (await self.resolve_many((citation,)))[0]

    async def resolve_many(
        self,
        citations: Sequence[HandoffCitation],
        /,
    ) -> tuple[HandoffGenerationEvidence, ...]:
        requested = tuple(citations)
        source_refs = tuple(
            citation.source_ref for citation in requested if isinstance(citation, HandoffSourceCitation)
        )
        artifact_refs = tuple(
            citation.artifact_ref for citation in requested if isinstance(citation, HandoffArtifactCitation)
        )
        try:
            async with self._database.connection(self._bound_connection) as connection:
                sources = await self._sources.require_for_generation(connection, self._scope_id, source_refs)
                artifacts = await self._artifacts.get_many(connection, self._scope_id, artifact_refs)
        except RepositoryNotFoundError as error:
            unavailable = _missing_citation(requested, error)
            raise HandoffEvidenceUnavailableError(unavailable) from error

        source_values = {(row.ref.source_type, row.ref.source_id): row.value for row in sources}
        artifact_values = {
            (artifact.family, artifact.artifact_id, artifact.revision): artifact for artifact in artifacts
        }
        evidence: list[HandoffGenerationEvidence] = []
        for citation in requested:
            if isinstance(citation, HandoffSourceCitation):
                source = source_values[(citation.source_ref.source_type, citation.source_ref.source_id)]
                evidence.append(HandoffSourceEvidence(citation=citation, source=source))
            elif isinstance(citation, HandoffArtifactCitation):
                reference = citation.artifact_ref
                evidence.append(
                    HandoffArtifactEvidence(
                        citation=citation,
                        artifact=artifact_values[(reference.family, reference.artifact_id, reference.revision)],
                    )
                )
            elif isinstance(citation, HandoffMemoryCitation):
                try:
                    entry = await self._memory.validate_citation(citation.memory_citation)
                except (ArtifactNotFoundError, InvalidMemoryCitationError, MemoryEntryNotFoundError) as error:
                    raise HandoffEvidenceUnavailableError(citation) from error
                evidence.append(HandoffMemoryEvidence(citation=citation, entry=entry))
            else:
                raise TypeError(f"unsupported Handoff citation: {type(citation).__name__}")  # noqa: TRY003
        return tuple(evidence)

    async def validate(self, citation: HandoffCitation, /) -> None:
        await self.resolve(citation)


def _missing_citation(
    citations: tuple[HandoffCitation, ...],
    error: RepositoryNotFoundError,
) -> HandoffCitation:
    identity = error.identity
    missing = identity[-1] if isinstance(identity, tuple) and identity else None
    for citation in citations:
        if isinstance(citation, HandoffSourceCitation) and citation.source_ref == missing:
            return citation
        if isinstance(citation, HandoffArtifactCitation) and citation.artifact_ref == missing:
            return citation
    if citations:
        return citations[0]
    raise error


__all__ = ["RelationalHandoffBackend", "RelationalHandoffEvidenceResolver"]
