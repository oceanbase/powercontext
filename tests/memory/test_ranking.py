from __future__ import annotations

import pytest

from powercontext import ArtifactRef
from powercontext.memory import MemoryChannelHit
from powercontext.memory.ranking import fuse_rankings


def channel_hit(entry_id: str, *, memory_id: str = "memory-a") -> MemoryChannelHit:
    return MemoryChannelHit(
        memory_ref=ArtifactRef(memory_id, 2),
        entry_id=entry_id,
        entry_version_id=f"version-{entry_id}",
        text=entry_id,
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
