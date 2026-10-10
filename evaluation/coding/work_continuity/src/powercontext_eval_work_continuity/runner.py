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

"""Orchestration for one work-continuity run.

A run has two halves that stay separable. Assembly is always available and needs
no host: it renders each method's continuation context and measures injected
bytes. Scoring needs recorded attempts, and when none are supplied the run
reports assembly only instead of inventing an outcome.

Neither half calls a model, a server, or a provider. A run therefore reproduces
byte for byte from the lock file alone, and the recorded attempts are the only
input that carries what a host observed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from powercontext_eval_work_continuity.analysis import (
    ArmOutcome,
    TaskAnalysis,
    WorkContinuityAnalysis,
    analyse_task_outcomes,
    analyse_work_continuity,
)
from powercontext_eval_work_continuity.arms import (
    BASELINE_ARM_IDS,
    CONTINUATION_ARMS,
    DEFAULT_ASSEMBLY_MAX_BYTES,
    TREATMENT_ARM_ID,
    ContinuationArm,
    arm_manifest_record,
    resolve_continuation_arms,
)
from powercontext_eval_work_continuity.assembly import ContinuationContext, assemble_context
from powercontext_eval_work_continuity.attempts import AttemptSet, load_attempts
from powercontext_eval_work_continuity.catalog import ContinuationTask, TaskCatalog
from powercontext_eval_work_continuity.errors import PowerContextEvalError
from powercontext_eval_work_continuity.quality import QualityReport
from powercontext_eval_work_continuity.rubric import AttemptScore, score_attempt

ASSEMBLY_ONLY_CLASSIFICATION = "assembly-only-no-recorded-attempts"
RECORDED_CLASSIFICATION = "recorded-attempts-not-a-complete-benchmark-result"
CONTINUATION_WORKLOAD = "work-continuity"
RUN_MANIFEST_SCHEMA = "powercontext.work-continuity-run-manifest.v1"


class WorkContinuityRunError(PowerContextEvalError):
    """A work-continuity run cannot satisfy its own contract."""


@dataclass(frozen=True)
class WorkContinuityRun:
    """Everything one work-continuity run measured, before it is rendered."""

    workload: str
    run_id: str
    task_set_id: str
    classification: str
    max_bytes: int
    hosts: tuple[str, ...]
    tasks: tuple[ContinuationTask, ...]
    contexts: tuple[ContinuationContext, ...]
    scores: tuple[AttemptScore, ...]
    analysis: WorkContinuityAnalysis | None
    manifest: Mapping[str, object]

    def context(self, task_id: str, arm_id: str) -> ContinuationContext:
        for context in self.contexts:
            if context.task_id == task_id and context.arm_id == arm_id:
                return context
        raise WorkContinuityRunError(f"no assembled context for task {task_id} arm {arm_id}")

    def task(self, task_id: str) -> ContinuationTask:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise WorkContinuityRunError(f"no selected task {task_id}")


def run_work_continuity(
    *,
    task_lock: Path,
    run_id: str,
    max_bytes: int = DEFAULT_ASSEMBLY_MAX_BYTES,
    arm_ids: tuple[str, ...] | None = None,
    task_ids: Sequence[str] | None = None,
    attempts_path: Path | None = None,
    powercontext_revision: str | None = None,
    integration_revision: str | None = None,
) -> WorkContinuityRun:
    """Assemble every selected arm and, when supplied, score every recorded attempt."""

    if max_bytes < 1:
        raise WorkContinuityRunError("Work-continuity assembly budget must be positive")
    catalog = TaskCatalog.load(task_lock)
    arms = resolve_continuation_arms(arm_ids)
    tasks = catalog.select(task_ids)
    attempts = load_attempts(attempts_path, catalog=catalog) if attempts_path is not None else None
    _require_attempts_cover_selection(tasks, arms, attempts)
    _require_attempts_match_protocol(attempts, catalog=catalog, max_bytes=max_bytes)

    contexts = tuple(assemble_context(task, arm, max_bytes=max_bytes) for task in tasks for arm in arms)
    if attempts is not None:
        _require_attempts_match_contexts(attempts, contexts)
    scores: list[AttemptScore] = []
    analysis: WorkContinuityAnalysis | None = None
    hosts: tuple[str, ...] = ()
    if attempts is not None:
        hosts = attempts.hosts
        by_key = {(context.task_id, context.arm_id): context for context in contexts}
        task_analyses: list[TaskAnalysis] = []
        for task in tasks:
            task_outcomes: list[ArmOutcome] = []
            for arm in arms:
                context = by_key[(task.task_id, arm.arm_id)]
                for host in hosts:
                    matching = tuple(
                        attempt
                        for attempt in attempts.for_task_and_arm(task.task_id, arm.arm_id)
                        if attempt.host == host
                    )
                    score = score_attempt(task, context, matching[0]) if matching else None
                    if score is not None:
                        scores.append(score)
                    task_outcomes.append(
                        ArmOutcome(
                            task_id=task.task_id,
                            arm_id=arm.arm_id,
                            host=host,
                            context=context,
                            score=score,
                        )
                    )
            task_analyses.append(analyse_task_outcomes(task, task_outcomes))
        analysis = analyse_work_continuity(task_analyses)

    manifest = _manifest(
        catalog=catalog,
        tasks=tasks,
        arms=arms,
        max_bytes=max_bytes,
        run_id=run_id,
        attempts=attempts,
        powercontext_revision=powercontext_revision,
        integration_revision=integration_revision,
    )
    return WorkContinuityRun(
        workload=CONTINUATION_WORKLOAD,
        run_id=run_id,
        task_set_id=catalog.task_set_id,
        classification=ASSEMBLY_ONLY_CLASSIFICATION if attempts is None else RECORDED_CLASSIFICATION,
        max_bytes=max_bytes,
        hosts=hosts,
        tasks=tuple(tasks),
        contexts=contexts,
        scores=tuple(scores),
        analysis=analysis,
        manifest=manifest,
    )


def write_run_artifacts(run: WorkContinuityRun, output_dir: Path) -> RunArtifacts:
    """Write one run directory, refusing to overwrite an existing one."""

    target = output_dir.resolve()
    if target.exists():
        raise WorkContinuityRunError(f"Work-continuity output directory already exists: {target}")
    target.mkdir(parents=True)
    manifest_path = target / "run-manifest.json"
    assembly_path = target / "assembly.jsonl"
    scores_path = target / "scores.jsonl"
    summary_path = target / "run-summary.json"
    _write_json(manifest_path, run.manifest)
    _write_jsonl(assembly_path, (_assembly_row(context) for context in run.contexts))
    _write_jsonl(scores_path, (_score_row(score) for score in run.scores))
    _write_json(summary_path, run_summary(run))
    return RunArtifacts(
        output_dir=target,
        manifest_path=manifest_path,
        assembly_path=assembly_path,
        scores_path=scores_path,
        summary_path=summary_path,
    )


@dataclass(frozen=True)
class RunArtifacts:
    """Paths written for one work-continuity run."""

    output_dir: Path
    manifest_path: Path
    assembly_path: Path
    scores_path: Path
    summary_path: Path


def run_summary(run: WorkContinuityRun) -> dict[str, object]:
    """Aggregate one run per method, keeping outcome and injected bytes apart."""

    arm_ids = tuple(dict.fromkeys(context.arm_id for context in run.contexts))
    by_arm: list[dict[str, object]] = []
    for arm_id in arm_ids:
        contexts = tuple(context for context in run.contexts if context.arm_id == arm_id)
        scores = tuple(score for score in run.scores if score.arm_id == arm_id)
        by_arm.append(
            {
                "arm_id": arm_id,
                "method": contexts[0].method,
                "task_count": len(contexts),
                "injected_bytes": {
                    "total": sum(context.injected_bytes for context in contexts),
                    "mean": round(sum(context.injected_bytes for context in contexts) / len(contexts), 1),
                    "max": max(context.injected_bytes for context in contexts),
                    "truncated_task_count": sum(1 for context in contexts if context.truncated),
                },
                "outcome": {
                    "recorded_attempts": len(scores),
                    "task_success": sum(1 for score in scores if score.task_success),
                    "incorrect_assumptions": sum(score.incorrect_assumptions for score in scores),
                    "missing_evidence": sum(score.missing_evidence for score in scores),
                    "unverifiable_claims": sum(score.unverifiable_claims for score in scores),
                    "user_correction_burden": sum(score.user_correction_burden for score in scores),
                    "mean_time_to_recover_state": _mean_recovery(scores),
                },
                "context_quality": _quality_block(contexts),
            }
        )
    summary: dict[str, object] = {
        "workload": run.workload,
        "run_id": run.run_id,
        "task_set_id": run.task_set_id,
        "classification": run.classification,
        "assembly_max_bytes": run.max_bytes,
        "hosts": list(run.hosts),
        "execution_configuration": run.manifest.get("execution_configuration", []),
        "comparison": _comparison_block(run),
        "arms": by_arm,
    }
    if run.analysis is not None:
        summary["failure_analysis"] = {
            "underperforming_task_ids": list(run.analysis.underperforming_task_ids),
            "findings_by_class": run.analysis.findings_by_class,
            "unrecorded_keys": [list(key) for key in run.analysis.unrecorded_keys],
        }
    return summary


def _comparison_block(run: WorkContinuityRun) -> dict[str, object]:
    """State whether this run held a baseline/treatment pair at all.

    Recording coverage and comparison coverage are different questions, and the
    unit of comparison is one task on one host. A run can have a recorded attempt
    for every selected task and method and still hold no comparison: the treatment
    may not be selected, may have no recording, or may never have been recorded on
    the same task *and* host as a baseline. Reporting "the treatment did not rank
    below a baseline" for such a run describes a measurement that never happened.

    Coverage is therefore read from the matched ``(task, host)`` outcome, not from
    a per-host set of arm ids. Grouping by host alone would let a run whose
    recordings partition the tasks between the arms report a comparison on a host
    where no task ever had both.
    """

    selected = tuple(dict.fromkeys(context.arm_id for context in run.contexts))
    baselines = tuple(arm_id for arm_id in selected if arm_id in BASELINE_ARM_IDS)
    recorded = {score.arm_id for score in run.scores}
    by_unit: dict[tuple[str, str], set[str]] = {}
    for score in run.scores:
        by_unit.setdefault((score.task_id, score.host), set()).add(score.arm_id)
    compared_pairs = tuple(
        sorted(
            unit
            for unit, arm_ids in by_unit.items()
            if TREATMENT_ARM_ID in arm_ids and any(baseline in arm_ids for baseline in baselines)
        )
    )
    return {
        "treatment_arm_id": TREATMENT_ARM_ID,
        "treatment_selected": TREATMENT_ARM_ID in selected,
        "treatment_recorded": TREATMENT_ARM_ID in recorded,
        "baselines_selected": list(baselines),
        "baselines_recorded": [baseline for baseline in baselines if baseline in recorded],
        "compared_pair_count": len(compared_pairs),
        "compared_pairs": [[task_id, host] for task_id, host in compared_pairs],
    }


def _require_attempts_cover_selection(
    tasks: Sequence[ContinuationTask],
    arms: Sequence[ContinuationArm],
    attempts: AttemptSet | None,
) -> None:
    """Reject an attempt set that names a task or arm the run did not select.

    Silently ignoring an extra recording would let a run look complete while a
    recorded method never appears in its comparison.
    """

    if attempts is None:
        return
    task_ids = {task.task_id for task in tasks}
    arm_ids = {arm.arm_id for arm in arms}
    for attempt in attempts.attempts:
        if attempt.task_id not in task_ids:
            raise WorkContinuityRunError(f"attempts name task {attempt.task_id} but the run selects {sorted(task_ids)}")
        if attempt.arm_id not in arm_ids:
            raise WorkContinuityRunError(f"attempts name arm {attempt.arm_id} but the run selects {sorted(arm_ids)}")


def _require_attempts_match_protocol(
    attempts: AttemptSet | None,
    *,
    catalog: TaskCatalog,
    max_bytes: int,
) -> None:
    """Reject a recording made under a protocol other than the one this run assembles.

    Contexts are a deterministic function of the task lock and the byte ceiling,
    so a recording that pins different ones is evidence about contexts this run
    never built. Scoring it anyway is how a default-ceiling recording ended up
    reporting successes for contexts assembled under a one-byte ceiling.
    """

    if attempts is None:
        return
    protocol = attempts.protocol
    if protocol.task_set_id != catalog.task_set_id:
        raise WorkContinuityRunError(
            f"recorded attempts declare task set {protocol.task_set_id!r} but the run loaded {catalog.task_set_id!r}"
        )
    if protocol.task_lock_sha256 != catalog.content_sha256:
        raise WorkContinuityRunError(
            "recorded attempts were made against a different task lock: "
            f"attempts pin {protocol.task_lock_sha256} but this run loaded {catalog.content_sha256}"
        )
    if protocol.assembly_max_bytes != max_bytes:
        raise WorkContinuityRunError(
            f"recorded attempts were made under a {protocol.assembly_max_bytes} byte ceiling but this run "
            f"assembles contexts under {max_bytes} bytes"
        )


def _require_attempts_match_contexts(
    attempts: AttemptSet,
    contexts: Sequence[ContinuationContext],
) -> None:
    """Reject a recording whose declared context digest is not the one delivered.

    Selection coverage is checked separately, so every attempt here has a context
    to bind to; the digest is what proves the recording is about that context and
    not about an earlier assembly of the same task and arm.
    """

    by_key = {(context.task_id, context.arm_id): context for context in contexts}
    for attempt in attempts.attempts:
        context = by_key.get((attempt.task_id, attempt.arm_id))
        if context is None:
            raise WorkContinuityRunError(
                f"recorded attempt for task {attempt.task_id} arm {attempt.arm_id} has no assembled context"
            )
        if attempt.context_sha256 != context.content_sha256:
            raise WorkContinuityRunError(
                f"recorded attempt for task {attempt.task_id} arm {attempt.arm_id} host {attempt.host!r} "
                f"declares context {attempt.context_sha256} but this run delivers {context.content_sha256}"
            )


def _manifest(
    *,
    catalog: TaskCatalog,
    tasks: Sequence[ContinuationTask],
    arms: Sequence[ContinuationArm],
    max_bytes: int,
    run_id: str,
    attempts: AttemptSet | None,
    powercontext_revision: str | None,
    integration_revision: str | None,
) -> dict[str, object]:
    return {
        "schema": RUN_MANIFEST_SCHEMA,
        "workload": CONTINUATION_WORKLOAD,
        "run_id": run_id,
        "task_set_id": catalog.task_set_id,
        "task_ids": [task.task_id for task in tasks],
        "task_evidence_digest": _task_evidence_digest(tasks),
        "inputs": {
            "task_lock": {
                "path": catalog.path.name,
                "content_sha256": catalog.content_sha256,
            },
            "attempts": (
                None
                if attempts is None
                else {
                    "path": attempts.path.name,
                    "content_sha256": attempts.content_sha256,
                    "protocol": {
                        "task_set_id": attempts.protocol.task_set_id,
                        "task_lock_sha256": attempts.protocol.task_lock_sha256,
                        "assembly_max_bytes": attempts.protocol.assembly_max_bytes,
                    },
                }
            ),
        },
        "assembly": {"max_bytes": max_bytes},
        "experiment_arm": arm_manifest_record(arms[0], max_bytes=max_bytes),
        "comparable_arms": [arm_manifest_record(arm, max_bytes=max_bytes) for arm in arms],
        "revisions": {
            "powercontext": powercontext_revision,
            "integration": integration_revision,
        },
        "hosts": list(attempts.hosts) if attempts is not None else [],
        "execution_configuration": execution_configuration(attempts),
    }


def execution_configuration(attempts: AttemptSet | None) -> list[dict[str, object]]:
    """Return the declared execution configuration of every recorded host.

    The configuration is retained rather than only used for validation, so a
    reader can see which model and host revision produced the outcomes and can
    tell a method difference from a configuration difference. Each entry also
    carries how many recordings that host contributed and how those recordings
    were distributed over the tasks *and the methods*: the comparison gate has to
    weigh a configuration by its share of the outcomes at the unit this benchmark
    scores — one task under one method. Two runs can agree on the hosts, the
    models, the host revisions, the tasks and the totals while running a method on
    the other model, and with method-specific model behaviour that swap alone
    moves the per-arm success counts. A per-host per-task total cannot see that
    swap, because it merges the attempts every method made on that task.
    """

    if attempts is None:
        return []
    by_host: dict[str, tuple[str, str]] = {}
    counts: dict[str, int] = {}
    by_task: dict[str, dict[str, dict[str, int]]] = {}
    for attempt in attempts.attempts:
        by_host.setdefault(attempt.host, attempt.configuration)
        counts[attempt.host] = counts.get(attempt.host, 0) + 1
        per_arm = by_task.setdefault(attempt.host, {}).setdefault(attempt.task_id, {})
        per_arm[attempt.arm_id] = per_arm.get(attempt.arm_id, 0) + 1
    return [
        {
            "host": host,
            "host_revision": configuration[0],
            "model": configuration[1],
            "attempt_count": counts[host],
            "tasks": {task_id: dict(sorted(per_arm.items())) for task_id, per_arm in sorted(by_task[host].items())},
        }
        for host, configuration in sorted(by_host.items())
    ]


def require_recording_bindings(*, catalog: TaskCatalog, attempts: AttemptSet) -> None:
    """Check a recording against the contexts its own declared protocol produces.

    ``run`` applies the same checks to the contexts it assembles for its own
    selection. This is the ceiling-independent form, used by the preflight: a
    recording that pins another task set, another task lock, or a context digest
    this project cannot assemble is rejected here too, so the documented
    validation step catches what the run would refuse.
    """

    max_bytes = attempts.protocol.assembly_max_bytes
    _require_attempts_match_protocol(attempts, catalog=catalog, max_bytes=max_bytes)
    contexts = tuple(
        assemble_context(catalog.require(task_id), arm, max_bytes=max_bytes)
        for task_id in catalog.task_ids
        for arm in CONTINUATION_ARMS
    )
    _require_attempts_match_contexts(attempts, contexts)


def _task_evidence_digest(tasks: Sequence[ContinuationTask]) -> str:
    """Hash the selected ground truth so a run names exactly what it scored against."""

    payload = json.dumps(
        [
            {
                "task_id": task.task_id,
                "expected_action": task.expected_next_action.action_id,
                "required_fact_ids": list(task.expected_next_action.required_fact_ids),
                "obsolete_fact_ids": list(task.obsolete_fact_ids),
                "unavailable_fact_ids": list(task.unavailable_fact_ids),
            }
            for task in tasks
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assembly_row(context: ContinuationContext) -> dict[str, object]:
    return {
        "task_id": context.task_id,
        "arm_id": context.arm_id,
        "method": context.method,
        "max_bytes": context.max_bytes,
        "injected_bytes": context.injected_bytes,
        "line_count": context.line_count,
        "truncated": context.truncated,
        "delivered_item_count": len(context.delivered_item_ids),
        "dropped_item_ids": list(context.dropped_item_ids),
        "delivered_turn_numbers": list(context.delivered_turn_numbers),
        "delivered_fact_ids": list(context.delivered_fact_ids),
        "unavailable_fact_ids": list(context.unavailable_fact_ids),
        "delivered_superseded_turns": list(context.delivered_superseded_turns),
        "carries_next_action": context.carries_next_action,
        "content_sha256": context.content_sha256,
        "quality": _quality_row(context.quality),
        "draft_quality": _quality_row(context.draft_quality),
    }


def _quality_row(report: QualityReport | None) -> dict[str, object] | None:
    if report is None:
        return None
    return {
        "satisfied": report.satisfied,
        "violations_by_requirement": report.violations_by_requirement,
        "violations": [
            {
                "requirement": finding.requirement,
                "field": finding.field,
                "detail": finding.detail,
            }
            for finding in report.violations
        ],
        "advisories": [
            {
                "requirement": finding.requirement,
                "field": finding.field,
                "detail": finding.detail,
            }
            for finding in report.advisories
        ],
    }


def _score_row(score: AttemptScore) -> dict[str, object]:
    return {
        "task_id": score.task_id,
        "arm_id": score.arm_id,
        "host": score.host,
        "task_success": score.task_success,
        "time_to_recover_state": score.time_to_recover_state,
        "incorrect_assumptions": score.incorrect_assumptions,
        "missing_evidence": score.missing_evidence,
        "unverifiable_claims": score.unverifiable_claims,
        "user_correction_burden": score.user_correction_burden,
        "injected_bytes": score.injected_bytes,
        "assembly_max_bytes": score.max_bytes,
        "context_truncated": score.context_truncated,
        "context_assembly_gap": score.has_assembly_gap,
        "facts_missing_from_context": list(score.facts_missing_from_context),
        "steps": [
            {
                "step": scored.step,
                "relied_on": list(scored.relied_on),
                "performed_action_id": scored.performed_action_id,
                "performs_expected_action": scored.performs_expected_action,
                "superseded_reliance": list(scored.superseded_reliance),
                "undelivered_reliance": list(scored.undelivered_reliance),
                "unavailable_reliance": list(scored.unavailable_reliance),
                "correction": scored.correction,
                "is_recovery": scored.is_recovery,
            }
            for scored in score.steps
        ],
    }


def _quality_block(contexts: Sequence[ContinuationContext]) -> dict[str, object]:
    """Summarise handoff quality for what was delivered, and separately for what was drafted.

    Truncation must not be able to certify an unusable context, so the delivered
    counts are the headline and the draft counts are reported next to them rather
    than in place of them. A method that carries no Handoff draft at all has
    nothing to check rather than a checked total of zero, so the block says which
    of the two it is: ``0 of 0 satisfied`` would otherwise read as a measured
    failure as easily as it reads as an absent question.
    """

    delivered = [context.quality for context in contexts if context.quality is not None]
    drafts = [context.draft_quality for context in contexts if context.draft_quality is not None]
    return {
        "applicable": bool(delivered),
        "checked_task_count": len(delivered),
        "satisfied_task_count": sum(1 for report in delivered if report.satisfied),
        "draft_checked_task_count": len(drafts),
        "draft_satisfied_task_count": sum(1 for report in drafts if report.satisfied),
        "violations_by_requirement": _violation_counts(delivered),
    }


def _violation_counts(reports: Sequence[QualityReport]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for report in reports:
        for requirement, count in report.violations_by_requirement.items():
            counts[requirement] = counts.get(requirement, 0) + count
    return counts


def _mean_recovery(scores: Sequence[AttemptScore]) -> float | None:
    recovered = [score.time_to_recover_state for score in scores if score.time_to_recover_state is not None]
    if not recovered:
        return None
    return round(sum(recovered) / len(recovered), 2)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as sink:
        for row in rows:
            sink.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
