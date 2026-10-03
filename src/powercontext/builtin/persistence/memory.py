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

"""SQLAlchemy implementation of the built-in Memory backend contract."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any, ClassVar, TypeVar

from pydantic import RootModel
from sqlalchemy import delete, insert, select, tuple_
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactDraft, ArtifactRef
from powercontext.builtin.artifacts.memory import (
    EmbeddingProfile,
    InvalidEmbeddingError,
    Memory,
    MemoryCapabilities,
    MemoryCommit,
    MemoryContent,
    MemoryEntryVersion,
    MemoryEvidenceSnapshot,
    MemoryHit,
    MemoryLifecycleProjection,
    MemoryProjection,
    MemoryRevisionChanges,
    MemorySearchChannels,
    MemorySearchRequest,
    MemoryUnitOfWork,
)
from powercontext.builtin.artifacts.memory.canonical import (
    canonical_embedding,
    embedding_content_hash,
    entry_content_hash,
    memory_content_hash,
)
from powercontext.builtin.artifacts.memory.errors import (
    CapabilityNotSupportedError,
    InvalidMemoryCitationError,
    MemoryBackendConfigurationError,
)
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.database import SELECTION_BATCH_SIZE, AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.memory_index import MemoryIndex, NoMemoryIndex
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_TAGS_TABLE,
    MEMORY_ENTRY_EVIDENCE_TABLE,
    MEMORY_ENTRY_HEADS_TABLE,
    MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
)
from powercontext.builtin.persistence.tags import tag_predicate
from powercontext.builtin.tags import TagFilter
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import (
    MemoryEvidenceAuthority,
    MemoryEvidenceDeclaration,
    MemoryEvidenceVerification,
    SourceRef,
)


class _SourceRefs(RootModel[tuple[SourceRef, ...]]):
    pass


class _ArtifactRefs(RootModel[tuple[ArtifactRef, ...]]):
    pass


# The evidence lookup binds three identity columns per entry, so its rows chunk at
# the scope's share of the shared bind budget. The single-column entry-version
# lookups bind one value per row and chunk at the row budget less the fixed
# parameters that accompany the batch: the scope, the artifact identity, and the
# correlated tag predicate, which binds about two values per requested label.
# ``TagFilter`` admits at most 16 labels, so the reserve covers that maximum with
# headroom instead of landing exactly on the budget.
_EVIDENCE_BIND_RESERVE = 1
_ENTRY_BIND_RESERVE = 40
_ENTRY_SELECTION_BATCH_SIZE = max(1, SELECTION_BATCH_SIZE - _ENTRY_BIND_RESERVE)
_EVIDENCE_SELECTION_BATCH_SIZE = max(1, (SELECTION_BATCH_SIZE - _EVIDENCE_BIND_RESERVE) // 3)

_BatchItem = TypeVar("_BatchItem")


def _batched(items: Iterable[_BatchItem], size: int, /) -> Iterator[tuple[_BatchItem, ...]]:
    """Yield disjoint chunks of at most ``size`` items.

    One statement may bind only a bounded number of values, so a lookup over an
    unbounded manifest has to be chunked. Sharing this helper keeps every lookup
    on the same budget instead of letting them drift apart.
    """

    batch: list[_BatchItem] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield tuple(batch)
            batch = []
    if batch:
        yield tuple(batch)


class _MemoryDraft(ArtifactDraft[MemoryContent]):
    family: ClassVar[str] = Memory.family


@dataclass(frozen=True, slots=True)
class _RebuildSnapshot:
    memory_ref: ArtifactRef
    projections: tuple[MemoryProjection, ...]
    lifecycle: tuple[MemoryLifecycleProjection, ...]


class _InvalidMemoryCommitError(MemoryBackendConfigurationError):
    def __init__(self, code: str, actual: str | None = None) -> None:
        details = {
            "artifact-result": "generic Artifact result differs from prepared revision",
            "base-identity": "base and revision identities differ",
            "complete": "unit of work is already complete",
            "content-hash": "content hash does not match canonical content",
            "family": "family is not memory",
            "memory-type": f"expected Memory, got {actual}",
            "projection": "active manifest and projections differ",
            "revision": "revision is not the next prepared revision",
        }
        super().__init__(f"invalid relational Memory commit: {details[code]}")


class _MemoryProjectionRebuildError(MemoryBackendConfigurationError):
    def __init__(self, code: str) -> None:
        messages = {
            "snapshot": "authoritative Memory heads changed while projections were rebuilt",
            "vector": "the configured Memory index has no vector capability",
            "profile": "the rebuild embedding profile does not match the Memory index",
        }
        super().__init__(messages[code])


class RelationalMemoryBackend:
    """Use shared Artifact revisions plus Memory-owned entry projections."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        artifacts: ArtifactRepository,
        index: MemoryIndex | None = None,
        connection: AsyncConnection | None = None,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._artifacts = artifacts
        self._index = NoMemoryIndex() if index is None else index
        self._bound_connection = connection

    async def capabilities(self) -> MemoryCapabilities:
        return self._index.capabilities

    async def get(self, memory: ArtifactRef, /) -> Memory:
        if memory.family != Memory.family:
            raise ArtifactNotFoundError(memory)
        async with self._database.connection(self._bound_connection) as connection:
            try:
                artifact = await self._artifacts.get(connection, self._scope_id, memory)
            except RepositoryNotFoundError:
                raise ArtifactNotFoundError(memory) from None
        return _require_memory(artifact)

    async def latest(self, artifact_id: str, /) -> Memory:
        async with self._database.connection(self._bound_connection) as connection:
            try:
                artifact = await self._artifacts.latest(
                    connection,
                    self._scope_id,
                    Memory.family,
                    artifact_id,
                )
            except RepositoryNotFoundError:
                raise ArtifactNotFoundError(artifact_id) from None
        return _require_memory(artifact)

    async def tagged_entry_ids(self, memory: ArtifactRef, tag_filter: TagFilter) -> frozenset[str]:
        canonical = await self.get(memory)
        versions = tuple(entry.entry_version_id for entry in canonical.content.manifest.entries)
        table = MEMORY_ENTRY_VERSIONS_TABLE
        tagged: set[str] = set()
        async with self._database.connection(self._bound_connection) as connection:
            for batch in _batched(dict.fromkeys(versions), _ENTRY_SELECTION_BATCH_SIZE):
                rows = await connection.scalars(
                    select(table.c.entry_id).where(
                        table.c.scope_id == self._scope_id,
                        table.c.memory_artifact_id == memory.artifact_id,
                        table.c.entry_version_id.in_(batch),
                        tag_predicate(
                            self._scope_id,
                            "memory",
                            table.c.memory_artifact_id,
                            "memory_entry",
                            table.c.entry_id,
                            tag_filter,
                        ),
                    )
                )
                tagged.update(rows)
        return frozenset(tagged)

    async def any_tagged_entry_ids(self, memory: ArtifactRef, /) -> frozenset[str]:
        await self.get(memory)
        async with self._database.connection(self._bound_connection) as connection:
            return await self._any_tagged_entry_ids(connection, memory)

    async def _any_tagged_entry_ids(
        self, connection: AsyncConnection, memory: ArtifactRef, *, for_update: bool = False
    ) -> frozenset[str]:
        table = ARTIFACT_TAGS_TABLE
        query = select(table.c.target_id).where(
            table.c.scope_id == self._scope_id,
            table.c.family == Memory.family,
            table.c.artifact_id == memory.artifact_id,
            table.c.target_type == "memory_entry",
        )
        return frozenset(await connection.scalars(query.with_for_update() if for_update else query))

    async def entries(self, memory: ArtifactRef, /) -> tuple[MemoryEntryVersion, ...]:
        canonical = await self.get(memory)
        version_ids = tuple(item.entry_version_id for item in canonical.content.manifest.entries)
        if not version_ids:
            return ()
        async with self._database.connection(self._bound_connection) as connection:
            return await self._hydrate_entry_versions(connection, memory, version_ids)

    async def lifecycle_projections(self, memory: ArtifactRef, /) -> tuple[MemoryLifecycleProjection, ...]:
        """Load the internal neutral lifecycle projection for one exact Memory head.

        Rows are keyed by entry and re-stamped only by the revision that changes
        them, so the read selects the artifact's rows and proves completeness by
        entry identity, exactly as the active-head projection does.
        """

        canonical = await self.get(memory)
        manifest = canonical.content.manifest.entries
        version_ids = tuple(item.entry_version_id for item in manifest)
        if not version_ids:
            return ()
        async with self._database.connection(self._bound_connection) as connection:
            versions = await self._hydrate_entry_versions(connection, memory, version_ids)
            by_version = {entry.entry_version_id: entry for entry in versions}
            rows = (
                await connection.execute(
                    select(MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE)
                    .where(
                        MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE.c.scope_id == self._scope_id,
                        MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE.c.memory_artifact_id == memory.artifact_id,
                    )
                    .order_by(MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE.c.entry_id)
                )
            ).mappings()
            by_entry = {str(row["entry_id"]): row for row in rows}
        # The manifest owns each entry's state and version, so derive those from the
        # requested revision. Deactivating or reactivating an entry keeps its version
        # identity, so a row written by a later revision would otherwise pass an
        # identity check and report that later state under a historical reference.
        projections: list[MemoryLifecycleProjection] = []
        for item in manifest:
            row = by_entry.get(item.entry_id)
            projections.append(
                _lifecycle_projection(
                    memory,
                    item.state,
                    by_version[item.entry_version_id],
                    row=row,
                )
            )
        return tuple(projections)

    async def projections(self, memory: ArtifactRef, /) -> tuple[MemoryProjection, ...]:
        canonical = await self.get(memory)
        async with self._database.connection(self._bound_connection) as connection:
            rows = (
                (
                    await connection.execute(
                        select(MEMORY_ENTRY_HEADS_TABLE, MEMORY_ENTRY_VERSIONS_TABLE)
                        .join(
                            MEMORY_ENTRY_VERSIONS_TABLE,
                            (MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id == MEMORY_ENTRY_HEADS_TABLE.c.scope_id)
                            & (
                                MEMORY_ENTRY_VERSIONS_TABLE.c.memory_artifact_id
                                == MEMORY_ENTRY_HEADS_TABLE.c.memory_artifact_id
                            )
                            & (
                                MEMORY_ENTRY_VERSIONS_TABLE.c.entry_version_id
                                == MEMORY_ENTRY_HEADS_TABLE.c.entry_version_id
                            ),
                        )
                        .where(
                            MEMORY_ENTRY_HEADS_TABLE.c.scope_id == self._scope_id,
                            MEMORY_ENTRY_HEADS_TABLE.c.memory_artifact_id == memory.artifact_id,
                        )
                        .order_by(MEMORY_ENTRY_HEADS_TABLE.c.entry_id)
                    )
                )
                .mappings()
                .all()
            )
            entries = await self._hydrate_source_evidence(connection, tuple(_decode_entry(row) for row in rows))
            projections = tuple(
                MemoryProjection(
                    entry_version=entry,
                    searchable_text=str(row["searchable_text"]),
                )
                for row, entry in zip(rows, entries, strict=True)
            )
            projections = await self._index.hydrate(connection, self._scope_id, projections)
        active_ids = {item.entry_version_id for item in canonical.content.manifest.entries if item.state == "active"}
        if {item.entry_version.entry_version_id for item in projections} != active_ids:
            raise InvalidMemoryCitationError("projection-version")
        return projections

    async def rebuild_projections(self, embedding_model: EmbeddingModel | None = None, /) -> None:
        """Rebuild this scope's active-head and search projections from authoritative rows."""

        async with self._database.transaction() as connection:
            baseline = await self._authoritative_projections(connection)
        rebuilt = await self._embed_rebuild(baseline, embedding_model)
        async with self._database.transaction() as connection:
            if await self._authoritative_projections(connection) != baseline:
                raise _MemoryProjectionRebuildError("snapshot")
            existing_ids = (
                await connection.execute(
                    select(MEMORY_ENTRY_HEADS_TABLE.c.memory_artifact_id)
                    .where(MEMORY_ENTRY_HEADS_TABLE.c.scope_id == self._scope_id)
                    .distinct()
                )
            ).scalars()
            by_id = {snapshot.memory_ref.artifact_id: snapshot for snapshot in rebuilt}
            for artifact_id in {str(value) for value in existing_ids} | set(by_id):
                snapshot = by_id.get(artifact_id)
                ref = (
                    ArtifactRef(family=Memory.family, artifact_id=artifact_id, revision=1)
                    if snapshot is None
                    else snapshot.memory_ref
                )
                await self._index.replace(connection, self._scope_id, ref, ())
            await connection.execute(
                delete(MEMORY_ENTRY_HEADS_TABLE).where(MEMORY_ENTRY_HEADS_TABLE.c.scope_id == self._scope_id)
            )
            await connection.execute(
                delete(MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE).where(
                    MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE.c.scope_id == self._scope_id
                )
            )
            for snapshot in rebuilt:
                if snapshot.projections:
                    await connection.execute(
                        insert(MEMORY_ENTRY_HEADS_TABLE),
                        [
                            _projection_values(self._scope_id, snapshot.memory_ref, projection)
                            for projection in snapshot.projections
                        ],
                    )
                if snapshot.lifecycle:
                    await connection.execute(
                        insert(MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE),
                        [_lifecycle_values(self._scope_id, lifecycle) for lifecycle in snapshot.lifecycle],
                    )
                await self._index.replace(
                    connection,
                    self._scope_id,
                    snapshot.memory_ref,
                    snapshot.projections,
                )

    def begin(self) -> AbstractAsyncContextManager[MemoryUnitOfWork]:
        return _unit_of_work(self)

    async def changes(
        self,
        memory: ArtifactRef,
        since_revision: int | None,
        /,
    ) -> tuple[MemoryRevisionChanges, ...]:
        target = await self.get(memory)
        lower = target.revision - 1 if since_revision is None else since_revision
        async with self._database.connection(self._bound_connection) as connection:
            revisions = await self._artifacts.revisions(
                connection,
                self._scope_id,
                Memory.family,
                memory.artifact_id,
                since_revision=lower,
                through_revision=target.revision,
            )
        selected = (_require_memory(value) for value in revisions)
        return tuple(
            MemoryRevisionChanges(memory_ref=value.as_ref(), changes=value.content.changes) for value in selected
        )

    async def vector_complete(self, memories: tuple[ArtifactRef, ...], profile: EmbeddingProfile, /) -> bool:
        async with self._database.connection(self._bound_connection) as connection:
            return await self._index.vector_complete(connection, self._scope_id, memories, profile)

    async def search(self, request: MemorySearchRequest, /) -> MemorySearchChannels:
        async with self._database.connection(self._bound_connection) as connection:
            await self._validate_search_heads(connection, request.memories)
            channels = await self._index.search(connection, self._scope_id, request)
            await self._validate_search_heads(connection, request.memories)
        return channels

    async def _validate_search_heads(
        self,
        connection: AsyncConnection,
        memories: tuple[ArtifactRef, ...],
    ) -> None:
        """Reject a projection read when any requested head has advanced."""

        for memory in memories:
            try:
                exact = _require_memory(await self._artifacts.get(connection, self._scope_id, memory))
                latest = _require_memory(
                    await self._artifacts.latest(
                        connection,
                        self._scope_id,
                        Memory.family,
                        memory.artifact_id,
                    )
                )
            except RepositoryNotFoundError:
                raise ArtifactNotFoundError(memory) from None
            if exact.as_ref() != latest.as_ref():
                raise InvalidMemoryCitationError("memory-mismatch")

    async def expand(self, hits: tuple[MemoryHit, ...], /) -> tuple[MemoryEntryVersion, ...]:
        expanded: list[MemoryEntryVersion] = []
        async with self._database.connection(self._bound_connection) as connection:
            for hit in hits:
                row = (
                    (
                        await connection.execute(
                            select(MEMORY_ENTRY_VERSIONS_TABLE).where(
                                MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id == self._scope_id,
                                MEMORY_ENTRY_VERSIONS_TABLE.c.memory_artifact_id == hit.memory_ref.artifact_id,
                                MEMORY_ENTRY_VERSIONS_TABLE.c.entry_id == hit.entry_id,
                                MEMORY_ENTRY_VERSIONS_TABLE.c.entry_version_id == hit.entry_version_id,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise InvalidMemoryCitationError("expand-anchor")
                expanded.append(_decode_entry(row))
            return await self._hydrate_source_evidence(connection, tuple(expanded))

    async def _hydrate_entry_versions(
        self,
        connection: AsyncConnection,
        memory_ref: ArtifactRef,
        version_ids: tuple[str, ...],
    ) -> tuple[MemoryEntryVersion, ...]:
        """Load the named entry versions in request order with their evidence.

        A manifest may name more entries than one statement can bind, so the lookup
        chunks at the shared budget. Every caller needs the same completeness
        guarantee, so it lives here rather than being restated at each call site.
        """

        if not version_ids:
            return ()
        by_version: dict[str, MemoryEntryVersion] = {}
        for batch in _batched(dict.fromkeys(version_ids), _ENTRY_SELECTION_BATCH_SIZE):
            rows = (
                await connection.execute(
                    select(MEMORY_ENTRY_VERSIONS_TABLE).where(
                        MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id == self._scope_id,
                        MEMORY_ENTRY_VERSIONS_TABLE.c.memory_artifact_id == memory_ref.artifact_id,
                        MEMORY_ENTRY_VERSIONS_TABLE.c.entry_version_id.in_(batch),
                    )
                )
            ).mappings()
            hydrated = await self._hydrate_source_evidence(connection, tuple(_decode_entry(row) for row in rows))
            by_version.update({entry.entry_version_id: entry for entry in hydrated})
        entries: list[MemoryEntryVersion] = []
        for version_id in version_ids:
            entry = by_version.get(version_id)
            if entry is None:
                raise InvalidMemoryCitationError("missing-version")
            entries.append(entry)
        return tuple(entries)

    async def _hydrate_source_evidence(
        self,
        connection: AsyncConnection,
        entries: tuple[MemoryEntryVersion, ...],
    ) -> tuple[MemoryEntryVersion, ...]:
        if not entries:
            return ()
        identities = dict.fromkeys(
            (entry.memory_artifact_id, entry.entry_id, entry.entry_version_id) for entry in entries
        )
        snapshots: dict[tuple[str, str, str], list[MemoryEvidenceSnapshot]] = {}
        for batch in _batched(identities, _EVIDENCE_SELECTION_BATCH_SIZE):
            rows = (
                await connection.execute(
                    select(MEMORY_ENTRY_EVIDENCE_TABLE)
                    .where(
                        MEMORY_ENTRY_EVIDENCE_TABLE.c.scope_id == self._scope_id,
                        tuple_(
                            MEMORY_ENTRY_EVIDENCE_TABLE.c.memory_artifact_id,
                            MEMORY_ENTRY_EVIDENCE_TABLE.c.entry_id,
                            MEMORY_ENTRY_EVIDENCE_TABLE.c.entry_version_id,
                        ).in_(batch),
                    )
                    .order_by(
                        MEMORY_ENTRY_EVIDENCE_TABLE.c.memory_artifact_id,
                        MEMORY_ENTRY_EVIDENCE_TABLE.c.entry_id,
                        MEMORY_ENTRY_EVIDENCE_TABLE.c.entry_version_id,
                        MEMORY_ENTRY_EVIDENCE_TABLE.c.ordinal,
                    )
                )
            ).mappings()
            for row in rows:
                identity = (str(row["memory_artifact_id"]), str(row["entry_id"]), str(row["entry_version_id"]))
                snapshots.setdefault(identity, []).append(
                    MemoryEvidenceSnapshot(
                        source=SourceRef(source_type=str(row["source_type"]), source_id=str(row["source_id"])),
                        declaration=MemoryEvidenceDeclaration(
                            authority=MemoryEvidenceAuthority(str(row["authority"])),
                            verification=MemoryEvidenceVerification(str(row["verification"])),
                            declaration_version=str(row["declaration_version"]),
                        ),
                    )
                )
        hydrated: list[MemoryEntryVersion] = []
        for entry in entries:
            identity = (entry.memory_artifact_id, entry.entry_id, entry.entry_version_id)
            evidence = tuple(snapshots.get(identity, ()))
            if evidence and tuple(snapshot.source for snapshot in evidence) != entry.sources:
                raise InvalidMemoryCitationError("evidence-source")
            hydrated.append(
                entry.model_copy(update={"source_evidence": evidence or _neutral_source_evidence(entry.sources)})
            )
        return tuple(hydrated)

    async def _lifecycle_for_memory(
        self,
        connection: AsyncConnection,
        memory: Memory,
    ) -> tuple[MemoryLifecycleProjection, ...]:
        return await self._lifecycle_for_entries(
            connection,
            memory,
            tuple(item.entry_id for item in memory.content.manifest.entries),
        )

    async def _lifecycle_for_entries(
        self,
        connection: AsyncConnection,
        memory: Memory,
        entry_ids: tuple[str, ...],
    ) -> tuple[MemoryLifecycleProjection, ...]:
        """Derive lifecycle rows for the named manifest entries of one Memory."""

        wanted = frozenset(entry_ids)
        manifest = tuple(item for item in memory.content.manifest.entries if item.entry_id in wanted)
        if not manifest:
            return ()
        version_ids = tuple(item.entry_version_id for item in manifest)
        versions = await self._hydrate_entry_versions(connection, memory.as_ref(), version_ids)
        by_version = {entry.entry_version_id: entry for entry in versions}
        lifecycle: list[MemoryLifecycleProjection] = []
        for item in manifest:
            entry = by_version.get(item.entry_version_id)
            if entry is None:
                raise InvalidMemoryCitationError("missing-version")
            _validate_rebuild_entry(memory.as_ref(), item.entry_id, item.entry_content_hash, entry)
            lifecycle.append(_lifecycle_projection(memory.as_ref(), item.state, entry))
        return tuple(lifecycle)

    async def _authoritative_projections(
        self,
        connection: AsyncConnection,
    ) -> tuple[_RebuildSnapshot, ...]:
        heads = (
            await connection.execute(
                select(
                    ARTIFACT_HEADS_TABLE.c.artifact_id,
                    ARTIFACT_HEADS_TABLE.c.revision,
                )
                .where(
                    ARTIFACT_HEADS_TABLE.c.scope_id == self._scope_id,
                    ARTIFACT_HEADS_TABLE.c.family == Memory.family,
                )
                .order_by(ARTIFACT_HEADS_TABLE.c.artifact_id)
            )
        ).all()
        snapshots: list[_RebuildSnapshot] = []
        for artifact_id, revision in heads:
            ref = ArtifactRef(
                family=Memory.family,
                artifact_id=str(artifact_id),
                revision=int(revision),
            )
            memory = _require_memory(await self._artifacts.get(connection, self._scope_id, ref))
            lifecycle = await self._lifecycle_for_memory(connection, memory)
            active = tuple(item for item in memory.content.manifest.entries if item.state == "active")
            if not active:
                snapshots.append(_RebuildSnapshot(memory_ref=ref, projections=(), lifecycle=lifecycle))
                continue
            version_ids = tuple(item.entry_version_id for item in active)
            hydrated = await self._hydrate_entry_versions(connection, ref, version_ids)
            versions = {entry.entry_version_id: entry for entry in hydrated}
            projections: list[MemoryProjection] = []
            for item in active:
                version = versions.get(item.entry_version_id)
                if version is None:
                    raise InvalidMemoryCitationError("missing-version")
                _validate_rebuild_entry(ref, item.entry_id, item.entry_content_hash, version)
                projections.append(
                    MemoryProjection(
                        entry_version=version,
                        searchable_text=analyze_text(version.text),
                    )
                )
            snapshots.append(
                _RebuildSnapshot(
                    memory_ref=ref,
                    projections=tuple(projections),
                    lifecycle=lifecycle,
                )
            )
        return tuple(snapshots)

    async def _embed_rebuild(
        self,
        snapshots: tuple[_RebuildSnapshot, ...],
        embedding_model: EmbeddingModel | None,
    ) -> tuple[_RebuildSnapshot, ...]:
        if embedding_model is None:
            return snapshots
        profile = self._index.capabilities.embedding_profile
        if not self._index.capabilities.vector or profile is None:
            raise _MemoryProjectionRebuildError("vector")
        if embedding_model.profile != profile:
            raise _MemoryProjectionRebuildError("profile")
        projections = tuple(projection for snapshot in snapshots for projection in snapshot.projections)
        if not projections:
            return snapshots
        vectors = (await embedding_model.embed(tuple(item.entry_version.text for item in projections))).vectors
        if len(vectors) != len(projections):
            raise InvalidEmbeddingError("count")
        embedded = iter(
            projection.model_copy(
                update={
                    "embedding": canonical_embedding(
                        vector,
                        dimension=profile.dimension,
                        normalization=profile.normalization,
                    ),
                    "embedding_content_hash": embedding_content_hash(
                        profile_id=profile.profile_id,
                        model=profile.model,
                        dimension=profile.dimension,
                        distance=profile.distance,
                        normalization=profile.normalization,
                        entry_content_hash=projection.entry_version.entry_content_hash,
                    ),
                }
            )
            for projection, vector in zip(projections, vectors, strict=True)
        )
        return tuple(
            replace(snapshot, projections=tuple(next(embedded) for _ in snapshot.projections)) for snapshot in snapshots
        )

    async def _commit(self, connection: AsyncConnection, value: MemoryCommit) -> Memory:
        _validate_commit(value)
        draft = _MemoryDraft(
            content=value.memory.content,
            sources=value.memory.lineage.sources,
            artifacts=value.memory.lineage.artifacts,
        )
        if value.base is None:
            artifact = await self._artifacts.create(
                connection,
                self._scope_id,
                value.memory.artifact_id,
                draft,
            )
        else:
            artifact = await self._artifacts.revise(
                connection,
                self._scope_id,
                value.base,
                draft,
            )
        committed = _require_memory(artifact)
        if committed != value.memory:
            raise _InvalidMemoryCommitError("artifact-result")

        compacted = {change.entry_id for change in value.memory.content.changes if change.op == "compact"}
        if compacted:
            # Artifact revision CAS holds the same head lock as tag replacement.
            # Recheck with a current read so a newly tagged entry rolls back the
            # entire compaction instead of leaving a dangling tag.
            tagged = await self._any_tagged_entry_ids(connection, value.memory.as_ref(), for_update=True)
            if compacted & tagged:
                raise CapabilityNotSupportedError("compaction-tag-conflict")

        if value.entry_versions:
            await connection.execute(
                insert(MEMORY_ENTRY_VERSIONS_TABLE),
                [_entry_values(self._scope_id, entry) for entry in value.entry_versions],
            )
            await _insert_source_evidence(connection, self._scope_id, value.entry_versions)
        # Only entries whose pointer or state changed need projection work; the
        # rest of the active head stays exactly as the previous revision left it.
        previous_active = (
            {}
            if value.base is None
            else {
                item.entry_id: item.entry_version_id
                for item in value.base.content.manifest.entries
                if item.state == "active"
            }
        )
        current_active = {
            item.entry_id: item.entry_version_id
            for item in value.memory.content.manifest.entries
            if item.state == "active"
        }
        removed = tuple(sorted(entry_id for entry_id in previous_active if entry_id not in current_active))
        changed = tuple(
            sorted(
                entry_id
                for entry_id, entry_version_id in current_active.items()
                if previous_active.get(entry_id) != entry_version_id
            )
        )
        drop = tuple(sorted({*removed, *changed}))
        # Clear the index rows before the heads go, as rebuild_projections does:
        # index metadata may cascade from the heads, and an index can only find its
        # rows through that metadata.
        await self._index.delete(connection, self._scope_id, value.memory.as_ref(), drop)
        if drop:
            await connection.execute(
                delete(MEMORY_ENTRY_HEADS_TABLE).where(
                    MEMORY_ENTRY_HEADS_TABLE.c.scope_id == self._scope_id,
                    MEMORY_ENTRY_HEADS_TABLE.c.memory_artifact_id == value.memory.artifact_id,
                    MEMORY_ENTRY_HEADS_TABLE.c.entry_id.in_(drop),
                )
            )
        if changed:
            by_entry = {projection.entry_version.entry_id: projection for projection in value.projections}
            upserts = tuple(by_entry[entry_id] for entry_id in changed)
            await connection.execute(
                insert(MEMORY_ENTRY_HEADS_TABLE),
                [_projection_values(self._scope_id, value.memory.as_ref(), projection) for projection in upserts],
            )
            await self._index.upsert(
                connection,
                self._scope_id,
                value.memory.as_ref(),
                upserts,
            )
        await _refresh_lifecycle_projections(connection, self, value, committed)
        return committed


class _RelationalMemoryUnitOfWork:
    def __init__(self, backend: RelationalMemoryBackend, connection: AsyncConnection) -> None:
        self._backend = backend
        self._connection = connection
        self._complete = False

    async def commit(self, value: MemoryCommit, /) -> Memory:
        if self._complete:
            raise _InvalidMemoryCommitError("complete")
        result = await self._backend._commit(self._connection, value)
        self._complete = True
        return result


@asynccontextmanager
async def _unit_of_work(
    backend: RelationalMemoryBackend,
) -> AsyncIterator[_RelationalMemoryUnitOfWork]:
    async with backend._database.connection(backend._bound_connection) as connection:
        yield _RelationalMemoryUnitOfWork(backend, connection)


def _validate_commit(value: MemoryCommit) -> None:
    if value.memory.family != Memory.family:
        raise _InvalidMemoryCommitError("family")
    if value.content_hash != memory_content_hash(value.memory.content):
        raise _InvalidMemoryCommitError("content-hash")
    expected_revision = 1 if value.base is None else value.base.revision + 1
    if value.memory.revision != expected_revision:
        raise _InvalidMemoryCommitError("revision")
    if value.base is not None and value.base.artifact_id != value.memory.artifact_id:
        raise _InvalidMemoryCommitError("base-identity")
    active = {item.entry_version_id for item in value.memory.content.manifest.entries if item.state == "active"}
    projected = {item.entry_version.entry_version_id for item in value.projections}
    if active != projected:
        raise _InvalidMemoryCommitError("projection")


def _entry_values(scope_id: str, value: MemoryEntryVersion) -> dict[str, object]:
    return {
        "scope_id": scope_id,
        "family": Memory.family,
        "memory_artifact_id": value.memory_artifact_id,
        "entry_id": value.entry_id,
        "entry_version_id": value.entry_version_id,
        "version": value.version,
        "previous_version_id": value.previous_version_id,
        "kind": value.kind,
        "text": value.text,
        "source_refs": dump_model(
            _SourceRefs(value.sources),
            kind="memory-entry",
            name=value.entry_version_id,
        ),
        "artifact_refs": dump_model(
            _ArtifactRefs(value.artifacts),
            kind="memory-entry",
            name=value.entry_version_id,
        ),
        "entry_content_hash": value.entry_content_hash,
        "created_in_revision": value.created_in_revision,
    }


async def _refresh_lifecycle_projections(
    connection: AsyncConnection,
    backend: RelationalMemoryBackend,
    value: MemoryCommit,
    committed: Memory,
) -> None:
    """Rewrite the lifecycle rows whose entry identity or state changed.

    Lifecycle rows cover every manifest entry, including inactive ones, and follow
    the same per-entry rule as the active head: only changed entries are rewritten,
    so repeated appends stay bounded by the appended entries rather than the
    manifest size.
    """

    previous_entries = (
        {}
        if value.base is None
        else {item.entry_id: (item.entry_version_id, item.state) for item in value.base.content.manifest.entries}
    )
    current_entries = {
        item.entry_id: (item.entry_version_id, item.state) for item in value.memory.content.manifest.entries
    }
    removed = tuple(sorted(entry_id for entry_id in previous_entries if entry_id not in current_entries))
    changed = tuple(
        sorted(entry_id for entry_id, identity in current_entries.items() if previous_entries.get(entry_id) != identity)
    )
    drop = tuple(sorted({*removed, *changed}))
    if drop:
        await connection.execute(
            delete(MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE).where(
                MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE.c.scope_id == backend._scope_id,
                MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE.c.memory_artifact_id == value.memory.artifact_id,
                MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE.c.entry_id.in_(drop),
            )
        )
    if changed:
        lifecycle = await backend._lifecycle_for_entries(connection, committed, changed)
        if len(lifecycle) != len(changed):
            raise _InvalidMemoryCommitError("lifecycle-projection")
        await connection.execute(
            insert(MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE),
            [_lifecycle_values(backend._scope_id, projection) for projection in lifecycle],
        )


async def _insert_source_evidence(
    connection: AsyncConnection,
    scope_id: str,
    entry_versions: tuple[MemoryEntryVersion, ...],
) -> None:
    """Write the evidence rows for one batch of entry versions, if any carry sources."""

    rows: list[dict[str, object]] = []
    for entry in entry_versions:
        rows.extend(_evidence_values(scope_id, entry))
    if rows:
        await connection.execute(insert(MEMORY_ENTRY_EVIDENCE_TABLE), rows)


def _evidence_values(scope_id: str, value: MemoryEntryVersion) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "scope_id": scope_id,
            "memory_artifact_id": value.memory_artifact_id,
            "entry_id": value.entry_id,
            "entry_version_id": value.entry_version_id,
            "ordinal": ordinal,
            "source_type": snapshot.source.source_type,
            "source_id": snapshot.source.source_id,
            "authority": snapshot.declaration.authority.value,
            "verification": snapshot.declaration.verification.value,
            "declaration_version": snapshot.declaration.declaration_version,
        }
        for ordinal, snapshot in enumerate(value.source_evidence)
    )


def _projection_values(
    scope_id: str,
    memory_ref: ArtifactRef,
    value: MemoryProjection,
) -> dict[str, object]:
    entry = value.entry_version
    return {
        "scope_id": scope_id,
        "family": Memory.family,
        "memory_artifact_id": memory_ref.artifact_id,
        "head_revision": memory_ref.revision,
        "entry_id": entry.entry_id,
        "entry_version_id": entry.entry_version_id,
        "entry_content_hash": entry.entry_content_hash,
        "searchable_text": value.searchable_text,
    }


def _lifecycle_values(scope_id: str, value: MemoryLifecycleProjection) -> dict[str, object]:
    return {
        "scope_id": scope_id,
        "family": Memory.family,
        "memory_artifact_id": value.memory_ref.artifact_id,
        "head_revision": value.memory_ref.revision,
        "entry_id": value.entry_id,
        "entry_version_id": value.entry_version_id,
        "validity": value.validity,
        "validity_reason": value.validity_reason,
        "successor_entry_id": value.successor_entry_id,
        "source_count": len(value.source_evidence),
        "rule_version": value.rule_version,
        "quality_policy": value.quality_policy,
    }


def _decode_entry(row: Mapping[Any, Any]) -> MemoryEntryVersion:
    entry_version_id = str(row["entry_version_id"])
    return MemoryEntryVersion(
        memory_artifact_id=str(row["memory_artifact_id"]),
        entry_id=str(row["entry_id"]),
        entry_version_id=entry_version_id,
        version=int(row["version"]),
        previous_version_id=(None if row["previous_version_id"] is None else str(row["previous_version_id"])),
        kind=str(row["kind"]),
        text=str(row["text"]),
        sources=load_model(
            _SourceRefs,
            stored_bytes(row["source_refs"], column="memory payload"),
            kind="memory-entry",
            name=entry_version_id,
        ).root,
        artifacts=load_model(
            _ArtifactRefs,
            stored_bytes(row["artifact_refs"], column="memory payload"),
            kind="memory-entry",
            name=entry_version_id,
        ).root,
        entry_content_hash=str(row["entry_content_hash"]),
        created_in_revision=int(row["created_in_revision"]),
    )


def _neutral_source_evidence(sources: tuple[SourceRef, ...]) -> tuple[MemoryEvidenceSnapshot, ...]:
    return tuple(MemoryEvidenceSnapshot(source=source, declaration=MemoryEvidenceDeclaration()) for source in sources)


def _lifecycle_projection(
    memory_ref: ArtifactRef,
    state: str,
    entry: MemoryEntryVersion,
    *,
    row: Mapping[Any, Any] | None = None,
) -> MemoryLifecycleProjection:
    """Derive one lifecycle projection for a requested revision's entry.

    Every field comes from the entry the requested manifest names. A persisted row
    is only a cross-check that the entry has one, because its columns describe the
    revision that last wrote it: reusing them would report a later revision's state
    under a historical reference, which the entry identity alone does not prevent.
    """

    if row is not None and str(row["entry_id"]) != entry.entry_id:
        raise InvalidMemoryCitationError("lifecycle-version")
    return MemoryLifecycleProjection(
        memory_ref=memory_ref,
        entry_id=entry.entry_id,
        entry_version_id=entry.entry_version_id,
        validity="current" if state == "active" else "inactive",
        source_evidence=entry.source_evidence,
    )


def _validate_rebuild_entry(
    memory_ref: ArtifactRef,
    entry_id: str,
    content_hash: str,
    version: MemoryEntryVersion,
) -> None:
    if (
        version.memory_artifact_id != memory_ref.artifact_id
        or version.entry_id != entry_id
        or version.entry_content_hash != content_hash
    ):
        raise InvalidMemoryCitationError("cross-identity")
    actual_hash = entry_content_hash(
        kind=version.kind,
        text=version.text,
        source_refs=tuple({"source_type": ref.source_type, "source_id": ref.source_id} for ref in version.sources),
        artifact_refs=tuple(
            {
                "family": ref.family,
                "artifact_id": ref.artifact_id,
                "revision": ref.revision,
            }
            for ref in version.artifacts
        ),
    )
    if actual_hash != content_hash:
        raise InvalidMemoryCitationError("hash-mismatch")


def _require_memory(value: object) -> Memory:
    if type(value) is not Memory:
        raise _InvalidMemoryCommitError("memory-type", type(value).__name__)
    return value
