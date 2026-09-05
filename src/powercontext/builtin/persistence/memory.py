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

import math
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any, ClassVar

from pydantic import RootModel
from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactDraft, ArtifactRef
from powercontext.builtin.artifacts.memory import (
    EmbeddingProfile,
    InvalidEmbeddingError,
    Memory,
    MemoryCapabilities,
    MemoryChange,
    MemoryCommit,
    MemoryContent,
    MemoryEntryVersion,
    MemoryHit,
    MemoryManifestEntry,
    MemoryProjection,
    MemoryRevisionChanges,
    MemorySearchChannels,
    MemorySearchRequest,
    MemoryUnitOfWork,
)
from powercontext.builtin.artifacts.memory.canonical import (
    canonical_embedding,
    embedding_content_hash,
    entry_content_bytes,
    entry_content_hash,
    memory_content_hash,
    validate_embedding,
)
from powercontext.builtin.artifacts.memory.errors import (
    InvalidMemoryCitationError,
    MemoryBackendConfigurationError,
)
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.memory_index import MemoryIndex, NoMemoryIndex
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    MEMORY_ENTRY_HEADS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
)
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef

# OceanBase VECTOR and SQLite Vec1 hydrate float32 values, so a valid unit
# vector can drift slightly after a storage round trip.
_UNIT_VECTOR_ABS_TOLERANCE = 1e-6


class _SourceRefs(RootModel[tuple[SourceRef, ...]]):
    pass


class _ArtifactRefs(RootModel[tuple[ArtifactRef, ...]]):
    pass


class _MemoryDraft(ArtifactDraft[MemoryContent]):
    family: ClassVar[str] = Memory.family


@dataclass(frozen=True, slots=True)
class _RebuildSnapshot:
    memory_ref: ArtifactRef
    projections: tuple[MemoryProjection, ...]


class _InvalidMemoryCommitError(MemoryBackendConfigurationError):
    def __init__(self, code: str, actual: str | None = None) -> None:
        details = {
            "artifact-result": "generic Artifact result differs from prepared revision",
            "base": "base is not the authoritative stored Memory revision",
            "base-identity": "base and revision identities differ",
            "changes": "revision changes are not unique, ordered, and complete",
            "complete": "unit of work is already complete",
            "content-hash": "content hash does not match canonical content",
            "entry-hash": "entry body, declared hash, and manifest hash differ",
            "entry-history": "entry version history is incomplete or has an invalid predecessor",
            "entry-identity": "entry versions do not match the committed Memory revision",
            "family": "family is not memory",
            "manifest": "manifest entries are not unique and canonically ordered",
            "memory-type": f"expected Memory, got {actual}",
            "projection": "active manifest and projections differ or contain non-canonical content",
            "revision": "revision is not the next prepared revision",
            "transition": "changes do not describe the exact base-to-revision transition",
            "vector": "embedding projection does not match the configured profile and entry content",
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

    async def entries(self, memory: ArtifactRef, /) -> tuple[MemoryEntryVersion, ...]:
        async with self._database.connection(self._bound_connection) as connection:
            canonical = await self._get_memory(connection, memory)
            version_ids = tuple(item.entry_version_id for item in canonical.content.manifest.entries)
            if not version_ids:
                return ()
            rows = (
                await connection.execute(
                    select(MEMORY_ENTRY_VERSIONS_TABLE).where(
                        MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id == self._scope_id,
                        MEMORY_ENTRY_VERSIONS_TABLE.c.memory_artifact_id == memory.artifact_id,
                        MEMORY_ENTRY_VERSIONS_TABLE.c.entry_version_id.in_(version_ids),
                    )
                )
            ).mappings()
            by_id = {str(row["entry_version_id"]): _decode_entry(row) for row in rows}
        if len(by_id) != len(version_ids) or set(by_id) != set(version_ids):
            raise InvalidMemoryCitationError("missing-version")
        ordered: list[MemoryEntryVersion] = []
        for item in canonical.content.manifest.entries:
            version = by_id[item.entry_version_id]
            _validate_manifest_entry(canonical.as_ref(), item, version)
            ordered.append(version)
        return tuple(ordered)

    async def projections(self, memory: ArtifactRef, /) -> tuple[MemoryProjection, ...]:
        async with self._database.connection(self._bound_connection) as connection:
            canonical = await self._get_memory(connection, memory)
            rows = (
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
                        MEMORY_ENTRY_HEADS_TABLE.c.head_revision == memory.revision,
                    )
                    .order_by(MEMORY_ENTRY_HEADS_TABLE.c.entry_id)
                )
            ).mappings()
            projections = tuple(
                MemoryProjection(
                    entry_version=_decode_entry(row),
                    searchable_text=str(row["searchable_text"]),
                )
                for row in rows
            )
            projections = await self._index.hydrate(connection, self._scope_id, projections)
        active = {item.entry_version_id: item for item in canonical.content.manifest.entries if item.state == "active"}
        by_id = {item.entry_version.entry_version_id: item for item in projections}
        if len(by_id) != len(projections) or set(by_id) != set(active):
            raise InvalidMemoryCitationError("projection-version")
        for entry_version_id, projection in by_id.items():
            _validate_manifest_entry(canonical.as_ref(), active[entry_version_id], projection.entry_version)
            if projection.searchable_text != analyze_text(projection.entry_version.text):
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
            for snapshot in rebuilt:
                if snapshot.projections:
                    await connection.execute(
                        insert(MEMORY_ENTRY_HEADS_TABLE),
                        [
                            _projection_values(self._scope_id, snapshot.memory_ref, projection)
                            for projection in snapshot.projections
                        ],
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
            )
        selected = (_require_memory(value) for value in revisions if lower < value.revision <= target.revision)
        return tuple(
            MemoryRevisionChanges(memory_ref=value.as_ref(), changes=value.content.changes) for value in selected
        )

    async def vector_complete(self, memories: tuple[ArtifactRef, ...], profile: EmbeddingProfile, /) -> bool:
        async with self._database.connection(self._bound_connection) as connection:
            return await self._index.vector_complete(connection, self._scope_id, memories, profile)

    async def search(self, request: MemorySearchRequest, /) -> MemorySearchChannels:
        async with self._database.connection(self._bound_connection) as connection:
            memories = await self._validate_search_heads(connection, request.memories)
            channels = await self._index.search(connection, self._scope_id, request)
            await self._validate_search_channels(connection, memories, channels)
            await self._validate_search_heads(connection, request.memories)
        return channels

    async def _validate_search_heads(
        self,
        connection: AsyncConnection,
        memories: tuple[ArtifactRef, ...],
    ) -> dict[tuple[str, int], Memory]:
        """Reject a projection read when any requested head has advanced."""

        canonical: dict[tuple[str, int], Memory] = {}
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
            canonical[(memory.artifact_id, memory.revision)] = exact
        return canonical

    async def _validate_search_channels(
        self,
        connection: AsyncConnection,
        memories: Mapping[tuple[str, int], Memory],
        channels: MemorySearchChannels,
    ) -> None:
        candidates = (*channels.fts, *channels.vector)
        if not candidates:
            return
        version_ids = tuple({hit.entry_version_id for hit in candidates})
        rows = (
            await connection.execute(
                select(
                    MEMORY_ENTRY_VERSIONS_TABLE,
                    MEMORY_ENTRY_HEADS_TABLE.c.head_revision.label("_head_revision"),
                    MEMORY_ENTRY_HEADS_TABLE.c.entry_content_hash.label("_head_content_hash"),
                    MEMORY_ENTRY_HEADS_TABLE.c.searchable_text.label("_searchable_text"),
                )
                .join(
                    MEMORY_ENTRY_HEADS_TABLE,
                    (MEMORY_ENTRY_HEADS_TABLE.c.scope_id == MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id)
                    & (
                        MEMORY_ENTRY_HEADS_TABLE.c.memory_artifact_id
                        == MEMORY_ENTRY_VERSIONS_TABLE.c.memory_artifact_id
                    )
                    & (MEMORY_ENTRY_HEADS_TABLE.c.entry_id == MEMORY_ENTRY_VERSIONS_TABLE.c.entry_id)
                    & (MEMORY_ENTRY_HEADS_TABLE.c.entry_version_id == MEMORY_ENTRY_VERSIONS_TABLE.c.entry_version_id),
                )
                .where(
                    MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id == self._scope_id,
                    MEMORY_ENTRY_VERSIONS_TABLE.c.entry_version_id.in_(version_ids),
                )
            )
        ).mappings()
        authoritative = {
            (
                str(row["memory_artifact_id"]),
                int(row["_head_revision"]),
                str(row["entry_id"]),
                str(row["entry_version_id"]),
            ): row
            for row in rows
        }
        manifests = {
            key: {item.entry_id: item for item in memory.content.manifest.entries} for key, memory in memories.items()
        }
        for hit in candidates:
            memory_key = (hit.memory_ref.artifact_id, hit.memory_ref.revision)
            memory = memories.get(memory_key)
            item = manifests.get(memory_key, {}).get(hit.entry_id)
            row = authoritative.get((*memory_key, hit.entry_id, hit.entry_version_id))
            if (
                memory is None
                or hit.memory_ref.family != Memory.family
                or item is None
                or item.state != "active"
                or item.entry_version_id != hit.entry_version_id
                or row is None
                or str(row["_head_content_hash"]) != item.entry_content_hash
            ):
                raise InvalidMemoryCitationError("search-anchor")
            version = _decode_entry(row)
            _validate_manifest_entry(memory.as_ref(), item, version)
            if hit.text != version.text or str(row["_searchable_text"]) != analyze_text(version.text):
                raise InvalidMemoryCitationError("hash-mismatch")

    async def expand(self, hits: tuple[MemoryHit, ...], /) -> tuple[MemoryEntryVersion, ...]:
        expanded: list[MemoryEntryVersion] = []
        async with self._database.connection(self._bound_connection) as connection:
            memories: dict[tuple[str, int], Memory] = {}
            for hit in hits:
                memory_key = (hit.memory_ref.artifact_id, hit.memory_ref.revision)
                memory = memories.get(memory_key)
                if memory is None:
                    memory = await self._get_memory(connection, hit.memory_ref)
                    memories[memory_key] = memory
                item = next(
                    (
                        candidate
                        for candidate in memory.content.manifest.entries
                        if candidate.entry_id == hit.entry_id and candidate.entry_version_id == hit.entry_version_id
                    ),
                    None,
                )
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
                if item is None or row is None:
                    raise InvalidMemoryCitationError("expand-anchor")
                version = _decode_entry(row)
                _validate_manifest_entry(memory.as_ref(), item, version)
                expanded.append(version)
        return tuple(expanded)

    async def _get_memory(self, connection: AsyncConnection, memory: ArtifactRef) -> Memory:
        if memory.family != Memory.family:
            raise ArtifactNotFoundError(memory)
        try:
            return _require_memory(await self._artifacts.get(connection, self._scope_id, memory))
        except RepositoryNotFoundError:
            raise ArtifactNotFoundError(memory) from None

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
            active = tuple(item for item in memory.content.manifest.entries if item.state == "active")
            if not active:
                snapshots.append(_RebuildSnapshot(memory_ref=ref, projections=()))
                continue
            version_ids = tuple(item.entry_version_id for item in active)
            rows = (
                await connection.execute(
                    select(MEMORY_ENTRY_VERSIONS_TABLE).where(
                        MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id == self._scope_id,
                        MEMORY_ENTRY_VERSIONS_TABLE.c.memory_artifact_id == ref.artifact_id,
                        MEMORY_ENTRY_VERSIONS_TABLE.c.entry_version_id.in_(version_ids),
                    )
                )
            ).mappings()
            versions = {str(row["entry_version_id"]): _decode_entry(row) for row in rows}
            projections: list[MemoryProjection] = []
            for item in active:
                version = versions.get(item.entry_version_id)
                if version is None:
                    raise InvalidMemoryCitationError("missing-version")
                _validate_manifest_entry(ref, item, version)
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
        await self._validate_commit_relations(connection, value)
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

        if value.entry_versions:
            try:
                await connection.execute(
                    insert(MEMORY_ENTRY_VERSIONS_TABLE),
                    [_entry_values(self._scope_id, entry) for entry in value.entry_versions],
                )
            except IntegrityError as error:
                raise _InvalidMemoryCommitError("entry-identity") from error
        # Clear the index before the heads go, as rebuild_projections does: index
        # metadata may cascade from the heads, and an index can only find its
        # rows through that metadata.
        await self._index.replace(connection, self._scope_id, value.memory.as_ref(), ())
        await connection.execute(
            delete(MEMORY_ENTRY_HEADS_TABLE).where(
                MEMORY_ENTRY_HEADS_TABLE.c.scope_id == self._scope_id,
                MEMORY_ENTRY_HEADS_TABLE.c.memory_artifact_id == value.memory.artifact_id,
            )
        )
        if value.projections:
            await connection.execute(
                insert(MEMORY_ENTRY_HEADS_TABLE),
                [
                    _projection_values(self._scope_id, value.memory.as_ref(), projection)
                    for projection in value.projections
                ],
            )
        await self._index.replace(
            connection,
            self._scope_id,
            value.memory.as_ref(),
            value.projections,
        )
        return committed

    async def _validate_commit_relations(self, connection: AsyncConnection, value: MemoryCommit) -> None:
        canonical_base = await self._canonical_commit_base(connection, value)
        await self._validate_base_history(connection, canonical_base)
        new_by_entry = _validate_revision_transition(value, canonical_base)
        versions = await self._commit_versions(connection, value)
        await self._validate_new_version_history(connection, value, canonical_base, new_by_entry)
        self._validate_commit_projections(value, versions)

    async def _canonical_commit_base(
        self,
        connection: AsyncConnection,
        value: MemoryCommit,
    ) -> Memory | None:
        if value.base is not None:
            try:
                canonical = _require_memory(await self._artifacts.get(connection, self._scope_id, value.base.as_ref()))
            except RepositoryNotFoundError:
                raise _InvalidMemoryCommitError("base") from None
            if canonical != value.base:
                raise _InvalidMemoryCommitError("base")
            return canonical
        return None

    async def _validate_base_history(self, connection: AsyncConnection, base: Memory | None) -> None:
        if base is None:
            return
        rows = (
            await connection.execute(
                select(MEMORY_ENTRY_VERSIONS_TABLE).where(
                    MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id == self._scope_id,
                    MEMORY_ENTRY_VERSIONS_TABLE.c.memory_artifact_id == base.artifact_id,
                )
            )
        ).mappings()
        stored = tuple(rows)
        rows_by_id = {str(row["entry_version_id"]): row for row in stored}
        if len(rows_by_id) != len(stored):
            raise _InvalidMemoryCommitError("entry-history")
        by_id: dict[str, MemoryEntryVersion] = {}
        pending = [item.entry_version_id for item in base.content.manifest.entries]
        while pending:
            entry_version_id = pending.pop()
            if entry_version_id in by_id:
                continue
            row = rows_by_id.get(entry_version_id)
            if row is None:
                continue
            version = _decode_entry(row)
            by_id[entry_version_id] = version
            if version.previous_version_id is not None:
                pending.append(version.previous_version_id)
        for item in base.content.manifest.entries:
            version = by_id.get(item.entry_version_id)
            if version is None or not _manifest_entry_matches(base.as_ref(), item, version):
                raise _InvalidMemoryCommitError("entry-history")
            if not _entry_history_matches(base.as_ref(), item, version, by_id):
                raise _InvalidMemoryCommitError("entry-history")

    async def _commit_versions(
        self,
        connection: AsyncConnection,
        value: MemoryCommit,
    ) -> dict[str, MemoryEntryVersion]:
        manifest = {item.entry_version_id: item for item in value.memory.content.manifest.entries}
        new_by_id = {version.entry_version_id: version for version in value.entry_versions}
        rows = (
            await connection.execute(
                select(MEMORY_ENTRY_VERSIONS_TABLE).where(
                    MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id == self._scope_id,
                    MEMORY_ENTRY_VERSIONS_TABLE.c.entry_version_id.in_(tuple(manifest)),
                )
            )
        ).mappings()
        stored_rows = tuple(rows)
        if any(str(row["entry_version_id"]) in new_by_id for row in stored_rows):
            raise _InvalidMemoryCommitError("entry-identity")
        stored_by_id = {
            str(row["entry_version_id"]): _decode_entry(row)
            for row in stored_rows
            if str(row["memory_artifact_id"]) == value.memory.artifact_id
        }
        versions = stored_by_id | new_by_id
        if set(versions) != set(manifest):
            raise _InvalidMemoryCommitError("entry-identity")
        for entry_version_id, item in manifest.items():
            if not _manifest_entry_matches(value.memory.as_ref(), item, versions[entry_version_id]):
                raise _InvalidMemoryCommitError("entry-hash")
        return versions

    async def _validate_new_version_history(
        self,
        connection: AsyncConnection,
        value: MemoryCommit,
        canonical_base: Memory | None,
        new_by_entry: Mapping[str, MemoryEntryVersion],
    ) -> None:
        previous_ids = tuple(
            version.previous_version_id for version in value.entry_versions if version.previous_version_id is not None
        )
        previous_rows = (
            ()
            if not previous_ids
            else (
                await connection.execute(
                    select(MEMORY_ENTRY_VERSIONS_TABLE).where(
                        MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id == self._scope_id,
                        MEMORY_ENTRY_VERSIONS_TABLE.c.entry_version_id.in_(previous_ids),
                    )
                )
            )
            .mappings()
            .all()
        )
        previous_by_id: dict[str, list[MemoryEntryVersion]] = {}
        for row in previous_rows:
            previous_by_id.setdefault(str(row["entry_version_id"]), []).append(_decode_entry(row))
        base_manifest = (
            {} if canonical_base is None else {item.entry_id: item for item in canonical_base.content.manifest.entries}
        )
        changes = {change.entry_id: change for change in value.memory.content.changes}
        for entry_id, version in new_by_entry.items():
            if version.created_in_revision != value.memory.revision:
                raise _InvalidMemoryCommitError("entry-history")
            change = changes[entry_id]
            if change.op == "add":
                if version.version != 1 or version.previous_version_id is not None:
                    raise _InvalidMemoryCommitError("entry-history")
                continue
            if canonical_base is None:
                raise _InvalidMemoryCommitError("entry-history")
            base_item = base_manifest[entry_id]
            predecessors = previous_by_id.get(version.previous_version_id or "", [])
            if len(predecessors) != 1:
                raise _InvalidMemoryCommitError("entry-history")
            predecessor = predecessors[0]
            if (
                version.previous_version_id != base_item.entry_version_id
                or version.version != predecessor.version + 1
                or not _manifest_entry_matches(canonical_base.as_ref(), base_item, predecessor)
            ):
                raise _InvalidMemoryCommitError("entry-history")
            if base_item.state == "inactive" and (
                change.reason != "normalize" or not _canonical_entry_content_matches(version, predecessor)
            ):
                raise _InvalidMemoryCommitError("entry-history")

    def _validate_commit_projections(
        self,
        value: MemoryCommit,
        versions: Mapping[str, MemoryEntryVersion],
    ) -> None:
        active = {item.entry_id: item for item in value.memory.content.manifest.entries if item.state == "active"}
        projected = {projection.entry_version.entry_id: projection for projection in value.projections}
        if len(projected) != len(value.projections) or set(projected) != set(active):
            raise _InvalidMemoryCommitError("projection")
        for entry_id, item in active.items():
            projection = projected[entry_id]
            authoritative = versions[item.entry_version_id]
            if projection.entry_version != authoritative or projection.searchable_text != analyze_text(
                authoritative.text
            ):
                raise _InvalidMemoryCommitError("projection")
            self._validate_embedding_projection(projection)

    def _validate_embedding_projection(self, projection: MemoryProjection) -> None:
        if projection.embedding is None and projection.embedding_content_hash is None:
            return
        if projection.embedding is None or projection.embedding_content_hash is None:
            raise _InvalidMemoryCommitError("vector")
        profile = self._index.capabilities.embedding_profile
        if not self._index.capabilities.vector or profile is None:
            raise _InvalidMemoryCommitError("vector")
        try:
            vector = validate_embedding(
                projection.embedding,
                dimension=profile.dimension,
            )
            expected_hash = embedding_content_hash(
                profile_id=profile.profile_id,
                model=profile.model,
                dimension=profile.dimension,
                distance=profile.distance,
                normalization=profile.normalization,
                entry_content_hash=projection.entry_version.entry_content_hash,
            )
        except (TypeError, ValueError) as error:
            raise _InvalidMemoryCommitError("vector") from error
        if (
            profile.normalization == "unit"
            and not math.isclose(
                math.hypot(*vector),
                1.0,
                rel_tol=0.0,
                abs_tol=_UNIT_VECTOR_ABS_TOLERANCE,
            )
        ) or expected_hash != projection.embedding_content_hash:
            raise _InvalidMemoryCommitError("vector")


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
    if type(value.memory) is not Memory or value.memory.family != Memory.family:
        raise _InvalidMemoryCommitError("family")
    if value.base is not None and type(value.base) is not Memory:
        raise _InvalidMemoryCommitError("base")
    try:
        canonical_hash = memory_content_hash(value.memory.content)
    except (TypeError, ValueError) as error:
        raise _InvalidMemoryCommitError("content-hash") from error
    if value.content_hash != canonical_hash:
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


def _validate_revision_transition(
    value: MemoryCommit,
    base: Memory | None,
) -> dict[str, MemoryEntryVersion]:
    manifest_entries = value.memory.content.manifest.entries
    manifest_ids = tuple(item.entry_id for item in manifest_entries)
    manifest_version_ids = tuple(item.entry_version_id for item in manifest_entries)
    if (
        len(set(manifest_ids)) != len(manifest_ids)
        or len(set(manifest_version_ids)) != len(manifest_version_ids)
        or manifest_ids != tuple(sorted(manifest_ids, key=str.encode))
    ):
        raise _InvalidMemoryCommitError("manifest")

    changes = value.memory.content.changes
    change_ids = tuple(change.entry_id for change in changes)
    if (
        not changes
        or len(set(change_ids)) != len(change_ids)
        or change_ids != tuple(sorted(change_ids, key=str.encode))
    ):
        raise _InvalidMemoryCommitError("changes")

    base_entries = () if base is None else base.content.manifest.entries
    base_ids = tuple(item.entry_id for item in base_entries)
    if len(set(base_ids)) != len(base_ids):
        raise _InvalidMemoryCommitError("base")
    before = {item.entry_id: item for item in base_entries}
    after = {item.entry_id: item for item in manifest_entries}
    changed: set[str] = set()
    new_targets: dict[str, str] = {}

    for change in changes:
        previous = before.get(change.entry_id)
        current = after.get(change.entry_id)
        target = _validate_transition_change(change, previous, current)
        if target is not None:
            new_targets[change.entry_id] = target
        changed.add(change.entry_id)

    if set(after) != set(before) | {entry_id for entry_id in changed if entry_id not in before}:
        raise _InvalidMemoryCommitError("transition")
    for entry_id, item in before.items():
        if entry_id not in changed and after.get(entry_id) != item:
            raise _InvalidMemoryCommitError("transition")

    entry_versions = value.entry_versions
    by_entry = {version.entry_id: version for version in entry_versions}
    version_ids = {version.entry_version_id for version in entry_versions}
    if (
        len(by_entry) != len(entry_versions)
        or len(version_ids) != len(entry_versions)
        or set(by_entry) != set(new_targets)
        or any(by_entry[entry_id].entry_version_id != target for entry_id, target in new_targets.items())
    ):
        raise _InvalidMemoryCommitError("entry-identity")
    return by_entry


def _validate_transition_change(
    change: MemoryChange,
    previous: MemoryManifestEntry | None,
    current: MemoryManifestEntry | None,
) -> str | None:
    if current is None:
        raise _InvalidMemoryCommitError("transition")
    if change.op == "add":
        valid = (
            previous is None
            and change.from_entry_version_id is None
            and change.to_entry_version_id == current.entry_version_id
            and current.state == "active"
        )
        target = current.entry_version_id
    elif change.op == "revise":
        valid = (
            previous is not None
            and change.from_entry_version_id == previous.entry_version_id
            and change.to_entry_version_id == current.entry_version_id
            and current.entry_version_id != previous.entry_version_id
            and current.state == previous.state
        )
        target = current.entry_version_id
    elif change.op == "deactivate":
        valid = (
            previous is not None
            and previous.state == "active"
            and change.from_entry_version_id == previous.entry_version_id
            and change.to_entry_version_id is None
            and current == previous.model_copy(update={"state": "inactive"})
        )
        target = None
    else:
        valid = (
            previous is not None
            and previous.state == "inactive"
            and change.from_entry_version_id is None
            and change.to_entry_version_id == previous.entry_version_id
            and current == previous.model_copy(update={"state": "active"})
        )
        target = None
    if not valid:
        raise _InvalidMemoryCommitError("transition")
    return target


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


def _manifest_entry_matches(
    memory_ref: ArtifactRef,
    item: MemoryManifestEntry,
    version: MemoryEntryVersion,
) -> bool:
    if (
        version.memory_artifact_id != memory_ref.artifact_id
        or version.entry_id != item.entry_id
        or version.entry_version_id != item.entry_version_id
        or version.entry_content_hash != item.entry_content_hash
        or version.created_in_revision < 1
        or version.created_in_revision > memory_ref.revision
    ):
        return False
    return _entry_declared_hash_matches(version)


def _entry_history_matches(
    memory_ref: ArtifactRef,
    item: MemoryManifestEntry,
    head: MemoryEntryVersion,
    versions: Mapping[str, MemoryEntryVersion],
) -> bool:
    current = head
    expected_version = head.version
    visited: set[str] = set()
    while True:
        if current.entry_version_id in visited:
            return False
        visited.add(current.entry_version_id)
        if (
            current.memory_artifact_id != memory_ref.artifact_id
            or current.entry_id != item.entry_id
            or current.version != expected_version
            or current.created_in_revision < 1
            or current.created_in_revision > memory_ref.revision
            or not _entry_declared_hash_matches(current)
        ):
            return False
        if expected_version == 1:
            return current.previous_version_id is None
        if current.previous_version_id is None:
            return False
        predecessor = versions.get(current.previous_version_id)
        if predecessor is None or predecessor.created_in_revision >= current.created_in_revision:
            return False
        current = predecessor
        expected_version -= 1


def _entry_declared_hash_matches(version: MemoryEntryVersion) -> bool:
    try:
        actual_hash = entry_content_hash(
            kind=version.kind,
            text=version.text,
            source_refs=_entry_source_refs(version),
            artifact_refs=_entry_artifact_refs(version),
        )
    except (TypeError, ValueError):
        return False
    return actual_hash == version.entry_content_hash


def _canonical_entry_content_matches(left: MemoryEntryVersion, right: MemoryEntryVersion) -> bool:
    try:
        return _canonical_entry_bytes(left) == _canonical_entry_bytes(right)
    except (TypeError, ValueError):
        return False


def _canonical_entry_bytes(version: MemoryEntryVersion) -> bytes:
    return entry_content_bytes(
        kind=version.kind,
        text=version.text,
        source_refs=_entry_source_refs(version),
        artifact_refs=_entry_artifact_refs(version),
    )


def _entry_source_refs(version: MemoryEntryVersion) -> tuple[dict[str, str], ...]:
    return tuple({"source_type": ref.source_type, "source_id": ref.source_id} for ref in version.sources)


def _entry_artifact_refs(version: MemoryEntryVersion) -> tuple[dict[str, str | int], ...]:
    return tuple(
        {
            "family": ref.family,
            "artifact_id": ref.artifact_id,
            "revision": ref.revision,
        }
        for ref in version.artifacts
    )


def _validate_manifest_entry(
    memory_ref: ArtifactRef,
    item: MemoryManifestEntry,
    version: MemoryEntryVersion,
) -> None:
    if (
        version.memory_artifact_id != memory_ref.artifact_id
        or version.entry_id != item.entry_id
        or version.entry_version_id != item.entry_version_id
    ):
        raise InvalidMemoryCitationError("cross-identity")
    if not _manifest_entry_matches(memory_ref, item, version):
        raise InvalidMemoryCitationError("hash-mismatch")


def _require_memory(value: object) -> Memory:
    if type(value) is not Memory:
        raise _InvalidMemoryCommitError("memory-type", type(value).__name__)
    return value
