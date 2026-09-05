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

"""Shared deterministic lexical analysis for built-in Artifact projections."""

from __future__ import annotations

import math
import unicodedata
from itertools import pairwise

_FTS_MIN_QUERY_COVERAGE = 0.25
_FTS_MIN_MATCHED_TERMS = 2
_FTS_SHORT_QUERY_MAX_TERMS = 2


def analyze_text(value: str) -> str:
    """Apply Analyzer v1 and return space-delimited backend-safe terms."""

    return " ".join(term for term, _start, _end in analyze_text_with_spans(value))


def analyze_text_with_spans(value: str) -> tuple[tuple[str, int, int], ...]:
    """Return Analyzer v1 terms with offsets in the NFC+casefold text."""

    normalized = unicodedata.normalize("NFC", value).casefold()
    terms: list[tuple[str, int, int]] = []
    word_start: int | None = None
    cjk: list[tuple[str, int]] = []

    def flush_word(end: int) -> None:
        nonlocal word_start
        if word_start is not None:
            terms.append((normalized[word_start:end], word_start, end))
            word_start = None

    def flush_cjk() -> None:
        if not cjk:
            return
        terms.extend((f"u_{ord(character):x}", position, position + 1) for character, position in cjk)
        terms.extend(
            (
                f"b_{ord(left[0]):x}_{ord(right[0]):x}",
                left[1],
                right[1] + 1,
            )
            for left, right in pairwise(cjk)
        )
        cjk.clear()

    for position, character in enumerate(normalized):
        if _is_cjk(character):
            flush_word(position)
            cjk.append((character, position))
        elif character.isalnum() or character == "_":
            flush_cjk()
            if word_start is None:
                word_start = position
        else:
            flush_word(position)
            flush_cjk()
    flush_word(len(normalized))
    flush_cjk()
    return tuple(terms)


def fts_query_requirements(value: str) -> tuple[tuple[str, ...], int]:
    """Return distinct Analyzer terms and the shared admission threshold."""

    query_terms = tuple(sorted(set(analyze_text(value).split())))
    if not query_terms:
        return (), 0
    required_matches = (
        1
        if len(query_terms) <= _FTS_SHORT_QUERY_MAX_TERMS
        else max(
            _FTS_MIN_MATCHED_TERMS,
            math.ceil(len(query_terms) * _FTS_MIN_QUERY_COVERAGE),
        )
    )
    return query_terms, required_matches


def fts_match_query(value: str) -> str | None:
    """Build a MATCH expression solely from Analyzer v1 output tokens."""

    analyzed = analyze_text(value)
    if not analyzed:
        return None
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in analyzed.split())


def admits_fts_text(query: str, text: str, /) -> bool:
    """Return whether one lexical candidate covers enough distinct query terms."""

    query_terms, required_matches = fts_query_requirements(query)
    if not query_terms:
        return False
    return len(set(query_terms).intersection(analyze_text(text).split())) >= required_matches


def _is_cjk(character: str) -> bool:
    point = ord(character)
    return (
        0x3400 <= point <= 0x4DBF
        or 0x4E00 <= point <= 0x9FFF
        or 0xF900 <= point <= 0xFAFF
        or 0x20000 <= point <= 0x2FA1F
    )


__all__ = [
    "admits_fts_text",
    "analyze_text",
    "analyze_text_with_spans",
    "fts_match_query",
    "fts_query_requirements",
]
