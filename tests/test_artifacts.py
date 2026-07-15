from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass
from typing import ClassVar

import pytest

from powercontext.artifacts import (
    Artifact,
    ArtifactCatalog,
    ArtifactDraft,
    ArtifactLineage,
    ArtifactRef,
    ArtifactStore,
)
from powercontext.errors import ArtifactNotFoundError, RevisionConflictError
from powercontext.sources import Source, SourceMaterialization


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationSource(Source):
    session_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractedMemoryDraft(ArtifactDraft[tuple[str, ...]]):
    family: ClassVar[str] = "extracted-memory"


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractedMemory(Artifact[tuple[str, ...]]):
    family: ClassVar[str] = "extracted-memory"


class InMemoryArtifactRepository(
    ArtifactCatalog[ExtractedMemory],
    ArtifactStore[ExtractedMemoryDraft, ExtractedMemory],
):
    def __init__(self) -> None:
        self._by_ref: dict[ArtifactRef, ExtractedMemory] = {}
        self._next_id = 1

    async def get(self, artifact: ExtractedMemory, /) -> ExtractedMemory:
        stored = self._by_ref.get(artifact.ref)
        if stored is None or stored != artifact:
            raise ArtifactNotFoundError(artifact)
        return stored

    async def latest(self, artifact: ExtractedMemory, /) -> ExtractedMemory:
        revisions = await self.revisions(artifact)
        if not revisions:
            raise ArtifactNotFoundError(artifact)
        return revisions[-1]

    async def revisions(self, artifact: ExtractedMemory, /) -> tuple[ExtractedMemory, ...]:
        matches = (candidate for ref, candidate in self._by_ref.items() if ref.artifact_id == artifact.artifact_id)
        return tuple(sorted(matches, key=lambda candidate: candidate.revision))

    async def add(self, draft: ExtractedMemoryDraft, /) -> ExtractedMemory:
        artifact = self._commit(f"memory-{self._next_id}", 1, draft)
        self._next_id += 1
        return artifact

    async def revise(
        self,
        artifact: ExtractedMemory,
        draft: ExtractedMemoryDraft,
        /,
    ) -> ExtractedMemory:
        current = await self.latest(artifact)
        if current != artifact:
            raise RevisionConflictError(artifact, current)
        return self._commit(artifact.artifact_id, artifact.revision + 1, draft)

    def _commit(self, artifact_id: str, revision: int, draft: ExtractedMemoryDraft) -> ExtractedMemory:
        artifact = ExtractedMemory(
            artifact_id=artifact_id,
            revision=revision,
            content=draft.content,
            lineage=ArtifactLineage(
                sources=draft.sources,
                artifacts=tuple(dependency.ref for dependency in draft.artifacts),
            ),
        )
        self._by_ref[artifact.ref] = artifact
        return artifact


def test_artifact_ref_identifies_one_revision() -> None:
    assert ArtifactRef("preference-memory", 1) == ArtifactRef("preference-memory", 1)


def test_artifact_is_an_immutable_fixed_family_snapshot_with_direct_lineage() -> None:
    source = ConversationSource(
        name="session-42-snapshot",
        materialization=SourceMaterialization.CAPTURED,
        session_id="session-42",
    )
    dependency = ArtifactRef("user-profile", 2)
    artifact = ExtractedMemory(
        artifact_id="preference-memory",
        revision=3,
        content=("User prefers aisle seats.",),
        lineage=ArtifactLineage(sources=(source,), artifacts=(dependency,)),
    )

    assert artifact.family == "extracted-memory"
    assert artifact.ref == ArtifactRef("preference-memory", 3)
    assert artifact.lineage.sources == (source,)
    assert artifact.lineage.artifacts == (dependency,)
    with pytest.raises(FrozenInstanceError):
        artifact.revision = 4  # ty: ignore[invalid-assignment]


def test_artifact_store_adds_and_revises_complete_domain_objects() -> None:
    async def scenario() -> None:
        repository = InMemoryArtifactRepository()
        catalog: ArtifactCatalog[ExtractedMemory] = repository
        store: ArtifactStore[ExtractedMemoryDraft, ExtractedMemory] = repository
        initial_conversation = ConversationSource(
            name="session-42-snapshot",
            materialization=SourceMaterialization.CAPTURED,
            session_id="session-42",
        )
        followup_conversation = ConversationSource(
            name="session-43-snapshot",
            materialization=SourceMaterialization.CAPTURED,
            session_id="session-43",
        )
        profile = await store.add(ExtractedMemoryDraft(content=("User travels frequently.",)))
        first = await store.add(
            ExtractedMemoryDraft(
                content=("User prefers aisle seats.",),
                sources=(initial_conversation,),
                artifacts=(profile,),
            )
        )
        second = await store.revise(
            first,
            ExtractedMemoryDraft(
                content=("User prefers aisle seats.", "User avoids overnight flights."),
                sources=(followup_conversation,),
                artifacts=(profile,),
            ),
        )

        assert await catalog.get(first) == first
        assert await catalog.latest(first) == second
        assert await catalog.revisions(first) == (first, second)
        assert first.content == ("User prefers aisle seats.",)
        assert first.lineage.sources == (initial_conversation,)
        assert first.lineage.artifacts == (profile.ref,)
        assert second.artifact_id == first.artifact_id
        assert second.revision == first.revision + 1
        assert second.lineage.sources == (followup_conversation,)
        assert second.lineage.artifacts == (profile.ref,)

        with pytest.raises(RevisionConflictError) as error:
            await store.revise(first, ExtractedMemoryDraft(content=("Stale extraction.",)))
        assert error.value.artifact is first
        assert error.value.current is second

    asyncio.run(scenario())


def test_artifact_catalog_uses_objects_for_lookup() -> None:
    async def scenario() -> None:
        catalog: ArtifactCatalog[ExtractedMemory] = InMemoryArtifactRepository()
        missing = ExtractedMemory(
            artifact_id="missing",
            revision=2,
            content=("Detached memory.",),
        )

        with pytest.raises(ArtifactNotFoundError) as error:
            await catalog.get(missing)
        assert error.value.artifact is missing

    asyncio.run(scenario())
