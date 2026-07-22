"""Reusable database-backed implementation of the MemoryBackend domain port."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from types import TracebackType

from powercontext.artifacts import ArtifactRef
from powercontext.errors import CapabilityNotSupportedError, MemoryBackendConfigurationError, RevisionConflictError
from powercontext.memory.backends._sql import (
    decode_lineage,
    decode_memory_content,
    encode_entry_refs,
    validate_commit_evidence,
)
from powercontext.memory.canonical import (
    analyze_text,
    entry_content_hash,
    memory_content_hash,
    validate_content_hash,
    validate_identifier,
)
from powercontext.memory.models import (
    EmbeddingProfile,
    EmbeddingVector,
    Memory,
    MemoryCapabilities,
    MemoryChannelHit,
    MemoryEntryVersion,
    MemoryHit,
    MemoryManifestEntry,
    MemoryRevisionChanges,
)
from powercontext.memory.protocols import (
    MemoryBackend,
    MemoryCommit,
    MemoryEvidenceCodec,
    MemoryProjection,
    MemorySearchChannels,
    MemorySearchRequest,
    MemoryUnitOfWork,
)


class DatabaseMemoryBackend(MemoryBackend, ABC):
    """Database driver SPI with shared Memory validation, locking, and unit-of-work behavior.

    A new database adapter implements the synchronous ``*_sync`` primitives and its
    schema/search dialect. This base keeps canonical Memory semantics out of drivers.
    """

    def __init__(self, *, evidence_codec: MemoryEvidenceCodec | None = None) -> None:
        self._evidence_codec = evidence_codec
        self._lock = asyncio.Lock()
        self._closed = False

    async def capabilities(self) -> MemoryCapabilities:
        async with self._lock:
            return await asyncio.to_thread(self._capabilities_sync)

    async def get(self, memory: ArtifactRef, /) -> Memory:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, memory)

    async def latest(self, artifact_id: str, /) -> Memory:
        async with self._lock:
            return await asyncio.to_thread(self._latest_sync, artifact_id)

    async def entries(self, memory: ArtifactRef, /) -> tuple[MemoryEntryVersion, ...]:
        async with self._lock:
            return await asyncio.to_thread(self._entries_sync, memory)

    async def projections(self, memory: ArtifactRef, /) -> tuple[MemoryProjection, ...]:
        async with self._lock:
            return await asyncio.to_thread(self._projections_sync, memory)

    def begin(self) -> AbstractAsyncContextManager[MemoryUnitOfWork]:
        return _DatabaseMemoryUnitOfWork(self)

    async def changes(
        self,
        memory: ArtifactRef,
        since_revision: int | None,
        /,
    ) -> tuple[MemoryRevisionChanges, ...]:
        async with self._lock:
            return await asyncio.to_thread(self._changes_sync, memory, since_revision)

    async def vector_complete(
        self,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._vector_complete_sync, memories, profile)

    async def search(self, request: MemorySearchRequest, /) -> MemorySearchChannels:
        async with self._lock:
            return await asyncio.to_thread(self._search_sync, request)

    async def expand(self, hits: tuple[MemoryHit, ...], /) -> tuple[MemoryEntryVersion, ...]:
        async with self._lock:
            return await asyncio.to_thread(self._expand_sync, hits)

    async def _commit(self, value: MemoryCommit) -> Memory:
        async with self._lock:
            return await asyncio.to_thread(self._commit_sync, value)

    def _entries_sync(self, memory: ArtifactRef) -> tuple[MemoryEntryVersion, ...]:
        revision = self._get_sync(memory)
        return tuple(self._load_manifest_entry(memory.artifact_id, item) for item in revision.content.manifest.entries)

    def _assert_current_refs(self, memories: Sequence[ArtifactRef]) -> None:
        for memory in memories:
            current = self._latest_sync(memory.artifact_id)
            if current.ref != memory:
                raise CapabilityNotSupportedError("head")
            self._entries_sync(memory)

    def _validated_channel_hits(
        self,
        rows: Iterable[Sequence[object]],
        memories: Sequence[ArtifactRef],
    ) -> tuple[MemoryChannelHit, ...]:
        selected = {memory.artifact_id: memory for memory in memories}
        hits: list[MemoryChannelHit] = []
        for row in rows:
            artifact_id, revision, entry_id, version_id, projection_hash, text = row[:6]
            memory_ref = ArtifactRef(str(artifact_id), self._stored_int(revision))
            if selected.get(memory_ref.artifact_id) != memory_ref:
                continue
            manifest = self._get_sync(memory_ref).content.manifest.entries
            item = next((entry for entry in manifest if entry.entry_id == entry_id), None)
            if (
                item is None
                or item.state != "active"
                or item.entry_version_id != version_id
                or item.entry_content_hash != projection_hash
            ):
                continue
            version = self._load_manifest_entry(memory_ref.artifact_id, item)
            hits.append(MemoryChannelHit(memory_ref, version.entry_id, version.entry_version_id, str(text)))
        return tuple(hits)

    def _expand_sync(self, hits: tuple[MemoryHit, ...]) -> tuple[MemoryEntryVersion, ...]:
        revisions: dict[ArtifactRef, Memory] = {}
        versions: list[MemoryEntryVersion] = []
        for hit in hits:
            memory = revisions.setdefault(hit.memory_ref, self._get_sync(hit.memory_ref))
            item = next(
                (
                    entry
                    for entry in memory.content.manifest.entries
                    if entry.entry_id == hit.entry_id and entry.entry_version_id == hit.entry_version_id
                ),
                None,
            )
            if item is None:
                raise self._database_error("entry-link")
            versions.append(self._load_manifest_entry(memory.artifact_id, item))
        return tuple(versions)

    def _decode_memory_row(self, row: Sequence[object]) -> Memory:
        artifact_id, revision, family, content_json, content_hash, lineage_json = row
        if family != "memory":
            raise self._database_error("family")
        content = decode_memory_content(self._stored_text(content_json))
        stored_hash = validate_content_hash(self._stored_text(content_hash))
        if memory_content_hash(content) != stored_hash:
            raise self._database_error("content-hash")
        memory = Memory(
            artifact_id=self._stored_text(artifact_id),
            revision=self._stored_int(revision),
            content=content,
            lineage=decode_lineage(self._stored_text(lineage_json), self._evidence_codec),
        )
        self._validate_manifest(memory)
        return memory

    def _validate_manifest(self, memory: Memory) -> None:
        previous_identity: bytes | None = None
        version_ids: set[str] = set()
        for item in memory.content.manifest.entries:
            validate_identifier(item.entry_id)
            validate_identifier(item.entry_version_id)
            validate_content_hash(item.entry_content_hash)
            identity = item.entry_id.encode("utf-8")
            if (
                previous_identity is not None and identity <= previous_identity
            ) or item.entry_version_id in version_ids:
                raise self._database_error("manifest")
            previous_identity = identity
            version_ids.add(item.entry_version_id)

    def _load_manifest_entry(self, memory_artifact_id: str, item: MemoryManifestEntry) -> MemoryEntryVersion:
        version = self._load_entry_version(memory_artifact_id, item.entry_version_id)
        self._validate_entry_link(item, version, memory_artifact_id)
        return version

    def _validate_entry_link(
        self,
        item: MemoryManifestEntry,
        version: MemoryEntryVersion,
        memory_artifact_id: str,
    ) -> None:
        if (
            version.memory_artifact_id != memory_artifact_id
            or version.entry_id != item.entry_id
            or version.entry_version_id != item.entry_version_id
            or version.entry_content_hash != item.entry_content_hash
        ):
            raise self._database_error("entry-link")
        source_refs, artifact_refs = encode_entry_refs(version, self._evidence_codec)
        actual_hash = entry_content_hash(
            kind=version.kind,
            text=version.text,
            source_refs=self._json_array(source_refs),
            artifact_refs=self._json_array(artifact_refs),
        )
        if actual_hash != version.entry_content_hash:
            raise self._database_error("entry-hash")

    def _validate_commit(self, value: MemoryCommit) -> None:
        memory = value.memory
        validate_identifier(memory.artifact_id)
        expected_revision = 1 if value.base is None else value.base.revision + 1
        if memory.revision != expected_revision:
            raise self._database_error("commit", "revision is not the direct successor")
        if value.base is not None and memory.artifact_id != value.base.artifact_id:
            raise self._database_error("commit", "artifact identity changed")
        if value.content_hash != memory_content_hash(memory.content):
            raise self._database_error("commit", "content hash is not canonical")
        self._validate_manifest(memory)
        validate_commit_evidence(value, self._evidence_codec)
        self._validate_commit_transition(value)

        new_versions: dict[str, MemoryEntryVersion] = {}
        for version in value.entry_versions:
            if version.entry_version_id in new_versions:
                raise self._database_error("commit", "duplicate new entry version")
            self._validate_new_entry_version(value, version)
            new_versions[version.entry_version_id] = version
        self._validate_commit_projections(value, new_versions)

    def _validate_commit_transition(self, value: MemoryCommit) -> None:
        base_entries = (
            {} if value.base is None else {item.entry_id: item for item in value.base.content.manifest.entries}
        )
        target_entries = {item.entry_id: item for item in value.memory.content.manifest.entries}
        expected_changes: dict[str, tuple[str, str | None, str | None]] = {}

        for entry_id, target in target_entries.items():
            expected_change = self._expected_commit_change(base_entries.get(entry_id), target)
            if expected_change is not None:
                expected_changes[entry_id] = expected_change

        if set(base_entries) - set(target_entries):
            raise self._database_error("commit", "changes remove a manifest entry")

        changes = value.memory.content.changes
        change_ids = tuple(change.entry_id for change in changes)
        if change_ids != tuple(sorted(change_ids, key=str.encode)) or len(set(change_ids)) != len(change_ids):
            raise self._database_error("commit", "changes are not ordered uniquely")
        supplied_changes = {
            change.entry_id: (change.op, change.from_entry_version_id, change.to_entry_version_id) for change in changes
        }
        if supplied_changes != expected_changes:
            raise self._database_error("commit", "changes do not match the manifest transition")

        expected_version_ids = {
            target_version_id
            for operation, _, target_version_id in expected_changes.values()
            if operation in {"add", "revise"} and target_version_id is not None
        }
        supplied_version_ids = tuple(version.entry_version_id for version in value.entry_versions)
        if (
            len(set(supplied_version_ids)) != len(supplied_version_ids)
            or set(supplied_version_ids) != expected_version_ids
        ):
            raise self._database_error("commit", "entry versions do not match the manifest transition")

    def _expected_commit_change(
        self,
        base: MemoryManifestEntry | None,
        target: MemoryManifestEntry,
    ) -> tuple[str, str | None, str | None] | None:
        if base is None:
            if target.state != "active":
                raise self._database_error("commit", "changes add an inactive entry")
            return ("add", None, target.entry_version_id)
        if base.state == "inactive":
            if target.entry_version_id != base.entry_version_id:
                raise self._database_error("commit", "changes revise an inactive entry")
            return ("reactivate", None, target.entry_version_id) if target.state == "active" else None
        if target.entry_version_id != base.entry_version_id:
            if target.state != "active":
                raise self._database_error("commit", "changes revise and change state")
            return ("revise", base.entry_version_id, target.entry_version_id)
        return ("deactivate", base.entry_version_id, None) if target.state == "inactive" else None

    def _validate_new_entry_version(self, value: MemoryCommit, version: MemoryEntryVersion) -> None:
        memory = value.memory
        validate_identifier(version.entry_id)
        validate_identifier(version.entry_version_id)
        if version.memory_artifact_id != memory.artifact_id or version.created_in_revision != memory.revision:
            raise self._database_error("commit", "entry version crosses its creating Revision")
        source_refs, artifact_refs = encode_entry_refs(version, self._evidence_codec)
        expected_hash = entry_content_hash(
            kind=version.kind,
            text=version.text,
            source_refs=self._json_array(source_refs),
            artifact_refs=self._json_array(artifact_refs),
        )
        if expected_hash != version.entry_content_hash:
            raise self._database_error("commit", "entry content hash is not canonical")

        base_item = None
        if value.base is not None:
            base_item = next(
                (item for item in value.base.content.manifest.entries if item.entry_id == version.entry_id),
                None,
            )
        if base_item is None:
            if version.version != 1 or version.previous_version_id is not None:
                raise self._database_error("commit", "new logical entry has an invalid predecessor")
            return
        previous = self._load_entry_version(memory.artifact_id, base_item.entry_version_id)
        if version.previous_version_id != base_item.entry_version_id or version.version != previous.version + 1:
            raise self._database_error("commit", "entry version is not the direct successor")

    def _validate_commit_projections(
        self,
        value: MemoryCommit,
        new_versions: dict[str, MemoryEntryVersion],
    ) -> None:
        memory = value.memory
        active = {item.entry_id: item for item in memory.content.manifest.entries if item.state == "active"}
        projections = {projection.entry_version.entry_id: projection for projection in value.projections}
        if len(projections) != len(value.projections) or projections.keys() != active.keys():
            raise self._database_error("commit", "active projections do not match the manifest")
        for item in memory.content.manifest.entries:
            version = new_versions.get(item.entry_version_id)
            if version is None:
                version = self._load_entry_version(memory.artifact_id, item.entry_version_id)
            self._validate_entry_link(item, version, memory.artifact_id)
            if item.state != "active":
                continue
            projection = projections[item.entry_id]
            if projection.entry_version != version or projection.searchable_text != analyze_text(version.text):
                raise self._database_error("commit", "projection does not match authoritative content")
            self._validate_projection_vector(
                projection.embedding,
                projection.embedding_content_hash,
                version,
            )

    @staticmethod
    def _assert_commit_base(value: MemoryCommit, current: Memory | None) -> None:
        if value.base is None and current is not None:
            raise RevisionConflictError(value.memory, current)
        if value.base is not None and current != value.base:
            raise RevisionConflictError(value.base, current)

    @abstractmethod
    def _capabilities_sync(self) -> MemoryCapabilities: ...

    @abstractmethod
    def _get_sync(self, memory: ArtifactRef) -> Memory: ...

    @abstractmethod
    def _latest_sync(self, artifact_id: str) -> Memory: ...

    @abstractmethod
    def _projections_sync(self, memory: ArtifactRef) -> tuple[MemoryProjection, ...]: ...

    @abstractmethod
    def _changes_sync(
        self,
        memory: ArtifactRef,
        since_revision: int | None,
    ) -> tuple[MemoryRevisionChanges, ...]: ...

    @abstractmethod
    def _vector_complete_sync(
        self,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        *,
        validate_heads: bool = True,
    ) -> bool: ...

    @abstractmethod
    def _search_sync(self, request: MemorySearchRequest) -> MemorySearchChannels: ...

    @abstractmethod
    def _commit_sync(self, value: MemoryCommit) -> Memory: ...

    @abstractmethod
    def _load_entry_version(self, memory_artifact_id: str, entry_version_id: str) -> MemoryEntryVersion: ...

    @abstractmethod
    def _validate_projection_vector(
        self,
        embedding: EmbeddingVector | None,
        stored_hash: str | None,
        version: MemoryEntryVersion,
    ) -> None: ...

    @staticmethod
    @abstractmethod
    def _json_array(value: str) -> tuple[object, ...]: ...

    @staticmethod
    @abstractmethod
    def _stored_int(value: object) -> int: ...

    @staticmethod
    @abstractmethod
    def _stored_text(value: object) -> str: ...

    @staticmethod
    @abstractmethod
    def _database_error(code: str, detail: object | None = None) -> MemoryBackendConfigurationError: ...


class _DatabaseMemoryUnitOfWork(AbstractAsyncContextManager[MemoryUnitOfWork], MemoryUnitOfWork):
    def __init__(self, backend: DatabaseMemoryBackend) -> None:
        self._backend = backend
        self._complete = False

    async def __aenter__(self) -> MemoryUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        if exc_type is not None and not self._complete:
            await self.rollback()

    async def commit(self, value: MemoryCommit, /) -> Memory:
        if self._complete:
            raise self._backend._database_error("transaction")
        try:
            return await self._backend._commit(value)
        finally:
            self._complete = True

    async def rollback(self) -> None:
        self._complete = True
