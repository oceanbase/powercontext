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

"""Select literal, query-relevant evidence spans under a UTF-8 byte budget.

The selector never rewrites text: it splits each recalled Memory entry into
sentence spans, ranks those spans by rare-term overlap with the query, and packs
the best spans until the fully rendered context -- trust policy, envelope,
citations, offsets and bodies together -- reaches the caller's budget.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.runtime.prepared_context import MemoryEntryAddress, PreparedMemoryCandidates
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

_BEGIN_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END_MARKER = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
_LONG_SENTENCE = 600
_CLAUSE = re.compile(r"(?<=[,;:])\s+")


@dataclass(frozen=True)
class PreparedContextFragment:
    """One literal span of one Memory entry version."""

    address: MemoryEntryAddress
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _Span:
    group: int
    order: int
    address: MemoryEntryAddress
    scope_id: str
    memory_ref: object
    start: int
    end: int
    text: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceSelection:
    """The selected fragments, their origins and the rendered context."""

    fragments: tuple[PreparedContextFragment, ...]
    origins: tuple[MemoryEntryAddress, ...]
    content: str


def select_evidence(
    *,
    query: str,
    memory_candidates: Sequence[PreparedMemoryCandidates],
    current_scope_id: str | None,
    max_bytes: int,
) -> EvidenceSelection | None:
    """Return query-relevant fragments that fit the rendered byte budget."""

    spans = _candidate_spans(memory_candidates, current_scope_id)
    if not spans:
        return None
    scores = _score_spans(query, spans)
    if not scores:
        return None

    chosen: list[int] = []
    remaining = set(range(len(spans)))
    while remaining:
        best = max(remaining, key=lambda index: (scores[index], -index))
        remaining.discard(best)
        trial = [*chosen, best]
        if len(_render([spans[index] for index in _ordered(trial, spans)]).encode("utf-8")) > max_bytes:
            continue
        chosen.append(best)
        for neighbour in (best - 1, best + 1):
            if 0 <= neighbour < len(spans) and neighbour in remaining:
                same_document = spans[neighbour].group == spans[best].group
                if same_document:
                    scores[neighbour] *= 1.35

    if not chosen:
        return None
    fragments: list[PreparedContextFragment] = []
    origins: list[MemoryEntryAddress] = []
    rendered: list[_Span] = []
    for index in _ordered(chosen, spans):
        span = spans[index]
        fragments.append(
            PreparedContextFragment(address=span.address, start=span.start, end=span.end, text=span.text)
        )
        origins.append(span.address)
        rendered.append(span)
    return EvidenceSelection(
        fragments=tuple(fragments),
        origins=tuple(origins),
        content=_render(rendered),
    )


def _ordered(indices: Sequence[int], spans: Sequence[_Span]) -> list[int]:
    return sorted(indices, key=lambda index: (spans[index].group, spans[index].start))


def _render(spans: Sequence[_Span]) -> str:
    items = []
    for span in spans:
        items.append(
            {
                "citation": {
                    "scope_id": span.scope_id,
                    "memory_ref": span.memory_ref.model_dump(mode="json"),
                    "entry_id": span.address.entry_id,
                    "entry_version_id": span.address.entry_version_id,
                },
                "content": span.text,
                "start": span.start,
                "end": span.end,
            }
        )
    envelope = {"trust": "untrusted_history", "items": items}
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _candidate_spans(
    memory_candidates: Sequence[PreparedMemoryCandidates],
    current_scope_id: str | None,
) -> list[_Span]:
    spans: list[_Span] = []
    order = 0
    for group_index, group in enumerate(memory_candidates):
        scope_id = group.scope_id or current_scope_id
        if not scope_id:
            continue
        seen: set[tuple[str, str]] = set()
        for hit in group.hits:
            key = (hit.entry_id, hit.entry_version_id)
            if key in seen:
                continue
            seen.add(key)
            text = hit.text
            if not text.strip():
                continue
            address = MemoryEntryAddress(
                memory=ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref),
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            for start, end in _sentence_spans(text):
                body = text[start:end]
                terms = tuple(analyze_text(body).split())
                if not terms:
                    continue
                spans.append(
                    _Span(
                        group=group_index,
                        order=order,
                        address=address,
                        scope_id=scope_id,
                        memory_ref=hit.memory_ref,
                        start=start,
                        end=end,
                        text=body,
                        terms=terms,
                    )
                )
                order += 1
    return spans


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    raw: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE_BREAK.finditer(text):
        if match.start() > cursor:
            raw.append((cursor, match.start()))
        cursor = match.end()
    if cursor < len(text):
        raw.append((cursor, len(text)))

    spans: list[tuple[int, int]] = []
    for start, end in raw:
        start, end = _trim(text, start, end)
        if end <= start:
            continue
        if end - start > _LONG_SENTENCE:
            spans.extend(_split_long(text, start, end))
        else:
            spans.append((start, end))
    return spans


def _split_long(text: str, start: int, end: int) -> list[tuple[int, int]]:
    pieces: list[tuple[int, int]] = []
    cursor = start
    for match in _CLAUSE.finditer(text, start, end):
        if match.start() > cursor:
            pieces.append(_trim(text, cursor, match.start()))
        cursor = match.end()
    if cursor < end:
        pieces.append(_trim(text, cursor, end))
    if len(pieces) <= 1:
        return [(start, end)]
    merged: list[tuple[int, int]] = []
    for piece in pieces:
        if piece[1] <= piece[0]:
            continue
        if merged and piece[1] - merged[-1][0] <= 320:
            merged[-1] = (merged[-1][0], piece[1])
        else:
            merged.append(piece)
    return merged


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _score_spans(query: str, spans: Sequence[_Span]) -> dict[int, float]:
    query_terms = [term for term in dict.fromkeys(analyze_text(unicodedata.normalize("NFC", query)).split())]
    if not query_terms:
        return {}
    total = len(spans)
    document_frequency: dict[str, int] = {}
    for term in query_terms:
        document_frequency[term] = sum(1 for span in spans if term in span.terms)
    weights = {
        term: math.log(1.0 + (total / frequency)) if frequency else 0.0
        for term, frequency in document_frequency.items()
    }
    whole_query = " ".join(query_terms)
    scores: dict[int, float] = {}
    for index, span in enumerate(spans):
        term_set = set(span.terms)
        matched = [term for term in query_terms if term in term_set]
        if not matched:
            scores[index] = 0.0
            continue
        covered = sum(weights[term] for term in matched)
        coverage = len(matched) / len(query_terms)
        length_penalty = 1.0 + 0.10 * math.log1p(len(span.terms))
        phrase_bonus = 0.0
        joined = " ".join(span.terms)
        if len(query_terms) > 1 and whole_query in joined:
            phrase_bonus = 0.5 * sum(weights.values())
        scores[index] = (covered * (0.6 + coverage)) / length_penalty + phrase_bonus
    return scores
