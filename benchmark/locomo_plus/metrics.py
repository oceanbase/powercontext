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

"""Deterministic score, failure, latency, evidence, and resource accounting."""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from benchmark.locomo.metrics import percentile
from benchmark.locomo_plus.prompts import PARTIAL_CATEGORIES, category_name

FAILURE_STAGES = ("dataset", "infrastructure", "retrieval", "generation", "judge")
_CONSTRAINT_TYPES = frozenset({"causal", "state", "goal", "value"})
_ABSTENTION = re.compile(
    r"^(?:unknown\b|i (?:do not|don't) know\b|(?:the |this )?information is (?:unknown|unavailable)\b)"
    r"|\b(?:not mentioned|not provided|cannot be answered|insufficient (?:information|evidence)|"
    r"(?:conversation|evidence|context) (?:does not|doesn't) (?:mention|provide|specify|contain))\b",
    re.IGNORECASE,
)


def is_abstention(answer: str) -> bool:
    """Detect explicit unknown/insufficient-evidence phrases, not semantic refusals."""

    return bool(_ABSTENTION.search(answer.strip()))


def summarize_observations(
    observations: Iterable[Mapping[str, Any]],
    *,
    scope: str = "subset",
    planned_count: int | None = None,
    prepare_latency_ms: float | None = None,
    ingestion_usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Report scored quality separately from execution failures and unobserved cases.

    ``planned_count`` includes selected cases that failed or have not produced an
    observation. Ingestion usage is passed once for the run, never copied onto
    each question. Missing provider usage and prices remain unknown, not free.
    """

    values = tuple(observations)
    planned = len(values) if planned_count is None else planned_count
    if scope not in {"subset", "full"}:
        raise ValueError("Report scope must be subset or full")  # noqa: TRY003
    if planned < len(values):
        raise ValueError("Planned count cannot be smaller than observed count")  # noqa: TRY003
    categories = sorted({category_name(value["category"]) for value in values})
    cognitive = tuple(value for value in values if category_name(value["category"]) == "Cognitive")
    factual = tuple(value for value in values if category_name(value["category"]) != "Cognitive")
    labels = sorted({_constraint_type(value) for value in cognitive} - {""})
    report: dict[str, Any] = {
        "scope": scope,
        "result_label": "LoCoMo-Plus SUBSET result" if scope == "subset" else "LoCoMo-Plus FULL workload result",
        "aggregation": "micro average of released-category judge scores; completed-only and planned denominators",
        "overall": _summarize_group(values, planned),
        "factual": _summarize_group(factual, len(factual)),
        "cognitive": _summarize_group(cognitive, len(cognitive)),
        "by_qa_type": {
            category: _summarize_group(tuple(value for value in values if category_name(value["category"]) == category))
            for category in categories
        },
        "cognitive_constraint_labels": {
            "labeled_count": sum(bool(_constraint_type(value)) for value in cognitive),
            "unlabeled_count": sum(not _constraint_type(value) for value in cognitive),
            "source": "recorded dataset labels only; no inferred labels",
        },
        "prepare_latency_ms": prepare_latency_ms,
        "usage": _summarize_usage(values, ingestion_usage),
    }
    if labels:
        report["by_cognitive_constraint"] = {
            label: _summarize_group(tuple(value for value in cognitive if _constraint_type(value) == label))
            for label in labels
        }
    return report


def _summarize_group(values: Sequence[Mapping[str, Any]], planned: int | None = None) -> dict[str, Any]:
    planned = len(values) if planned is None else planned
    completed = tuple(value for value in values if _score(value) is not None)
    scores = [float(value["judge"]["score"]) for value in completed]
    answered = tuple(value for value in values if isinstance(value.get("generated_answer"), str))
    abstentions = sum(is_abstention(value["generated_answer"]) for value in answered)
    phases = sorted({phase for value in values for phase in value.get("latency_ms", {})})
    contexts = tuple(value["context"] for value in values if isinstance(value.get("context"), Mapping))
    citation = tuple(value["citation"] for value in values if isinstance(value.get("citation"), Mapping))
    failures = {stage: sum(_failure_stage(value) == stage for value in values) for stage in FAILURE_STAGES}
    return {
        "planned_count": planned,
        "observed_count": len(values),
        "completed_count": len(completed),
        "unobserved_count": planned - len(values),
        "failure_count": len(values) - len(completed),
        "failures_by_stage": failures,
        "failure_rates_by_stage": {stage: _rate(count, planned) for stage, count in failures.items()},
        "score_sum": sum(scores),
        "quality_rate_on_completed": _rate(sum(scores), len(completed)),
        "end_to_end_success_rate": _rate(sum(scores), planned),
        "correct_count": sum(score == 1.0 for score in scores),
        "partial_count": sum(score == 0.5 for score in scores),
        "wrong_count": sum(score == 0.0 for score in scores),
        "generated_answer_count": len(answered),
        "abstention_count": abstentions,
        "abstention_rate": _rate(abstentions, len(answered)),
        "abstention_policy": "explicit-unknown-or-insufficient-evidence-phrases-v1",
        "latency_ms": {
            phase: _distribution([value.get("latency_ms", {}).get(phase) for value in values]) for phase in phases
        },
        "context": {
            "bytes": _distribution([context.get("bytes") for context in contexts]),
            "tokens": _distribution([context.get("tokens") for context in contexts]),
            "tokenizers": sorted({str(context["tokenizer"]) for context in contexts if context.get("tokenizer")}),
            "estimated_token_observations": sum(
                context.get("tokens_estimated", str(context.get("tokenizer", "")).startswith("character-estimate"))
                is True
                for context in contexts
            ),
        },
        "citation": {
            "source_provenance_available": _availability(citation, "available"),
            "exact_source_content_available": _availability(
                [
                    {"available": value["available"] and value["exact_source_content"]}
                    for value in citation
                    if isinstance(value.get("available"), bool) and isinstance(value.get("exact_source_content"), bool)
                ],
                "available",
            ),
            "target_evidence_available": _availability(citation, "target_evidence_available"),
        },
        "retrieval": _summarize_retrieval(values),
    }


def _score(value: Mapping[str, Any]) -> float | None:
    if value.get("status") != "ok":
        return None
    score = value.get("judge", {}).get("score")
    allowed = {0, 0.5, 1} if category_name(value["category"]) in PARTIAL_CATEGORIES else {0, 1}
    if isinstance(score, bool) or not isinstance(score, (int, float)) or score not in allowed:
        return None
    return float(score)


def _failure_stage(value: Mapping[str, Any]) -> str | None:
    if _score(value) is not None:
        return None
    if value.get("status") == "ok":
        return "judge"
    stage = value.get("failure_stage") or value.get("error", {}).get("stage") or "infrastructure"
    return stage if stage in FAILURE_STAGES else "infrastructure"


def _constraint_type(value: Mapping[str, Any]) -> str:
    label = value.get("constraint_type") or value.get("relation_type")
    return str(label) if label in _CONSTRAINT_TYPES else ""


def _distribution(values: Iterable[Any]) -> dict[str, float | int | None]:
    numbers = [float(value) for value in values if _valid_number(value)]
    return {
        "count": len(numbers),
        "sum": sum(numbers) if numbers else None,
        "mean": statistics.fmean(numbers) if numbers else None,
        "p50": percentile(numbers, 0.50),
        "p95": percentile(numbers, 0.95),
    }


def _availability(values: Sequence[Mapping[str, Any]], field: str) -> dict[str, float | int | None]:
    applicable = [value[field] for value in values if isinstance(value.get(field), bool)]
    return {
        "applicable_count": len(applicable),
        "available_count": sum(applicable),
        "rate": _rate(sum(applicable), len(applicable)),
    }


def _summarize_retrieval(values: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    records = tuple(value["retrieval"] for value in values if isinstance(value.get("retrieval"), Mapping))
    metrics = sorted({name for record in records for name, value in record.items() if _valid_number(value)})
    return {name: _distribution([record.get(name) for record in records]) for name in metrics}


def _summarize_usage(values: Sequence[Mapping[str, Any]], ingestion_usage: Mapping[str, Any] | None) -> dict[str, Any]:
    by_stage: dict[str, list[Mapping[str, Any]]] = {}
    for value in values:
        for stage, usage in value.get("usage", {}).items():
            if isinstance(usage, Mapping):
                by_stage.setdefault(stage, []).append(usage)
    if ingestion_usage is not None:
        by_stage.setdefault("ingestion", []).append(ingestion_usage)
    result: dict[str, Any] = {}
    for stage, usages in sorted(by_stage.items()):
        result[stage] = {"observation_count": len(usages)}
        for field in ("requests", "input_tokens", "output_tokens", "cost_usd"):
            reported = [float(usage[field]) for usage in usages if _valid_number(usage.get(field))]
            result[stage][field] = sum(reported) if len(reported) == len(usages) else None
            result[stage][f"{field}_reported_count"] = len(reported)
            if field == "cost_usd":
                result[stage]["known_cost_usd"] = sum(reported) if reported else None
        result[stage]["pricing_versions"] = sorted({
            str(usage["pricing_version"]) for usage in usages if usage.get("pricing_version")
        })
    return result


def _valid_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _rate(numerator: float, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def render_summary(summary: Mapping[str, Any]) -> str:
    """Render inspectable Markdown with run scope and unknown accounting visible."""

    selection = summary.get("selection", {})
    configuration = summary.get("configuration", {})
    overall = summary["overall"]
    lines = [
        f"# {_cell(summary['result_label'])}",
        "",
        f"Run: {_cell(summary.get('run_id'))}. Requested profile: {_cell(selection.get('requested_profile'))}. "
        f"Result scope: {_cell(summary['scope']).upper()}. Arm: {_cell(selection.get('arm'))}.",
        "",
        f"Planned valid cases: {overall['planned_count']}; observed: {overall['observed_count']}; "
        f"unobserved: {overall['unobserved_count']}. "
        f"Excluded dataset cases: {_cell(summary.get('excluded_dataset_cases', selection.get('excluded_count')))}.",
        "",
        f"History sessions per case: {_cell(selection.get('max_history_sessions'), missing='unlimited')}; "
        f"history truncated: {_cell(selection.get('history_truncated'))}. "
        f"History policy: {_cell(selection.get('history_policy'))}.",
        "",
    ]
    if summary["scope"] == "subset":
        lines += ["**This is a subset result, not a complete published LoCoMo-Plus benchmark score.**", ""]
    lines += [
        "| Role | Configured model |",
        "| --- | --- |",
        *[
            f"| {role} | {_cell(configuration.get(field))} |"
            for role, field in (
                ("Generator", "generation_model"),
                ("Judge", "judge_model"),
                ("Embedding", "embedding_model"),
            )
        ],
        "",
        "## Scores",
        "",
        "Quality averages judge scores over completed judgments. End-to-end averages use all planned valid cases. "
        "Factual partial credit contributes 0.5 where permitted; failures are counted separately from wrong answers.",
        "",
        "| Group | Planned | Scored | Correct | Partial | Wrong | Failed | Quality | End-to-end |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    groups: list[tuple[str, Mapping[str, Any]]] = [
        (name.title(), summary[name]) for name in ("overall", "factual", "cognitive")
    ]
    groups += [(f"QA: {name}", group) for name, group in summary.get("by_qa_type", {}).items()]
    groups += [(f"Cognitive: {name}", group) for name, group in summary.get("by_cognitive_constraint", {}).items()]
    for name, group in groups:
        counts = [
            group[field]
            for field in (
                "planned_count",
                "completed_count",
                "correct_count",
                "partial_count",
                "wrong_count",
                "failure_count",
            )
        ]
        cells = [
            _cell(name),
            *map(_cell, counts),
            _percentage(group["quality_rate_on_completed"]),
            _percentage(group["end_to_end_success_rate"]),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    labels = summary.get("cognitive_constraint_labels", {})
    lines += [
        "",
        f"Cognitive labels: {_cell(labels.get('labeled_count'))} recorded, {_cell(labels.get('unlabeled_count'))} unavailable. "
        "Constraint labels are never inferred.",
        "",
        "## Failures and abstention",
        "",
        "| Failure stage | Cases | Rate of planned cases |",
        "| --- | ---: | ---: |",
        *[
            f"| {stage} | {overall['failures_by_stage'][stage]} | {_percentage(overall['failure_rates_by_stage'][stage])} |"
            for stage in FAILURE_STAGES
        ],
        "",
        f"Explicit abstentions: {overall['abstention_count']} / {overall['generated_answer_count']} generated answers "
        f"({_percentage(overall['abstention_rate'])}); phrase heuristic: `{overall['abstention_policy']}`.",
        "",
        "## Latency and context",
        "",
        f"Run preparation: {_cell(summary.get('prepare_latency_ms'))} ms. "
        f"Recorded ingestion: {_cell(summary.get('ingestion', {}).get('latency_ms'))} ms.",
        "",
        "| Phase | Recorded cases | Sum ms | p50 ms | p95 ms |",
        "| --- | ---: | ---: | ---: | ---: |",
        *[
            f"| {_cell(phase)} | {values['count']} | {_cell(values['sum'])} | {_cell(values['p50'])} | {_cell(values['p95'])} |"
            for phase, values in overall["latency_ms"].items()
        ],
        "",
        f"Context bytes: {_cell(overall['context']['bytes']['sum'])}; "
        f"context tokens: {_cell(overall['context']['tokens']['sum'])}. "
        f"Token estimates: {overall['context']['estimated_token_observations']} observations. "
        f"Tokenizers: {_cell(', '.join(overall['context']['tokenizers']) or None)}.",
        "",
        "## Evidence availability",
        "",
        "Source provenance means the context identifies an original Source. Exact source content additionally "
        "requires the original dialogue text in the generator context. Target evidence availability uses recorded "
        "Source identities and is not a claim that the response cites or applies that evidence.",
        "",
        "| Evidence measure | Applicable | Available | Rate |",
        "| --- | ---: | ---: | ---: |",
        *[
            f"| {_cell(name)} | {value['applicable_count']} | {value['available_count']} | {_percentage(value['rate'])} |"
            for name, value in overall["citation"].items()
        ],
        "",
    ]
    if overall["retrieval"]:
        lines += [
            "| Retrieval metric | Recorded cases | Mean |",
            "| --- | ---: | ---: |",
            *[
                f"| {_cell(name)} | {value['count']} | {_cell(value['mean'])} |"
                for name, value in overall["retrieval"].items()
            ],
            "",
        ]
    lines += [
        "## Model usage and cost",
        "",
        "Unknown usage or pricing remains unknown. Known cost is the sum of reported amounts only and can be incomplete. "
        "Ingestion is counted once for the run.",
        "",
        "| Stage | Requests | Input tokens | Output tokens | Total USD | Known USD | Price versions |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for stage, usage in summary["usage"].items():
        cells = [
            _cell(stage),
            *[
                _cell(usage.get(field))
                for field in ("requests", "input_tokens", "output_tokens", "cost_usd", "known_cost_usd")
            ],
            _cell(", ".join(usage.get("pricing_versions", [])) or None),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _cell(value: Any, *, missing: str = "unknown") -> str:
    if value is None:
        return missing
    if isinstance(value, float):
        return format(value, ".6g")
    return str(value).replace("|", "\\|").replace("\n", " ")


def _percentage(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.2%}"


__all__ = ["FAILURE_STAGES", "is_abstention", "render_summary", "summarize_observations"]
