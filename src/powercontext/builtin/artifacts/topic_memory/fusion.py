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

import unicodedata
from collections import Counter
from collections.abc import Sequence

from powercontext.builtin.artifacts.search import admits_fts_text, analyze_text, analyze_text_with_spans
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
_MAX_RRF_SCORE = len(_CHANNEL_ORDER) / (_RRF_CONSTANT + 1)


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
                snippets.setdefault(identity, _snippet(query, candidate.chunk_text, lexical=channel == "detail_fts"))
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
            score=min(100.0, scores[identity] / _MAX_RRF_SCORE * 100.0),
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


def _snippet(query: str, value: str, *, lexical: bool) -> str:
    compact = " ".join(value.split())
    if len(compact) <= _SNIPPET_MAX_CHARACTERS:
        return compact
    focus = _lexical_focus(query, compact) if lexical else len(compact) // 2
    prefix = focus > _SNIPPET_MAX_CHARACTERS // 2
    suffix = len(compact) - focus > _SNIPPET_MAX_CHARACTERS // 2
    budget = _SNIPPET_MAX_CHARACTERS - int(prefix) - int(suffix)
    start = max(0, min(focus - budget // 2, len(compact) - budget))
    end = start + budget
    prefix = start > 0
    suffix = end < len(compact)
    if int(prefix) + int(suffix) != _SNIPPET_MAX_CHARACTERS - budget:
        budget = _SNIPPET_MAX_CHARACTERS - int(prefix) - int(suffix)
        start = max(0, min(focus - budget // 2, len(compact) - budget))
        end = start + budget
    return f"{'…' if start else ''}{compact[start:end]}{'…' if end < len(compact) else ''}"


def _lexical_focus(query: str, value: str) -> int:
    normalized, spans = _normalized_spans(value)
    query_terms = set(analyze_text(query).split())
    occurrences = sorted(
        (start, end - start, term) for term, start, end in analyze_text_with_spans(normalized) if term in query_terms
    )
    if not occurrences:
        return len(value) // 2
    counts: Counter[str] = Counter()
    left = 0
    best = (0, 0, occurrences[0][0])
    best_focus = occurrences[0][0]
    window = _SNIPPET_MAX_CHARACTERS - 2
    for position, length, needle in occurrences:
        counts[needle] += 1
        while position + length - occurrences[left][0] > window:
            counts[occurrences[left][2]] -= 1
            if counts[occurrences[left][2]] == 0:
                del counts[occurrences[left][2]]
            left += 1
        start = occurrences[left][0]
        candidate = (len(counts), -(position + length - start), -start)
        if candidate > best:
            best = candidate
            best_focus = (start + position + length) // 2
    return sum(spans[best_focus]) // 2


def _normalized_spans(value: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Map each NFC+casefold code point back to its complete source span."""

    normalized = unicodedata.normalize("NFC", value).casefold()
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    start = 0
    for end in range(1, len(value) + 1):
        if end < len(value) and unicodedata.combining(value[end]):
            continue
        part = unicodedata.normalize("NFC", value[start:end]).casefold()
        parts.append(part)
        spans.extend(((start, end),) * len(part))
        start = end
    if "".join(parts) == normalized:
        return normalized, tuple(spans)

    # NFC can compose across code points whose combining class is zero (for
    # example Hangul Jamo). Prefix lengths provide an exact, bounded fallback.
    prefix_lengths = tuple(len(unicodedata.normalize("NFC", value[:end]).casefold()) for end in range(len(value) + 1))
    spans = []
    for position in range(len(normalized)):
        source_start = max(end for end, length in enumerate(prefix_lengths) if length <= position)
        source_end = max(end for end, length in enumerate(prefix_lengths) if length <= position + 1)
        spans.append((source_start, max(source_start + 1, source_end)))
    return normalized, tuple(spans)


__all__ = ["fuse_topic_memory_rankings"]
