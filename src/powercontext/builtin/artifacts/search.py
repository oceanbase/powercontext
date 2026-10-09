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
import re
import unicodedata
from dataclasses import dataclass
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from powercontext.artifacts.search import ChannelScore

_FTS_MIN_QUERY_COVERAGE = 0.25
_FTS_MIN_MATCHED_TERMS = 2
_FTS_SHORT_QUERY_MAX_TERMS = 2
_MIN_SEMANTIC_SIMILARITY = 0.3

# Query-only normalization: persisted Analyzer v1 projections remain unchanged. Negations
# are deliberately absent; domain constraints such as "without a backup" remain evidence.
_QUERY_FUNCTION_WORDS = frozenset([
    "a",
    "an",
    "the",
    "and",
    "or",
    "for",
    "of",
    "to",
    "in",
    "on",
    "at",
    "by",
    "from",
    "with",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "what",
    "which",
    "who",
    "how",
    "why",
    "when",
    "where",
    "do",
    "does",
    "did",
    "can",
    "could",
    "should",
    "would",
    "i",
    "we",
    "you",
    "it",
    "its",
    "our",
    "your",
    "this",
    "that",
    "these",
    "those",
    "please",
])
_QUOTED_QUERY_TEXT = re.compile(r"""(`[^`]*`|"[^"]*"|“[^”]*”|‘[^’]*’|(?<!\w)'[^']*'(?!\w))""")  # noqa: RUF001
_EXECUTION_SENTENCE = re.compile(
    r"(?:use only (?:the )?(?:(?:context|evidence|information) (?:already )?"
    r"(?:supplied|provided|available)(?: to you)?|(?:supplied|provided|available) (?:context|evidence|information))"
    r"|answer (?:concisely|briefly)(?: using (?:the )?(?:available|supplied|provided) evidence)?"
    r"|if (?:the )?(?:facts|evidence|information) (?:are|is) (?:absent|missing|unavailable), "
    r"(?:say|answer|respond with) unknown)"
)
_EXECUTION_ACTION = re.compile(
    r"(?:(?:call|invoke|use) (?:any |external |available )?tools"
    r"|read (?:any |local )?files|inspect (?:old |previous |prior )?sessions|delegate"
    r"|browsing websites|running commands|accessing documents|querying external services"
    r"|editing repositories|creating tasks|starting background work)"
)


@dataclass(frozen=True)
class AdmissionFloor:
    """Fusion-time admission thresholds shared by every participating family.

    The defaults MUST equal the historical module constants — ``0.25`` and ``2`` in this
    module and ``0.3`` mirrored by ``memory/fusion.py`` and ``topic_memory/fusion.py`` — so
    that a ``floor=None`` / ``admission=None`` call reproduces today's behaviour bit for bit.

    This type plays the ``RecallAdmissionPolicy`` role described by RFC 1560: it is the value
    threaded into each searchable family's search to override its floor. Passing ``None``
    (the historical default) is therefore equivalent to the RFC's ``RecallAdmissionPolicy()``
    with both overrides unset, which is exactly what round 0 does.
    """

    lexical_coverage: float = _FTS_MIN_QUERY_COVERAGE
    lexical_min_matched_terms: int = _FTS_MIN_MATCHED_TERMS
    min_semantic_similarity: float = _MIN_SEMANTIC_SIMILARITY


DEFAULT_ADMISSION_FLOOR = AdmissionFloor()


class EmptyFilters(BaseModel):
    """An explicitly empty filter object for families without filter fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


class LexicalSearchAdmission(BaseModel):
    """Strict lexical admission controls for text-only Artifact searches."""

    model_config = ConfigDict(extra="forbid", strict=True)

    lexical_coverage: float = Field(default=_FTS_MIN_QUERY_COVERAGE, ge=0, le=1, allow_inf_nan=False)
    lexical_min_matched_terms: StrictInt = Field(default=_FTS_MIN_MATCHED_TERMS, ge=1)

    @field_validator("*", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("omit the field instead of sending null")  # noqa: TRY003
        return value

    def as_floor(self) -> AdmissionFloor:
        """Adapt the request controls to the existing lexical admission contract."""

        return AdmissionFloor(
            lexical_coverage=self.lexical_coverage,
            lexical_min_matched_terms=self.lexical_min_matched_terms,
        )


class InvalidSearchScore(RuntimeError):
    """The backend returned an unavailable or invalid lexical relevance value."""


class UnsupportedScoreMetric(RuntimeError):
    """The backend did not identify a supported lexical score metric."""


def lexical_search_score(raw: object, metric: object, /) -> tuple[float, ChannelScore]:
    """Normalize an explicit backend metric while preserving its true raw score."""

    if not isinstance(metric, str) or metric not in ("sqlite_bm25", "oceanbase_match"):
        raise UnsupportedScoreMetric(f"unsupported lexical score metric: {metric!r}")  # noqa: TRY003
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise InvalidSearchScore("lexical raw score must be a finite number")  # noqa: TRY003
    try:
        raw_score = float(raw)
    except OverflowError as error:
        raise InvalidSearchScore("lexical raw score must be finite") from error  # noqa: TRY003
    relevance = -raw_score if metric == "sqlite_bm25" else raw_score
    if not math.isfinite(relevance) or relevance < 0:
        raise InvalidSearchScore("lexical relevance must be finite and nonnegative")  # noqa: TRY003
    # This form avoids overflow in 1 + relevance for the largest finite values.
    normalized = relevance / (1 + relevance) if relevance <= 1 else 1 / (1 + 1 / relevance)
    return normalized, ChannelScore(
        raw=raw_score,
        metric=metric,
        higher_is_better=metric == "oceanbase_match",
    )


@dataclass(frozen=True)
class AdmissionCounts:
    """Per-family, per-scope admission accounting for one search.

    ``retrieved`` is what the backend returned *before* the admission floor was applied;
    ``admitted`` is what survived it. A family may also provide ``rejected`` when those two
    counts are not measured on a comparable basis, for example when the trace keeps an
    unbounded pre-admission count but the delivered candidate pool is capped. ``rejected``
    is the number of same-search candidates rejected by the admission floor, excluding
    candidate-pool truncation.

    These are plain aggregate integers with no candidate identity, no query text and no
    per-entry attribution, so the value is safe to carry in a trace and safe to hand to the
    Runtime without touching HTTP or persistence.

    It lives next to :class:`AdmissionFloor` in ``artifacts/search.py`` because that is the
    only module importable by ``artifacts/**``, ``persistence/**`` and ``runtime/**`` at once
    without a layering violation: the counts are produced under ``artifacts/`` and
    ``persistence/`` and consumed under ``runtime/``.
    """

    family: str = ""
    scope_id: str = ""
    retrieved: int = 0
    admitted: int = 0
    rejected: int | None = None


def analyze_text(value: str) -> str:
    """Apply Analyzer v1 and return space-delimited backend-safe terms."""

    return " ".join(term for term, _start, _end in analyze_text_with_spans(value))


def analyze_fts_query(value: str) -> str:
    """Extract lexical evidence terms without changing stored text or semantic queries.

    Recognize only standalone, generic English execution instructions accompanying other
    query content. Preserve quoted text, domain-specific directives and unknown phrasing.
    Unquoted function words cannot supply the sole overlap during a relaxed recall-gate round.
    """

    content: list[str] = []
    quoted_terms: set[str] = set()
    for index, part in enumerate(_QUOTED_QUERY_TEXT.split(unicodedata.normalize("NFC", value).casefold())):
        if index % 2:
            content.append(part)
            quoted_terms.update(analyze_text(part).split())
        else:
            sentences = re.split(r"(?<=[.!?])\s+|\n+", part)
            content.extend(sentence for sentence in sentences if not _is_execution_sentence(sentence))
    # A search consisting solely of a directive may be looking up that very policy.
    terms = analyze_text(" ".join(content) if any(sentence.strip() for sentence in content) else value).split()
    if len(set(terms)) > _FTS_SHORT_QUERY_MAX_TERMS:
        meaningful = [term for term in terms if term in quoted_terms or term not in _QUERY_FUNCTION_WORDS]
        if meaningful:
            terms = meaningful
    return " ".join(terms)


def _is_execution_sentence(value: str) -> bool:
    sentence = " ".join(value.split()).removesuffix(".")
    if _EXECUTION_SENTENCE.fullmatch(sentence):
        return True
    for prefix in ("do not ", "don't ", "never ", "avoid "):
        if sentence.startswith(prefix):
            actions = re.split(r",\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+", sentence[len(prefix) :])
            return all(_EXECUTION_ACTION.fullmatch(action) is not None for action in actions)
    return False


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


def fts_query_requirements(value: str, /, *, floor: AdmissionFloor | None = None) -> tuple[tuple[str, ...], int]:
    """Return distinct lexical evidence terms and the shared admission threshold.

    Candidate retrieval and admission use the same normalized query. Coverage applies to
    all remaining terms, without a length cap. A supplied ``floor`` only
    relaxes the score-style coverage requirement; the term-count floor is never below one, so a
    candidate must still share at least one real Analyzer term. For a short query (two terms or
    fewer) the term side is already one, so lowering the floor there is a no-op.
    """

    query_terms = tuple(sorted(set(analyze_fts_query(value).split())))
    if not query_terms:
        return (), 0
    coverage = _FTS_MIN_QUERY_COVERAGE if floor is None else floor.lexical_coverage
    min_matched = _FTS_MIN_MATCHED_TERMS if floor is None else floor.lexical_min_matched_terms
    required_matches = (
        1
        if len(query_terms) <= _FTS_SHORT_QUERY_MAX_TERMS
        else max(
            min_matched,
            math.ceil(len(query_terms) * coverage),
        )
    )
    return query_terms, required_matches


def fts_match_query(value: str) -> str | None:
    """Build a MATCH expression solely from normalized lexical query tokens."""

    terms = tuple(sorted(set(analyze_fts_query(value).split())))
    if not terms:
        return None
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in terms)


def admits_fts_text(query: str, text: str, /, *, floor: AdmissionFloor | None = None) -> bool:
    """Return whether one lexical candidate covers enough distinct query terms.

    ``floor=None`` uses this module's historical constants; a supplied ``floor`` relaxes the
    shared admission requirement exactly as ``fts_query_requirements`` documents.
    """

    query_terms, required_matches = fts_query_requirements(query, floor=floor)
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
    "DEFAULT_ADMISSION_FLOOR",
    "AdmissionCounts",
    "AdmissionFloor",
    "EmptyFilters",
    "InvalidSearchScore",
    "LexicalSearchAdmission",
    "UnsupportedScoreMetric",
    "admits_fts_text",
    "analyze_fts_query",
    "analyze_text",
    "analyze_text_with_spans",
    "fts_match_query",
    "fts_query_requirements",
    "lexical_search_score",
]
