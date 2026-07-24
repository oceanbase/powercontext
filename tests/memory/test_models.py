from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import powercontext
from powercontext import ArtifactRef, PowerContextError
from powercontext.memory import (
    CapabilityNotSupportedError,
    Memory,
    MemoryChange,
    MemoryContent,
    MemoryEntryInactiveError,
    MemoryEntryInput,
    MemoryEntryNotFoundError,
    MemoryEntryVersion,
    MemoryManifest,
    MemoryManifestEntry,
)


def test_package_root_exposes_only_user_facing_memory_api() -> None:
    assert {"Memory", "MemoryEntryInput", "MemorySearchResult", "MemoryService"} <= set(powercontext.__all__)
    assert {
        "CandidatePipeline",
        "MemoryBackend",
        "MemoryUnitOfWork",
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


def test_memory_errors_are_stable_powercontext_errors() -> None:
    assert issubclass(CapabilityNotSupportedError, PowerContextError)
    assert issubclass(MemoryEntryInactiveError, PowerContextError)
    assert issubclass(MemoryEntryNotFoundError, PowerContextError)
