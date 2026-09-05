# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""Bounded full-content signatures and deterministic related proposal groups."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import sqrt

from powercontext.builtin.artifacts.search import admits_fts_text, analyze_text
from powercontext.builtin.artifacts.topic_memory.fusion import admits_topic_memory_vector_distance
from powercontext.builtin.artifacts.topic_memory.generation import MAX_TOPIC_MEMORY_STAGE_ITEMS, TopicMemoryProposal
from powercontext.builtin.artifacts.topic_memory.models import (
    MAX_TOPIC_MEMORY_QUERY_LENGTH,
    MAX_TOPIC_MEMORY_QUERY_TERMS,
    TopicMemoryContent,
)
from powercontext.builtin.inference import EmbeddingVector


@dataclass(frozen=True, slots=True)
class TopicMemorySignature:
    """One bounded query derived by scanning every content field."""

    lexical: str
    vector: EmbeddingVector | None = None


def topic_memory_lexical_signature(content: TopicMemoryContent, /) -> str:
    """Select weighted terms after scanning the full title, summary, and detail."""

    weighted: Counter[str] = Counter()
    first: dict[str, tuple[int, int]] = {}
    for field_order, (weight, value) in enumerate(((8, content.title), (4, content.summary), (1, content.detail))):
        for position, term in enumerate(analyze_text(value).split()):
            weighted[term] += weight
            first.setdefault(term, (field_order, position))
    ordered = sorted(weighted, key=lambda term: (-weighted[term], first[term], term.encode()))
    selected: list[str] = []
    characters = 0
    for term in ordered[:MAX_TOPIC_MEMORY_QUERY_TERMS]:
        additional = len(term) + int(bool(selected))
        if characters + additional > MAX_TOPIC_MEMORY_QUERY_LENGTH:
            continue
        selected.append(term)
        characters += additional
    if not selected:
        raise ValueError("Topic Memory content has no bounded lexical signature")  # noqa: TRY003
    return " ".join(selected)


def topic_memory_vector_centroid(
    topic_vector: EmbeddingVector,
    chunks: tuple[tuple[int, EmbeddingVector], ...],
    /,
    *,
    topic_length: int,
) -> EmbeddingVector:
    """Return a normalized length-weighted centroid in fixed memory."""

    dimension = len(topic_vector)
    if dimension < 1 or any(len(vector) != dimension for _, vector in chunks):
        raise ValueError("Topic Memory centroid vectors must share a positive dimension")  # noqa: TRY003
    normalized_topic_length = max(1, topic_length)
    total_weight = normalized_topic_length + sum(max(1, length) for length, _ in chunks)
    values = [component * normalized_topic_length / total_weight for component in topic_vector]
    for length, vector in chunks:
        weight = max(1, length) / total_weight
        for index, component in enumerate(vector):
            values[index] += component * weight
    norm = sqrt(sum(component * component for component in values))
    if norm == 0:
        raise ValueError("Topic Memory centroid must not be zero")  # noqa: TRY003
    return tuple(component / norm for component in values)


def topic_memory_related_components(  # noqa: C901
    proposals: tuple[TopicMemoryProposal, ...],
    /,
    *,
    secondary_candidates: tuple[frozenset[str], ...],
    vectors: tuple[EmbeddingVector | None, ...] = (),
) -> tuple[tuple[int, ...], ...]:
    """Build bounded connected components from shared targets and bidirectional similarity."""

    if len(proposals) > MAX_TOPIC_MEMORY_STAGE_ITEMS or len(secondary_candidates) != len(proposals):
        raise ValueError("Topic Memory relatedness input exceeds its bounded contract")  # noqa: TRY003
    if vectors and len(vectors) != len(proposals):
        raise ValueError("Topic Memory relatedness vectors must cover every proposal")  # noqa: TRY003
    parents = list(range(len(proposals)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    signatures = tuple(topic_memory_lexical_signature(proposal.content) for proposal in proposals)
    for left in range(len(proposals)):
        for right in range(left + 1, len(proposals)):
            if proposals[left].candidate_id is not None and proposals[left].candidate_id == proposals[right].candidate_id:
                union(left, right)
                continue
            if secondary_candidates[left] & secondary_candidates[right]:
                union(left, right)
                continue
            left_text = _content_text(proposals[left].content)
            right_text = _content_text(proposals[right].content)
            lexical = admits_fts_text(signatures[left], right_text) and admits_fts_text(signatures[right], left_text)
            semantic = False
            left_vector = None if not vectors else vectors[left]
            right_vector = None if not vectors else vectors[right]
            if left_vector is not None and right_vector is not None:
                distance = sqrt(sum((a - b) ** 2 for a, b in zip(left_vector, right_vector, strict=True)))
                semantic = admits_topic_memory_vector_distance(distance)
            if lexical or semantic:
                union(left, right)

    groups: dict[int, list[int]] = {}
    for index in range(len(proposals)):
        groups.setdefault(find(index), []).append(index)
    components = tuple(tuple(indices) for _, indices in sorted(groups.items()))
    if any(len(component) > MAX_TOPIC_MEMORY_STAGE_ITEMS for component in components):
        raise ValueError("Topic Memory related component exceeds its bounded contract")  # noqa: TRY003
    return components


def _content_text(content: TopicMemoryContent) -> str:
    return f"{content.title}\n{content.summary}\n{content.detail}"


__all__ = [
    "TopicMemorySignature",
    "topic_memory_lexical_signature",
    "topic_memory_related_components",
    "topic_memory_vector_centroid",
]
