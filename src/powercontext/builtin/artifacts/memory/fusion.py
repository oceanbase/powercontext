# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Backend-neutral reciprocal-rank fusion for Memory retrieval."""

from __future__ import annotations

from collections.abc import Sequence

from powercontext.builtin.artifacts.memory.models import (
    MemoryChannelHit,
    MemoryHit,
    MemoryMatchedBy,
)
from powercontext.builtin.artifacts.search import (
    AdmissionFloor,
    RecallChannelWeights,
    admits_fts_text,
    unit_l2_cosine_similarity,
)

_RRF_CONSTANT = 60
_MIN_SEMANTIC_SIMILARITY = 0.3

_HitIdentity = tuple[str, int, str, str]


def admit_fts_candidates(
    query: str,
    candidates: Sequence[MemoryChannelHit],
    *,
    admission: AdmissionFloor | None = None,
) -> tuple[MemoryChannelHit, ...]:
    """Keep lexical candidates with enough distinct query-term evidence.

    ``admission=None`` reproduces today's behaviour bit for bit.
    """

    return tuple(candidate for candidate in candidates if admits_fts_text(query, candidate.text, floor=admission))


def admit_vector_candidates(
    candidates: Sequence[MemoryChannelHit],
    *,
    admission: AdmissionFloor | None = None,
) -> tuple[MemoryChannelHit, ...]:
    """Keep unit-vector L2 candidates meeting the semantic similarity baseline."""

    baseline = _MIN_SEMANTIC_SIMILARITY if admission is None else admission.min_semantic_similarity
    return tuple(
        candidate
        for candidate in candidates
        if candidate.distance is not None and unit_l2_cosine_similarity(candidate.distance) >= baseline
    )


def fuse_rankings(
    *,
    fts: Sequence[MemoryChannelHit],
    vector: Sequence[MemoryChannelHit],
    limit: int,
    channel_weights: RecallChannelWeights | None = None,
) -> tuple[MemoryHit, ...]:
    """Fuse ordered channel candidates with reciprocal rank fusion.

    ``channel_weights=None`` is the historical unweighted behavior used by the
    single-channel modes. A caller supplies weights only for hybrid search.
    """

    if limit < 1:
        raise ValueError("memory search limit must be positive")  # noqa: TRY003

    candidates: dict[_HitIdentity, MemoryChannelHit] = {}
    scores: dict[_HitIdentity, float] = {}
    channels: dict[_HitIdentity, set[MemoryMatchedBy]] = {}
    relevance: dict[_HitIdentity, float] = {}

    weights: dict[MemoryMatchedBy, float] = {
        "fts": 1.0 if channel_weights is None else channel_weights.fts,
        "vector": 1.0 if channel_weights is None else channel_weights.vector,
    }
    for channel, ranking in (("fts", fts), ("vector", vector)):
        weight = weights[channel]
        if weight == 0.0:
            continue
        seen: set[_HitIdentity] = set()
        for rank, candidate in enumerate(ranking, start=1):
            identity = _identity(candidate)
            if identity in seen:
                continue
            seen.add(identity)
            candidates.setdefault(identity, candidate)
            scores[identity] = scores.get(identity, 0.0) + weight / (_RRF_CONSTANT + rank)
            channels.setdefault(identity, set()).add(channel)
            if channel == "vector" and candidate.distance is not None:
                similarity = unit_l2_cosine_similarity(candidate.distance)
                relevance[identity] = max(relevance.get(identity, -1.0), similarity)

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
            score_upper_bound=sum(weights[channel] for channel in channels[identity]) / (_RRF_CONSTANT + 1),
            relevance=relevance.get(identity),
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
