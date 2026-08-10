"""Deterministic answer, evidence-retrieval, and latency metrics."""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

_TOKEN = re.compile(r"\w+", re.UNICODE)
_ARTICLES = re.compile(r"\b(a|an|the)\b", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def normalize_answer(value: object) -> str:
    """Apply the standard QA normalization used for exact match."""

    text = str(value).lower()
    text = "".join(character if character.isalnum() or character.isspace() else " " for character in text)
    text = _ARTICLES.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def token_f1(prediction: object, reference: object) -> float:
    """Calculate multiset token F1 after standard QA normalization."""

    predicted = normalize_answer(prediction).split()
    expected = normalize_answer(reference).split()
    if not predicted or not expected:
        return float(predicted == expected)
    common = Counter(predicted) & Counter(expected)
    overlap = sum(common.values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(predicted)
    recall = overlap / len(expected)
    return 2 * precision * recall / (precision + recall)


def set_token_f1(prediction: object, reference: object) -> float:
    """Calculate F1 over unique normalized tokens."""

    predicted = set(_simple_tokens(prediction))
    expected = set(_simple_tokens(reference))
    if not predicted or not expected:
        return float(predicted == expected)
    overlap = len(predicted & expected)
    if overlap == 0:
        return 0.0
    precision = overlap / len(predicted)
    recall = overlap / len(expected)
    return 2 * precision * recall / (precision + recall)


def exact_match(prediction: object, reference: object) -> float:
    """Return normalized exact-match accuracy for one answer."""

    return float(normalize_answer(prediction) == normalize_answer(reference))


def bleu1(prediction: object, reference: object) -> float:
    """Calculate sentence BLEU-1 with clipped precision and brevity penalty."""

    predicted = _TOKEN.findall(str(prediction).lower())
    expected = _TOKEN.findall(str(reference).lower())
    if not predicted or not expected:
        return 0.0
    clipped = sum((Counter(predicted) & Counter(expected)).values())
    precision = clipped / len(predicted)
    if precision == 0:
        return 0.0
    brevity_penalty = 1.0 if len(predicted) > len(expected) else math.exp(1 - len(expected) / len(predicted))
    return brevity_penalty * precision


def retrieval_metrics(
    *,
    evidence_sessions: Sequence[str],
    hit_source_ids: Sequence[Sequence[str]],
) -> dict[str, float]:
    """Score session-level provenance because Memory cites Source sessions, not dialogue turns."""

    expected = set(evidence_sessions)
    if not expected:
        return {"evidence_hit": 0.0, "evidence_recall": 0.0, "evidence_mrr": 0.0}
    observed: set[str] = set()
    reciprocal_rank = 0.0
    for rank, source_ids in enumerate(hit_source_ids, start=1):
        sessions = {_source_session(source_id) for source_id in source_ids}
        sessions.discard(None)
        matched = expected.intersection(sessions)
        observed.update(matched)
        if matched and reciprocal_rank == 0.0:
            reciprocal_rank = 1 / rank
    return {
        "evidence_hit": float(bool(observed)),
        "evidence_recall": len(observed) / len(expected),
        "evidence_mrr": reciprocal_rank,
    }


def summarize_observations(observations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate raw per-question observations overall and by LoCoMo category."""

    values = tuple(observations)
    groups: dict[str, tuple[Mapping[str, Any], ...]] = {"overall": values}
    categories = sorted({int(value["category"]) for value in values})
    groups.update({
        f"category_{category}": tuple(v for v in values if int(v["category"]) == category) for category in categories
    })
    return {name: _summarize_group(group) for name, group in groups.items()}


def diagnose_observations(observations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Expose retrieval-conditioned answer quality and recorded provider usage."""

    values = tuple(observations)
    completed = tuple(value for value in values if value.get("status") == "ok")
    hit = tuple(value for value in completed if float(value["metrics"]["evidence_hit"]) == 1.0)
    miss = tuple(value for value in completed if float(value["metrics"]["evidence_hit"]) == 0.0)
    wrong = tuple(value for value in completed if float(value["metrics"]["llm_judge"]) == 0.0)
    has_answer_fallback = any("answer_fallback" in value for value in completed)
    retry_phases = ("search", "rerank", "answer", "judge")
    usage_stages = ("rerank", "answer", "judge")
    if has_answer_fallback:
        retry_phases = (*retry_phases, "answer_fallback")
        usage_stages = (*usage_stages, "answer_fallback")
    buckets = (
        ("rank_1", lambda score: score == 1.0),
        ("rank_2_5", lambda score: 0.2 <= score < 1.0),
        ("rank_6_10", lambda score: 0.1 <= score < 0.2),
        ("rank_11_30", lambda score: 0.0 < score < 0.1),
        ("miss", lambda score: score == 0.0),
    )
    diagnostics: dict[str, Any] = {
        "unknown_answer_count": sum(
            normalize_answer(value.get("generated_answer", "")) == "unknown" for value in completed
        ),
        "unknown_answer_rate": _rate(
            sum(normalize_answer(value.get("generated_answer", "")) == "unknown" for value in completed),
            len(values),
        ),
        "retrieval_conditioned": {
            "hit": _condition_summary(hit),
            "miss": _condition_summary(miss),
        },
        "wrong_answer_count": len(wrong),
        "wrong_with_evidence_hit_count": sum(float(value["metrics"]["evidence_hit"]) == 1.0 for value in wrong),
        "wrong_with_evidence_hit_rate": _rate(
            sum(float(value["metrics"]["evidence_hit"]) == 1.0 for value in wrong),
            len(wrong),
        ),
        "evidence_rank_buckets": {
            name: _condition_summary(
                tuple(value for value in completed if predicate(float(value["metrics"]["evidence_mrr"])))
            )
            for name, predicate in buckets
        },
        "transient_retries": {
            phase: sum(int(value.get("transient_retries", {}).get(phase, 0)) for value in completed)
            for phase in retry_phases
        },
        "model_usage": {
            stage: {
                field: sum(int(value.get("usage", {}).get(stage, {}).get(field) or 0) for value in completed)
                for field in ("requests", "input_tokens", "output_tokens")
            }
            for stage in usage_stages
        },
    }
    if has_answer_fallback:
        triggered = tuple(value for value in completed if value["answer_fallback"]["triggered"])
        resolved_count = sum(normalize_answer(value.get("generated_answer", "")) != "unknown" for value in triggered)
        diagnostics["answer_fallback"] = {
            "trigger": "normalized-answer-equals-unknown",
            "triggered_count": len(triggered),
            "triggered_rate": _rate(len(triggered), len(completed)),
            "resolved_count": resolved_count,
            "resolved_rate": _rate(resolved_count, len(triggered)),
            "llm_judge_accuracy": _rate(
                sum(float(value["metrics"]["llm_judge"]) for value in triggered),
                len(triggered),
            ),
        }
    return diagnostics


def percentile(values: Sequence[float], percentage: float) -> float | None:
    """Return a linearly interpolated percentile without third-party dependencies."""

    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentage
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _summarize_group(values: tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
    completed = tuple(value for value in values if value.get("status") == "ok")
    metrics = (
        "exact_match",
        "token_f1",
        "reference_set_f1",
        "bleu1",
        "llm_judge",
        "evidence_hit",
        "evidence_recall",
        "evidence_mrr",
        "candidate_evidence_hit",
        "candidate_evidence_recall",
        "candidate_evidence_mrr",
    )
    summary: dict[str, Any] = {
        "question_count": len(values),
        "completed_count": len(completed),
        "error_count": len(values) - len(completed),
    }
    for metric in metrics:
        # Errors count as zero so headline accuracy always uses the intended denominator.
        scores = [
            float(value.get("metrics", {}).get(metric, 0.0)) if value.get("status") == "ok" else 0.0 for value in values
        ]
        summary[metric] = statistics.fmean(scores) if scores else 0.0
    phases = ("search", "rerank", "answer", "judge", "total")
    if any("answer_fallback" in value.get("latency_ms", {}) for value in completed):
        phases = ("answer_fallback", *phases)
    for phase in phases:
        latencies = [float(value["latency_ms"][phase]) for value in completed if phase in value.get("latency_ms", {})]
        summary[f"{phase}_latency_ms_p50"] = percentile(latencies, 0.50)
        summary[f"{phase}_latency_ms_p95"] = percentile(latencies, 0.95)
    return summary


def _condition_summary(values: tuple[Mapping[str, Any], ...]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "llm_judge_accuracy": _rate(
            sum(float(value["metrics"]["llm_judge"]) for value in values),
            len(values),
        ),
        "unknown_answer_rate": _rate(
            sum(normalize_answer(value.get("generated_answer", "")) == "unknown" for value in values),
            len(values),
        ),
    }


def _rate(numerator: float, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _simple_tokens(value: object) -> tuple[str, ...]:
    text = str(value).lower()
    for character in ".,!?:;":
        text = text.replace(character, " ")
    return tuple(text.split())


def _source_session(source_id: str) -> str | None:
    candidate = source_id.rsplit(":", maxsplit=1)[-1]
    return candidate if re.fullmatch(r"D\d+", candidate) else None


__all__ = [
    "bleu1",
    "diagnose_observations",
    "exact_match",
    "normalize_answer",
    "percentile",
    "retrieval_metrics",
    "set_token_f1",
    "summarize_observations",
    "token_f1",
]
