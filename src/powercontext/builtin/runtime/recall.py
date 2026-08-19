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

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryCitation, MemoryService
from powercontext.builtin.inference import TokenEstimator
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.runtime.prepared_context import PreparedContextBuild
from powercontext.builtin.sources import ContentSource, ExternalSkillSnapshotSource
from powercontext.builtin.statistics import RecallTokenMeasurement
from powercontext.sources import Source, SourceRef


class RecallTokenEstimationError(RuntimeError):
    """Report a Source type without a stable primary-text projection."""

    def __init__(self, source: Source) -> None:
        super().__init__(f"no recall token projection is registered for {type(source).__name__}")


class RelationalRecallTokenEstimator:
    """Resolve exact recall lineage and estimate its token reduction."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        memory_service: Callable[[AsyncConnection], MemoryService],
        estimator: TokenEstimator,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._sources = sources
        self._artifacts = artifacts
        self._memory_service = memory_service
        self._estimator = estimator

    async def estimate(self, build: PreparedContextBuild, /) -> RecallTokenMeasurement:
        source_refs: set[tuple[str, str]] = set()
        comparable = build.context.status == "ready" and bool(build.origins)

        async with self._database.transaction() as connection:
            memory = self._memory_service(connection)
            artifact_sources: dict[tuple[str, str, int], frozenset[tuple[str, str]]] = {}
            resolving_artifacts: set[tuple[str, str, int]] = set()

            async def resolve_artifact(artifact_ref: ArtifactRef) -> frozenset[tuple[str, str]]:
                identity = _artifact_identity(artifact_ref)
                if identity in artifact_sources:
                    return artifact_sources[identity]
                if identity in resolving_artifacts:
                    return frozenset()
                resolving_artifacts.add(identity)
                artifact = await self._artifacts.get(connection, self._scope_id, artifact_ref)
                resolved = _source_identities(artifact.lineage.sources)
                for parent in artifact.lineage.artifacts:
                    resolved.update(await resolve_artifact(parent))
                resolving_artifacts.remove(identity)
                result = frozenset(resolved)
                artifact_sources[identity] = result
                return result

            for origin in build.origins:
                if isinstance(origin, MemoryCitation):
                    entry = await memory.validate_citation(origin)
                    origin_sources = _source_identities(entry.sources)
                    for artifact_ref in entry.artifacts:
                        origin_sources.update(await resolve_artifact(artifact_ref))
                else:
                    origin_sources = set(await resolve_artifact(origin))
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
            for source_type, source_id in sorted(source_refs, key=lambda ref: (ref[0].encode(), ref[1].encode())):
                stored = await self._sources.get(
                    connection,
                    self._scope_id,
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


def _source_identities(sources: tuple[SourceRef, ...], /) -> set[tuple[str, str]]:
    return {(source.source_type, source.source_id) for source in sources}


def _artifact_identity(artifact: ArtifactRef, /) -> tuple[str, str, int]:
    return artifact.family, artifact.artifact_id, artifact.revision


def _source_text(source: Source, /) -> str:
    if isinstance(source, ContentSource):
        return source.content
    if isinstance(source, ExternalSkillSnapshotSource):
        return source.snapshot.manifest
    raise RecallTokenEstimationError(source)


__all__ = ["RecallTokenEstimationError", "RelationalRecallTokenEstimator"]
