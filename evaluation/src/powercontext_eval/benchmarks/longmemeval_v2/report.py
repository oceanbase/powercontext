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

"""Summarize one saved LongMemEval-V2 smoke run without calling a model or server."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias, TypeGuard, cast

from powercontext_eval.benchmarks.longmemeval_v2.costs import model_free_cost_block
from powercontext_eval.errors import PowerContextEvalError

REPORT_SCHEMA = "powercontext.longmemeval-v2-smoke-report.v1"
REPORT_BANNER = "LongMemEval-V2 smoke subset — not a complete benchmark result."
REPORT_BOUNDARY = (
    "This report describes one fixed smoke subset over pinned upstream trajectories. "
    "It is not a complete benchmark result, not a product reliability claim, and not a substitute "
    "for a full LongMemEval-V2 run. See evaluation/README.md and "
    "evaluation/docs/longmemeval-v2-full-run.md for the recorded boundaries and the unexecuted full run."
)
ERROR_CLASSES = ("configuration", "infrastructure", "retrieval", "generation", "judge", "integrity")
_PHASES = ("preflight", "retrieval", "prepare", "reader", "score", "replay")
_FAILURE_CLASS_BY_PHASE_FILE = {
    "prepare": "infrastructure",
    "reader": "generation",
    "score": "judge",
    "replay": "integrity",
}

ReportStatus: TypeAlias = str


class ReportError(PowerContextEvalError):
    """Saved run artifacts cannot produce one unified report."""


@dataclass(frozen=True)
class ReportRun:
    """The written unified report artifacts."""

    report_path: Path
    markdown_path: Path


def build_report(*, run_dir: Path, output_dir: Path | None = None) -> ReportRun:
    """Read saved stage artifacts and write ``report.json`` and ``report.md`` fail-closed."""

    from powercontext_eval.benchmarks.longmemeval_v2.run_smoke import PHASE_DIRECTORIES

    run_root = run_dir.resolve()
    if not run_root.is_dir():
        raise ReportError(f"LongMemEval-V2 smoke run directory does not exist: {run_root}")
    # Rebuild the phase mapping with plain string keys: ``Mapping`` is invariant in its key
    # type, so the runner's ``Mapping[Phase, str]`` cannot be passed to the str-keyed helpers.
    directories = {str(phase): name for phase, name in PHASE_DIRECTORIES.items()}
    report = _build(run_root, directories)
    if output_dir is None:
        report_path = run_root / "report.json"
        markdown_path = run_root / "report.md"
        _write_text_exclusive(report_path, _json(report))
        _write_text_exclusive(markdown_path, _markdown(report))
        return ReportRun(report_path=report_path, markdown_path=markdown_path)
    target = output_dir.resolve()
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise ReportError(f"Refusing to overwrite report artifacts: {target}") from error
    except OSError as error:
        raise ReportError(f"Cannot create report artifact directory: {target}") from error
    report_path = target / "report.json"
    markdown_path = target / "report.md"
    _write_text_exclusive(report_path, _json(report))
    _write_text_exclusive(markdown_path, _markdown(report))
    return ReportRun(report_path=report_path, markdown_path=markdown_path)


def _build(run_root: Path, directories: Mapping[str, str]) -> dict[str, object]:
    manifest = _load_json(run_root / "run-manifest.json")
    summary = _load_json(run_root / "run-summary.json")
    retrieval_manifest = _load_json(run_root / directories["retrieval"] / "retrieval-manifest.json")
    retrieval_summary = _load_json(run_root / directories["retrieval"] / "summary.json")
    prepare_summary = _load_json(run_root / directories["prepare"] / "prepare-summary.json")
    reader_summary = _load_json(run_root / directories["reader"] / "reader-summary.json")
    score_summary = _load_json(run_root / directories["score"] / "score-summary.json")
    replay_summary = _load_json(run_root / directories["replay"] / "replay-summary.json")
    retrieval_results = run_root / directories["retrieval"] / "retrieval-results.jsonl"
    audit = run_root / directories["retrieval"] / "adapter-audit.jsonl"
    reader_outputs = run_root / directories["reader"] / "reader-outputs.jsonl"
    judge_outputs = run_root / directories["score"] / "judge-outputs.jsonl"

    context_items = _sum_list_length(retrieval_results, "memory_context")
    retrieval_latency = _sum_nested_ms(retrieval_results, "timings_ms")
    ingest_latency = _sum_ingest_ms(audit)
    reader_latency = _sum_number(reader_outputs, "reader_latency_ms")
    judge_latency, abstention = _judge_totals(judge_outputs)
    artifacts = _artifacts(run_root, directories)
    usage = _usage(reader_summary, score_summary, manifest)
    return {
        "schema": REPORT_SCHEMA,
        "classification": "smoke-subset",
        "status": _status(summary, score_summary, artifacts),
        "run_id": _run_id(manifest, summary),
        "experiment_arm": _experiment_arm(manifest, retrieval_manifest),
        "generated_at": datetime.now(UTC).isoformat(),
        "question_count": _question_count(
            summary, score_summary, replay_summary, reader_summary, prepare_summary, retrieval_summary
        ),
        "accuracy": _accuracy(score_summary, replay_summary),
        "latency_ms": {
            "ingest": ingest_latency,
            "retrieval": retrieval_latency,
            "prepare": _number(prepare_summary, "elapsed_ms"),
            "reader": reader_latency,
            "judge": judge_latency,
        },
        "context": {
            "items": context_items,
            "bytes": _number(retrieval_summary, "context_bytes"),
            "tokens": _number(prepare_summary, "memory_context_tokens"),
            "citations_available": _number(retrieval_summary, "citation_count"),
        },
        "usage": usage,
        "failures": _failure_counts(run_root, directories),
        "abstention": abstention,
        "artifacts": artifacts,
        "boundary": REPORT_BOUNDARY,
    }


def _status(
    summary: dict[str, object] | None,
    score_summary: dict[str, object] | None,
    artifacts: Mapping[str, object],
) -> ReportStatus:
    if summary is not None and summary.get("status") in {"completed", "partial", "failed"}:
        return str(summary["status"])
    if score_summary is not None and score_summary.get("failed") == 0:
        return "completed"
    if any(artifacts.get(phase) is not None for phase in _PHASES):
        return "partial"
    return "failed"


def _run_id(manifest: dict[str, object] | None, summary: dict[str, object] | None) -> str | None:
    for source in (manifest, summary):
        if source is None:
            continue
        value = source.get("run_id")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _experiment_arm(
    manifest: dict[str, object] | None, retrieval_manifest: dict[str, object] | None
) -> dict[str, object] | None:
    """Read the recorded arm identity, preferring the run manifest over a retrieval-only run."""

    for source in (manifest, retrieval_manifest):
        if source is None:
            continue
        value = source.get("experiment_arm")
        if isinstance(value, dict):
            return {str(key): item for key, item in value.items()}
    return None


def _usage(
    reader_summary: dict[str, object] | None,
    score_summary: dict[str, object] | None,
    manifest: dict[str, object] | None,
) -> dict[str, object]:
    """Report tokens, per-stage usage-based cost, and the ingestion zero-cost record.

    The total is reported only when every model stage that actually ran has a priced cost
    recorded under one price policy revision. A stage counts as run when its summary
    exists, or when the manifest modes say it was configured and no summary proves
    otherwise — a missing or unpriced stage cost therefore keeps the total ``null``
    instead of silently summing the stages that happen to be present.
    """

    reader_cost = _stage_cost(reader_summary, "cost")
    judge_cost = _stage_cost(score_summary, "judge_cost")
    ingestion = model_free_cost_block(
        stage="ingestion",
        reason=(
            "the PowerContext Memory adapter ingests without a model, so no provider reports "
            "ingestion usage and ingestion cost is zero"
        ),
    )
    modes = _mapping(manifest.get("modes")) if manifest is not None else {}
    judge_calls = _judge_call_count(score_summary)
    reader_state = _model_stage_state(
        summary=reader_summary,
        cost=reader_cost,
        configured=modes.get("reader") is True,
        calls_expected=False,
        role="Reader",
        configured_provider=_configured_provider(manifest, "reader"),
        configured_model=_configured_model(manifest, "reader"),
        configured_policy=_configured_cost_policy(manifest, "reader"),
    )
    judge_state = _model_stage_state(
        summary=score_summary,
        cost=judge_cost,
        configured=modes.get("score") is True,
        calls_expected=judge_calls > 0,
        role="Judge",
        configured_provider=_configured_provider(manifest, "judge"),
        configured_model=_configured_model(manifest, "judge"),
        configured_policy=_configured_cost_policy(manifest, "judge"),
        recorded_usage_nonzero=_mapping_nonzero(score_summary, "judge_usage"),
        calls=judge_calls,
    )
    total, note = _total_cost(
        reader_cost,
        judge_cost,
        reader_state=reader_state,
        judge_state=judge_state,
        include_judge=judge_calls > 0,
    )
    return {
        "ingestion_tokens": 0,
        "ingestion_tokens_note": (
            "the PowerContext Memory adapter ingests without a model, so no provider reports ingestion usage"
        ),
        "ingestion_cost": ingestion,
        "reader_input_tokens": _nested_number(reader_summary, "usage", "input_tokens"),
        "reader_output_tokens": _nested_number(reader_summary, "usage", "output_tokens"),
        "judge_input_tokens": _nested_number(score_summary, "judge_usage", "input_tokens"),
        "judge_output_tokens": _nested_number(score_summary, "judge_usage", "output_tokens"),
        "reader_cost": reader_cost,
        "judge_cost": judge_cost,
        "estimated_cost_usd": None if total is None else total["cost_usd"],
        "estimated_cost_note": note,
    }


def _model_stage_state(
    *,
    summary: dict[str, object] | None,
    cost: Mapping[str, object] | None,
    configured: bool,
    calls_expected: bool,
    role: str,
    configured_provider: str | None,
    configured_model: str | None,
    configured_policy: Mapping[str, object] | None,
    recorded_usage_nonzero: bool = False,
    calls: int = 0,
) -> str | None:
    """Return the reason this model stage blocks a total, or ``None`` when it is settled.

    ``None`` also means the stage did not run and therefore owes no cost: only a stage whose
    summary exists, or that the manifest modes configured without a contrary summary, must
    account for its cost.
    """

    if summary is None:
        if not configured:
            return None
        return f"the run was configured to run the {role} but no {role} summary recorded a cost"
    if role == "Judge" and not calls_expected:
        if recorded_usage_nonzero or (cost is not None and _nonnull_usage(cost)):
            return "the Judge recorded non-zero usage despite zero recorded model calls"
        return None
    if cost is None:
        # A score stage that made no model call owes no Judge cost; any other absent block is
        # a missing artifact the report must not silently drop.
        if role == "Judge" and not calls_expected:
            return None
        if calls_expected:
            return f"the {role} made {calls} model call(s) but recorded no priced cost"
        return f"the {role} stage ran but its summary recorded no cost block"
    amount = _cost_amount(cost)
    if amount is None:
        if not calls_expected and role == "Judge" and not _nonnull_usage(cost):
            # No Judge call happened and the recorded usage is zero, so the absent price is
            # genuinely zero cost rather than a missing artifact.
            return None
        reason = cost.get("unavailable_reason")
        detail = f": {reason}" if reason else ""
        if calls_expected:
            return f"the {role} made {calls} model call(s) but recorded no priced cost{detail}"
        if _nonnull_usage(cost):
            return f"the {role} recorded non-zero usage without a priced cost{detail}"
        return f"the {role} stage recorded no priced cost{detail}"
    identity_problem = _cost_identity_problem(
        cost,
        configured_provider=configured_provider,
        configured_model=configured_model,
        configured_policy=configured_policy,
    )
    if identity_problem is not None:
        return f"the {role} cost {identity_problem}"
    if configured_model is not None and str(cost.get("model")) != configured_model:
        return f"the {role} cost was recorded for model {cost.get('model')!r}, not the configured {configured_model!r}"
    return None


def _nonnull_usage(cost: Mapping[str, object]) -> bool:
    """Report whether a cost block carries any non-zero token count."""

    for key in ("input_tokens", "input_cache_hit_tokens", "input_cache_miss_tokens", "output_tokens"):
        value = cost.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return True
    return False


def _judge_call_count(score_summary: dict[str, object] | None) -> int:
    """Count real Judge model calls, falling back to recorded usage for older artifacts."""

    if score_summary is None:
        return 0
    value = score_summary.get("judge_calls")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    usage = score_summary.get("judge_usage")
    if isinstance(usage, Mapping) and any(
        isinstance(token_count, int) and not isinstance(token_count, bool) and token_count > 0
        for token_count in usage.values()
    ):
        return 1
    cost = score_summary.get("judge_cost")
    if isinstance(cost, Mapping):
        return 1 if _cost_amount({str(name): item for name, item in cost.items()}) is not None else 0
    return 0


def _configured_model(manifest: dict[str, object] | None, role: str) -> str | None:
    """Read the model the run manifest pinned for one role, when it recorded one."""

    if manifest is None:
        return None
    block = manifest.get(role)
    if not isinstance(block, Mapping):
        return None
    model = block.get("model")
    return model.strip() if isinstance(model, str) and model.strip() else None


def _configured_provider(manifest: dict[str, object] | None, role: str) -> str | None:
    if manifest is None:
        return None
    block = manifest.get(role)
    if not isinstance(block, Mapping):
        return None
    provider = block.get("provider")
    return provider.strip() if isinstance(provider, str) and provider.strip() else None


def _configured_cost_policy(manifest: dict[str, object] | None, role: str) -> Mapping[str, object] | None:
    if manifest is None:
        return None
    policies = manifest.get("cost_policy")
    if not isinstance(policies, Mapping):
        return None
    policy = policies.get(role.lower())
    if not isinstance(policy, Mapping):
        return None
    return {str(key): value for key, value in policy.items()}


def _mapping_nonzero(summary: dict[str, object] | None, key: str) -> bool:
    if summary is None:
        return False
    usage = summary.get(key)
    if not isinstance(usage, Mapping):
        return False
    return any(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in usage.values())


def _cost_identity_problem(
    cost: Mapping[str, object],
    *,
    configured_provider: str | None,
    configured_model: str | None,
    configured_policy: Mapping[str, object] | None,
) -> str | None:
    if cost.get("currency") != "USD":
        return "was not recorded in USD"
    if configured_provider is not None and cost.get("provider") != configured_provider:
        return f"was recorded for provider {cost.get('provider')!r}, not the configured {configured_provider!r}"
    if configured_model is not None and cost.get("model") != configured_model:
        return f"was recorded for model {cost.get('model')!r}, not the configured {configured_model!r}"
    if configured_policy is None:
        return "was priced but the run manifest has no configured price policy"
    for key in (
        "provider",
        "model",
        "currency",
        "input_cache_hit_price_per_million",
        "input_cache_miss_price_per_million",
        "output_price_per_million",
        "price_policy_revision",
    ):
        if cost.get(key) != configured_policy.get(key):
            return f"does not match the run's configured price policy field {key}"
    input_tokens = cost.get("input_tokens")
    hit = cost.get("input_cache_hit_tokens")
    miss = cost.get("input_cache_miss_tokens")
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in (input_tokens, hit, miss)
    ):
        return "has invalid cache token counts"
    normalized_input = cast(int, input_tokens)
    normalized_hit = cast(int, hit)
    normalized_miss = cast(int, miss)
    if normalized_input != normalized_hit + normalized_miss:
        return "has cache token counts that do not equal its input token total"
    return None


def _stage_cost(summary: dict[str, object] | None, key: str) -> dict[str, object] | None:
    """Read one stage's cost block, preserving an unconfigured cost as null with its reason."""

    if summary is None:
        return None
    value = summary.get(key)
    if not isinstance(value, Mapping):
        return None
    record = {str(name): item for name, item in value.items()}
    cost = record.get("cost_usd")
    if cost is not None and not _finite_nonnegative_number(cost):
        return None
    return record


def _total_cost(
    reader_cost: Mapping[str, object] | None,
    judge_cost: Mapping[str, object] | None,
    *,
    reader_state: str | None,
    judge_state: str | None,
    include_judge: bool,
) -> tuple[dict[str, object] | None, str]:
    """Sum stage costs only when every model stage that ran was priced under one policy."""

    problems = [state for state in (reader_state, judge_state) if state]
    if problems:
        return None, "; ".join(problems)
    costs = [
        cost
        for cost in (reader_cost, judge_cost if include_judge else None)
        if cost is not None and _cost_amount(cost) is not None
    ]
    if not costs:
        return None, "no Reader or Judge model stage ran in this smoke run, so no model cost is reported"
    currencies = {str(cost.get("currency")) for cost in costs}
    if len(currencies) != 1:
        return None, "the recorded stage costs use different currencies, so no single total is reported"
    revisions = {str(cost.get("price_policy_revision")) for cost in costs}
    if len(revisions) != 1:
        return None, "the recorded stage costs were priced under different price policy revisions"
    amounts = [_cost_amount(cost) for cost in costs]
    return (
        {"cost_usd": round(sum(amount for amount in amounts if amount is not None), 6), "currency": currencies.pop()},
        "sum of the Reader and Judge usage costs recorded under the run's explicit price policy",
    )


def _cost_amount(cost: Mapping[str, object]) -> float | None:
    """Read one recorded cost amount, keeping a null cost distinguishable from zero."""

    value = cost.get("cost_usd")
    if not _finite_nonnegative_number(value):
        return None
    return float(value)


def _finite_nonnegative_number(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and value >= 0
    )


def _question_count(*summaries: dict[str, object] | None) -> int | None:
    for summary in summaries:
        if summary is None:
            continue
        value = summary.get("question_count")
        if _is_int(value):
            return value
    return None


def _accuracy(
    score_summary: dict[str, object] | None, replay_summary: dict[str, object] | None
) -> dict[str, object] | None:
    for summary in (score_summary, replay_summary):
        if summary is None or summary.get("failed") != 0:
            continue
        correct = summary.get("correct")
        incorrect = summary.get("incorrect")
        if not _is_int(correct) or not _is_int(incorrect):
            continue
        value = summary.get("accuracy")
        return {
            "correct": correct,
            "incorrect": incorrect,
            "failed": 0,
            "value": float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None,
        }
    return None


def _failure_counts(run_root: Path, directories: Mapping[str, str]) -> dict[str, int]:
    counts = dict.fromkeys(ERROR_CLASSES, 0)
    for row in _iter_jsonl(run_root / "failures.jsonl"):
        error_class = row.get("error_class")
        if error_class in counts:
            counts[str(error_class)] += 1
    for phase, error_class in _FAILURE_CLASS_BY_PHASE_FILE.items():
        for row in _iter_jsonl(run_root / directories[phase] / f"{phase}-failures.jsonl"):
            counts[_phase_failure_class(phase, row, error_class)] += 1
    for row in _iter_jsonl(run_root / directories["retrieval"] / "failures.jsonl"):
        counts[_phase_failure_class("retrieval", row, "infrastructure")] += 1
    return counts


def _phase_failure_class(phase: str, row: Mapping[str, object], fallback: str) -> str:
    if phase != "retrieval":
        return fallback
    category = row.get("category")
    if category == "integration":
        return "retrieval"
    if category == "integrity":
        return "integrity"
    return "infrastructure"


def _judge_totals(path: Path) -> tuple[float | None, dict[str, object]]:
    latency = 0.0
    seen = False
    count = 0
    correct = 0
    available = False
    for row in _iter_jsonl(path):
        seen = True
        value = row.get("judge_latency_ms")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            latency += float(value)
        if row.get("evaluator") != "llm_abstention_checker":
            continue
        available = True
        count += 1
        if row.get("label") == 1:
            correct += 1
    abstention: dict[str, object] = {
        "count": count,
        "correct": correct if available else None,
        "incorrect": count - correct if available else None,
        "unavailable": not available,
    }
    return (round(latency, 3) if seen else None), abstention


def _artifacts(run_root: Path, directories: Mapping[str, str]) -> dict[str, str | None]:
    candidates: dict[str, str] = {
        "run_manifest": "run-manifest.json",
        "run_summary": "run-summary.json",
        "failures": "failures.jsonl",
    }
    candidates.update(dict(directories))
    return {name: value if (run_root / value).exists() else None for name, value in candidates.items()}


def _number(source: dict[str, object] | None, key: str) -> int | float | None:
    """Preserve a saved integer count as an integer and never invent a value that was not recorded."""

    if source is None:
        return None
    value = source.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    return None


def _nested_number(source: dict[str, object] | None, key: str, nested: str) -> int | None:
    if source is None:
        return None
    inner = source.get(key)
    if not isinstance(inner, Mapping):
        return None
    value = inner.get(nested)
    return value if _is_int(value) else None


def _sum_list_length(path: Path, key: str) -> int | None:
    total = 0
    seen = False
    for row in _iter_jsonl(path):
        seen = True
        value = row.get(key)
        if isinstance(value, list):
            total += len(value)
    return total if seen else None


def _sum_number(path: Path, key: str) -> float | None:
    total = 0.0
    seen = False
    for row in _iter_jsonl(path):
        seen = True
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value)
    return round(total, 3) if seen else None


def _sum_nested_ms(path: Path, key: str) -> float | None:
    total = 0.0
    seen = False
    for row in _iter_jsonl(path):
        inner = row.get(key)
        if not isinstance(inner, Mapping):
            continue
        value = inner.get("total")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            seen = True
            total += float(value)
    return round(total, 3) if seen else None


def _sum_ingest_ms(path: Path) -> float | None:
    total = 0.0
    seen = False
    for row in _iter_jsonl(path):
        if row.get("operation") != "ingest":
            continue
        inner = row.get("timings_ms")
        if not isinstance(inner, Mapping):
            continue
        value = inner.get("total")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            seen = True
            total += float(value)
    return round(total, 3) if seen else None


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _load_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _iter_jsonl(path: Path) -> Iterator[dict[str, object]]:
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _markdown(report: Mapping[str, object]) -> str:
    accuracy = _mapping(report["accuracy"])
    latency = _mapping(report["latency_ms"])
    context = _mapping(report["context"])
    usage = _mapping(report["usage"])
    failures = _mapping(report["failures"])
    abstention = _mapping(report["abstention"])
    artifacts = _mapping(report["artifacts"])
    lines = [
        REPORT_BANNER,
        "",
        f"Status: {report['status']}",
        f"Run: {_display(report['run_id'])}",
        f"Arm: {_arm_id(report['experiment_arm'])}",
        f"Questions: {_display(report['question_count'])}",
        f"Accuracy: {_display_accuracy(accuracy)}",
        "",
        "Latency (ms): " + _pairs(latency),
        "Context: " + _pairs(context),
        "Usage: "
        + _pairs(
            {name: value for name, value in usage.items() if name.endswith("tokens") or name == "estimated_cost_usd"}
        ),
        "Cost: " + _pairs(_cost_line(usage)),
        "Failures: " + _pairs(failures),
        "Abstention: " + _pairs(abstention),
        "",
        "Artifacts:",
    ]
    lines.extend(f"- {name}: {_display(value)}" for name, value in sorted(artifacts.items()))
    lines.extend(
        [
            "",
            "What this evaluates:",
            "- Whether the PowerContext Memory adapter retrieved citable evidence through public interfaces",
            "  under fixed upstream data, fixed questions, and a fixed context budget.",
            "- Answer accuracy, latency, context size, failures, and abstention for the configured Reader and Judge.",
            "- Whether saved outputs replay the same deterministic scoring inputs without a model.",
            "",
            "What this does not evaluate:",
            "- Handoff, cross-host recovery, normal Runtime persistence, or Work Continuity.",
            "- Full LongMemEval-V2 performance, general model capability, or product leadership.",
            "- LoCoMo, SWE-bench Pro, or real user-task acceptance.",
            "",
            REPORT_BOUNDARY,
            "",
        ]
    )
    return "\n".join(lines)


def _mapping(value: object) -> Mapping[str, object]:
    """Rebuild an arbitrary mapping with string keys so downstream lookups stay typed."""

    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _arm_id(value: object) -> str:
    if isinstance(value, Mapping):
        identifier = value.get("id")
        if isinstance(identifier, str) and identifier.strip():
            return identifier
    return "unavailable"


def _cost_line(usage: Mapping[str, object]) -> dict[str, object]:
    """Render one readable cost line without leaking whole nested stage records."""

    reader = usage.get("reader_cost")
    judge = usage.get("judge_cost")
    return {
        "ingestion": _stage_cost_display(usage.get("ingestion_cost")),
        "reader": _stage_cost_display(reader),
        "judge": _stage_cost_display(judge),
        "total_usd": usage.get("estimated_cost_usd"),
    }


def _stage_cost_display(value: object) -> object:
    if not isinstance(value, Mapping):
        return None
    cost = value.get("cost_usd")
    if cost is None:
        reason = value.get("unavailable_reason")
        return f"unavailable ({reason})" if reason else "unavailable"
    currency = value.get("currency")
    return str(cost) if currency is None else f"{cost} {currency}"


def _pairs(values: Mapping[str, object]) -> str:
    return ", ".join(f"{name}={_display(value)}" for name, value in sorted(values.items()))


def _display(value: object) -> str:
    return "unavailable" if value is None else str(value)


def _display_accuracy(accuracy: Mapping[str, object]) -> str:
    value = accuracy.get("value")
    correct = accuracy.get("correct")
    incorrect = accuracy.get("incorrect")
    if value is None:
        return "unavailable"
    if isinstance(correct, bool) or not isinstance(correct, int):
        return "unavailable"
    if isinstance(incorrect, bool) or not isinstance(incorrect, int):
        return "unavailable"
    return f"{correct}/{correct + incorrect} ({value})"


def _write_text_exclusive(path: Path, value: str) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(value)
    except OSError as error:
        raise ReportError(f"Cannot write report artifact: {path}") from error
