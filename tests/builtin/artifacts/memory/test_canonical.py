from __future__ import annotations

import math

import pytest

from powercontext.builtin.artifacts.memory import MemoryChange, MemoryContent, MemoryManifest, MemoryManifestEntry
from powercontext.builtin.artifacts.memory.canonical import (
    canonical_json,
    embedding_content_hash,
    entry_content_bytes,
    entry_content_hash,
    memory_content_hash,
    normalize_embedding,
    normalize_kind,
    normalize_reason,
    normalize_text,
    validate_embedding,
    validate_identifier,
)


def test_entry_hash_uses_normalized_content_and_sorted_refs() -> None:
    first = entry_content_hash(
        kind="fact",
        text="Cafe\N{COMBINING ACUTE ACCENT}  ",
        source_refs=({"id": "b"}, {"id": "a"}, {"id": "a"}),
        artifact_refs=({"artifact_id": "z", "revision": 2},),
    )
    second = entry_content_hash(
        kind="fact",
        text="Caf\N{LATIN SMALL LETTER E WITH ACUTE}",
        source_refs=({"id": "a"}, {"id": "b"}),
        artifact_refs=({"revision": 2, "artifact_id": "z"},),
    )

    assert first == second
    assert len(first) == 64
    assert canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_entry_hash_covers_kind_text_and_both_evidence_sets() -> None:
    def hash_for(
        *,
        kind: str = "fact",
        text: str = "Durable fact.",
        source_refs: tuple[object, ...] = ({"name": "source-a"},),
        artifact_refs: tuple[object, ...] = ({"artifact_id": "artifact-a", "revision": 1},),
    ) -> str:
        return entry_content_hash(
            kind=kind,
            text=text,
            source_refs=source_refs,
            artifact_refs=artifact_refs,
        )

    assert hash_for() != hash_for(kind="decision")
    assert hash_for() != hash_for(text="Changed fact.")
    assert hash_for() != hash_for(source_refs=())
    assert hash_for() != hash_for(artifact_refs=())
    assert entry_content_bytes(
        kind="fact",
        text="Durable fact.",
        source_refs=({"name": "source-a"},),
        artifact_refs=({"artifact_id": "artifact-a", "revision": 1},),
    ).startswith(b'{"artifact_refs"')


def test_memory_hash_covers_complete_manifest_and_change_summary() -> None:
    entry = MemoryManifestEntry(
        entry_id="entry-a",
        entry_version_id="version-a1",
        entry_content_hash="a" * 64,
        state="active",
    )
    initial = MemoryContent(
        manifest=MemoryManifest(entries=(entry,)),
        changes=(
            MemoryChange(
                op="add",
                entry_id="entry-a",
                from_entry_version_id=None,
                to_entry_version_id="version-a1",
            ),
        ),
    )
    restored = MemoryContent(
        manifest=MemoryManifest(entries=(entry,)),
        changes=(
            MemoryChange(
                op="reactivate",
                entry_id="entry-a",
                from_entry_version_id=None,
                to_entry_version_id="version-a1",
            ),
        ),
    )

    assert memory_content_hash(initial) != memory_content_hash(restored)
    assert len(memory_content_hash(initial)) == 64


def test_limits_are_measured_after_normalization() -> None:
    assert normalize_text("  durable  ") == "durable"
    assert normalize_kind("  integration-kind  ") == "integration-kind"
    assert normalize_reason("  user requested  ") == "user requested"
    assert normalize_reason("   ") is None
    with pytest.raises(ValueError, match="8192 UTF-8 bytes"):
        normalize_text("界" * 2731)
    with pytest.raises(ValueError, match="512 Unicode code points"):
        normalize_reason("x" * 513)
    with pytest.raises(ValueError, match="non-empty"):
        normalize_kind("   ")
    with pytest.raises(ValueError, match="ASCII"):
        validate_identifier("记忆")
    with pytest.raises(ValueError, match="128"):
        validate_identifier("x" * 129)


def test_embedding_validation_rejects_wrong_or_non_finite_vectors() -> None:
    assert validate_embedding((1, 2.5, -3), dimension=3) == (1.0, 2.5, -3.0)
    with pytest.raises(ValueError, match="3 dimensions"):
        validate_embedding((1.0, 2.0), dimension=3)
    for invalid in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="finite"):
            validate_embedding((1.0, invalid, 3.0), dimension=3)
    with pytest.raises(ValueError, match="positive"):
        validate_embedding((), dimension=0)


def test_embedding_normalization_produces_a_unit_vector() -> None:
    assert normalize_embedding((3.0, 4.0), dimension=2) == pytest.approx((0.6, 0.8))
    assert normalize_embedding((1e308, 1e308), dimension=2) == pytest.approx((math.sqrt(0.5), math.sqrt(0.5)))


def test_embedding_normalization_rejects_zero_vectors() -> None:
    with pytest.raises(ValueError, match="non-zero norm"):
        normalize_embedding((0.0, 0.0), dimension=2)


def test_embedding_hash_binds_profile_and_entry_content() -> None:
    first = embedding_content_hash(
        profile_id="profile-a",
        model="model-a",
        dimension=3,
        distance="l2",
        normalization="none",
        entry_content_hash="a" * 64,
    )
    second = embedding_content_hash(
        profile_id="profile-b",
        model="model-a",
        dimension=3,
        distance="l2",
        normalization="none",
        entry_content_hash="a" * 64,
    )

    assert first != second
    assert len(first) == 64
