from __future__ import annotations

import asyncio
import math
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace

import pytest

from powercontext import ArtifactNotFoundError, ArtifactRef, RevisionConflictError
from powercontext.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    EmbeddingProviderUnavailableError,
    EmbeddingVector,
    Memory,
    MemoryBackend,
    MemoryCapabilities,
    MemoryChange,
    MemoryChannelHit,
    MemoryCitation,
    MemoryCommit,
    MemoryContent,
    MemoryEntryInput,
    MemoryEntryVersion,
    MemoryEvidenceCodec,
    MemoryHit,
    MemoryManifest,
    MemoryManifestEntry,
    MemoryProjection,
    MemoryRevisionChanges,
    MemorySearchChannels,
    MemorySearchRequest,
    MemoryService,
    MemoryUnitOfWork,
)
from powercontext.memory.canonical import entry_content_hash
from powercontext.sources import Source, SourceMaterialization


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteSource(Source):
    body: str = ""


class MultiSourceResolver:
    def __init__(self, sources: tuple[Source, ...]) -> None:
        self._sources = {source.name: source for source in sources}

    async def get(self, source: Source, /) -> Source:
        return self._sources[source.name]


class MultiSourceCodec(MemoryEvidenceCodec):
    def __init__(self, sources: tuple[Source, ...]) -> None:
        self._sources = {source.name: source for source in sources}

    def encode_source(self, source: Source, /) -> object:
        return {"name": source.name}

    def decode_source(self, value: object, /) -> Source:
        assert isinstance(value, dict)
        name = value.get("name")
        assert isinstance(name, str)
        return self._sources[name]

    def encode_artifact(self, artifact: ArtifactRef, /) -> object:
        return {"artifact_id": artifact.artifact_id, "revision": artifact.revision}

    def decode_artifact(self, value: object, /) -> ArtifactRef:
        assert isinstance(value, dict)
        artifact_id = value.get("artifact_id")
        revision = value.get("revision")
        assert isinstance(artifact_id, str)
        assert isinstance(revision, int) and not isinstance(revision, bool)
        return ArtifactRef(artifact_id, revision)


class SequentialIds:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def __call__(self, kind: str) -> str:
        count = self._counts.get(kind, 0) + 1
        self._counts[kind] = count
        return f"{kind}-{count}"


class RecordingMemoryBackend(MemoryBackend, MemoryUnitOfWork, AbstractAsyncContextManager[MemoryUnitOfWork]):
    def __init__(
        self,
        *,
        capabilities: MemoryCapabilities | None = None,
        vector_complete: bool = False,
    ) -> None:
        self.commits: list[MemoryCommit] = []
        self.rollbacks = 0
        self.requests: list[MemorySearchRequest] = []
        self.channels = MemorySearchChannels()
        self._capabilities = MemoryCapabilities(fts=True) if capabilities is None else capabilities
        self._vector_complete = vector_complete
        self._revisions: dict[ArtifactRef, Memory] = {}
        self._heads: dict[str, Memory] = {}
        self._versions: dict[str, MemoryEntryVersion] = {}
        self._projections: dict[ArtifactRef, tuple[MemoryProjection, ...]] = {}

    @property
    def entry_version_count(self) -> int:
        return len(self._versions)

    def seed(self, memory: Memory, entries: tuple[MemoryEntryVersion, ...]) -> None:
        self._revisions[memory.ref] = memory
        self._heads[memory.artifact_id] = memory
        self._versions.update((entry.entry_version_id, entry) for entry in entries)

    async def __aenter__(self) -> MemoryUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        if exc_type is not None:
            await self.rollback()

    async def capabilities(self) -> MemoryCapabilities:
        return self._capabilities

    async def get(self, memory: ArtifactRef, /) -> Memory:
        try:
            return self._revisions[memory]
        except KeyError:
            raise ArtifactNotFoundError(memory) from None

    async def latest(self, artifact_id: str, /) -> Memory:
        try:
            return self._heads[artifact_id]
        except KeyError:
            raise ArtifactNotFoundError(artifact_id) from None

    async def entries(self, memory: ArtifactRef, /) -> tuple[MemoryEntryVersion, ...]:
        revision = await self.get(memory)
        return tuple(self._versions[item.entry_version_id] for item in revision.content.manifest.entries)

    async def projections(self, memory: ArtifactRef, /) -> tuple[MemoryProjection, ...]:
        return self._projections.get(memory, ())

    def begin(self) -> AbstractAsyncContextManager[MemoryUnitOfWork]:
        return self

    async def commit(self, value: MemoryCommit, /) -> Memory:
        current = self._heads.get(value.memory.artifact_id)
        if value.base is None:
            if current is not None:
                raise RevisionConflictError(value.memory, current)
        elif current != value.base:
            raise RevisionConflictError(value.base, current)

        self.commits.append(value)
        self._versions.update((entry.entry_version_id, entry) for entry in value.entry_versions)
        self._revisions[value.memory.ref] = value.memory
        self._heads[value.memory.artifact_id] = value.memory
        self._projections[value.memory.ref] = value.projections
        return value.memory

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def changes(
        self,
        memory: ArtifactRef,
        since_revision: int | None,
        /,
    ) -> tuple[MemoryRevisionChanges, ...]:
        target = await self.get(memory)
        lower = target.revision - 1 if since_revision is None else since_revision
        return tuple(
            MemoryRevisionChanges(memory_ref=revision.ref, changes=revision.content.changes)
            for revision_number in range(lower + 1, target.revision + 1)
            if (revision := self._revisions.get(ArtifactRef(target.artifact_id, revision_number))) is not None
        )

    async def vector_complete(
        self,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        return self._vector_complete

    async def search(self, request: MemorySearchRequest, /) -> MemorySearchChannels:
        self.requests.append(request)
        return self.channels

    async def expand(self, hits: tuple[MemoryHit, ...], /) -> tuple[MemoryEntryVersion, ...]:
        return tuple(self._versions[hit.entry_version_id] for hit in hits)


class RecordingEmbeddingProvider:
    def __init__(
        self,
        *,
        profile: EmbeddingProfile,
        responses: tuple[tuple[EmbeddingVector, ...] | EmbeddingProviderUnavailableError, ...] = (),
    ) -> None:
        self._profile = profile
        self._responses = list(responses)
        self.calls: list[tuple[str, ...]] = []

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    async def embed(self, texts: tuple[str, ...], /) -> tuple[EmbeddingVector, ...]:
        self.calls.append(texts)
        if self._responses:
            response = self._responses.pop(0)
            if isinstance(response, EmbeddingProviderUnavailableError):
                raise response
            return response
        return tuple((1.0, 0.0, 0.0) for _ in texts)


TEST_PROFILE = EmbeddingProfile(
    profile_id="keyword-v1",
    model="keyword",
    dimension=3,
    distance="l2",
    normalization="none",
)


async def searchable_memory(
    *,
    vector_complete: bool,
    provider: RecordingEmbeddingProvider | None = None,
) -> tuple[RecordingMemoryBackend, MemoryService, Memory, RecordingEmbeddingProvider]:
    configured_provider = RecordingEmbeddingProvider(profile=TEST_PROFILE) if provider is None else provider
    backend = RecordingMemoryBackend(
        capabilities=MemoryCapabilities(
            fts=True,
            vector=True,
            hybrid=True,
            embedding_profile=TEST_PROFILE,
        ),
        vector_complete=vector_complete,
    )
    service = MemoryService(
        backend=backend,
        embedding_provider=configured_provider,
        id_factory=SequentialIds(),
    )
    memory = await service.remember(
        memory=None,
        entries=(MemoryEntryInput(kind="constraint", text="修改文档前运行 make docs-test。"),),
        mode="append",
    )
    assert memory is not None
    return backend, service, memory, configured_provider


async def memory_with_one_entry() -> tuple[RecordingMemoryBackend, MemoryService, Memory]:
    backend = RecordingMemoryBackend()
    service = MemoryService(backend=backend, id_factory=SequentialIds())
    memory = await service.remember(
        memory=None,
        entries=(MemoryEntryInput(kind="decision", text="Use direct SQL adapters."),),
        mode="append",
    )
    assert memory is not None
    return backend, service, memory


async def current_entry(
    service: MemoryService,
    memory: Memory,
    entry_id: str | None = None,
) -> MemoryEntryVersion:
    entries = await service.entries(memory)
    if entry_id is None:
        return entries[0]
    return next(entry for entry in entries if entry.entry_id == entry_id)


def test_append_creates_revision_one_only_for_a_real_entry() -> None:
    async def scenario() -> None:
        backend = RecordingMemoryBackend()
        service = MemoryService(backend=backend, id_factory=SequentialIds())
        memory = await service.remember(
            memory=None,
            entries=(MemoryEntryInput(kind="decision", text="Use direct SQL adapters."),),
            mode="append",
        )

        assert memory is not None
        assert memory.revision == 1
        assert memory.content.manifest.entries[0].entry_id == "entry-1"
        assert memory.content.changes[0].op == "add"
        assert backend.commits[0].base is None
        assert backend.commits[0].memory == memory
        assert backend.entry_version_count == 1

    asyncio.run(scenario())


def test_revision_uses_base_current_version_as_direct_predecessor() -> None:
    async def scenario() -> None:
        backend, service, first = await memory_with_one_entry()
        entry_id = first.content.manifest.entries[0].entry_id
        second = await service.remember(
            memory=first,
            entries=(
                MemoryEntryInput(
                    entry=await current_entry(service, first, entry_id),
                    kind="decision",
                    text="Use direct SQL adapters and JCS.",
                ),
            ),
            mode="append",
        )

        assert second is not None
        version = backend.commits[-1].entry_versions[0]
        assert second.artifact_id == first.artifact_id
        assert second.revision == 2
        assert version.version == 2
        assert version.previous_version_id == first.content.manifest.entries[0].entry_version_id
        assert second.content.changes[0].op == "revise"

    asyncio.run(scenario())


def test_revision_merges_predecessor_and_candidate_evidence() -> None:
    async def scenario() -> None:
        agents = NoteSource(name="src_agents_md", materialization=SourceMaterialization.CAPTURED, body="agents")
        makefile = NoteSource(name="src_makefile", materialization=SourceMaterialization.CAPTURED, body="make")
        user_note = NoteSource(name="src_user_note", materialization=SourceMaterialization.CAPTURED, body="prefer docs")
        sources = (agents, makefile, user_note)
        codec = MultiSourceCodec(sources)
        resolver = MultiSourceResolver(sources)
        backend = RecordingMemoryBackend()
        service = MemoryService(
            backend=backend,
            evidence_codec=codec,
            source_resolver=resolver,
            id_factory=SequentialIds(),
        )

        first = await service.remember(
            memory=None,
            sources=(agents, makefile),
            entries=(
                MemoryEntryInput(
                    kind="preference",
                    text="Run make test for routine changes; run make check before review.",
                    sources=(agents, makefile),
                ),
            ),
            mode="append",
        )
        assert first is not None
        entry_id = first.content.manifest.entries[0].entry_id
        previous = backend.commits[-1].entry_versions[0]
        assert previous.sources == (agents, makefile)

        second = await service.remember(
            memory=first,
            sources=(user_note,),
            entries=(
                MemoryEntryInput(
                    entry=await current_entry(service, first, entry_id),
                    kind="preference",
                    text=(
                        "Run make test for routine changes; prefer make docs-test for "
                        "documentation-only changes; run make check before review."
                    ),
                    sources=(user_note,),
                ),
            ),
            mode="append",
        )
        assert second is not None
        revised = backend.commits[-1].entry_versions[0]
        assert revised.version == 2
        assert revised.sources == (agents, makefile, user_note)

        text_only = await service.remember(
            memory=second,
            sources=(user_note,),
            entries=(
                MemoryEntryInput(
                    entry=await current_entry(service, second, entry_id),
                    kind="preference",
                    text="Prefer make docs-test for documentation-only changes.",
                ),
            ),
            mode="append",
        )
        assert text_only is not None
        assert backend.commits[-1].entry_versions[0].sources == (agents, makefile, user_note)

    asyncio.run(scenario())


def test_identical_revision_is_an_exact_noop() -> None:
    async def scenario() -> None:
        backend, service, first = await memory_with_one_entry()
        entry_id = first.content.manifest.entries[0].entry_id
        unchanged = await service.remember(
            memory=first,
            entries=(
                MemoryEntryInput(
                    entry=await current_entry(service, first, entry_id),
                    kind="decision",
                    text="  Use direct SQL adapters.  ",
                    reason="reason does not turn an identical body into a change",
                ),
            ),
            mode="append",
        )

        assert unchanged is first
        assert len(backend.commits) == 1
        assert backend.entry_version_count == 1

    asyncio.run(scenario())


def test_identical_new_entries_keep_the_first_candidate() -> None:
    async def scenario() -> None:
        backend = RecordingMemoryBackend()
        service = MemoryService(backend=backend, id_factory=SequentialIds())
        memory = await service.remember(
            memory=None,
            entries=(
                MemoryEntryInput(kind="fact", text="PowerContext uses uv."),
                MemoryEntryInput(kind="fact", text="  PowerContext uses uv.  "),
            ),
            mode="append",
        )

        assert memory is not None
        assert len(memory.content.manifest.entries) == 1
        assert len(memory.content.changes) == 1
        assert backend.entry_version_count == 1

    asyncio.run(scenario())


def test_same_existing_entry_cannot_be_targeted_twice() -> None:
    async def scenario() -> None:
        _, service, first = await memory_with_one_entry()
        with pytest.raises(ValueError, match="once"):
            await service.remember(
                memory=first,
                entries=(
                    MemoryEntryInput(
                        entry=await current_entry(service, first), kind="decision", text="First revision."
                    ),
                    MemoryEntryInput(
                        entry=await current_entry(service, first), kind="decision", text="Second revision."
                    ),
                ),
                mode="append",
            )

    asyncio.run(scenario())


def test_missing_existing_entry_is_rejected() -> None:
    async def scenario() -> None:
        _, service, first = await memory_with_one_entry()
        existing = await current_entry(service, first)
        with pytest.raises(LookupError, match="missing"):
            await service.remember(
                memory=first,
                entries=(
                    MemoryEntryInput(
                        entry=replace(existing, entry_id="missing"), kind="fact", text="Detached revision."
                    ),
                ),
                mode="append",
            )

    asyncio.run(scenario())


def test_stale_memory_is_rejected_before_a_new_commit() -> None:
    async def scenario() -> None:
        backend, service, first = await memory_with_one_entry()
        second = await service.remember(
            memory=first,
            entries=(
                MemoryEntryInput(entry=await current_entry(service, first), kind="decision", text="Current revision."),
            ),
            mode="append",
        )
        assert second is not None

        with pytest.raises(RevisionConflictError):
            await service.remember(
                memory=first,
                entries=(
                    MemoryEntryInput(
                        entry=await current_entry(service, first), kind="decision", text="Stale revision."
                    ),
                ),
                mode="append",
            )
        assert len(backend.commits) == 2

    asyncio.run(scenario())


def test_append_requires_entries_and_no_work_does_not_allocate_memory() -> None:
    async def scenario() -> None:
        backend = RecordingMemoryBackend()
        service = MemoryService(backend=backend, id_factory=SequentialIds())
        with pytest.raises(ValueError, match="entry"):
            await service.remember(memory=None, entries=(), mode="append")
        with pytest.raises(ValueError, match="work"):
            await service.remember(memory=None, entries=(), mode="auto")
        assert backend.commits == []

    asyncio.run(scenario())


def test_forget_and_reactivate_change_only_manifest_state() -> None:
    async def scenario() -> None:
        backend, service, first = await memory_with_one_entry()
        head = first.content.manifest.entries[0]
        entry = await current_entry(service, first)
        forgotten = await service.forget(first, entries=(entry,), reason="user_requested")
        inactive_entry = await current_entry(service, forgotten)
        assert await service.forget(forgotten, entries=(inactive_entry,)) is forgotten
        restored = await service.reactivate(forgotten, entries=(inactive_entry,), reason="user_restored")

        assert forgotten.content.manifest.entries[0].state == "inactive"
        assert forgotten.content.changes == (
            MemoryChange("deactivate", head.entry_id, head.entry_version_id, None, "user_requested"),
        )
        assert restored.content.manifest.entries[0].state == "active"
        assert restored.content.manifest.entries[0].entry_version_id == head.entry_version_id
        assert restored.content.changes == (
            MemoryChange("reactivate", head.entry_id, None, head.entry_version_id, "user_restored"),
        )
        assert backend.entry_version_count == 1
        assert await service.reactivate(restored, entries=(await current_entry(service, restored),)) is restored

    asyncio.run(scenario())


def test_lifecycle_rejects_missing_ids_and_long_reasons() -> None:
    async def scenario() -> None:
        _, service, first = await memory_with_one_entry()
        entry = await current_entry(service, first)
        with pytest.raises(LookupError, match="missing"):
            await service.forget(first, entries=(replace(entry, entry_id="missing"),))
        with pytest.raises(ValueError, match="512 Unicode code points"):
            await service.forget(first, entries=(entry,), reason="x" * 513)

    asyncio.run(scenario())


def test_organize_deduplicates_active_entries_by_utf8_identity() -> None:
    async def scenario() -> None:
        backend = RecordingMemoryBackend()
        service = MemoryService(backend=backend, id_factory=SequentialIds())
        content_hash = entry_content_hash(kind="fact", text="Duplicate.", source_refs=(), artifact_refs=())
        first_version = MemoryEntryVersion(
            memory_artifact_id="memory-seeded",
            entry_id="entry-a",
            entry_version_id="version-a1",
            version=1,
            previous_version_id=None,
            kind="fact",
            text="Duplicate.",
            entry_content_hash=content_hash,
            created_in_revision=1,
        )
        second_version = MemoryEntryVersion(
            memory_artifact_id="memory-seeded",
            entry_id="entry-b",
            entry_version_id="version-b1",
            version=1,
            previous_version_id=None,
            kind="fact",
            text="Duplicate.",
            entry_content_hash=content_hash,
            created_in_revision=1,
        )
        memory = Memory(
            artifact_id="memory-seeded",
            revision=1,
            content=MemoryContent(
                manifest=MemoryManifest(
                    entries=(
                        MemoryManifestEntry("entry-a", "version-a1", content_hash, "active"),
                        MemoryManifestEntry("entry-b", "version-b1", content_hash, "active"),
                    )
                )
            ),
        )
        backend.seed(memory, (first_version, second_version))

        organized = await service.organize(memory, mode="dedupe")

        assert tuple(entry.state for entry in organized.content.manifest.entries) == ("active", "inactive")
        assert organized.content.changes == (MemoryChange("deactivate", "entry-b", "version-b1", None, "dedupe"),)
        assert backend.entry_version_count == 2

    asyncio.run(scenario())


def test_organize_normalizes_stored_content_with_a_new_version() -> None:
    async def scenario() -> None:
        backend = RecordingMemoryBackend()
        service = MemoryService(backend=backend, id_factory=SequentialIds())
        content_hash = entry_content_hash(kind="fact", text="Durable.", source_refs=(), artifact_refs=())
        version = MemoryEntryVersion(
            memory_artifact_id="memory-seeded",
            entry_id="entry-a",
            entry_version_id="version-a1",
            version=1,
            previous_version_id=None,
            kind=" fact ",
            text="  Durable.  ",
            entry_content_hash=content_hash,
            created_in_revision=1,
        )
        memory = Memory(
            artifact_id="memory-seeded",
            revision=1,
            content=MemoryContent(
                manifest=MemoryManifest(entries=(MemoryManifestEntry("entry-a", "version-a1", content_hash, "active"),))
            ),
        )
        backend.seed(memory, (version,))

        organized = await service.organize(memory, mode="normalize")

        normalized = backend.commits[-1].entry_versions[0]
        assert normalized.kind == "fact"
        assert normalized.text == "Durable."
        assert normalized.version == 2
        assert normalized.previous_version_id == "version-a1"
        assert organized.content.changes == (
            MemoryChange("revise", "entry-a", "version-a1", normalized.entry_version_id, "normalize"),
        )

    asyncio.run(scenario())


def test_changes_are_revision_ordered_and_since_is_exclusive() -> None:
    async def scenario() -> None:
        _, service, first = await memory_with_one_entry()
        second = await service.remember(
            memory=first,
            entries=(
                MemoryEntryInput(entry=await current_entry(service, first), kind="decision", text="Revision two."),
            ),
            mode="append",
        )
        assert second is not None
        third = await service.forget(second, entries=(await current_entry(service, second),), reason="obsolete")

        deltas = await service.changes(third, since_revision=1)
        target_only = await service.changes(third)

        assert tuple(delta.memory_ref.revision for delta in deltas) == (2, 3)
        assert target_only == (MemoryRevisionChanges(third.ref, third.content.changes),)
        with pytest.raises(ValueError, match="greater"):
            await service.changes(third, since_revision=4)

    asyncio.run(scenario())


def test_expand_and_citation_validate_exact_historical_content() -> None:
    async def scenario() -> None:
        _, service, first = await memory_with_one_entry()
        head = first.content.manifest.entries[0]
        second = await service.remember(
            memory=first,
            entries=(
                MemoryEntryInput(entry=await current_entry(service, first), kind="decision", text="A newer decision."),
            ),
            mode="append",
        )
        assert second is not None
        hit = MemoryHit(
            memory_ref=first.ref,
            entry_id=head.entry_id,
            entry_version_id=head.entry_version_id,
            text="Use direct SQL adapters.",
            score=1 / 61,
            matched_by=("fts",),
        )

        expanded = await service.expand((hit,))
        cited = await service.validate_citation(MemoryCitation(first.ref, head.entry_id, head.entry_version_id))

        assert expanded == (cited,)
        assert cited.text == "Use direct SQL adapters."

        invalid = MemoryHit(
            memory_ref=first.ref,
            entry_id="other-entry",
            entry_version_id=head.entry_version_id,
            text=hit.text,
            score=hit.score,
            matched_by=hit.matched_by,
        )
        with pytest.raises(ValueError, match="citation"):
            await service.expand((invalid,))

    asyncio.run(scenario())


def test_write_embeddings_are_profile_and_content_bound() -> None:
    async def scenario() -> None:
        backend, _, _, provider = await searchable_memory(vector_complete=True)
        projection = backend.commits[0].projections[0]

        assert provider.calls == [("修改文档前运行 make docs-test。",)]
        assert projection.embedding == (1.0, 0.0, 0.0)
        assert projection.embedding_content_hash is not None
        assert len(projection.embedding_content_hash) == 64

    asyncio.run(scenario())


def test_transient_embedding_outage_does_not_block_authoritative_write() -> None:
    async def scenario() -> None:
        backend = RecordingMemoryBackend(
            capabilities=MemoryCapabilities(
                fts=True,
                vector=True,
                hybrid=True,
                embedding_profile=TEST_PROFILE,
            )
        )
        provider = RecordingEmbeddingProvider(
            profile=TEST_PROFILE,
            responses=(EmbeddingProviderUnavailableError("provider timeout"),),
        )
        service = MemoryService(backend=backend, embedding_provider=provider, id_factory=SequentialIds())

        memory = await service.remember(
            memory=None,
            entries=(MemoryEntryInput(kind="fact", text="Authoritative content survives embedding outages."),),
            mode="append",
        )

        assert memory is not None
        assert backend.commits[0].projections[0].embedding is None
        assert backend.commits[0].projections[0].embedding_content_hash is None

    asyncio.run(scenario())


def test_forget_reuses_unchanged_vectors_without_reembedding() -> None:
    async def scenario() -> None:
        backend = RecordingMemoryBackend(
            capabilities=MemoryCapabilities(
                fts=True,
                vector=True,
                hybrid=True,
                embedding_profile=TEST_PROFILE,
            ),
            vector_complete=True,
        )
        provider = RecordingEmbeddingProvider(profile=TEST_PROFILE)
        service = MemoryService(backend=backend, embedding_provider=provider, id_factory=SequentialIds())
        memory = await service.remember(
            memory=None,
            entries=(
                MemoryEntryInput(kind="fact", text="Alpha entry."),
                MemoryEntryInput(kind="fact", text="Beta entry."),
            ),
            mode="append",
        )
        assert memory is not None
        assert len(provider.calls) == 1
        assert provider.calls[0] == ("Alpha entry.", "Beta entry.")
        alpha_id = memory.content.manifest.entries[0].entry_id
        beta_projection = next(
            projection for projection in backend.commits[0].projections if projection.entry_version.entry_id != alpha_id
        )

        forgotten = await service.forget(memory, entries=(await current_entry(service, memory, alpha_id),))

        assert forgotten.revision == 2
        assert len(provider.calls) == 1
        remaining = backend.commits[-1].projections
        assert len(remaining) == 1
        assert remaining[0].entry_version.entry_id == beta_projection.entry_version.entry_id
        assert remaining[0].embedding == beta_projection.embedding
        assert remaining[0].embedding_content_hash == beta_projection.embedding_content_hash

    asyncio.run(scenario())


def test_forget_during_embedding_outage_preserves_sibling_vectors() -> None:
    async def scenario() -> None:
        backend = RecordingMemoryBackend(
            capabilities=MemoryCapabilities(
                fts=True,
                vector=True,
                hybrid=True,
                embedding_profile=TEST_PROFILE,
            ),
            vector_complete=True,
        )
        provider = RecordingEmbeddingProvider(profile=TEST_PROFILE)
        service = MemoryService(backend=backend, embedding_provider=provider, id_factory=SequentialIds())
        memory = await service.remember(
            memory=None,
            entries=(
                MemoryEntryInput(kind="fact", text="Alpha entry."),
                MemoryEntryInput(kind="fact", text="Beta entry."),
            ),
            mode="append",
        )
        assert memory is not None
        alpha_id = memory.content.manifest.entries[0].entry_id
        beta_projection = next(
            projection for projection in backend.commits[0].projections if projection.entry_version.entry_id != alpha_id
        )
        provider._responses.append(EmbeddingProviderUnavailableError("provider timeout"))

        forgotten = await service.forget(memory, entries=(await current_entry(service, memory, alpha_id),))

        assert forgotten is not None
        remaining = backend.commits[-1].projections
        assert len(remaining) == 1
        assert remaining[0].embedding == beta_projection.embedding
        assert remaining[0].embedding_content_hash == beta_projection.embedding_content_hash

    asyncio.run(scenario())


def test_reactivate_only_embeds_restored_entry() -> None:
    async def scenario() -> None:
        backend = RecordingMemoryBackend(
            capabilities=MemoryCapabilities(
                fts=True,
                vector=True,
                hybrid=True,
                embedding_profile=TEST_PROFILE,
            ),
            vector_complete=True,
        )
        provider = RecordingEmbeddingProvider(profile=TEST_PROFILE)
        service = MemoryService(backend=backend, embedding_provider=provider, id_factory=SequentialIds())
        memory = await service.remember(
            memory=None,
            entries=(
                MemoryEntryInput(kind="fact", text="Alpha entry."),
                MemoryEntryInput(kind="fact", text="Beta entry."),
            ),
            mode="append",
        )
        assert memory is not None
        alpha_id = memory.content.manifest.entries[0].entry_id
        beta_id = memory.content.manifest.entries[1].entry_id
        forgotten = await service.forget(memory, entries=(await current_entry(service, memory, alpha_id),))
        beta_after_forget = next(
            projection for projection in backend.commits[-1].projections if projection.entry_version.entry_id == beta_id
        )
        calls_before = len(provider.calls)

        restored = await service.reactivate(
            forgotten,
            entries=(await current_entry(service, forgotten, alpha_id),),
        )

        assert restored.revision == forgotten.revision + 1
        assert provider.calls[calls_before:] == [("Alpha entry.",)]
        by_entry = {projection.entry_version.entry_id: projection for projection in backend.commits[-1].projections}
        assert by_entry[beta_id].embedding == beta_after_forget.embedding
        assert by_entry[alpha_id].embedding == (1.0, 0.0, 0.0)

    asyncio.run(scenario())


def test_revise_only_embeds_changed_entry_text() -> None:
    async def scenario() -> None:
        backend = RecordingMemoryBackend(
            capabilities=MemoryCapabilities(
                fts=True,
                vector=True,
                hybrid=True,
                embedding_profile=TEST_PROFILE,
            ),
            vector_complete=True,
        )
        provider = RecordingEmbeddingProvider(profile=TEST_PROFILE)
        service = MemoryService(backend=backend, embedding_provider=provider, id_factory=SequentialIds())
        memory = await service.remember(
            memory=None,
            entries=(
                MemoryEntryInput(kind="fact", text="Alpha entry."),
                MemoryEntryInput(kind="fact", text="Beta entry."),
            ),
            mode="append",
        )
        assert memory is not None
        alpha_id = memory.content.manifest.entries[0].entry_id
        beta_id = memory.content.manifest.entries[1].entry_id
        beta_before = next(
            projection for projection in backend.commits[0].projections if projection.entry_version.entry_id == beta_id
        )

        updated = await service.remember(
            memory=memory,
            entries=(
                MemoryEntryInput(
                    entry=await current_entry(service, memory, alpha_id),
                    kind="fact",
                    text="Alpha entry revised.",
                ),
            ),
            mode="append",
        )
        assert updated is not None
        assert provider.calls[-1] == ("Alpha entry revised.",)
        by_entry = {projection.entry_version.entry_id: projection for projection in backend.commits[-1].projections}
        assert by_entry[beta_id].embedding == beta_before.embedding
        assert by_entry[alpha_id].embedding == (1.0, 0.0, 0.0)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response, message",
    [
        ((), "one vector per text"),
        (((1.0, 2.0),), "3 dimensions"),
        (((1.0, math.nan, 3.0),), "finite"),
    ],
)
def test_invalid_write_embeddings_abort_before_commit(
    response: tuple[EmbeddingVector, ...],
    message: str,
) -> None:
    async def scenario() -> None:
        backend = RecordingMemoryBackend(
            capabilities=MemoryCapabilities(
                fts=True,
                vector=True,
                hybrid=True,
                embedding_profile=TEST_PROFILE,
            )
        )
        provider = RecordingEmbeddingProvider(profile=TEST_PROFILE, responses=(response,))
        service = MemoryService(backend=backend, embedding_provider=provider, id_factory=SequentialIds())

        with pytest.raises(ValueError, match=message):
            await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Embedding target."),),
                mode="append",
            )
        assert backend.commits == []

    asyncio.run(scenario())


def test_auto_falls_back_to_fts_before_query_embedding_when_projection_is_incomplete() -> None:
    async def scenario() -> None:
        backend, service, memory, provider = await searchable_memory(vector_complete=False)
        entry = backend.commits[0].entry_versions[0]
        backend.channels = MemorySearchChannels(
            fts=(
                MemoryChannelHit(
                    memory_ref=memory.ref,
                    entry_id=entry.entry_id,
                    entry_version_id=entry.entry_version_id,
                    text=entry.text,
                ),
            )
        )
        write_call_count = len(provider.calls)

        result = await service.search("构建约定", memories=(memory,), limit=8, mode="auto")

        assert result.mode == "fts"
        assert result.hits[0].matched_by == ("fts",)
        assert backend.requests[-1].candidate_limit == 32
        assert backend.requests[-1].query_vector is None
        assert len(provider.calls) == write_call_count

    asyncio.run(scenario())


def test_auto_falls_back_to_fts_when_query_provider_becomes_unavailable() -> None:
    async def scenario() -> None:
        provider = RecordingEmbeddingProvider(
            profile=TEST_PROFILE,
            responses=(
                ((1.0, 0.0, 0.0),),
                EmbeddingProviderUnavailableError("provider timeout"),
            ),
        )
        backend, service, memory, _ = await searchable_memory(vector_complete=True, provider=provider)
        entry = backend.commits[0].entry_versions[0]
        backend.channels = MemorySearchChannels(
            fts=(MemoryChannelHit(memory.ref, entry.entry_id, entry.entry_version_id, entry.text),)
        )

        result = await service.search("构建约定", memories=(memory,), mode="auto")

        assert result.mode == "fts"
        assert result.hits[0].matched_by == ("fts",)
        assert backend.requests[-1].mode == "fts"
        assert backend.requests[-1].query_vector is None

    asyncio.run(scenario())


def test_explicit_vector_maps_transient_provider_outage_to_capability_error() -> None:
    async def scenario() -> None:
        provider = RecordingEmbeddingProvider(
            profile=TEST_PROFILE,
            responses=(
                ((1.0, 0.0, 0.0),),
                EmbeddingProviderUnavailableError("provider timeout"),
            ),
        )
        _, service, memory, _ = await searchable_memory(vector_complete=True, provider=provider)

        with pytest.raises(CapabilityNotSupportedError, match="temporarily unavailable"):
            await service.search("query", memories=(memory,), mode="vector")

    asyncio.run(scenario())


def test_auto_uses_hybrid_and_shared_rrf_when_projection_is_complete() -> None:
    async def scenario() -> None:
        backend, service, memory, provider = await searchable_memory(vector_complete=True)
        first = backend.commits[0].entry_versions[0]
        second = MemoryChannelHit(memory.ref, "entry-z", "version-z1", "Vector-only entry.")
        shared = MemoryChannelHit(memory.ref, first.entry_id, first.entry_version_id, first.text)
        backend.channels = MemorySearchChannels(fts=(shared,), vector=(second, shared))

        result = await service.search("documentation checks", memories=(memory,), limit=2, mode="auto")

        assert result.mode == "hybrid"
        assert tuple(hit.entry_id for hit in result.hits) == (first.entry_id, "entry-z")
        assert result.hits[0].matched_by == ("fts", "vector")
        assert provider.calls[-1] == ("documentation checks",)
        assert backend.requests[-1].query_vector == (1.0, 0.0, 0.0)

    asyncio.run(scenario())


def test_search_requires_explicit_current_memories_and_positive_limit() -> None:
    async def scenario() -> None:
        backend, service, first, _ = await searchable_memory(vector_complete=False)
        second = await service.remember(
            memory=first,
            entries=(
                MemoryEntryInput(entry=await current_entry(service, first), kind="constraint", text="Current head."),
            ),
            mode="append",
        )
        assert second is not None

        with pytest.raises(ValueError, match="explicit"):
            await service.search("query", memories=())
        with pytest.raises(ValueError, match="positive"):
            await service.search("query", memories=(second,), limit=0)
        with pytest.raises(CapabilityNotSupportedError, match="head"):
            await service.search("query", memories=(first,))

        await service.search("query", memories=(second, second), mode="fts")
        assert backend.requests[-1].memories == (second.ref,)

    asyncio.run(scenario())


def test_explicit_vector_does_not_fallback_when_projection_is_incomplete() -> None:
    async def scenario() -> None:
        _, service, memory, _ = await searchable_memory(vector_complete=False)
        with pytest.raises(CapabilityNotSupportedError, match="vector"):
            await service.search("query", memories=(memory,), mode="vector")
        with pytest.raises(CapabilityNotSupportedError, match="hybrid"):
            await service.search("query", memories=(memory,), mode="hybrid")

    asyncio.run(scenario())


def test_query_embedding_is_validated_before_backend_search() -> None:
    async def scenario() -> None:
        provider = RecordingEmbeddingProvider(
            profile=TEST_PROFILE,
            responses=(((1.0, 0.0, 0.0),), (((1.0, 2.0),))),
        )
        backend, service, memory, _ = await searchable_memory(vector_complete=True, provider=provider)
        with pytest.raises(ValueError, match="3 dimensions"):
            await service.search("query", memories=(memory,), mode="vector")
        assert backend.requests == []

    asyncio.run(scenario())
