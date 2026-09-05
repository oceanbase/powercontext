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

"""Deterministic four-channel reciprocal-rank fusion for Topic Memory."""

from __future__ import annotations

from collections.abc import Sequence

from powercontext.builtin.artifacts.search import admits_fts_text
from powercontext.builtin.artifacts.topic_memory.models import (
    TopicMemoryChannelHit,
    TopicMemoryMatchedBy,
    TopicMemorySearchChannels,
    TopicMemorySearchHit,
)

_RRF_CONSTANT = 60
_MIN_SEMANTIC_SIMILARITY = 0.3
_SNIPPET_MAX_CHARACTERS = 480
_CHANNEL_ORDER: tuple[TopicMemoryMatchedBy, ...] = (
    "topic_fts",
    "topic_vector",
    "detail_fts",
    "detail_vector",
)


def fuse_topic_memory_rankings(
    query: str,
    channels: TopicMemorySearchChannels,
    limit: int,
    /,
) -> tuple[TopicMemorySearchHit, ...]:
    """Collapse each channel by Topic and fuse ranks without comparing raw scores."""

    candidates: dict[tuple[str, int], TopicMemoryChannelHit] = {}
    snippets: dict[tuple[str, int], str] = {}
    scores: dict[tuple[str, int], float] = {}
    matched: dict[tuple[str, int], set[TopicMemoryMatchedBy]] = {}
    rankings: tuple[tuple[TopicMemoryMatchedBy, tuple[TopicMemoryChannelHit, ...]], ...] = (
        ("topic_fts", _admit_fts(query, channels.topic_fts)),
        ("topic_vector", _admit_vector(channels.topic_vector)),
        ("detail_fts", _admit_fts(query, channels.detail_fts)),
        ("detail_vector", _admit_vector(channels.detail_vector)),
    )
    for channel, ranking in rankings:
        seen: set[tuple[str, int]] = set()
        for rank, candidate in enumerate(ranking, start=1):
            identity = (candidate.artifact_ref.artifact_id, candidate.artifact_ref.revision)
            if identity in seen:
                continue
            seen.add(identity)
            candidates.setdefault(identity, candidate)
            if candidate.chunk_text is not None:
                snippets.setdefault(identity, _snippet(candidate.chunk_text))
            scores[identity] = scores.get(identity, 0.0) + 1.0 / (_RRF_CONSTANT + rank)
            matched.setdefault(identity, set()).add(channel)

    ordered = sorted(
        candidates,
        key=lambda identity: (-scores[identity], identity[0].encode(), -identity[1]),
    )[:limit]
    return tuple(
        TopicMemorySearchHit(
            artifact_ref=candidates[identity].artifact_ref,
            title=candidates[identity].title,
            summary=candidates[identity].summary,
            snippet=snippets.get(identity),
            score=scores[identity],
            matched_by=tuple(channel for channel in _CHANNEL_ORDER if channel in matched[identity]),
        )
        for identity in ordered
    )


def _admit_fts(query: str, hits: Sequence[TopicMemoryChannelHit]) -> tuple[TopicMemoryChannelHit, ...]:
    return tuple(
        hit
        for hit in hits
        if admits_fts_text(query, hit.chunk_text if hit.chunk_text is not None else f"{hit.title}\n{hit.summary}")
    )


def _admit_vector(hits: Sequence[TopicMemoryChannelHit]) -> tuple[TopicMemoryChannelHit, ...]:
    return tuple(
        hit
        for hit in hits
        if hit.distance is not None and max(-1.0, min(1.0, 1.0 - hit.distance**2 / 2.0)) >= _MIN_SEMANTIC_SIMILARITY
    )


def _snippet(value: str) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= _SNIPPET_MAX_CHARACTERS else f"{compact[: _SNIPPET_MAX_CHARACTERS - 1]}…"


__all__ = ["fuse_topic_memory_rankings"]
