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

"""Unified report for one work-continuity run.

The report keeps two tables that are never merged. One states what each method
injected; the other states how each method's continuations turned out. Issue
requirement: injected bytes are reported separately from task success, so a
smaller context is never quietly presented as a better continuation.

Every report states its own boundary. A fixture task set and synthetic recorded
attempts validate the harness; they are not evidence about a real host, model, or
PowerContext build, and the report says so in its own first line.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from powercontext_eval.benchmarks.work_continuity.analysis import (
    NO_RECORDING,
    ArmOutcome,
    WorkContinuityAnalysis,
)
from powercontext_eval.benchmarks.work_continuity.arms import TREATMENT_ARM_ID
from powercontext_eval.benchmarks.work_continuity.runner import WorkContinuityRun, run_summary

REPORT_SCHEMA = "powercontext.work-continuity-report.v1"

NOT_A_BENCHMARK_BANNER = (
    "Not a complete benchmark result. This run reports what the checked-in fixture task set and the "
    "supplied recorded attempts produced under one declared protocol. It does not establish that "
    "Rollover Handoff outperforms any alternative in production, on a real host, or on a real model."
)


class ReportError(Exception):
    """A work-continuity report cannot be rendered from the supplied run."""


@dataclass(frozen=True)
class ReportResult:
    """Paths written for one work-continuity report."""

    report_path: Path
    markdown_path: Path


def build_report(run: WorkContinuityRun, *, output_dir: Path | None = None) -> ReportResult:
    """Render one run as a machine-readable report plus a human summary."""

    target = (output_dir or Path(".")).resolve()
    target.mkdir(parents=True, exist_ok=True)
    payload = report_payload(run)
    report_path = target / "report.json"
    markdown_path = target / "report.md"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(payload), encoding="utf-8")
    return ReportResult(report_path=report_path, markdown_path=markdown_path)


def report_payload(run: WorkContinuityRun) -> dict[str, object]:
    """Build the unified report body for one run."""

    summary = run_summary(run)
    payload: dict[str, object] = {
        "schema": REPORT_SCHEMA,
        "banner": NOT_A_BENCHMARK_BANNER,
        "workload": run.workload,
        "classification": run.classification,
        "run_id": run.run_id,
        "task_set_id": run.task_set_id,
        "assembly_max_bytes": run.max_bytes,
        "hosts": list(run.hosts),
        "execution_configuration": summary.get("execution_configuration", []),
        "comparison": summary.get("comparison", {}),
        "separated_measurements": {
            "injected_bytes": "reported per method and per task, never combined with an outcome metric",
            "outcome": "reported per method and per task as success, recovery cost, and conflict counts",
        },
        "methods": summary["arms"],
        "tasks": _task_rows(run),
        "failure_analysis": _failure_block(run.analysis),
        "recommendations": _recommendations(run.analysis),
        "boundaries": _boundaries(run),
    }
    return payload


def render_markdown(payload: dict[str, object]) -> str:
    """Render the unified report body as a human summary."""

    lines: list[str] = [
        f"# Work-continuity report: {payload['run_id']}",
        "",
        f"> {payload['banner']}",
        "",
        f"- Task set: `{payload['task_set_id']}`",
        f"- Classification: `{payload['classification']}`",
        f"- Assembly ceiling: {payload['assembly_max_bytes']} bytes per task",
        f"- Recorded hosts: {', '.join(_host_names(payload))}",
        "",
    ]
    configuration = _entries(payload.get("execution_configuration"), "execution_configuration")
    if configuration:
        lines.extend(
            [
                "## Recorded execution configuration",
                "",
                (
                    "The model and host revision that produced each host's recordings. Outcomes are only compared "
                    "within one task on one host, and a host that reports two configurations is rejected before "
                    "scoring, so a configuration difference cannot be read as a method difference. The recordings "
                    "each host contributed are shown per task and per method, because the comparison gate weighs a "
                    "configuration by which task under which method it recorded and how many times, not only by "
                    "whether it appears somewhere."
                ),
                "",
                "| Host | Host revision | Model | Recorded attempts | Recorded tasks and methods |",
                "| --- | --- | --- | ---: | --- |",
            ]
        )
        for entry in configuration:
            lines.append(
                f"| `{entry['host']}` | `{entry['host_revision']}` | `{entry['model']}` | "
                f"{entry['attempt_count']} | {_task_allocation(entry.get('tasks'))} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Injected bytes",
            "",
            "What each method delivered. These numbers are not a success measure.",
            "",
            "| Method | Tasks | Total bytes | Mean bytes | Max bytes | Truncated tasks |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for method in _entries(payload["methods"], "methods"):
        injected = _mapping(method.get("injected_bytes"), "methods[].injected_bytes")
        lines.append(
            f"| `{method['arm_id']}` | {method['task_count']} | {injected['total']} | "
            f"{injected['mean']} | {injected['max']} | {injected['truncated_task_count']} |"
        )
    lines.extend(
        [
            "",
            "## Continuation outcome",
            "",
            (
                "What the recorded continuations did. Success counts recovered tasks; the conflict columns count "
                "recorded steps, and none of them is derived from the byte counts above."
            ),
            "",
            (
                "| Method | Recorded | Recovered | Mean recovery step | Incorrect assumptions | "
                "Missing evidence | Unverifiable claims | Corrections |"
            ),
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for method in _entries(payload["methods"], "methods"):
        outcome = _mapping(method.get("outcome"), "methods[].outcome")
        mean = outcome["mean_time_to_recover_state"]
        lines.append(
            f"| `{method['arm_id']}` | {outcome['recorded_attempts']} | {outcome['task_success']} | "
            f"{'-' if mean is None else mean} | {outcome['incorrect_assumptions']} | "
            f"{outcome['missing_evidence']} | {outcome['unverifiable_claims']} | "
            f"{outcome['user_correction_burden']} |"
        )
    lines.extend(
        [
            "",
            "## Delivered context quality",
            "",
            (
                "RFC 1783 requirements checked against the fields that survived the byte ceiling. The complete "
                "draft is counted next to them, not in place of them, so a budget that emptied a context cannot "
                "certify it as satisfying the requirements. A method that carries no Handoff draft reports "
                "`n/a`, because it was never checked rather than found wanting."
            ),
            "",
            "| Method | Deliveries checked | Delivered satisfied | Drafts checked | Drafts satisfied |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for method in _entries(payload["methods"], "methods"):
        quality = _mapping(method.get("context_quality"), "methods[].context_quality")
        lines.append(f"| `{method['arm_id']}` | {' | '.join(_quality_columns(quality))} |")
    lines.extend(
        [
            "",
            "## Per task",
            "",
            "| Task | Kind | Treatment outcome | Baselines ranked above the treatment |",
            "| --- | --- | --- | --- |",
        ]
    )
    for task in _entries(payload["tasks"], "tasks"):
        by_host = _mapping(task.get("treatment_by_host"), "tasks[].treatment_by_host")
        treatment = (
            "; ".join(f"{host}: {summary}" for host, summary in by_host.items())
            if by_host
            else "assembly only, no recorded attempt"
        )
        beaten_by = task.get("underperforming_baselines")
        if not isinstance(beaten_by, list):
            raise ReportError("report payload tasks[].underperforming_baselines must be an array")
        lines.append(
            f"| `{task['task_id']}` | {task['kind']} | {treatment} | "
            f"{', '.join(str(entry) for entry in beaten_by) if beaten_by else 'none'} |"
        )
    failure = _mapping(payload.get("failure_analysis"), "failure_analysis")
    available = _analysis_available(failure)
    lines.extend(
        [
            "",
            "## Failure analysis",
            "",
            f"Findings by class: {_inline_counts(failure['findings_by_class']) if available else 'unavailable'}",
            "",
        ]
    )
    unrecorded = failure["unrecorded_keys"]
    if not isinstance(unrecorded, list):
        raise ReportError("report payload failure_analysis.unrecorded_keys must be an array")
    if not available:
        lines.append(f"Recording coverage is unavailable: {_unavailable_reason(failure)}")
    elif unrecorded:
        lines.append(f"Unrecorded task/method/host combinations: {len(unrecorded)}")
    else:
        lines.append("Every selected task and method has a recorded attempt.")
    lines.extend(["", "### Where the treatment underperformed", ""])
    comparison = _mapping(payload.get("comparison"), "comparison")
    underperformance = (
        _entries(failure.get("treatment_underperformance"), "failure_analysis.treatment_underperformance")
        if available
        else []
    )
    if not available:
        lines.append("No comparison was performed, because recording coverage is unavailable for this run.")
    elif _compared_pair_count(comparison) == 0:
        lines.append(f"No comparison was performed: {_comparison_reason(comparison)}.")
    elif not underperformance:
        lines.append("The treatment did not rank below a baseline for any task and host in this run.")
    for entry in underperformance:
        beaten_by = entry.get("beaten_by")
        if not isinstance(beaten_by, list):
            raise ReportError("report payload treatment_underperformance[].beaten_by must be an array")
        caused_by = entry.get("treatment_failure_class") or "no classified failure"
        lines.append(
            f"- `{entry['task_id']}` on `{entry['host']}`: `{TREATMENT_ARM_ID}` ranked below "
            f"{', '.join(f'`{arm}`' for arm in beaten_by)}; treatment failure class "
            f"`{caused_by}`."
        )
    lines.extend(["", "### Findings", ""])
    findings = _entries(failure.get("findings"), "failure_analysis.findings") if available else []
    if not available:
        lines.append("No finding was classified, because recording coverage is unavailable for this run.")
    elif not findings:
        lines.append("No finding was classified for this run.")
    for finding in findings:
        requirement = finding.get("requirement") or "no contract change requested"
        lines.append(
            f"- `{finding['task_id']}` / `{finding['arm_id']}` / `{finding['host']}`: "
            f"**{finding['failure_class']}** — {finding['detail']} (argues about: {requirement})"
        )
    lines.extend(["", "## Recommendations", ""])
    recommendations = _entries(payload.get("recommendations"), "recommendations")
    if not recommendations:
        lines.append("No contract change is recommended by this run.")
    for entry in recommendations:
        lines.append(
            f"- **{entry['requirement'] or 'recording gap'}** ({entry['failure_class']}, "
            f"{entry['occurrences']} finding(s)): {entry['recommendation']}"
        )
    lines.extend(["", "## Boundaries", ""])
    boundaries = payload["boundaries"]
    if not isinstance(boundaries, list):
        raise ReportError("report payload boundaries must be an array")
    lines.extend(f"- {boundary}" for boundary in boundaries)
    lines.append("")
    return "\n".join(lines)


def _task_rows(run: WorkContinuityRun) -> list[dict[str, object]]:
    if run.analysis is None:
        rows: list[dict[str, object]] = []
        for task in run.tasks:
            contexts = [context for context in run.contexts if context.task_id == task.task_id]
            rows.append(
                {
                    "task_id": task.task_id,
                    "kind": task.kind,
                    "treatment_by_host": {},
                    "injected_bytes": {context.arm_id: context.injected_bytes for context in contexts},
                    "underperforming_baselines": [],
                    "findings": [],
                }
            )
        return rows
    rows = []
    for analysis in run.analysis.task_analyses:
        rows.append(
            {
                "task_id": analysis.task_id,
                "kind": run.task(analysis.task_id).kind,
                "treatment_by_host": {host: _outcome_summary(analysis.treatment_for(host)) for host in analysis.hosts},
                "injected_bytes": {outcome.arm_id: outcome.context.injected_bytes for outcome in analysis.outcomes},
                "underperforming_baselines": [
                    f"{outcome.arm_id}@{outcome.host}" for outcome in analysis.underperforming_baselines
                ],
                "findings": [
                    {
                        "arm_id": finding.arm_id,
                        "host": finding.host,
                        "failure_class": finding.failure_class,
                    }
                    for finding in analysis.findings
                ],
            }
        )
    return cast("list[dict[str, object]]", rows)


def _outcome_summary(outcome: ArmOutcome | None) -> str:
    if outcome is None or outcome.score is None:
        return "not recorded"
    score = outcome.score
    if not score.task_success:
        return "not recovered"
    return f"recovered at step {score.time_to_recover_state}"


def _failure_block(analysis: WorkContinuityAnalysis | None) -> dict[str, object]:
    if analysis is None:
        return {
            "available": False,
            "reason": "no recorded attempts were supplied, so no outcome could be classified",
            "findings": [],
            "findings_by_class": {},
            "underperforming_task_ids": [],
            "treatment_underperformance": [],
            "unrecorded_keys": [],
        }
    return {
        "available": True,
        "treatment_arm_id": TREATMENT_ARM_ID,
        "underperforming_task_ids": list(analysis.underperforming_task_ids),
        "treatment_underperformance": [
            {
                "task_id": entry.task_id,
                "host": entry.host,
                "beaten_by": list(entry.beaten_by),
                "treatment_failure_class": entry.treatment_failure_class,
                "requirement": entry.requirement,
                "recommendation": entry.recommendation,
            }
            for entry in analysis.underperformance
        ],
        "findings_by_class": analysis.findings_by_class,
        "unrecorded_keys": [list(key) for key in analysis.unrecorded_keys],
        "findings": [
            {
                "task_id": finding.task_id,
                "arm_id": finding.arm_id,
                "host": finding.host,
                "failure_class": finding.failure_class,
                "requirement": finding.requirement,
                "detail": finding.detail,
                "recommendation": finding.recommendation,
            }
            for finding in analysis.findings
        ],
    }


@dataclass
class _Recommendation:
    """One recommendation aggregated over every finding that asked for it."""

    failure_class: str
    requirement: str | None
    recommendation: str
    occurrences: int = 0
    tasks: list[str] = field(default_factory=list)
    arms: list[str] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, object]:
        return {
            "failure_class": self.failure_class,
            "requirement": self.requirement,
            "recommendation": self.recommendation,
            "occurrences": self.occurrences,
            "tasks": list(self.tasks),
            "arms": list(self.arms),
            "hosts": list(self.hosts),
        }


def _recommendations(analysis: WorkContinuityAnalysis | None) -> list[dict[str, object]]:
    """Collapse findings into one recommendation per failure class.

    ``no_recording`` is excluded: it asks for evidence rather than for a contract
    change, and mixing the two would make an incomplete run look like a finding.
    """

    if analysis is None:
        return []
    grouped: dict[str, _Recommendation] = {}
    for finding in analysis.findings:
        if finding.failure_class == NO_RECORDING:
            continue
        entry = grouped.setdefault(
            finding.failure_class,
            _Recommendation(
                failure_class=finding.failure_class,
                requirement=finding.requirement,
                recommendation=finding.recommendation,
            ),
        )
        entry.occurrences += 1
        _append_unique(entry.tasks, finding.task_id)
        _append_unique(entry.arms, finding.arm_id)
        _append_unique(entry.hosts, finding.host)
    return [grouped[key].as_json() for key in sorted(grouped)]


def _append_unique(target: list[str], value: str) -> None:
    if value not in target:
        target.append(value)


def _boundaries(run: WorkContinuityRun) -> list[str]:
    boundaries = [
        (
            "The checked-in task set is authored fixture material for harness validation, not a sample of real "
            "user sessions, and its ground truth is declared rather than harvested."
        ),
        (
            "The harness runs no model: a recorded attempt is the only input that describes what a host did, and "
            "the recorder is responsible for mapping steps onto the task's declared fact ids."
        ),
        (
            "Injected bytes count the assembled continuation context only. A host's own system prompt, tool "
            "schemas, repository instructions, and workspace files are outside this measurement."
        ),
        (
            "Task success is scored against one declared next action per task, so a different but equally correct "
            "continuation is not credited."
        ),
        (
            "Handoff quality is checked against the fields the byte ceiling actually delivered, with the complete "
            "draft counted next to them, so a truncated context cannot pass on material it never carried."
        ),
        (
            "Recording coverage is not comparison coverage: a comparison exists only where the treatment and a "
            "baseline were both recorded on the same task and host, so a run can record every selected method and "
            "still report that no comparison was performed."
        ),
    ]
    if run.analysis is None:
        boundaries.append("No recorded attempts were supplied, so this run reports assembly only.")
    else:
        boundaries.append(
            "Recorded hosts in this run are "
            + ", ".join(f"`{host}`" for host in run.hosts)
            + ". Names do not imply a verified host integration."
        )
    return boundaries


def _host_names(payload: dict[str, object]) -> list[str]:
    """Return the report's host names, or a placeholder when none recorded."""

    hosts = payload.get("hosts")
    if not isinstance(hosts, list) or any(not isinstance(host, str) for host in hosts):
        raise ReportError("report payload hosts must be an array of strings")
    names = [host for host in hosts if isinstance(host, str)]
    return names or ["none (assembly only)"]


def _mapping(value: object, label: str) -> dict[str, object]:
    """Return one report payload member as a JSON object mapping."""

    if not isinstance(value, dict):
        raise ReportError(f"report payload {label} must be an object")
    return cast("dict[str, object]", value)


def _entries(value: object, label: str) -> list[dict[str, object]]:
    """Return one report payload member as a list of JSON object mappings."""

    if not isinstance(value, list) or any(not isinstance(entry, dict) for entry in value):
        raise ReportError(f"report payload {label} must be an array of objects")
    return [cast("dict[str, object]", entry) for entry in value]


def _inline_counts(counts: object) -> str:
    rendered = ", ".join(f"{key}={value}" for key, value in sorted(_mapping(counts, "findings_by_class").items()))
    return rendered or "none"


def _quality_columns(quality: dict[str, object]) -> tuple[str, str, str, str]:
    """Render the four quality columns, or ``n/a`` for a method with no Handoff draft.

    A baseline carries no Handoff draft, so its zeros are the absence of a check
    rather than a check that found nothing; the block's own applicability flag is
    what separates the two.
    """

    if quality.get("applicable") is not True:
        return ("n/a", "n/a", "n/a", "n/a")
    return (
        f"{quality['checked_task_count']}",
        f"{quality['satisfied_task_count']}",
        f"{quality['draft_checked_task_count']}",
        f"{quality['draft_satisfied_task_count']}",
    )


def _task_allocation(tasks: object) -> str:
    """Render one host's per-task, per-method recording counts for the configuration table.

    The table has to show the unit the comparison gate compares — one task under
    one method — so a per-task total is not enough: two runs can agree on every
    task total while running the same method under the other model.
    """

    if not isinstance(tasks, dict):
        raise ReportError("report payload execution_configuration[].tasks must be an object")
    if not tasks:
        return "none"
    rendered: list[str] = []
    for task_id, arms in sorted(tasks.items()):
        if not isinstance(arms, dict):
            raise ReportError("report payload execution_configuration[].tasks[].methods must be an object")
        if not arms:
            continue
        recorded = ", ".join(f"`{arm_id}` x{count}" for arm_id, count in sorted(arms.items()))
        rendered.append(f"`{task_id}` {recorded}")
    return "; ".join(rendered) or "none"


def _analysis_available(failure: dict[str, object]) -> bool:
    """Return whether the run recorded anything it could classify.

    An assembly-only run carries an empty coverage list and an empty comparison
    list because neither was measured. Reporting those absences as "every method
    has a recorded attempt" and "the treatment did not rank below any baseline"
    would turn "not measured" into a clean bill of health.
    """

    return failure.get("available") is True


def _unavailable_reason(failure: dict[str, object]) -> str:
    reason = failure.get("reason")
    return reason if isinstance(reason, str) and reason.strip() else "no recorded attempts were supplied"


def _compared_pair_count(comparison: dict[str, object]) -> int:
    """Return how many task/host units held both the treatment and a baseline.

    Recording coverage only says every selected pair has an attempt. A comparison
    needs a pair to compare *of the same task on the same host*, so this is what
    separates "nothing looked wrong" from "nothing was looked at".
    """

    count = comparison.get("compared_pair_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ReportError("report payload comparison.compared_pair_count must be a non-negative integer")
    return count


def _comparison_reason(comparison: dict[str, object]) -> str:
    """Name the specific absence that left this run without a comparison."""

    baselines = comparison.get("baselines_selected")
    if not isinstance(baselines, list):
        raise ReportError("report payload comparison.baselines_selected must be an array")
    treatment_arm_id = comparison.get("treatment_arm_id")
    if not isinstance(treatment_arm_id, str):
        raise ReportError("report payload comparison.treatment_arm_id must be a string")
    if comparison.get("treatment_selected") is not True:
        return f"the treatment arm `{treatment_arm_id}` was not selected in this run"
    if not baselines:
        return "no baseline arm was selected in this run"
    if comparison.get("treatment_recorded") is not True:
        return f"the treatment arm `{treatment_arm_id}` has no recorded attempt in this run"
    recorded = comparison.get("baselines_recorded")
    if not isinstance(recorded, list):
        raise ReportError("report payload comparison.baselines_recorded must be an array")
    if not recorded:
        return "no baseline arm has a recorded attempt in this run"
    return (
        "the treatment and its baselines were never recorded on the same task and host, and this benchmark "
        "only compares one task on one host at a time"
    )
