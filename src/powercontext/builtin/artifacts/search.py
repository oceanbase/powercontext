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

"""Shared deterministic lexical and vector admission for built-in Artifact projections."""

from __future__ import annotations

import logging
import math
import re
import sys
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from powercontext.artifacts.search import ChannelScore

if TYPE_CHECKING:
    import jieba

# Bump when document segmentation or token encoding changes; query stopwords do not affect projections.
TEXT_ANALYZER_VERSION = "jieba-0.42.1-words-v2"

_FTS_MIN_QUERY_COVERAGE = 0.25
_FTS_MIN_MATCHED_TERMS = 2
_FTS_SHORT_QUERY_MAX_TERMS = 2
_MIN_SEMANTIC_SIMILARITY = 0.3
# Reject subnormal normalized weights. This conservative boundary leaves
# headroom for RRF division and prevents more extreme ratios from underflowing.
_MIN_NORMALIZED_RECALL_CHANNEL_WEIGHT = sys.float_info.min

_NEGATIONS = frozenset([
    "no",
    "not",
    "never",
    "neither",
    "nor",
    "without",
    "cannot",
    "can't",
    "don't",
    "doesn't",
    "didn't",
    "won't",
    "shan't",
    "wouldn't",
    "shouldn't",
    "couldn't",
    "mustn't",
    "daren't",
    "needn't",
    "oughtn't",
    "mightn't",
    "isn't",
    "aren't",
    "wasn't",
    "weren't",
    "hasn't",
    "haven't",
    "hadn't",
    "不",
    "没",
    "没有",
    "未",
    "无",
    "非",
    "别",
    "莫",
    "勿",
    "不要",
    "不能",
    "不得",
    "无需",
    "不必",
    "不是",
    "并非",
])
_HAN_RANGES = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f"
_LEXICAL_RUN = re.compile(rf"([{_HAN_RANGES}]+)|[^{_HAN_RANGES}]+")
_WORD = re.compile(r"\w+(?:['’]\w+)*")  # noqa: RUF001 - normalize typographic apostrophes within words.


@lru_cache(maxsize=1)
def _query_stopwords() -> frozenset[str]:
    data = Path(__file__).with_name("lexical_data")
    return (
        frozenset(
            unicodedata.normalize("NFC", word).casefold()
            for name in ("en.txt", "zh.txt")
            for word in (data / name).read_text(encoding="utf-8").split()
        )
        - _NEGATIONS
    )


@lru_cache(maxsize=1)
def _tokenizer() -> jieba.Tokenizer:
    import jieba

    jieba.default_logger.setLevel(logging.WARNING)
    return jieba.Tokenizer()


_QUOTED_QUERY_TEXT = re.compile(
    r"""(`[^`]*`|"[^"]*"|“[^”]*”|‘(?:[^‘’]|(?<=\w)’(?=\w))*’|"""  # noqa: RUF001
    r"""(?<!\w)'(?:[^']|(?<=\w)'(?=\w))*'(?!\w))"""
)
_EXECUTION_SENTENCE = re.compile(
    r"(?:use only (?:the )?(?:(?:context|evidence|information) (?:already )?"
    r"(?:supplied|provided|available)(?: to you)?|(?:supplied|provided|available) (?:context|evidence|information))"
    r"|answer (?:concisely|briefly)(?: using (?:the )?(?:available|supplied|provided) evidence"
    r"| and (?:cite|identify) (?:the )?(?:artifact|source|evidence) references?"
    r" from (?:the )?(?:available|supplied|provided) context)?"
    r"|if (?:the )?(?:facts|evidence|information) (?:are|is) (?:absent|missing|unavailable), "
    r"(?:say|answer|respond with) unknown"
    r"|if no (?:reliable )?(?:context|evidence|information) is (?:present|available), "
    r"(?:say so|(?:say|answer|respond with) unknown))"
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

    RFC 1560 threads this value into each searchable family's search to override its floor.
    Round zero passes ``None`` to keep the historical defaults.
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


def unit_l2_cosine_similarity(distance: float) -> float:
    """Convert L2 distance between unit vectors to cosine similarity."""

    return max(-1.0, min(1.0, 1.0 - distance**2 / 2.0))


@dataclass(frozen=True)
class RecallChannelWeights:
    """Normalized relative weights for lexical and semantic recall channels.

    Inputs express a ratio. The stored weights have a mean of one, so scaling both
    configured values by the same factor cannot change a public RRF score.
    """

    fts: float = 1.0
    vector: float = 1.0

    def __post_init__(self) -> None:
        if isinstance(self.fts, bool) or isinstance(self.vector, bool):
            raise TypeError("recall channel weights must be numbers")  # noqa: TRY003
        if not math.isfinite(self.fts) or not math.isfinite(self.vector):
            raise ValueError("recall channel weights must be finite")  # noqa: TRY003
        if self.fts < 0.0 or self.vector < 0.0:
            raise ValueError("recall channel weights must be non-negative")  # noqa: TRY003
        total = self.fts + self.vector
        if not math.isfinite(total):
            raise ValueError("recall channel weight total must be finite")  # noqa: TRY003
        if total <= 0.0:
            raise ValueError("at least one recall channel weight must be positive")  # noqa: TRY003
        # Divide each input before scaling so a finite, positive subnormal total
        # cannot overflow through an intermediate ``2.0 / total`` value.
        normalized_fts = 2.0 * (self.fts / total)
        normalized_vector = 2.0 * (self.vector / total)
        if not math.isfinite(normalized_fts) or not math.isfinite(normalized_vector):
            raise ValueError("normalized recall channel weights must be finite")  # noqa: TRY003
        if (self.fts > 0.0 and normalized_fts < _MIN_NORMALIZED_RECALL_CHANNEL_WEIGHT) or (
            self.vector > 0.0 and normalized_vector < _MIN_NORMALIZED_RECALL_CHANNEL_WEIGHT
        ):
            raise ValueError(  # noqa: TRY003
                "normalized non-zero recall channel weights must be at least "
                "the smallest normal IEEE 754 binary64 value"
            )
        object.__setattr__(self, "fts", normalized_fts)
        object.__setattr__(self, "vector", normalized_vector)


DEFAULT_RECALL_CHANNEL_WEIGHTS = RecallChannelWeights()


@dataclass(frozen=True)
class AdmissionCounts:
    """Per-family, per-scope admission accounting for one search.

    ``retrieved`` counts candidates actually observed by this search, including
    any bounded rejection evidence. ``admitted`` counts observed candidates that
    pass the floor. These are not collection totals. A Family may provide
    ``rejected`` when the two observations use different bounded pools; it counts
    actual rejection evidence, excluding candidate-pool truncation. Topic FTS
    observes at most one rejected Topic per channel; Experience observes at most
    one rejected match in its ranked prefix. Both support recovery planning without collection counts.

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
    """Analyze document words without filtering stopwords or losing word frequency."""

    return " ".join(term for term, _start, _end in analyze_text_with_spans(value))


def lexical_query_words(value: str) -> tuple[str, ...]:
    """Filter unquoted stopwords while retaining quoted identifiers and negation."""
    content: list[str] = []
    for index, part in enumerate(_QUOTED_QUERY_TEXT.split(unicodedata.normalize("NFC", value).casefold())):
        if index % 2:
            content.extend(word for word, _start, _end in lexical_terms_with_spans(part[1:-1]))
        else:
            sentences = re.split(r"(?<=[.!?])\s+|\n+", part)
            content.extend(
                word
                for sentence in sentences
                if not _is_execution_sentence(sentence)
                for word, _start, _end in lexical_terms_with_spans(sentence)
                if word not in _query_stopwords()
            )
    if not content and _is_execution_sentence(" ".join(value.casefold().split())):
        content = [word for word, _start, _end in lexical_terms_with_spans(value) if word not in _query_stopwords()]
    return tuple(content)


def analyze_fts_query(value: str) -> str:
    """Analyze query evidence with the same word encoding as stored projections."""
    return " ".join(_encode_term(word) for word in lexical_query_words(value))


def _is_execution_sentence(value: str) -> bool:
    sentence = " ".join(value.split()).removesuffix(".")
    if _EXECUTION_SENTENCE.fullmatch(sentence):
        return True
    for prefix in ("do not ", "don't ", "never ", "avoid "):
        if sentence.startswith(prefix):
            actions = re.split(r",\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+", sentence[len(prefix) :])
            return all(_EXECUTION_ACTION.fullmatch(action) is not None for action in actions)
    return False


def lexical_terms_with_spans(value: str) -> tuple[tuple[str, int, int], ...]:
    """Segment Chinese with Jieba, retaining Latin identifiers and normalized offsets."""
    normalized = unicodedata.normalize("NFC", value).casefold()
    terms: list[tuple[str, int, int]] = []
    for run in _LEXICAL_RUN.finditer(normalized):
        if run[1] is not None:
            words = _tokenizer().tokenize(run[0], HMM=False)
        else:
            words = ((word[0].replace("’", "'"), word.start(), word.end()) for word in _WORD.finditer(run[0]))  # noqa: RUF001
        terms.extend((word, run.start() + start, run.start() + end) for word, start, end in words)
    return tuple(terms)


def _encode_term(word: str) -> str:
    # Native FULLTEXT stopword lists also discard ordinary Latin words such as
    # "the" and "www". Encode every word so only our query analyzer filters it.
    raw = word.encode("utf-8")
    return "pcu" + raw.hex() if len(raw) <= 40 else "pch" + sha256(raw).hexdigest()


def analyze_text_with_spans(value: str) -> tuple[tuple[str, int, int], ...]:
    """Return backend-safe words and offsets in NFC+casefold text."""
    return tuple((_encode_term(word), start, end) for word, start, end in lexical_terms_with_spans(value))


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

    return admits_fts_projection(query, analyze_text(text), floor=floor)


def admits_fts_projection(query: str, projection: str, /, *, floor: AdmissionFloor | None = None) -> bool:
    """Apply admission to already analyzed words without treating them as raw text."""
    query_terms, required_matches = fts_query_requirements(query, floor=floor)
    return bool(query_terms) and len(set(query_terms).intersection(projection.split())) >= required_matches


__all__ = [
    "DEFAULT_ADMISSION_FLOOR",
    "TEXT_ANALYZER_VERSION",
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
    "unit_l2_cosine_similarity",
]
