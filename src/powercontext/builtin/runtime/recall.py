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

"""Estimate token reduction for one final prepared context."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryCitation, MemoryService
from powercontext.builtin.inference import TokenEstimator
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.runtime.prepared_context import (
    MemoryEntryAddress,
    PreparedContextBuild,
    PreparedContextOrigin,
)
from powercontext.builtin.sources import ContentSource, ExternalSkillSnapshotSource
from powercontext.builtin.statistics import RecallTokenMeasurement
from powercontext.sources import Source, SourceRef


class RecallTokenEstimationError(RuntimeError):
    """Report a Source type without a stable primary-text projection."""

    def __init__(self, source: Source) -> None:
        super().__init__(f"no recall token projection is registered for {type(source).__name__}")


SourceAddress = tuple[str, str, str]


class _RecallOriginResolver:
    def __init__(
        self,
        *,
        connection: AsyncConnection,
        current_scope_id: str,
        artifacts: ArtifactRepository,
        memory_service: Callable[[str, AsyncConnection], MemoryService],
    ) -> None:
        self._connection = connection
        self._current_scope_id = current_scope_id
        self._artifacts = artifacts
        self._memory_service = memory_service
        self._artifact_sources: dict[tuple[str, str, str, int], frozenset[SourceAddress]] = {}
        self._resolving_artifacts: set[tuple[str, str, str, int]] = set()

    async def resolve(self, origin: PreparedContextOrigin, /) -> set[SourceAddress]:
        if isinstance(origin, MemoryCitation):
            return await self._memory(self._current_scope_id, origin)
        if isinstance(origin, MemoryEntryAddress):
            return await self._memory(
                origin.memory.scope_id,
                MemoryCitation(
                    memory_ref=origin.memory.artifact,
                    entry_id=origin.entry_id,
                    entry_version_id=origin.entry_version_id,
                ),
            )
        if isinstance(origin, ArtifactAddress):
            return set(await self._artifact(origin.scope_id, origin.artifact))
        return set(await self._artifact(self._current_scope_id, origin))

    async def _memory(self, scope_id: str, citation: MemoryCitation) -> set[SourceAddress]:
        memory = self._memory_service(scope_id, self._connection)
        entry = await memory.validate_citation(citation)
        sources = _source_identities(scope_id, entry.sources)
        for artifact_ref in entry.artifacts:
            sources.update(await self._artifact(scope_id, artifact_ref))
        return sources

    async def _artifact(self, scope_id: str, artifact_ref: ArtifactRef) -> frozenset[SourceAddress]:
        identity = _artifact_identity(scope_id, artifact_ref)
        if identity in self._artifact_sources:
            return self._artifact_sources[identity]
        if identity in self._resolving_artifacts:
            return frozenset()
        self._resolving_artifacts.add(identity)
        artifact = await self._artifacts.get(self._connection, scope_id, artifact_ref)
        resolved = _source_identities(scope_id, artifact.lineage.sources)
        for parent in artifact.lineage.artifacts:
            resolved.update(await self._artifact(scope_id, parent))
        self._resolving_artifacts.remove(identity)
        result = frozenset(resolved)
        self._artifact_sources[identity] = result
        return result


class RelationalRecallTokenEstimator:
    """Resolve exact recall lineage and estimate its token reduction."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        memory_service: Callable[[str, AsyncConnection], MemoryService],
        estimator: TokenEstimator,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._sources = sources
        self._artifacts = artifacts
        self._memory_service = memory_service
        self._estimator = estimator

    async def estimate(self, build: PreparedContextBuild, /) -> RecallTokenMeasurement:
        source_refs: set[tuple[str, str, str]] = set()
        comparable = build.context.status == "ready" and bool(build.origins)

        async with self._database.transaction() as connection:
            resolver = _RecallOriginResolver(
                connection=connection,
                current_scope_id=self._scope_id,
                artifacts=self._artifacts,
                memory_service=self._memory_service,
            )
            for origin in build.origins:
                origin_sources = await resolver.resolve(origin)
                comparable = comparable and bool(origin_sources)
                source_refs.update(origin_sources)

            if not comparable:
                return RecallTokenMeasurement(
                    estimator=self._estimator.profile,
                    ready=build.context.status == "ready",
                    comparable=False,
                    baseline_tokens=0,
                    recalled_tokens=0,
                )

            texts = []
            for scope_id, source_type, source_id in sorted(
                source_refs,
                key=lambda ref: (ref[0].encode(), ref[1].encode(), ref[2].encode()),
            ):
                stored = await self._sources.get(
                    connection,
                    scope_id,
                    SourceRef(source_type=source_type, source_id=source_id),
                )
                texts.append(_source_text(stored.value))

        final_content = build.context.content or ""
        return RecallTokenMeasurement(
            estimator=self._estimator.profile,
            ready=build.context.status == "ready",
            comparable=True,
            baseline_tokens=sum(self._estimator.estimate(text) for text in texts),
            recalled_tokens=self._estimator.estimate(final_content),
        )


def _source_identities(scope_id: str, sources: tuple[SourceRef, ...], /) -> set[SourceAddress]:
    return {(scope_id, source.source_type, source.source_id) for source in sources}


def _artifact_identity(scope_id: str, artifact: ArtifactRef, /) -> tuple[str, str, str, int]:
    return scope_id, artifact.family, artifact.artifact_id, artifact.revision


def _source_text(source: Source, /) -> str:
    if isinstance(source, ContentSource):
        return source.content
    if isinstance(source, ExternalSkillSnapshotSource):
        return source.snapshot.manifest
    raise RecallTokenEstimationError(source)


__all__ = ["RecallTokenEstimationError", "RelationalRecallTokenEstimator"]
