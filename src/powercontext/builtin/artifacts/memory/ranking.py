"""Backend-neutral reciprocal-rank fusion for Memory retrieval."""

from __future__ import annotations

from collections.abc import Sequence

from powercontext.builtin.artifacts.memory.models import MemoryChannelHit, MemoryHit, MemoryMatchedBy

_RRF_CONSTANT = 60

_HitIdentity = tuple[str, int, str, str]


def fuse_rankings(
    *,
    fts: Sequence[MemoryChannelHit],
    vector: Sequence[MemoryChannelHit],
    limit: int,
) -> tuple[MemoryHit, ...]:
    """Fuse ordered channel candidates with reciprocal rank fusion."""

    if limit < 1:
        raise ValueError("memory search limit must be positive")  # noqa: TRY003

    candidates: dict[_HitIdentity, MemoryChannelHit] = {}
    scores: dict[_HitIdentity, float] = {}
    channels: dict[_HitIdentity, set[MemoryMatchedBy]] = {}

    for channel, ranking in (("fts", fts), ("vector", vector)):
        seen: set[_HitIdentity] = set()
        for rank, candidate in enumerate(ranking, start=1):
            identity = _identity(candidate)
            if identity in seen:
                continue
            seen.add(identity)
            candidates.setdefault(identity, candidate)
            scores[identity] = scores.get(identity, 0.0) + 1.0 / (_RRF_CONSTANT + rank)
            channels.setdefault(identity, set()).add(channel)

    ordered = sorted(
        candidates,
        key=lambda identity: (
            -scores[identity],
            identity[0].encode("utf-8"),
            identity[2].encode("utf-8"),
            identity[3].encode("utf-8"),
        ),
    )[:limit]
    return tuple(
        MemoryHit(
            memory_ref=candidates[identity].memory_ref,
            entry_id=candidates[identity].entry_id,
            entry_version_id=candidates[identity].entry_version_id,
            text=candidates[identity].text,
            score=scores[identity],
            matched_by=tuple(channel for channel in ("fts", "vector") if channel in channels[identity]),
        )
        for identity in ordered
    )


def _identity(hit: MemoryChannelHit) -> _HitIdentity:
    return (
        hit.memory_ref.artifact_id,
        hit.memory_ref.revision,
        hit.entry_id,
        hit.entry_version_id,
    )
