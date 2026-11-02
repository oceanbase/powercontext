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

from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactAddress, ArtifactLineage, ArtifactRef
from powercontext.builtin.inference import TokenEstimator
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.runtime.prepared_context import PreparedContextBuild, PreparedContextOrigin
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
        lineages: tuple[tuple[ArtifactAddress, ArtifactLineage], ...],
    ) -> None:
        self._connection = connection
        self._current_scope_id = current_scope_id
        self._artifacts = artifacts
        self._lineages = {
            _artifact_identity(address.scope_id, address.artifact): lineage for address, lineage in lineages
        }
        self._artifact_sources: dict[tuple[str, str, str, int], frozenset[SourceAddress]] = {}
        self._resolving_artifacts: set[tuple[str, str, str, int]] = set()

    async def resolve(self, origins: tuple[PreparedContextOrigin, ...], /) -> tuple[frozenset[SourceAddress], ...]:
        roots = tuple(
            (origin.scope_id, origin.artifact)
            if isinstance(origin, ArtifactAddress)
            else (self._current_scope_id, origin)
            for origin in origins
        )
        pending = roots
        expanded: set[tuple[str, str, str, int]] = set()
        while pending:
            grouped: dict[str, list[ArtifactRef]] = defaultdict(list)
            for scope_id, ref in pending:
                if _artifact_identity(scope_id, ref) not in self._lineages:
                    grouped[scope_id].append(ref)
            for scope_id, refs in grouped.items():
                artifacts = await self._artifacts.get_many(self._connection, scope_id, refs)
                for artifact in artifacts:
                    self._lineages[_artifact_identity(scope_id, artifact.as_ref())] = artifact.lineage
            expanded.update(_artifact_identity(scope_id, ref) for scope_id, ref in pending)
            parents = {
                _artifact_identity(scope_id, parent): (scope_id, parent)
                for scope_id, ref in pending
                for parent in self._lineages[_artifact_identity(scope_id, ref)].artifacts
                if _artifact_identity(scope_id, parent) not in expanded
            }
            pending = tuple(parents.values())
        return tuple(self._artifact(scope_id, ref) for scope_id, ref in roots)

    def _artifact(self, scope_id: str, artifact_ref: ArtifactRef) -> frozenset[SourceAddress]:
        identity = _artifact_identity(scope_id, artifact_ref)
        if identity in self._artifact_sources:
            return self._artifact_sources[identity]
        if identity in self._resolving_artifacts:
            return frozenset()
        self._resolving_artifacts.add(identity)
        lineage = self._lineages[identity]
        resolved = _source_identities(scope_id, lineage.sources)
        for parent in lineage.artifacts:
            resolved.update(self._artifact(scope_id, parent))
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
        estimator: TokenEstimator,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._sources = sources
        self._artifacts = artifacts
        self._estimator = estimator

    async def estimate(self, build: PreparedContextBuild, /) -> RecallTokenMeasurement:
        source_refs: set[tuple[str, str, str]] = set()
        comparable = build.context.status == "ready" and bool(build.origins)

        async with self._database.transaction() as connection:
            resolver = _RecallOriginResolver(
                connection=connection,
                current_scope_id=self._scope_id,
                artifacts=self._artifacts,
                lineages=build.lineages,
            )
            for origin_sources in await resolver.resolve(build.origins):
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
            grouped_sources: dict[str, list[SourceRef]] = defaultdict(list)
            for scope_id, source_type, source_id in sorted(
                source_refs,
                key=lambda ref: (ref[0].encode(), ref[1].encode(), ref[2].encode()),
            ):
                grouped_sources[scope_id].append(SourceRef(source_type=source_type, source_id=source_id))
            for scope_id, refs in grouped_sources.items():
                for offset in range(0, len(refs), 100):
                    stored = await self._sources.get_many(connection, scope_id, refs[offset : offset + 100])
                    texts.extend(_source_text(source.value) for source in stored)

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
