"""SQLAlchemy implementation of the built-in Memory backend contract."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, RootModel
from sqlalchemy import Table, delete, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactDraft, ArtifactRef
from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    InvalidEmbeddingError,
    Memory,
    MemoryCapabilities,
    MemoryChannelHit,
    MemoryCommit,
    MemoryContent,
    MemoryEntryVersion,
    MemoryHit,
    MemoryProjection,
    MemoryRevisionChanges,
    MemorySearchChannels,
    MemorySearchRequest,
    MemoryUnitOfWork,
)
from powercontext.builtin.artifacts.memory.canonical import (
    analyze_text,
    embedding_content_hash,
    entry_content_hash,
    memory_content_hash,
    validate_embedding,
)
from powercontext.builtin.artifacts.memory.errors import (
    InvalidMemoryCitationError,
    MemoryBackendConfigurationError,
)
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    MEMORY_ENTRY_HEADS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
)
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef


class _SourceRefs(RootModel[tuple[SourceRef, ...]]):
    pass


class _ArtifactRefs(RootModel[tuple[ArtifactRef, ...]]):
    pass


def memory_channel_hits(
    rows: Iterable[Mapping[Any, Any]],
    memories: tuple[ArtifactRef, ...],
    /,
) -> tuple[MemoryChannelHit, ...]:
    requested = {(memory.artifact_id, memory.revision) for memory in memories}
    return tuple(
        MemoryChannelHit(
            memory_ref=ArtifactRef(
                family="memory",
                artifact_id=str(row["memory_artifact_id"]),
                revision=int(row["head_revision"]),
            ),
            entry_id=str(row["entry_id"]),
            entry_version_id=str(row["entry_version_id"]),
            text=str(row["text"]),
        )
        for row in rows
        if (str(row["memory_artifact_id"]), int(row["head_revision"])) in requested
    )


class _MemoryDraft(ArtifactDraft[MemoryContent]):
    family: ClassVar[str] = Memory.family


class _RebuildSnapshot(BaseModel):
    memory_ref: ArtifactRef
    projections: tuple[MemoryProjection, ...]


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
        index: MemoryIndexStrategy | None = None,
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
        async with self._connection() as connection:
            try:
                artifact = await self._artifacts.get(connection, self._scope_id, memory)
            except RepositoryNotFoundError:
                raise ArtifactNotFoundError(memory) from None
        return _require_memory(artifact)

    async def latest(self, artifact_id: str, /) -> Memory:
        async with self._connection() as connection:
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
        canonical = await self.get(memory)
        version_ids = tuple(item.entry_version_id for item in canonical.content.manifest.entries)
        if not version_ids:
            return ()
        async with self._connection() as connection:
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
        if set(by_id) != set(version_ids):
            raise InvalidMemoryCitationError("missing-version")
        return tuple(by_id[version_id] for version_id in version_ids)

    async def projections(self, memory: ArtifactRef, /) -> tuple[MemoryProjection, ...]:
        canonical = await self.get(memory)
        async with self._connection() as connection:
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
        if self._bound_connection is not None:
            return _bound_unit_of_work(self, self._bound_connection)
        return _owned_unit_of_work(self)

    async def changes(
        self,
        memory: ArtifactRef,
        since_revision: int | None,
        /,
    ) -> tuple[MemoryRevisionChanges, ...]:
        target = await self.get(memory)
        lower = target.revision - 1 if since_revision is None else since_revision
        async with self._connection() as connection:
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
        async with self._connection() as connection:
            return await self._index.vector_complete(connection, self._scope_id, memories, profile)

    async def search(self, request: MemorySearchRequest, /) -> MemorySearchChannels:
        for memory in request.memories:
            exact = await self.get(memory)
            latest = await self.latest(memory.artifact_id)
            if exact.as_ref() != latest.as_ref():
                raise InvalidMemoryCitationError("memory-mismatch")
        async with self._connection() as connection:
            return await self._index.search(connection, self._scope_id, request)

    async def expand(self, hits: tuple[MemoryHit, ...], /) -> tuple[MemoryEntryVersion, ...]:
        expanded: list[MemoryEntryVersion] = []
        async with self._connection() as connection:
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
        return tuple(expanded)

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
                    "embedding": validate_embedding(vector, dimension=profile.dimension),
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
            snapshot.model_copy(update={"projections": tuple(next(embedded) for _ in snapshot.projections)})
            for snapshot in snapshots
        )

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[AsyncConnection]:
        if self._bound_connection is not None:
            yield self._bound_connection
            return
        async with self._database.transaction() as connection:
            yield connection

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

        if value.entry_versions:
            await connection.execute(
                insert(MEMORY_ENTRY_VERSIONS_TABLE),
                [_entry_values(self._scope_id, entry) for entry in value.entry_versions],
            )
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
async def _owned_unit_of_work(
    backend: RelationalMemoryBackend,
) -> AsyncIterator[_RelationalMemoryUnitOfWork]:
    async with backend._database.transaction() as connection:
        yield _RelationalMemoryUnitOfWork(backend, connection)


@asynccontextmanager
async def _bound_unit_of_work(
    backend: RelationalMemoryBackend,
    connection: AsyncConnection,
) -> AsyncIterator[_RelationalMemoryUnitOfWork]:
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


class MemoryIndexStrategy(Protocol):
    """Optional query projection updated in the authoritative write transaction."""

    capabilities: MemoryCapabilities
    tables: tuple[Table, ...]

    async def initialize(self, connection: AsyncConnection, /) -> None: ...

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None: ...

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels: ...

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool: ...

    async def hydrate(
        self,
        connection: AsyncConnection,
        scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]: ...


class NoMemoryIndex:
    """Truthfully expose an authoritative store without search projections."""

    capabilities = MemoryCapabilities(fts=False)
    tables: tuple[Table, ...] = ()

    async def initialize(self, connection: AsyncConnection, /) -> None:
        del connection

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        del connection, scope_id, memory_ref, projections

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        del connection, scope_id, request
        raise CapabilityNotSupportedError("fts")

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        del connection, scope_id, memories, profile
        return False

    async def hydrate(
        self,
        connection: AsyncConnection,
        scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]:
        del connection, scope_id
        return projections


class CompositeMemoryIndex:
    """Combine independent search indexes without leaking them into repositories."""

    def __init__(self, *indexes: MemoryIndexStrategy) -> None:
        self.indexes = indexes
        fts = any(index.capabilities.fts for index in indexes)
        vector_indexes = tuple(index for index in indexes if index.capabilities.vector)
        profile = vector_indexes[0].capabilities.embedding_profile if vector_indexes else None
        self.capabilities = MemoryCapabilities(
            fts=fts,
            vector=bool(vector_indexes),
            hybrid=fts and bool(vector_indexes),
            embedding_profile=profile,
        )
        self.tables = tuple(table for index in indexes for table in index.tables)

    async def initialize(self, connection: AsyncConnection, /) -> None:
        for index in self.indexes:
            await index.initialize(connection)

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        for index in self.indexes:
            await index.replace(connection, scope_id, memory_ref, projections)

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        fts: tuple[MemoryChannelHit, ...] = ()
        vector: tuple[MemoryChannelHit, ...] = ()
        for index in self.indexes:
            channels = await index.search(connection, scope_id, request)
            fts += channels.fts
            vector += channels.vector
        return MemorySearchChannels(fts=fts, vector=vector)

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        vector_indexes = tuple(index for index in self.indexes if index.capabilities.vector)
        if not vector_indexes:
            return False
        for index in vector_indexes:
            if not await index.vector_complete(connection, scope_id, memories, profile):
                return False
        return True

    async def hydrate(
        self,
        connection: AsyncConnection,
        scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]:
        for index in self.indexes:
            projections = await index.hydrate(connection, scope_id, projections)
        return projections
