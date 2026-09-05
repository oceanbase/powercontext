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

"""Versioned Markdown-aware chunking for Topic Memory detail projections."""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.artifacts.topic_memory.models import (
    TopicMemoryChunk,
    TopicMemoryContent,
    TopicMemoryProjection,
)
from powercontext.builtin.inference import EmbeddingVector

TOPIC_MEMORY_CHUNK_POLICY_VERSION = "markdown-v1"
TOPIC_MEMORY_CHUNK_TARGET_CHARACTERS = 1_200
TOPIC_MEMORY_CHUNK_MAX_CHARACTERS = 1_800
TOPIC_MEMORY_CHUNK_MIN_TAIL_CHARACTERS = 300
TOPIC_MEMORY_CHUNK_OVERLAP_CHARACTERS = 160

_BLOCK_BOUNDARY = re.compile(r"(?:\n[ \t]*\n+)|(?=^#{1,6}[ \t]+)|(?=^[ \t]*(?:[-*+] |\d+[.)] ))", re.MULTILINE)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。！？])(?:[ \t]+|(?=\n))")  # noqa: RUF001


@dataclass(frozen=True, slots=True)
class _Span:
    start: int
    end: int
    forced: bool = False


def prepare_topic_memory_projection(
    content: TopicMemoryContent,
    /,
    *,
    topic_embedding: EmbeddingVector | None = None,
    chunk_embeddings: tuple[EmbeddingVector, ...] = (),
    embedding_profile: EmbeddingProfile | None = None,
) -> TopicMemoryProjection:
    """Prepare deterministic chunks and lexical text outside a write transaction."""

    chunks = chunk_topic_memory_detail(content.detail)
    return TopicMemoryProjection(
        content=content,
        chunks=chunks,
        topic_searchable_text=analyze_text(f"{content.title}\n{content.summary}"),
        topic_embedding=topic_embedding,
        chunk_embeddings=chunk_embeddings,
        embedding_profile=embedding_profile,
    )


def chunk_topic_memory_detail(detail: str, /) -> tuple[TopicMemoryChunk, ...]:
    """Split Markdown on semantic boundaries, using overlap only for oversized atoms."""

    spans = _semantic_spans(detail)
    assembled: list[_Span] = []
    current: _Span | None = None
    for span in spans:
        if span.forced:
            if current is not None:
                assembled.append(current)
                current = None
            assembled.append(span)
            continue
        if current is None:
            current = span
            continue
        if span.end - current.start <= TOPIC_MEMORY_CHUNK_MAX_CHARACTERS:
            current = _Span(current.start, span.end)
            if current.end - current.start >= TOPIC_MEMORY_CHUNK_TARGET_CHARACTERS:
                assembled.append(current)
                current = None
            continue
        assembled.append(current)
        current = span
    if current is not None:
        assembled.append(current)

    if (
        len(assembled) >= 2
        and not assembled[-1].forced
        and assembled[-1].end - assembled[-1].start < TOPIC_MEMORY_CHUNK_MIN_TAIL_CHARACTERS
        and assembled[-1].end - assembled[-2].start <= TOPIC_MEMORY_CHUNK_MAX_CHARACTERS
    ):
        tail = assembled.pop()
        previous = assembled.pop()
        assembled.append(_Span(previous.start, tail.end))

    return tuple(
        TopicMemoryChunk(
            ordinal=ordinal,
            start_offset=span.start,
            end_offset=span.end,
            text=detail[span.start : span.end],
        )
        for ordinal, span in enumerate(assembled)
    )


def _semantic_spans(detail: str) -> tuple[_Span, ...]:
    boundaries = {0, len(detail)}
    boundaries.update(match.start() for match in _BLOCK_BOUNDARY.finditer(detail))
    blocks = tuple(_trimmed_span(detail, start, end) for start, end in pairwise(sorted(boundaries)))
    spans: list[_Span] = []
    for block in blocks:
        if block is None:
            continue
        if block.end - block.start <= TOPIC_MEMORY_CHUNK_MAX_CHARACTERS:
            spans.append(block)
            continue
        spans.extend(_split_oversized_block(detail, block.start, block.end))
    return tuple(spans)


def _split_oversized_block(detail: str, start: int, end: int) -> tuple[_Span, ...]:
    sentence_boundaries = {start, end}
    sentence_boundaries.update(start + match.end() for match in _SENTENCE_BOUNDARY.finditer(detail[start:end]))
    points = sorted(sentence_boundaries)
    sentences = tuple(_trimmed_span(detail, left, right) for left, right in pairwise(points))
    spans: list[_Span] = []
    for sentence in sentences:
        if sentence is None:
            continue
        if sentence.end - sentence.start <= TOPIC_MEMORY_CHUNK_MAX_CHARACTERS:
            spans.append(sentence)
            continue
        position = sentence.start
        while position < sentence.end:
            window_end = min(position + TOPIC_MEMORY_CHUNK_MAX_CHARACTERS, sentence.end)
            spans.append(_Span(position, window_end, forced=True))
            if window_end == sentence.end:
                break
            position = window_end - TOPIC_MEMORY_CHUNK_OVERLAP_CHARACTERS
    return tuple(spans)


def _trimmed_span(detail: str, start: int, end: int) -> _Span | None:
    while start < end and detail[start].isspace():
        start += 1
    while end > start and detail[end - 1].isspace():
        end -= 1
    return None if start == end else _Span(start, end)


__all__ = [
    "TOPIC_MEMORY_CHUNK_MAX_CHARACTERS",
    "TOPIC_MEMORY_CHUNK_MIN_TAIL_CHARACTERS",
    "TOPIC_MEMORY_CHUNK_OVERLAP_CHARACTERS",
    "TOPIC_MEMORY_CHUNK_POLICY_VERSION",
    "TOPIC_MEMORY_CHUNK_TARGET_CHARACTERS",
    "chunk_topic_memory_detail",
    "prepare_topic_memory_projection",
]
