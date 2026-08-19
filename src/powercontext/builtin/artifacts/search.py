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

    normalized = unicodedata.normalize("NFC", value).casefold()
    terms: list[str] = []
    word: list[str] = []
    cjk: list[str] = []

    def flush_word() -> None:
        if word:
            terms.append("".join(word))
            word.clear()

    def flush_cjk() -> None:
        if not cjk:
            return
        points = tuple(ord(character) for character in cjk)
        terms.extend(f"u_{point:x}" for point in points)
        terms.extend(f"b_{left:x}_{right:x}" for left, right in pairwise(points))
        cjk.clear()

    for character in normalized:
        if _is_cjk(character):
            flush_word()
            cjk.append(character)
        elif character.isalnum() or character == "_":
            flush_cjk()
            word.append(character)
        else:
            flush_word()
            flush_cjk()
    flush_word()
    flush_cjk()
    return " ".join(terms)


def fts_match_query(value: str) -> str | None:
    """Build a MATCH expression solely from Analyzer v1 output tokens."""

    analyzed = analyze_text(value)
    if not analyzed:
        return None
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in analyzed.split())


def admits_fts_text(query: str, text: str, /) -> bool:
    """Return whether one lexical candidate covers enough distinct query terms."""

    query_terms = frozenset(analyze_text(query).split())
    if not query_terms:
        return False
    required_matches = (
        1
        if len(query_terms) <= _FTS_SHORT_QUERY_MAX_TERMS
        else max(
            _FTS_MIN_MATCHED_TERMS,
            math.ceil(len(query_terms) * _FTS_MIN_QUERY_COVERAGE),
        )
    )
    return len(query_terms.intersection(analyze_text(text).split())) >= required_matches


def _is_cjk(character: str) -> bool:
    point = ord(character)
    return (
        0x3400 <= point <= 0x4DBF
        or 0x4E00 <= point <= 0x9FFF
        or 0xF900 <= point <= 0xFAFF
        or 0x20000 <= point <= 0x2FA1F
    )


__all__ = ["admits_fts_text", "analyze_text", "fts_match_query"]
