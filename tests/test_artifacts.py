from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass
from typing import ClassVar, cast

import pytest

from powercontext.artifacts import (
    Artifact,
    ArtifactCatalog,
    ArtifactDraft,
    ArtifactLineage,
    ArtifactRef,
    ArtifactStore,
)
from powercontext.context import Artifacts
from powercontext.errors import ArtifactFamilyMismatchError
from powercontext.sources import Source, SourceMaterialization


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationSource(Source):
    session_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractedMemory(Artifact[tuple[str, ...]]):
    family: ClassVar[str] = "extracted-memory"


@dataclass(frozen=True, slots=True, kw_only=True)
class HandoffDraft(ArtifactDraft[str]):
    family: ClassVar[str] = "handoff"


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
    assert artifact.lineage == ArtifactLineage(sources=(source,), artifacts=(dependency,))
    with pytest.raises(FrozenInstanceError):
        artifact.revision = 4  # ty: ignore[invalid-assignment]


def test_artifacts_reject_cross_family_revisions_before_storage() -> None:
    async def scenario() -> None:
        backend = object()
        artifacts = Artifacts(
            catalog=cast(ArtifactCatalog[Artifact[object]], backend),
            store=cast(ArtifactStore[ArtifactDraft[object], Artifact[object]], backend),
        )
        memory = ExtractedMemory(
            artifact_id="preference-memory",
            revision=3,
            content=("User prefers aisle seats.",),
        )
        draft = HandoffDraft(content="handoff")

        with pytest.raises(ArtifactFamilyMismatchError) as error:
            await artifacts.revise(memory, draft)

        assert error.value.artifact is memory
        assert error.value.draft is draft

    asyncio.run(scenario())
