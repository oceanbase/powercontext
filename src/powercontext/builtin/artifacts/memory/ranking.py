"""Backend-neutral reciprocal-rank fusion for Memory retrieval."""

from __future__ import annotations

import math
from collections.abc import Sequence

from powercontext.builtin.artifacts.memory.canonical import analyze_text
from powercontext.builtin.artifacts.memory.models import (
    MemoryChannelHit,
    MemoryHit,
    MemoryMatchedBy,
)

_RRF_CONSTANT = 60
_FTS_MIN_QUERY_COVERAGE = 0.25
_FTS_MIN_MATCHED_TERMS = 2
_FTS_SHORT_QUERY_MAX_TERMS = 2
_MIN_SEMANTIC_SIMILARITY = 0.3

_HitIdentity = tuple[str, int, str, str]


def admit_fts_candidates(
    query: str,
    candidates: Sequence[MemoryChannelHit],
) -> tuple[MemoryChannelHit, ...]:
    """Keep lexical candidates with enough distinct query-term evidence."""

    query_terms = frozenset(analyze_text(query).split())
    if not query_terms:
        return ()
    required_matches = (
        1
        if len(query_terms) <= _FTS_SHORT_QUERY_MAX_TERMS
        else max(
            _FTS_MIN_MATCHED_TERMS,
            math.ceil(len(query_terms) * _FTS_MIN_QUERY_COVERAGE),
        )
    )
    return tuple(
        candidate
        for candidate in candidates
        if len(query_terms.intersection(analyze_text(candidate.text).split())) >= required_matches
    )


def admit_vector_candidates(
    candidates: Sequence[MemoryChannelHit],
) -> tuple[MemoryChannelHit, ...]:
    """Keep unit-vector L2 candidates meeting the semantic similarity baseline."""

    return tuple(
        candidate
        for candidate in candidates
        if candidate.distance is not None and _unit_l2_cosine_similarity(candidate.distance) >= _MIN_SEMANTIC_SIMILARITY
    )


def _unit_l2_cosine_similarity(distance: float) -> float:
    return max(-1.0, min(1.0, 1.0 - distance**2 / 2.0))


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
