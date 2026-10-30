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

"""Failure-inclusive reports; estimated savings never become billed usage."""

from __future__ import annotations

import json
import math
import random
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from .dataset import MODES, SCHEMA, digest, write_json


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]


def usage_field_complete(row: dict[str, Any], usage: dict[str, Any], field: str) -> bool:
    """A failed call may have recorded prior usage before an unobserved final request."""
    return (
        row["status"] in {"succeeded", "no_proposal"}
        and not usage.get("incomplete", False)
        and usage.get(field) is not None
    )


def usage_columns(rows: list[dict[str, Any]], expected: int, operation: str | None = None) -> dict[str, Any]:
    totals = {}
    usages = [
        row.get("usage_by_operation", {}).get(operation, {}) if operation else row.get("usage", {}) for row in rows
    ]
    for field in (
        "requests",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    ):
        complete = [usage_field_complete(row, usage, field) for row, usage in zip(rows, usages, strict=True)]
        totals[field] = {
            "known_subtotal": sum(
                usage[field] if usage.get(field) is not None else usage.get(field + "_known_subtotal", 0)
                for usage in usages
            ),
            "unknown_attempts": expected - sum(complete),
            "complete": len(rows) == expected and all(complete),
        }
    return totals


def summarize(rows: list[dict[str, Any]], expected: int) -> dict[str, Any]:
    totals = usage_columns(rows, expected)
    generation_totals = usage_columns(rows, expected, "generation")
    embedding_totals = usage_columns(rows, expected, "embedding")
    succeeded = [row for row in rows if row["status"] == "succeeded"]
    elapsed = [row["elapsed_seconds"] for row in rows if "elapsed_seconds" in row]
    generation = [row["generation_latency_seconds"] for row in rows if "generation_latency_seconds" in row]
    queue = [row["queue_wait_seconds"] for row in rows if "queue_wait_seconds" in row]
    response = [row["queue_inclusive_seconds"] for row in rows if "queue_inclusive_seconds" in row]
    uncertified = [row for row in rows if row["status"] not in {"succeeded", "no_proposal"}]
    return {
        "planned_attempts": expected,
        "recorded_attempts": len(rows),
        "missing_attempts": expected - len(rows),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "successes": len(succeeded),
        "success_rate": len(succeeded) / expected if expected else None,
        "error_rate": sum(row["status"] == "failed" for row in rows) / expected if expected else None,
        "timeout_rate": sum("Timeout" in row.get("error_type", "") for row in rows) / expected if expected else None,
        "reopen_equal_count": sum(row.get("reopen_equal") is True for row in rows),
        "reopen_lineage_equal_count": sum(row.get("reopen_lineage_equal") is True for row in rows),
        "nonempty_count": sum(row.get("diagnostics", {}).get("nonempty_fields") is True for row in rows),
        "usage": totals,
        "generation_usage": generation_totals,
        "embedding_usage": embedding_totals,
        "failed_cost_completeness": {
            "status": "not_certified" if uncertified else "no_failed_or_unfinished_attempts",
            "uncertified_attempts": len(uncertified),
            "status_counts": dict(Counter(row["status"] for row in uncertified)),
            "policy": "Failed or unfinished workflows retain all recorded usage as known subtotals, but no field is certified complete: an interrupted final request may be absent from the observer. Explicit usage.incomplete also invalidates completeness for otherwise completed calls.",
        },
        "requests_per_success_known_subtotal": totals["requests"]["known_subtotal"] / len(succeeded)
        if succeeded
        else None,
        "total_tokens_per_success_known_subtotal": totals["total_tokens"]["known_subtotal"] / len(succeeded)
        if succeeded
        else None,
        "additional_generation_requests_known_subtotal": sum(
            max(0, row.get("usage_by_operation", {}).get("generation", {}).get("requests", 0) - 1)
            for row in rows
            if row.get("usage_by_operation", {}).get("generation", {}).get("requests") is not None
        ),
        "additional_request_boundary": "Generation schema retries and continuations only; approval embedding is separate. SDK transport retries without surfaced usage cannot be inferred.",
        "elapsed_seconds": {
            "p50": statistics.median(elapsed) if elapsed else None,
            "p95": percentile(elapsed, 0.95),
            "p99": percentile(elapsed, 0.99),
            "sum": sum(elapsed),
        },
        "generation_seconds": {
            "p50": statistics.median(generation) if generation else None,
            "p95": percentile(generation, 0.95),
            "p99": percentile(generation, 0.99),
        },
        "queue_wait_seconds": {
            "p50": percentile(queue, 0.50),
            "p95": percentile(queue, 0.95),
            "p99": percentile(queue, 0.99),
        },
        "queue_inclusive_seconds": {
            "p50": percentile(response, 0.50),
            "p95": percentile(response, 0.95),
            "p99": percentile(response, 0.99),
        },
        "encoding_reasons": dict(Counter(item["reason"] for row in rows for item in row.get("encodings", []))),
        "semantic_quality": "not_scored_without_blinded_judge",
        "cost": None,
    }


def throughput_report(directory: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each resumed segment uses only its own scheduled jobs and measured wall time."""
    result = []
    for path in sorted((directory / "segments").glob("*.json")):
        segment = json.loads(path.read_text())
        keys = set(segment["pending_attempt_ids"])
        current = [row for row in rows if row["attempt_id"] in keys and row.get("segment_id") == segment["segment_id"]]
        completed = sum(row["status"] != "running" for row in current)
        succeeded = sum(row["status"] == "succeeded" for row in current)
        wall = segment.get("execution_wall_seconds")
        result.append({
            **segment,
            "new_attempts_completed": completed,
            "new_attempts_succeeded": succeeded,
            "completed_per_second": completed / wall if wall else None,
            "successes_per_second": succeeded / wall if wall else None,
        })
    return result


def paired_confidence(pairs: list[dict[str, Any]], planned_cases: int, planned_repeats: int) -> dict[str, Any]:
    """Resample cases, averaging repeated attempts before the cluster bootstrap."""
    result = {
        "method": "2000 seeded case-cluster bootstrap draws, repeats averaged within case; exploratory 95% interval",
        "planned_cases": planned_cases,
    }
    for field in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens"):
        case_ids = sorted({row["instance_id"] for row in pairs})
        means = []
        for case_id in case_ids:
            rows = [row for row in pairs if row["instance_id"] == case_id]
            if len(rows) != planned_repeats or any(row[field + "_delta"] is None for row in rows):
                continue
            means.append({"instance_id": case_id, "mean_delta": statistics.mean(row[field + "_delta"] for row in rows)})
        rng = random.Random(2086)  # noqa: S311 - reproducible statistical resampling
        sampled = (
            [statistics.mean(rng.choice(means)["mean_delta"] for _ in means) for _ in range(2000)] if means else []
        )
        result[field] = {
            "cases_with_complete_recorded_pairs": len(means),
            "unrepresented_cases": planned_cases - len(means),
            "per_case": means,
            "mean_delta": statistics.mean(item["mean_delta"] for item in means) if means else None,
            "mean_delta_95pct_interval": [percentile(sampled, 0.025), percentile(sampled, 0.975)],
        }
    return result


def _quality_status(root: Path, row: dict[str, Any]) -> str:
    if row["status"] != "succeeded":
        return f"producer_{row['status']}"
    for component, key in (
        ("questions", digest(row["instance_id"])[:24]),
        ("answers", row["attempt_id"]),
        ("assessments", row["attempt_id"]),
    ):
        path = root / component / f"{key}.json"
        if not path.exists():
            return f"{component}_missing"
        record = json.loads(path.read_text())
        if record["status"] != "succeeded" or record.get("grounding_error"):
            state = "invalid_reference" if record.get("grounding_error") else record["status"]
            return f"{component}_{state}"
    return "assessed"


def judge_report(directory: Path, rows: list[dict[str, Any]], expected: Counter[str]) -> dict[str, Any]:
    """Grade the whole planned cohort, retaining failed/skipped/missing assessments."""
    root = directory / "judge"
    if not (root / "manifest.json").exists():
        return {"status": "not_run", "cost": None}
    by_component = {}
    for component in ("questions", "answers", "assessments"):
        records = [json.loads(path.read_text()) for path in sorted((root / component).glob("*.json"))]
        summary = summarize(records, len(records))
        by_component[component] = {
            "records": len(records),
            "status_counts": dict(Counter(row["status"] for row in records)),
            "source_counts": dict(Counter(row.get("source", "model_call") for row in records)),
            "reference_records_without_model_calls": sum(
                row.get("source") == "audited_reference" and row.get("actual_network_attempts") == 0 for row in records
            ),
            "usage": summary["usage"],
            "failed_cost_completeness": summary["failed_cost_completeness"],
        }
    arms = {}
    for mode in MODES:
        assessments = []
        selected = [row for row in rows if row["mode"] == mode]
        quality_statuses = Counter(_quality_status(root, row) for row in selected)
        for row in rows:
            path = root / "assessments" / f"{row['attempt_id']}.json"
            if row["mode"] == mode and path.exists():
                record = json.loads(path.read_text())
                if record["status"] == "succeeded":
                    assessments.append(record["output"])
        quality_statuses["producer_missing"] += expected[mode] - len(selected)
        passed = sum(
            item["grounding"] >= 1
            and item["contradiction_free"] == 2
            and item["outcome_honesty"] == 2
            and item["actionability"] >= 1
            for item in assessments
        )
        arms[mode] = {
            "planned_attempts": expected[mode],
            "assessed_attempts": len(assessments),
            "unassessed_attempts": expected[mode] - len(assessments),
            "quality_status_counts": dict(quality_statuses),
            "grounded_usable_count": passed,
            "grounded_usable_rate_all_planned": passed / expected[mode] if expected[mode] else None,
            "transfer_correct": sum(sum(item["transfer_correct"]) for item in assessments),
            "transfer_questions_all_planned": expected[mode] * 3,
            "transfer_accuracy_all_planned": sum(sum(item["transfer_correct"]) for item in assessments)
            / (expected[mode] * 3)
            if expected[mode]
            else None,
            "mean_scores_assessed_only": {
                key: statistics.mean(item[key] for item in assessments) if assessments else None
                for key in ("grounding", "contradiction_free", "outcome_honesty", "actionability")
            },
        }
    return {
        "status": "recorded",
        "manifest": json.loads((root / "manifest.json").read_text()),
        "by_component": by_component,
        "arms": arms,
        "cost": None,
        "usable_rule": "grounding>=1, contradiction_free=2, outcome_honesty=2, actionability>=1; each dimension 0..2",
        "denominator": "All planned attempts; generation failures, no-proposal, missing or failed judges are unassessed and do not count as correct.",
    }


def build_report(directory: Path) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text())
    rows = [json.loads(path.read_text()) for path in sorted((directory / "attempts").glob("*.json"))]
    expected = Counter(job["mode"] for job in manifest["jobs"])
    arms = {mode: summarize([row for row in rows if row["mode"] == mode], expected[mode]) for mode in MODES}
    pairs = {}
    indexed = {(row["instance_id"], row["repeat"], row["mode"]): row for row in rows}
    for mode in ("shadow", "active"):
        compared = []
        for key, baseline in indexed.items():
            if key[2] != "off":
                continue
            other = indexed.get((key[0], key[1], mode))
            if other is None:
                continue
            record = {
                "instance_id": key[0],
                "repeat": key[1],
                "both_succeeded": baseline["status"] == other["status"] == "succeeded",
            }
            for field in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens"):
                before = baseline.get("usage_by_operation", {}).get("generation", {})
                after = other.get("usage_by_operation", {}).get("generation", {})
                record[field + "_delta"] = (
                    after[field] - before[field]
                    if usage_field_complete(baseline, before, field) and usage_field_complete(other, after, field)
                    else None
                )
            compared.append(record)
        pairs[mode] = compared
    report = {
        "schema": SCHEMA,
        "manifest": manifest,
        "arms": arms,
        "paired_deltas": pairs,
        "paired_deltas_operation": "generation; only completed calls with complete recorded usage contribute deltas. Failed, unfinished, explicitly incomplete and unknown fields remain in attempt denominators and known subtotals but do not enter paired confidence intervals.",
        "paired_case_bootstrap": {
            mode: paired_confidence(values, len({job["instance_id"] for job in manifest["jobs"]}), manifest["repeats"])
            for mode, values in pairs.items()
        },
        "execution_segments": throughput_report(directory, rows),
        "boundary": "Historical Experience generation benchmark, not SWE-bench solution accuracy. Structural readback is not semantic correctness. Failures and missing usage remain in denominators. Provider caching is observed, not controlled cold/warm cache.",
        "judge": judge_report(directory, rows, expected),
        "cost": None,
    }
    workflow = {
        field: sum(arm["usage"][field]["known_subtotal"] for arm in arms.values())
        for field in ("input_tokens", "output_tokens", "total_tokens")
    }
    grading = {
        field: sum(
            component["usage"][field]["known_subtotal"]
            for component in report["judge"].get("by_component", {}).values()
        )
        for field in workflow
    }
    report["experiment_known_token_subtotals"] = {
        "experience_workflow": workflow,
        "evaluation_author_solver_judge": grading,
        "combined": {field: workflow[field] + grading[field] for field in workflow},
        "complete": all(arm["usage"]["total_tokens"]["complete"] for arm in arms.values())
        and all(
            component["usage"]["total_tokens"]["complete"]
            for component in report["judge"].get("by_component", {}).values()
        ),
    }
    write_json(directory / "report.json", report)
    lines = [
        "# Experience input encoding evaluation",
        "",
        report["boundary"],
        "",
        "| Mode | Success / planned | Generation input known | Generation output known | Generation total known | Unknown generation total attempts | Reopened | p95 seconds |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode, arm in arms.items():
        usage = arm["generation_usage"]
        lines.append(
            f"| {mode} | {arm['successes']} / {arm['planned_attempts']} | {usage['input_tokens']['known_subtotal']} | {usage['output_tokens']['known_subtotal']} | {usage['total_tokens']['known_subtotal']} | {usage['total_tokens']['unknown_attempts']} | {arm['reopen_equal_count']} | {arm['elapsed_seconds']['p95']} |"
        )
    lines += [
        "",
        "Known subtotals are not complete billing figures when unknown attempts exist. No dollar cost is calculated without an explicit price policy. Semantic quality and downstream usefulness require a separately recorded blinded assessment.",
        "",
    ]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return report
