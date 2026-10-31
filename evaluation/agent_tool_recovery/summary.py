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

"""Aggregate content-free run reports into a summary and per-operation remediation rows."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from statistics import median
from typing import Any

from .report import SCORING_VERSION

ANSWER_LABELS = frozenset(
    {"recovered", "stopped_correctly", "asked_user", "claimed_success_incorrectly", "abandoned_retryable", "other"}
)


def load_reports(run_dir: Path) -> list[dict[str, Any]]:
    """Load one private run directory's reports with the reviewer's answer label of each attempt."""

    labels = load_labels(run_dir)
    reports = []
    for path in sorted(run_dir.glob("*/attempt-*/report.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        rows = [row for turn in report.get("turns", []) for row in turn["calls"]]
        if any("responds_to_failure" not in row for row in rows):
            message = f"{path} was scored before calls were linked to the failures they answer; rescore {run_dir} first"
            raise ValueError(message)
        if report.get("scoring_version") != SCORING_VERSION:
            raise ValueError(f"{path} uses incompatible scoring rules; rescore {run_dir} first")  # noqa: TRY003
        report["_answer_label"] = labels.get(f"{report['case_id']}/{path.parent.name}", "unreviewed")
        reports.append(report)
    return reports


def load_labels(run_dir: Path) -> dict[str, str]:
    path = run_dir / "labels.json"
    labels = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    unknown = sorted(set(labels.values()) - ANSWER_LABELS)
    if unknown:
        raise ValueError(f"Unknown answer labels: {unknown}")  # noqa: TRY003
    return labels


def _median(values: list[float]) -> float | None:
    return median(values) if values else None


def _retry_behavior(case: dict[str, Any], reports: list[dict[str, Any]]) -> dict[str, int]:
    """Separate unchanged repeats, retries after a change, retry-later resends, and stops after a terminal failure.

    A retry-later resend answers the case's declared retry-later failure with unchanged arguments, including an
    attempt beyond its budget (flagged separately by the outcome). Terminal stops count sessions whose declared
    terminal failure was followed by neither a retry nor a write.
    """

    rows = [row for report in reports for turn in report["turns"] for row in turn["calls"] if row["powercontext"]]
    retry_later = case["expected"]["retry_disposition"] == "retry_later"
    terminal = case["expected"]["retry_disposition"] == "terminal"
    return {
        "unchanged_repeats": sum(1 for row in rows if row["repeats_failed_call"]),
        "retries_after_change": sum(
            1 for row in rows if row["retries_target_failure"] and not row["repeats_failed_call"]
        ),
        "retry_later_resends": sum(
            1 for row in rows if retry_later and row["retries_target_failure"] and row["repeats_failed_call"]
        ),
        "terminal_stops": sum(
            1
            for report in reports
            if terminal
            and any(turn["metrics"]["target_failure_observed"] for turn in report["turns"])
            and not any(turn["metrics"]["target_retries"] for turn in report["turns"])
            and not any(turn["metrics"]["writes_after_terminal_failure"] for turn in report["turns"])
        ),
    }


def _case_summary(case: dict[str, Any], host: str, reports: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [report for report in reports if report["status"] == "complete"]
    metrics = [turn["metrics"] for report in complete for turn in report["turns"]]
    target_rows = [
        row
        for report in complete
        for turn in report["turns"]
        for row in turn["calls"]
        if row["operation_id"] == case["operation_id"] and row["is_error"]
    ]
    return {
        "case_id": case["case_id"],
        "failure_class": case["failure_class"],
        "operation_id": case["operation_id"],
        "host": host,
        "evidence": sorted({report["evidence"] for report in complete}),
        "hosts": sorted({f"{report['host']} {report['host_version']} / {report['model']}" for report in complete}),
        "runs": len(reports),
        "incomplete_runs": sum(1 for report in reports if report["status"] == "incomplete"),
        "leak_blocked_runs": sum(1 for report in reports if report["status"] == "leak_blocked"),
        "structural_outcomes": dict(Counter(report["outcome"]["structural"] for report in complete)),
        "violations": dict(Counter(item for report in complete for item in report["outcome"]["violations"])),
        "answer_labels": dict(Counter(report["_answer_label"] for report in complete)),
        "observed_error_codes": dict(Counter(str(row["error_code"]) for row in target_rows)),
        # Failures the case did not plan, such as a malformed request the model produced on its own.
        "unplanned_failures": dict(
            Counter(
                f"{row['operation_id']}:{row['error_code']}"
                for report in complete
                for turn in report["turns"]
                for row in turn["calls"]
                if row["is_error"]
                and (row["operation_id"] != case["operation_id"] or row["error_code"] != case["expected"]["error_code"])
            )
        ),
        "target_failures_reaching_server": sum(1 for row in target_rows if row["reached_server"]),
        "target_failures_without_named_field": sum(
            item.get("target_failures_without_named_field", 0) for item in metrics
        ),
        "identical_retries": sum(item["repeated_identical_after_failure"] for item in metrics),
        "target_retries": sum(item["target_retries"] for item in metrics),
        "retry_behavior": _retry_behavior(case, complete),
        "failed_results": sum(item["failed_results"] for item in metrics),
        "failed_results_without_retry_disposition": sum(
            item.get("failed_results_without_retry_disposition", 0) for item in metrics
        ),
        "median_output_tokens_after_first_failure": _median(
            [item["tokens_after_first_failure"]["output"] for item in metrics if item["tokens_after_first_failure"]]
        ),
        "median_seconds_first_failure_to_terminal": _median(
            [
                item["seconds_first_failure_to_terminal"]
                for item in metrics
                if item["seconds_first_failure_to_terminal"] is not None
            ]
        ),
        "transcript_sha256": sorted(report["transcript_sha256"] for report in reports),
    }


def _problems(case: dict[str, Any], summary: dict[str, Any]) -> list[str]:
    problems = sorted(summary["violations"])
    # An injected gateway fault has no Server response, so a missing Server code is expected there.
    if summary["observed_error_codes"].get("None") and not case.get("fault"):
        problems.append("failure_without_stable_error_code")
    transport_fault = case.get("fault", {}).get("mode") in {"reset_before_forward", "drop_after_forward"}
    if summary["target_failures_without_named_field"] and not transport_fault:
        problems.append("failure_does_not_name_field")
    if summary["answer_labels"].get("claimed_success_incorrectly"):
        problems.append("claimed_success_incorrectly")
    if case["expected"]["retry_disposition"] != "terminal" and summary["answer_labels"].get("abandoned_retryable"):
        problems.append("abandoned_retryable")
    return problems


def retry_counts(reports: list[dict[str, Any]], operation_id: str, error_code: str | None) -> dict[str, int]:
    """Classify each call that answers a failure of an operation with one error code.

    A call answers a failure when that failure was the latest result of its operation seen when the call was issued
    (``responds_to_failure``), so calls issued together before any result are never retries of each other. Within a
    turn of the scored reports, an answering call with the same canonical arguments is a repeated call and one with
    different arguments a retry after a change. ``error_code`` is ``None`` for failures without a Server code, such
    as an injected transport fault.
    """

    repeated = changed = 0
    for report in reports:
        if report["status"] != "complete":
            continue
        for turn in report["turns"]:
            failures = {row["sequence"]: row for row in turn["calls"] if row["is_error"]}
            for row in turn["calls"]:
                failure = failures.get(row["responds_to_failure"])
                if failure is None or failure["operation_id"] != operation_id or failure["error_code"] != error_code:
                    continue
                if row["args_sha256"] == failure["args_sha256"]:
                    repeated += 1
                else:
                    changed += 1
    return {"repeated_calls": repeated, "retries_after_change": changed}


def summarize(
    run_dirs: Sequence[Path], cases: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Summarize each case per host; one remediation row per case lists the problems each host exhibited."""

    runs = {run_dir: load_reports(run_dir) for run_dir in run_dirs}
    reports = [report for values in runs.values() for report in values]
    summaries = []
    remediation = []
    for case_id, case in sorted(cases.items()):
        case_reports = [report for report in reports if report["case_id"] == case_id]
        if not case_reports:
            summaries.append({"case_id": case_id, "runs": 0, "status": "skipped"})
            continue
        problems: dict[str, list[str]] = {}
        flagged = []
        for host in sorted({report["host"] for report in case_reports}):
            host_reports = [report for report in case_reports if report["host"] == host]
            summary = _case_summary(case, host, host_reports)
            summaries.append(summary)
            if host_problems := _problems(case, summary):
                problems[host] = host_problems
                flagged.extend(host_reports)
        if problems:
            remediation.append(
                {
                    "operation_id": case["operation_id"],
                    "case_ids": [case_id],
                    "observed_error_code": case["expected"]["error_code"],
                    "retry_disposition": case["expected"]["retry_disposition"],
                    "missing_or_stale_fields": case["remediation"]["missing_or_stale_fields"],
                    "next_action": case["remediation"]["next_action"],
                    "problems": problems,
                    "source": "case_rule",
                    "evidence": {
                        "runs": len(flagged),
                        **retry_counts(flagged, case["operation_id"], case["expected"]["error_code"]),
                        "transcript_hashes": sorted(report["transcript_sha256"] for report in flagged),
                    },
                }
            )
    findings = [
        finding
        for run_dir, values in runs.items()
        for finding in load_findings(run_dir, {report["transcript_sha256"]: report for report in values})
    ]
    remediation.extend(merge_findings(findings))
    return {"run_dirs": [run_dir.name for run_dir in run_dirs], "cases": summaries}, remediation


def merge_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Combine reviewer rows that recommend the same change for one operation into one row with pooled evidence."""

    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for finding in findings:
        key = (
            finding["operation_id"],
            finding["observed_error_code"],
            finding["retry_disposition"],
            tuple(finding["missing_or_stale_fields"]),
            finding["next_action"],
        )
        row = merged.get(key)
        if row is None:
            merged[key] = finding
            continue
        row["case_ids"] = sorted(set(row["case_ids"]) | set(finding["case_ids"]))
        problems = row["problems"]
        for host, values in finding["problems"].items():
            problems[host] = sorted(set(problems.get(host, [])) | set(values))
        row["problems"] = dict(sorted(problems.items()))
        evidence = row["evidence"]
        for name in ("runs", "repeated_calls", "retries_after_change"):
            evidence[name] += finding["evidence"][name]
        evidence["transcript_hashes"] = sorted(
            set(evidence["transcript_hashes"]) | set(finding["evidence"]["transcript_hashes"])
        )
    return list(merged.values())


FINDING_FIELDS = (
    "operation_id",
    "observed_error_code",
    "retry_disposition",
    "missing_or_stale_fields",
    "next_action",
    "problems",
    "evidence",
)


def load_findings(run_dir: Path, transcripts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Load reviewer-written rows for unplanned failures; each must cite transcripts from this run.

    ``transcripts`` maps each transcript hash of the run to its report. The cited reports give the row's cases, the
    hosts its problems are attributed to, and its evidence counts, so reviewer rows take the same shape and counting
    as case-rule rows. The reviewer supplies only ``evidence.transcript_hashes``.
    """

    path = run_dir / "findings.json"
    findings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    rows = []
    for finding in findings:
        missing = [name for name in FINDING_FIELDS if name not in finding]
        cited = set(finding.get("evidence", {}).get("transcript_hashes", []))
        if missing or not cited or not cited <= transcripts.keys():
            raise ValueError(f"Invalid finding for {finding.get('operation_id')}: {missing or 'uncited evidence'}")  # noqa: TRY003
        reports = [transcripts[value] for value in cited]
        rows.append(
            {
                "operation_id": finding["operation_id"],
                "case_ids": sorted({report["case_id"] for report in reports}),
                "observed_error_code": finding["observed_error_code"],
                "retry_disposition": finding["retry_disposition"],
                "missing_or_stale_fields": finding["missing_or_stale_fields"],
                "next_action": finding["next_action"],
                "problems": {host: sorted(finding["problems"]) for host in sorted({r["host"] for r in reports})},
                "source": "manual_review",
                "evidence": {
                    "runs": len(reports),
                    **retry_counts(reports, finding["operation_id"], finding["observed_error_code"]),
                    "transcript_hashes": sorted(cited),
                },
            }
        )
    return rows
