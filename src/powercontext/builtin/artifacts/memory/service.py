"""Public orchestration for the Memory Artifact Family."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias, TypeVar, overload
from uuid import uuid4

from powercontext.artifacts import Artifact, ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.memory.canonical import (
    analyze_text,
    canonical_json,
    embedding_content_hash,
    entry_content_bytes,
    entry_content_hash,
    memory_content_hash,
    normalize_kind,
    normalize_reason,
    normalize_text,
    validate_embedding,
    validate_identifier,
)
from powercontext.builtin.artifacts.memory.errors import (
    CapabilityNotSupportedError,
    InvalidEmbeddingError,
    InvalidMemoryCandidateError,
    InvalidMemoryCitationError,
    InvalidMemoryEvidenceError,
    MemoryEntryInactiveError,
    MemoryEntryNotFoundError,
)
from powercontext.builtin.artifacts.memory.models import (
    EmbeddingProfile,
    Memory,
    MemoryCapabilities,
    MemoryChange,
    MemoryCitation,
    MemoryContent,
    MemoryEntryInput,
    MemoryEntryVersion,
    MemoryHit,
    MemoryManifest,
    MemoryManifestEntry,
    MemoryRevisionChanges,
    MemorySearchMode,
    MemorySearchResult,
    MemoryUsedSearchMode,
)
from powercontext.builtin.artifacts.memory.protocols import (
    CandidatePipeline,
    MemoryBackend,
    MemoryCandidateRequest,
    MemoryCommit,
    MemoryProjection,
    MemorySearchRequest,
    MemoryWritePlan,
)
from powercontext.builtin.artifacts.memory.ranking import fuse_rankings
from powercontext.builtin.inference import (
    EmbeddingModel,
    EmbeddingVector,
    InferenceTimeoutError,
    InferenceUnavailableError,
)
from powercontext.errors import RevisionConflictError
from powercontext.sources import Source, SourceRef

MemoryRememberMode: TypeAlias = Literal["append", "extract", "auto"]
IdFactory: TypeAlias = Callable[[str], str]
ValueT = TypeVar("ValueT")


class _SourceResolver(Protocol):
    async def get(self, source: Source, /) -> Source: ...

    def as_ref(self, source: Source, /) -> SourceRef: ...


class _ArtifactResolver(Protocol):
    async def get(self, artifact: Artifact[object], /) -> Artifact[object]: ...


@dataclass(frozen=True, slots=True)
class _OperationEvidence:
    sources: tuple[Source, ...]
    artifacts: tuple[Artifact[object], ...]


@dataclass(frozen=True, slots=True)
class _EntryMaterial:
    kind: str
    text: str
    sources: tuple[SourceRef, ...]
    artifacts: tuple[ArtifactRef, ...]
    content_bytes: bytes
    content_hash: str


class _InvalidMemoryOperationError(ValueError):
    def __init__(self, code: str) -> None:
        messages = {
            "append-entries": "memory append mode requires at least one entry",
            "extract-evidence": "memory extract mode requires evidence and does not accept entries",
            "no-work": "memory remember call has no executable work",
            "duplicate-target": "a memory entry can only be targeted once per operation",
            "id-collision": "generated memory identity collides with current content",
            "organize-mode": "unsupported memory organize mode",
            "since-greater": "since_revision cannot be greater than the target Revision",
            "since-negative": "since_revision cannot be negative",
            "search-memories": "memory search requires at least one explicit Memory ref",
            "search-limit": "memory search limit must be positive",
            "search-mode": "unsupported memory search mode",
        }
        super().__init__(messages[code])


class MemoryService:
    """Validate and orchestrate Memory operations without exposing storage details."""

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        candidate_pipeline: CandidatePipeline | None = None,
        embedding_model: EmbeddingModel | None = None,
        source_resolver: _SourceResolver | None = None,
        artifact_resolver: _ArtifactResolver | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._backend = backend
        self._candidate_pipeline = candidate_pipeline
        self._embedding_model = embedding_model
        self._source_resolver = source_resolver
        self._artifact_resolver = artifact_resolver
        self._id_factory = _default_id if id_factory is None else id_factory

    async def get(self, memory: Memory, /) -> Memory:
        """Return the canonical exact Memory Revision matching ``memory``."""

        return await self._canonical_memory(memory)

    async def latest(self, memory: Memory, /) -> Memory:
        """Return the current head of the same Memory identity."""

        canonical = await self.get(memory)
        return await self._backend.latest(canonical.artifact_id)

    async def revisions(self, memory: Memory, /) -> tuple[Memory, ...]:
        """Return the visible Memory history in ascending Revision order."""

        canonical = await self.get(memory)
        latest = await self._backend.latest(canonical.artifact_id)
        history = []
        for revision in range(1, latest.revision + 1):
            history.append(
                await self._backend.get(
                    ArtifactRef(family=Memory.family, artifact_id=canonical.artifact_id, revision=revision)
                )
            )
        return tuple(history)

    async def head(self, artifact_id: str, /) -> Memory:
        """Return the current Memory head by its stable Artifact identity."""

        return await self._backend.latest(artifact_id)

    async def revision(self, memory: ArtifactRef, /) -> Memory:
        """Return one exact Memory Revision by its stable reference."""

        return await self._backend.get(memory)

    async def remember(
        self,
        *,
        memory: Memory | None,
        sources: Sequence[Source] = (),
        artifacts: Sequence[Artifact[object]] = (),
        entries: Sequence[MemoryEntryInput] = (),
        mode: MemoryRememberMode = "auto",
    ) -> Memory | None:
        """Append or extract validated entry changes against one exact head."""

        return await self.apply(
            await self.plan_remember(
                memory=memory,
                sources=sources,
                artifacts=artifacts,
                entries=entries,
                mode=mode,
            )
        )

    async def plan_remember(
        self,
        *,
        memory: Memory | None,
        sources: Sequence[Source] = (),
        artifacts: Sequence[Artifact[object]] = (),
        entries: Sequence[MemoryEntryInput] = (),
        mode: MemoryRememberMode = "auto",
    ) -> MemoryWritePlan:
        """Validate and prepare a write without mutating authoritative storage."""

        selected_mode = _select_remember_mode(
            mode,
            has_entries=bool(entries),
            has_evidence=bool(sources or artifacts),
        )
        base = await self._canonical_base(memory)
        evidence = await self._canonical_operation_evidence(sources, artifacts)
        current_entries = () if base is None else await self._validated_entries(base)
        candidates = await self._candidates(
            selected_mode,
            tuple(entries),
            evidence,
            current_entries,
            active_version_ids=(
                frozenset()
                if base is None
                else frozenset(
                    item.entry_version_id for item in base.content.manifest.entries if item.state == "active"
                )
            ),
        )
        if not candidates:
            return MemoryWritePlan(result=base, commit=None)

        commit = await self._prepare_commit(
            base=base,
            candidates=candidates,
            evidence=evidence,
            current_entries=current_entries,
        )
        if commit is None:
            return MemoryWritePlan(result=base, commit=None)
        return MemoryWritePlan(result=commit.memory, commit=commit)

    async def apply(self, plan: MemoryWritePlan, /) -> Memory | None:
        """Apply one prepared write through this service's transaction boundary."""

        if plan.commit is None:
            return plan.result
        async with self._backend.begin() as unit_of_work:
            committed = await unit_of_work.commit(plan.commit)
        if plan.result != committed:
            raise InvalidMemoryCitationError("memory-mismatch")
        return committed

    async def forget(
        self,
        memory: Memory,
        *,
        entries: Sequence[MemoryEntryVersion],
        reason: str | None = None,
    ) -> Memory:
        """Deactivate logical entries without deleting immutable content."""

        return await self._set_entry_state(
            memory,
            entries=entries,
            target_state="inactive",
            reason=reason,
        )

    async def reactivate(
        self,
        memory: Memory,
        *,
        entries: Sequence[MemoryEntryVersion],
        reason: str | None = None,
    ) -> Memory:
        """Restore inactive logical entries without creating body versions."""

        return await self._set_entry_state(
            memory,
            entries=entries,
            target_state="active",
            reason=reason,
        )

    async def organize(
        self,
        memory: Memory,
        *,
        mode: Literal["default", "dedupe", "normalize"] = "default",
    ) -> Memory:
        """Apply only exact deduplication and canonical normalization."""

        if mode not in {"default", "dedupe", "normalize"}:
            raise _InvalidMemoryOperationError("organize-mode")
        base = await self._canonical_base(memory)
        current_entries = await self._validated_entries(base)
        manifest = {entry.entry_id: entry for entry in base.content.manifest.entries}
        current_by_entry = {entry.entry_id: entry for entry in current_entries}
        changes: list[MemoryChange] = []
        changed_ids: set[str] = set()
        new_versions: list[MemoryEntryVersion] = []

        if mode in {"default", "dedupe"}:
            dedupe_changes, changed_ids = self._deduplicate_manifest(manifest, current_by_entry)
            changes.extend(dedupe_changes)

        if mode in {"default", "normalize"}:
            normalize_changes, new_versions = self._normalize_manifest_entries(
                base,
                manifest,
                current_by_entry,
                skip=changed_ids,
            )
            changes.extend(normalize_changes)

        if not changes:
            return base
        return await self._commit_existing_transition(
            base=base,
            manifest=manifest,
            changes=changes,
            current_by_entry=current_by_entry,
            entry_versions=tuple(new_versions),
        )

    async def changes(
        self,
        memory: Memory,
        *,
        since_revision: int | None = None,
    ) -> tuple[MemoryRevisionChanges, ...]:
        """Read compact change summaries without expanding entry bodies."""

        target = await self._canonical_memory(memory)
        if since_revision is not None:
            if since_revision < 0:
                raise _InvalidMemoryOperationError("since-negative")
            if since_revision > target.revision:
                raise _InvalidMemoryOperationError("since-greater")
            if since_revision == target.revision:
                return ()
            if since_revision > 0:
                await self._backend.get(
                    ArtifactRef(
                        family=Memory.family,
                        artifact_id=target.artifact_id,
                        revision=since_revision,
                    )
                )
        return await self._backend.changes(target.as_ref(), since_revision)

    async def search(
        self,
        query: str,
        *,
        memories: Sequence[Memory],
        limit: int = 10,
        mode: MemorySearchMode = "auto",
    ) -> MemorySearchResult:
        """Search explicit current Memory heads with capability-safe fallback."""

        if not memories:
            raise _InvalidMemoryOperationError("search-memories")
        if limit < 1:
            raise _InvalidMemoryOperationError("search-limit")
        if mode not in {"fts", "vector", "hybrid", "auto"}:
            raise _InvalidMemoryOperationError("search-mode")
        selected_by_ref: dict[tuple[str, str, int], Memory] = {}
        for memory in memories:
            ref = memory.as_ref()
            selected_by_ref.setdefault((ref.family, ref.artifact_id, ref.revision), memory)
        selected = tuple(selected_by_ref.values())
        selected_memories = tuple(memory.as_ref() for memory in selected)
        await self._validate_search_heads(selected)
        capabilities = await self._backend.capabilities()
        selected_mode = await self._select_search_mode(
            mode,
            memories=selected_memories,
            capabilities=capabilities,
        )
        normalized_query = normalize_text(query)
        query_vector = None
        profile = None
        if selected_mode in {"vector", "hybrid"}:
            profile = capabilities.embedding_profile
            if profile is None:
                raise CapabilityNotSupportedError(selected_mode)
            try:
                query_vector = (await self._embed_texts((normalized_query,), profile))[0]
            except (InferenceUnavailableError, InferenceTimeoutError) as error:
                if mode == "auto" and capabilities.fts:
                    selected_mode = "fts"
                    profile = None
                else:
                    raise CapabilityNotSupportedError(
                        selected_mode,
                        "embedding model is temporarily unavailable",
                    ) from error
        request = MemorySearchRequest(
            query=normalized_query,
            analyzed_query=analyze_text(normalized_query),
            memories=selected_memories,
            candidate_limit=max(limit * 4, 32),
            mode=selected_mode,
            query_vector=query_vector,
            embedding_profile=profile,
        )
        channels = await self._backend.search(request)
        hits = fuse_rankings(
            fts=channels.fts if selected_mode in {"fts", "hybrid"} else (),
            vector=channels.vector if selected_mode in {"vector", "hybrid"} else (),
            limit=limit,
        )
        return MemorySearchResult(mode=selected_mode, hits=hits)

    async def expand(
        self,
        hits: Sequence[MemoryHit],
        *,
        layer: Literal["full"] = "full",
    ) -> tuple[MemoryEntryVersion, ...]:
        """Expand exact hit anchors and reject cross-Revision substitutions."""

        if layer != "full":
            raise CapabilityNotSupportedError("expand-layer")
        hit_tuple = tuple(hits)
        versions = await self._backend.expand(hit_tuple)
        if len(versions) != len(hit_tuple):
            raise InvalidMemoryCitationError("expand-count")
        for hit, version in zip(hit_tuple, versions, strict=True):
            await self._validate_anchor(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
                version=version,
            )
        return versions

    async def entries(self, memory: Memory, /) -> tuple[MemoryEntryVersion, ...]:
        """Return the entry objects referenced by one exact current Memory head."""

        canonical = await self._canonical_memory(memory)
        return await self._validated_entries(canonical)

    async def rebuild_projections(self, embedding_model: EmbeddingModel | None = None, /) -> None:
        """Rebuild current-head search projections from authoritative Memory revisions."""

        await self._backend.rebuild_projections(embedding_model)

    async def validate_citation(self, citation: MemoryCitation) -> MemoryEntryVersion:
        """Resolve one exact Handoff citation."""

        hit = MemoryHit(
            memory_ref=citation.memory_ref,
            entry_id=citation.entry_id,
            entry_version_id=citation.entry_version_id,
            text="",
            score=0.0,
            matched_by=(),
        )
        return (await self.expand((hit,)))[0]

    async def _validate_search_heads(self, memories: tuple[Memory, ...]) -> None:
        for memory in memories:
            exact = await self._backend.get(memory.as_ref())
            latest = await self._backend.latest(memory.artifact_id)
            if exact != memory or exact.as_ref() != latest.as_ref():
                raise CapabilityNotSupportedError("head")

    async def _select_search_mode(
        self,
        requested: MemorySearchMode,
        *,
        memories: tuple[ArtifactRef, ...],
        capabilities: MemoryCapabilities,
    ) -> MemoryUsedSearchMode:
        if requested == "fts":
            if not capabilities.fts:
                raise CapabilityNotSupportedError("fts")
            return "fts"

        embedding_profile = None if self._embedding_model is None else self._embedding_model.profile
        profile = capabilities.embedding_profile
        profile_matches = profile is not None and embedding_profile == profile
        vector_complete = (
            await self._backend.vector_complete(memories, profile)
            if capabilities.vector and profile_matches and profile is not None
            else False
        )
        vector_available = capabilities.vector and profile_matches and vector_complete
        hybrid_available = capabilities.hybrid and vector_available

        if requested == "vector":
            if not vector_available:
                raise CapabilityNotSupportedError("vector")
            return "vector"
        if requested == "hybrid":
            if not hybrid_available:
                raise CapabilityNotSupportedError("hybrid")
            return "hybrid"
        if requested == "auto":
            if hybrid_available:
                return "hybrid"
            if capabilities.fts:
                return "fts"
            raise CapabilityNotSupportedError("fts")
        raise _InvalidMemoryOperationError("search-mode")

    def _deduplicate_manifest(
        self,
        manifest: dict[str, MemoryManifestEntry],
        current_by_entry: dict[str, MemoryEntryVersion],
    ) -> tuple[list[MemoryChange], set[str]]:
        groups: dict[bytes, list[str]] = {}
        for item in manifest.values():
            if item.state == "active":
                material = self._material_from_version(current_by_entry[item.entry_id])
                groups.setdefault(material.content_bytes, []).append(item.entry_id)

        changes: list[MemoryChange] = []
        changed_ids: set[str] = set()
        for entry_ids in groups.values():
            ordered = sorted(entry_ids, key=str.encode)
            for entry_id in ordered[1:]:
                item = manifest[entry_id]
                manifest[entry_id] = item.model_copy(update={"state": "inactive"})
                changes.append(
                    MemoryChange(
                        op="deactivate",
                        entry_id=entry_id,
                        from_entry_version_id=item.entry_version_id,
                        to_entry_version_id=None,
                        reason="dedupe",
                    )
                )
                changed_ids.add(entry_id)
        return changes, changed_ids

    def _normalize_manifest_entries(
        self,
        base: Memory,
        manifest: dict[str, MemoryManifestEntry],
        current_by_entry: dict[str, MemoryEntryVersion],
        *,
        skip: set[str],
    ) -> tuple[list[MemoryChange], list[MemoryEntryVersion]]:
        changes: list[MemoryChange] = []
        new_versions: list[MemoryEntryVersion] = []
        for entry_id, previous in tuple(current_by_entry.items()):
            if entry_id in skip:
                continue
            material = self._material_from_version(previous)
            if _is_canonical_version(previous, material):
                continue
            version = self._new_entry_version(
                memory_id=base.artifact_id,
                entry_id=entry_id,
                previous=previous,
                material=material,
                created_in_revision=base.revision + 1,
            )
            item = manifest[entry_id]
            manifest[entry_id] = _manifest_entry(version, state=item.state)
            current_by_entry[entry_id] = version
            new_versions.append(version)
            changes.append(
                MemoryChange(
                    op="revise",
                    entry_id=entry_id,
                    from_entry_version_id=previous.entry_version_id,
                    to_entry_version_id=version.entry_version_id,
                    reason="normalize",
                )
            )
        return changes, new_versions

    async def _set_entry_state(
        self,
        memory: Memory,
        *,
        entries: Sequence[MemoryEntryVersion],
        target_state: Literal["active", "inactive"],
        reason: str | None,
    ) -> Memory:
        base = await self._canonical_base(memory)
        current_entries = await self._validated_entries(base)
        manifest = {entry.entry_id: entry for entry in base.content.manifest.entries}
        current_by_entry = {entry.entry_id: entry for entry in current_entries}
        normalized_reason = normalize_reason(reason)
        changes: list[MemoryChange] = []

        seen: set[tuple[str, str]] = set()
        for entry in entries:
            identity = (entry.entry_id, entry.entry_version_id)
            if identity in seen:
                continue
            seen.add(identity)
            entry_id = validate_identifier(entry.entry_id)
            item = manifest.get(entry_id)
            if item is None:
                raise MemoryEntryNotFoundError(entry_id)
            current = current_by_entry.get(entry_id)
            if current is None:
                raise MemoryEntryNotFoundError(entry_id)
            if entry != current:
                raise InvalidMemoryCitationError("entry-mismatch")
            if item.state == target_state:
                continue
            manifest[entry_id] = item.model_copy(update={"state": target_state})
            changes.append(
                MemoryChange(
                    op="reactivate" if target_state == "active" else "deactivate",
                    entry_id=entry_id,
                    from_entry_version_id=None if target_state == "active" else item.entry_version_id,
                    to_entry_version_id=item.entry_version_id if target_state == "active" else None,
                    reason=normalized_reason,
                )
            )

        if not changes:
            return base
        return await self._commit_existing_transition(
            base=base,
            manifest=manifest,
            changes=changes,
            current_by_entry=current_by_entry,
            entry_versions=(),
        )

    async def _commit_existing_transition(
        self,
        *,
        base: Memory,
        manifest: dict[str, MemoryManifestEntry],
        changes: Sequence[MemoryChange],
        current_by_entry: dict[str, MemoryEntryVersion],
        entry_versions: tuple[MemoryEntryVersion, ...],
    ) -> Memory:
        sorted_manifest = tuple(sorted(manifest.values(), key=lambda item: item.entry_id.encode("utf-8")))
        sorted_changes = tuple(sorted(changes, key=lambda change: change.entry_id.encode("utf-8")))
        content = MemoryContent(manifest=MemoryManifest(entries=sorted_manifest), changes=sorted_changes)
        memory = Memory(
            artifact_id=base.artifact_id,
            revision=base.revision + 1,
            content=content,
            lineage=ArtifactLineage(),
        )
        projections = await self._prepare_projections(
            base=base,
            manifest_entries=sorted_manifest,
            versions_by_entry=current_by_entry,
            changed_version_ids=frozenset(version.entry_version_id for version in entry_versions),
        )
        commit = MemoryCommit(
            base=base,
            memory=memory,
            content_hash=memory_content_hash(content),
            entry_versions=entry_versions,
            projections=projections,
        )
        async with self._backend.begin() as unit_of_work:
            return await unit_of_work.commit(commit)

    async def _validate_anchor(
        self,
        *,
        memory_ref: ArtifactRef,
        entry_id: str,
        entry_version_id: str,
        version: MemoryEntryVersion,
    ) -> None:
        memory = await self._backend.get(memory_ref)
        item = next(
            (candidate for candidate in memory.content.manifest.entries if candidate.entry_id == entry_id),
            None,
        )
        if (
            item is None
            or item.entry_version_id != entry_version_id
            or version.memory_artifact_id != memory_ref.artifact_id
            or version.entry_id != entry_id
            or version.entry_version_id != entry_version_id
        ):
            raise InvalidMemoryCitationError("expand-anchor")
        material = self._material_from_version(version)
        if material.content_hash != item.entry_content_hash or version.entry_content_hash != item.entry_content_hash:
            raise InvalidMemoryCitationError("hash-mismatch")

    async def _prepare_projections(
        self,
        *,
        base: Memory | None,
        manifest_entries: tuple[MemoryManifestEntry, ...],
        versions_by_entry: dict[str, MemoryEntryVersion],
        changed_version_ids: frozenset[str],
    ) -> tuple[MemoryProjection, ...]:
        """Build active-head projections, reusing unchanged vectors and embedding only changes."""

        previous = await self._previous_projections(base)
        prepared: list[MemoryProjection] = []
        embed_indices: list[int] = []
        for item in manifest_entries:
            if item.state != "active":
                continue
            version = versions_by_entry[item.entry_id]
            if version.entry_version_id != item.entry_version_id:
                raise InvalidMemoryCitationError("projection-version")
            searchable_text = analyze_text(version.text)
            reused = self._reused_projection(previous.get(version.entry_version_id), version, searchable_text)
            if reused is not None and version.entry_version_id not in changed_version_ids:
                prepared.append(reused)
                continue
            prepared.append(MemoryProjection(entry_version=version, searchable_text=searchable_text))
            embed_indices.append(len(prepared) - 1)
        return await self._attach_embeddings(tuple(prepared), embed_indices)

    async def _previous_projections(self, base: Memory | None) -> dict[str, MemoryProjection]:
        if base is None:
            return {}
        return {
            projection.entry_version.entry_version_id: projection
            for projection in await self._backend.projections(base.as_ref())
        }

    def _reused_projection(
        self,
        previous: MemoryProjection | None,
        version: MemoryEntryVersion,
        searchable_text: str,
    ) -> MemoryProjection | None:
        if previous is None:
            return None
        if (
            previous.entry_version.entry_version_id != version.entry_version_id
            or previous.entry_version.entry_content_hash != version.entry_content_hash
            or previous.searchable_text != searchable_text
        ):
            return None
        return previous.model_copy(
            update={
                "entry_version": version,
                "searchable_text": searchable_text,
            }
        )

    async def _attach_embeddings(
        self,
        projections: tuple[MemoryProjection, ...],
        embed_indices: Sequence[int],
    ) -> tuple[MemoryProjection, ...]:
        if not projections or not embed_indices or self._embedding_model is None:
            return projections
        capabilities = await self._backend.capabilities()
        profile = capabilities.embedding_profile
        if not capabilities.vector or profile is None:
            return projections
        embedding_profile = self._embedding_model.profile
        if embedding_profile != profile:
            return projections
        try:
            vectors = await self._embed_texts(
                tuple(projections[index].entry_version.text for index in embed_indices),
                profile,
            )
        except (InferenceUnavailableError, InferenceTimeoutError):
            return projections
        updated = list(projections)
        for index, vector in zip(embed_indices, vectors, strict=True):
            projection = updated[index]
            updated[index] = projection.model_copy(
                update={
                    "embedding": vector,
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
        return tuple(updated)

    async def _embed_texts(
        self,
        texts: tuple[str, ...],
        profile: EmbeddingProfile,
    ) -> tuple[EmbeddingVector, ...]:
        embedding_model = self._embedding_model
        if embedding_model is None or embedding_model.profile != profile:
            raise CapabilityNotSupportedError("embedding-profile")
        result = await embedding_model.embed(texts)
        vectors = result.vectors
        if len(vectors) != len(texts):
            raise InvalidEmbeddingError("count")
        return tuple(validate_embedding(vector, dimension=profile.dimension) for vector in vectors)

    @overload
    async def _canonical_base(self, memory: None) -> None: ...

    @overload
    async def _canonical_base(self, memory: Memory) -> Memory: ...

    async def _canonical_base(self, memory: Memory | None) -> Memory | None:
        if memory is None:
            return None
        exact = await self._backend.get(memory.as_ref())
        if exact != memory:
            raise InvalidMemoryCitationError("base-mismatch")
        latest = await self._backend.latest(memory.artifact_id)
        if latest != exact:
            raise RevisionConflictError(memory, latest)
        return exact

    async def _canonical_memory(self, memory: Memory) -> Memory:
        exact = await self._backend.get(memory.as_ref())
        if exact != memory:
            raise InvalidMemoryCitationError("memory-mismatch")
        return exact

    async def _canonical_operation_evidence(
        self,
        sources: Sequence[Source],
        artifacts: Sequence[Artifact[object]],
    ) -> _OperationEvidence:
        canonical_sources: list[Source] = []
        for source in sources:
            if self._source_resolver is None:
                raise InvalidMemoryEvidenceError("source-resolver")
            _append_unique(canonical_sources, await self._source_resolver.get(source))

        canonical_artifacts: list[Artifact[object]] = []
        for artifact in artifacts:
            if self._artifact_resolver is None:
                raise InvalidMemoryEvidenceError("artifact-resolver")
            _append_unique(canonical_artifacts, await self._artifact_resolver.get(artifact))
        return _OperationEvidence(
            sources=tuple(canonical_sources),
            artifacts=tuple(canonical_artifacts),
        )

    async def _validated_entries(self, memory: Memory) -> tuple[MemoryEntryVersion, ...]:
        versions = await self._backend.entries(memory.as_ref())
        versions_by_id = {version.entry_version_id: version for version in versions}
        if len(versions_by_id) != len(versions):
            raise InvalidMemoryCitationError("duplicate-versions")

        ordered: list[MemoryEntryVersion] = []
        for item in memory.content.manifest.entries:
            version = versions_by_id.get(item.entry_version_id)
            if version is None:
                raise InvalidMemoryCitationError("missing-version")
            if version.memory_artifact_id != memory.artifact_id or version.entry_id != item.entry_id:
                raise InvalidMemoryCitationError("cross-identity")
            material = self._material_from_version(version)
            if material.content_hash != item.entry_content_hash:
                raise InvalidMemoryCitationError("hash-mismatch")
            ordered.append(version)
        return tuple(ordered)

    async def _candidates(
        self,
        mode: Literal["append", "extract"],
        entries: tuple[MemoryEntryInput, ...],
        evidence: _OperationEvidence,
        current_entries: tuple[MemoryEntryVersion, ...],
        *,
        active_version_ids: frozenset[str],
    ) -> tuple[MemoryEntryInput, ...]:
        if mode == "append":
            return entries
        if self._candidate_pipeline is None:
            raise CapabilityNotSupportedError("extract")
        bounded = tuple(entry for entry in current_entries if entry.entry_version_id in active_version_ids)
        return await self._candidate_pipeline.extract(
            MemoryCandidateRequest(
                sources=evidence.sources,
                artifacts=evidence.artifacts,
                current_entries=bounded,
            )
        )

    async def _prepare_commit(
        self,
        *,
        base: Memory | None,
        candidates: tuple[MemoryEntryInput, ...],
        evidence: _OperationEvidence,
        current_entries: tuple[MemoryEntryVersion, ...],
    ) -> MemoryCommit | None:
        memory_id = base.artifact_id if base is not None else self._new_id("memory")
        next_revision = 1 if base is None else base.revision + 1
        manifest = {} if base is None else {entry.entry_id: entry for entry in base.content.manifest.entries}
        current_by_entry = {entry.entry_id: entry for entry in current_entries}
        new_versions: list[MemoryEntryVersion] = []
        changes: list[MemoryChange] = []
        targeted: set[str] = set()
        new_content: set[bytes] = {
            self._material_from_version(version).content_bytes
            for version in current_entries
            if manifest[version.entry_id].state == "active"
        }

        for candidate in candidates:
            if candidate.entry is None:
                material = await self._material_from_candidate(candidate, evidence.sources, evidence.artifacts)
                if material.content_bytes in new_content:
                    continue
                new_content.add(material.content_bytes)
                entry_id = self._new_id("entry")
                if entry_id in manifest:
                    raise _InvalidMemoryOperationError("id-collision")
                version = self._new_entry_version(
                    memory_id=memory_id,
                    entry_id=entry_id,
                    previous=None,
                    material=material,
                    created_in_revision=next_revision,
                )
                manifest[entry_id] = _manifest_entry(version, state="active")
                current_by_entry[entry_id] = version
                new_versions.append(version)
                changes.append(
                    MemoryChange(
                        op="add",
                        entry_id=entry_id,
                        from_entry_version_id=None,
                        to_entry_version_id=version.entry_version_id,
                        reason=normalize_reason(candidate.reason),
                    )
                )
                continue

            entry_id, previous = self._claim_revision_target(candidate, current_by_entry, targeted)
            item = manifest.get(entry_id)
            if item is None:
                raise MemoryEntryNotFoundError(entry_id)
            if item.state == "inactive":
                raise MemoryEntryInactiveError(entry_id)
            material = await self._material_from_candidate(
                candidate,
                evidence.sources,
                evidence.artifacts,
                previous=previous,
            )
            previous_material = self._material_from_version(previous)
            if (
                material.content_hash == previous_material.content_hash
                and material.content_bytes == previous_material.content_bytes
            ):
                continue
            version = self._new_entry_version(
                memory_id=memory_id,
                entry_id=entry_id,
                previous=previous,
                material=material,
                created_in_revision=next_revision,
            )
            manifest[entry_id] = _manifest_entry(version, state="active")
            current_by_entry[entry_id] = version
            new_versions.append(version)
            changes.append(
                MemoryChange(
                    op="revise",
                    entry_id=entry_id,
                    from_entry_version_id=previous.entry_version_id,
                    to_entry_version_id=version.entry_version_id,
                    reason=normalize_reason(candidate.reason),
                )
            )

        if not changes:
            return None

        sorted_manifest = tuple(sorted(manifest.values(), key=lambda item: item.entry_id.encode("utf-8")))
        sorted_changes = tuple(sorted(changes, key=lambda change: change.entry_id.encode("utf-8")))
        content = MemoryContent(manifest=MemoryManifest(entries=sorted_manifest), changes=sorted_changes)
        memory = Memory(
            artifact_id=memory_id,
            revision=next_revision,
            content=content,
            lineage=ArtifactLineage(
                sources=self._source_refs(evidence.sources),
                artifacts=tuple(artifact.as_ref() for artifact in evidence.artifacts),
            ),
        )
        projections = await self._prepare_projections(
            base=base,
            manifest_entries=sorted_manifest,
            versions_by_entry=current_by_entry,
            changed_version_ids=frozenset(version.entry_version_id for version in new_versions),
        )
        return MemoryCommit(
            base=base,
            memory=memory,
            content_hash=memory_content_hash(content),
            entry_versions=tuple(new_versions),
            projections=projections,
        )

    @staticmethod
    def _claim_revision_target(
        candidate: MemoryEntryInput,
        current_by_entry: dict[str, MemoryEntryVersion],
        targeted: set[str],
    ) -> tuple[str, MemoryEntryVersion]:
        entry = candidate.entry
        if entry is None:
            raise InvalidMemoryCitationError("entry-missing")
        entry_id = validate_identifier(entry.entry_id)
        if entry_id in targeted:
            raise _InvalidMemoryOperationError("duplicate-target")
        targeted.add(entry_id)
        previous = current_by_entry.get(entry_id)
        if previous is None:
            raise MemoryEntryNotFoundError(entry_id)
        if entry != previous:
            raise InvalidMemoryCitationError("entry-mismatch")
        return entry_id, previous

    async def _material_from_candidate(
        self,
        candidate: MemoryEntryInput,
        allowed_sources: Sequence[Source],
        allowed_artifacts: Sequence[Artifact[object]],
        *,
        previous: MemoryEntryVersion | None = None,
    ) -> _EntryMaterial:
        previous_artifacts = () if previous is None else previous.artifacts
        candidate_sources = await self._canonical_candidate_sources(candidate.sources, allowed_sources)
        candidate_source_refs = self._source_refs(candidate_sources)
        candidate_artifacts = await self._canonical_candidate_artifacts(
            candidate.artifacts,
            allowed_artifacts,
            previous_artifacts,
        )
        if previous is None:
            sources = candidate_source_refs
            artifacts = candidate_artifacts
        else:
            # Revises retain predecessor evidence and add current candidate evidence.
            sources = (*previous.sources, *candidate_source_refs)
            artifacts = (*previous.artifacts, *candidate_artifacts)
        try:
            return self._entry_material(
                kind=candidate.kind,
                text=candidate.text,
                sources=sources,
                artifacts=artifacts,
            )
        except (TypeError, ValueError) as error:
            raise InvalidMemoryCandidateError("canonical", str(error)) from error

    async def _canonical_candidate_sources(
        self,
        values: Sequence[Source],
        allowed: Sequence[Source],
    ) -> tuple[Source, ...]:
        result: list[Source] = []
        for value in values:
            canonical = value if self._source_resolver is None else await self._source_resolver.get(value)
            match = _equal_member(canonical, allowed)
            if match is None:
                raise InvalidMemoryEvidenceError("source-outside")
            _append_unique(result, match)
        return tuple(result)

    async def _canonical_candidate_artifacts(
        self,
        values: Sequence[Artifact[object]],
        allowed: Sequence[Artifact[object]],
        previous: Sequence[ArtifactRef],
    ) -> tuple[ArtifactRef, ...]:
        result: list[ArtifactRef] = []
        allowed_refs = tuple(artifact.as_ref() for artifact in allowed)
        for value in values:
            canonical = value if self._artifact_resolver is None else await self._artifact_resolver.get(value)
            reference = canonical.as_ref()
            if reference not in (*allowed_refs, *previous):
                raise InvalidMemoryEvidenceError("artifact-outside")
            if reference not in result:
                result.append(reference)
        return tuple(result)

    def _material_from_version(self, version: MemoryEntryVersion) -> _EntryMaterial:
        return self._entry_material(
            kind=version.kind,
            text=version.text,
            sources=version.sources,
            artifacts=version.artifacts,
        )

    def _entry_material(
        self,
        *,
        kind: str,
        text: str,
        sources: Sequence[SourceRef],
        artifacts: Sequence[ArtifactRef],
    ) -> _EntryMaterial:
        normalized_kind = normalize_kind(kind)
        normalized_text = normalize_text(text)
        canonical_sources = _canonical_source_refs(sources)
        canonical_artifacts = _canonical_artifact_refs(artifacts)
        source_refs = tuple(source.model_dump(mode="json") for source in canonical_sources)
        artifact_refs = tuple(artifact.model_dump(mode="json") for artifact in canonical_artifacts)
        content_bytes = entry_content_bytes(
            kind=normalized_kind,
            text=normalized_text,
            source_refs=source_refs,
            artifact_refs=artifact_refs,
        )
        return _EntryMaterial(
            kind=normalized_kind,
            text=normalized_text,
            sources=canonical_sources,
            artifacts=canonical_artifacts,
            content_bytes=content_bytes,
            content_hash=entry_content_hash(
                kind=normalized_kind,
                text=normalized_text,
                source_refs=source_refs,
                artifact_refs=artifact_refs,
            ),
        )

    def _source_refs(self, sources: Sequence[Source]) -> tuple[SourceRef, ...]:
        if not sources:
            return ()
        if self._source_resolver is None:
            raise InvalidMemoryEvidenceError("source-adapter")
        keyed: dict[bytes, SourceRef] = {}
        for source in sources:
            try:
                reference = self._source_resolver.as_ref(source)
            except (KeyError, LookupError, TypeError, ValueError):
                raise InvalidMemoryEvidenceError("source-adapter") from None
            keyed.setdefault(canonical_json(reference.model_dump(mode="json")), reference)
        return tuple(keyed[key] for key in sorted(keyed))

    def _new_entry_version(
        self,
        *,
        memory_id: str,
        entry_id: str,
        previous: MemoryEntryVersion | None,
        material: _EntryMaterial,
        created_in_revision: int,
    ) -> MemoryEntryVersion:
        return MemoryEntryVersion(
            memory_artifact_id=memory_id,
            entry_id=entry_id,
            entry_version_id=self._new_id("version"),
            version=1 if previous is None else previous.version + 1,
            previous_version_id=None if previous is None else previous.entry_version_id,
            kind=material.kind,
            text=material.text,
            sources=material.sources,
            artifacts=material.artifacts,
            entry_content_hash=material.content_hash,
            created_in_revision=created_in_revision,
        )

    def _new_id(self, kind: str) -> str:
        return validate_identifier(self._id_factory(kind))


def _select_remember_mode(
    mode: MemoryRememberMode,
    *,
    has_entries: bool,
    has_evidence: bool,
) -> Literal["append", "extract"]:
    if mode == "append":
        if not has_entries:
            raise _InvalidMemoryOperationError("append-entries")
        return "append"
    if mode == "extract":
        if has_entries or not has_evidence:
            raise _InvalidMemoryOperationError("extract-evidence")
        return "extract"
    if mode != "auto":
        raise InvalidMemoryCandidateError("remember-mode", mode)
    if has_entries:
        return "append"
    if has_evidence:
        return "extract"
    raise _InvalidMemoryOperationError("no-work")


def _manifest_entry(version: MemoryEntryVersion, *, state: Literal["active", "inactive"]) -> MemoryManifestEntry:
    return MemoryManifestEntry(
        entry_id=version.entry_id,
        entry_version_id=version.entry_version_id,
        entry_content_hash=version.entry_content_hash,
        state=state,
    )


def _canonical_source_refs(values: Sequence[SourceRef]) -> tuple[SourceRef, ...]:
    keyed = {canonical_json(value.model_dump(mode="json")): value for value in values}
    return tuple(keyed[key] for key in sorted(keyed))


def _canonical_artifact_refs(values: Sequence[ArtifactRef]) -> tuple[ArtifactRef, ...]:
    keyed = {canonical_json(value.model_dump(mode="json")): value for value in values}
    return tuple(keyed[key] for key in sorted(keyed))


def _is_canonical_version(version: MemoryEntryVersion, material: _EntryMaterial) -> bool:
    return (
        version.kind == material.kind
        and version.text == material.text
        and version.sources == material.sources
        and version.artifacts == material.artifacts
    )


def _append_unique(values: list[ValueT], value: ValueT) -> None:
    if not any(current == value for current in values):
        values.append(value)


def _equal_member(value: ValueT, values: Sequence[ValueT]) -> ValueT | None:
    return next((current for current in values if current == value), None)


def _default_id(kind: str) -> str:
    prefixes = {"memory": "mem_art", "entry": "mem_ent", "version": "mem_ver"}
    try:
        prefix = prefixes[kind]
    except KeyError:
        raise InvalidMemoryCandidateError("identity-kind", kind) from None
    return f"{prefix}_{uuid4().hex}"
