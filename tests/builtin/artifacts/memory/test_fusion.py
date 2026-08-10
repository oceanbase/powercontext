from __future__ import annotations

import pytest

from powercontext import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryChannelHit
from powercontext.builtin.artifacts.memory.fusion import (
    admit_fts_candidates,
    admit_vector_candidates,
    fuse_rankings,
)


def channel_hit(
    entry_id: str,
    *,
    memory_id: str = "memory-a",
    text: str | None = None,
    distance: float | None = None,
) -> MemoryChannelHit:
    return MemoryChannelHit(
        memory_ref=ArtifactRef(family="memory", artifact_id=memory_id, revision=2),
        entry_id=entry_id,
        entry_version_id=f"version-{entry_id}",
        text=entry_id if text is None else text,
        distance=distance,
    )


def test_rrf_merges_channels_and_uses_stable_identity_ties() -> None:
    alpha = channel_hit("alpha")
    beta = channel_hit("beta")
    hits = fuse_rankings(fts=(alpha, beta), vector=(beta, alpha), limit=2)

    assert tuple(hit.entry_id for hit in hits) == ("alpha", "beta")
    assert hits[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert hits[1].score == pytest.approx(1 / 61 + 1 / 62)
    assert hits[0].matched_by == hits[1].matched_by == ("fts", "vector")


def test_single_channel_uses_public_rrf_score_and_identity_order() -> None:
    zulu = channel_hit("zulu", memory_id="memory-z")
    alpha = channel_hit("alpha", memory_id="memory-a")

    hits = fuse_rankings(fts=(zulu, alpha), vector=(), limit=4)

    assert tuple(hit.entry_id for hit in hits) == ("zulu", "alpha")
    assert tuple(hit.score for hit in hits) == pytest.approx((1 / 61, 1 / 62))
    assert all(hit.matched_by == ("fts",) for hit in hits)


def test_duplicate_channel_rows_contribute_only_the_first_rank() -> None:
    alpha = channel_hit("alpha")

    hits = fuse_rankings(fts=(alpha, alpha), vector=(), limit=2)

    assert len(hits) == 1
    assert hits[0].score == pytest.approx(1 / 61)


def test_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        fuse_rankings(fts=(), vector=(), limit=0)


def test_fts_admission_rejects_single_common_term_but_keeps_specific_overlap() -> None:
    candidate = channel_hit(
        "leader-election",
        text="Use PostgreSQL advisory locks for leader election.",
    )
    unrelated = admit_fts_candidates(
        "Should we use blue icons in the mobile navigation bar?",
        (candidate,),
    )
    related = admit_fts_candidates(
        "Which locks should leader election use?",
        (candidate,),
    )

    assert unrelated == ()
    assert related == (candidate,)


def test_fts_admission_keeps_one_term_queries_usable() -> None:
    candidate = channel_hit("atomic", text="Use one atomic composition boundary.")

    assert admit_fts_candidates("atomic", (candidate,)) == (candidate,)


def test_vector_admission_converts_unit_l2_distance_to_cosine_threshold() -> None:
    boundary = (2.0 * (1.0 - 0.3)) ** 0.5
    accepted = channel_hit("accepted", distance=boundary)
    rejected = channel_hit("rejected", distance=boundary + 0.001)
    missing_evidence = channel_hit("missing")

    admitted = admit_vector_candidates(
        (accepted, rejected, missing_evidence),
    )

    assert admitted == (accepted,)
