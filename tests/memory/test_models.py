from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import powercontext
from powercontext import ArtifactRef, PowerContextError
from powercontext.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    Memory,
    MemoryCapabilities,
    MemoryChange,
    MemoryCitation,
    MemoryContent,
    MemoryEntryInactiveError,
    MemoryEntryInput,
    MemoryEntryNotFoundError,
    MemoryEntryVersion,
    MemoryHit,
    MemoryManifest,
    MemoryManifestEntry,
    MemoryRevisionChanges,
    MemorySearchResult,
)


def test_package_root_exposes_only_user_facing_memory_api() -> None:
    assert {"Memory", "MemoryEntryInput", "MemorySearchResult", "MemoryService"} <= set(powercontext.__all__)
    assert {
        "CandidatePipeline",
        "MemoryBackend",
        "MemoryUnitOfWork",
        "TaskOutcomeReport",
        "TaskOutcomeSource",
        "WorkingNoteCandidatePipeline",
    }.isdisjoint(powercontext.__all__)


def test_memory_models_express_exact_revision_and_entry_identity() -> None:
    manifest_entry = MemoryManifestEntry(
        entry_id="entry-a",
        entry_version_id="version-a1",
        entry_content_hash="a" * 64,
        state="active",
    )
    content = MemoryContent(
        manifest=MemoryManifest(entries=(manifest_entry,)),
        changes=(
            MemoryChange(
                op="add",
                entry_id="entry-a",
                from_entry_version_id=None,
                to_entry_version_id="version-a1",
                reason=None,
            ),
        ),
    )
    memory = Memory(artifact_id="memory-a", revision=1, content=content)

    assert memory.family == "memory"
    assert memory.ref == ArtifactRef("memory-a", 1)
    assert memory.content.schema == "powercontext.memory.v1"
    assert memory.content.manifest.format == "flat-v1"
    with pytest.raises(FrozenInstanceError):
        manifest_entry.state = "inactive"  # ty: ignore[invalid-assignment]


def test_memory_entry_kind_is_an_open_non_empty_string() -> None:
    entry = MemoryEntryVersion(
        memory_artifact_id="memory-a",
        entry_id="entry-a",
        entry_version_id="version-a1",
        version=1,
        previous_version_id=None,
        kind="integration-owned-kind",
        text="Durable text.",
        entry_content_hash="b" * 64,
        created_in_revision=1,
    )
    candidate = MemoryEntryInput(kind="integration-owned-kind", text="Durable text.")

    assert entry.kind == candidate.kind == "integration-owned-kind"
    assert entry.sources == candidate.sources == ()
    assert entry.artifacts == ()


def test_search_and_change_models_keep_exact_anchors_and_actual_mode() -> None:
    memory_ref = ArtifactRef("memory-a", 4)
    change = MemoryChange(
        op="reactivate",
        entry_id="entry-a",
        from_entry_version_id=None,
        to_entry_version_id="version-a1",
        reason="user_restored",
    )
    hit = MemoryHit(
        memory_ref=memory_ref,
        entry_id="entry-a",
        entry_version_id="version-a1",
        text="Durable text.",
        score=1 / 61,
        matched_by=("fts",),
    )

    assert MemoryRevisionChanges(memory_ref=memory_ref, changes=(change,)).memory_ref == memory_ref
    assert MemorySearchResult(mode="fts", hits=(hit,)).hits == (hit,)
    assert (
        MemoryCitation(
            memory_ref=memory_ref,
            entry_id="entry-a",
            entry_version_id="version-a1",
        ).entry_version_id
        == "version-a1"
    )


def test_capabilities_bind_one_optional_embedding_profile() -> None:
    profile = EmbeddingProfile(
        profile_id="keyword-v1",
        model="keyword",
        dimension=3,
        distance="l2",
        normalization="none",
    )

    assert MemoryCapabilities(fts=True).embedding_profile is None
    assert (
        MemoryCapabilities(fts=True, vector=True, hybrid=True, embedding_profile=profile).embedding_profile is profile
    )


def test_memory_errors_are_stable_powercontext_errors() -> None:
    assert issubclass(CapabilityNotSupportedError, PowerContextError)
    assert issubclass(MemoryEntryInactiveError, PowerContextError)
    assert issubclass(MemoryEntryNotFoundError, PowerContextError)
