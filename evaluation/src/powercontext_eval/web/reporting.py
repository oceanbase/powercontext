"""Validated, bounded projection of retained evaluation reports."""

from __future__ import annotations

import json
import os
import stat
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ValidationError

from powercontext_eval.artifacts import ArmState
from powercontext_eval.benchmarks.swebench_pro.adapter import DATASET_REVISION, HARNESS_COMMIT, SweBenchProInstance
from powercontext_eval.report import ArmReport, ReportBundle, TestGroupReport
from powercontext_eval.web.batches import (
    BatchRecord,
    BatchReportResponse,
    BatchTaskDetailResponse,
    BatchTaskItem,
    BatchTaskPage,
    ContextEventPage,
    ContextEventResponse,
    OfficialTestGroup,
    PairCategory,
    RequiredTests,
    ResolutionAggregate,
    TaskArmSummary,
    TaskDetailArm,
    TaskTokenDelta,
    TokenAggregate,
    TokenMetricAggregate,
    TokensFlowFinalizationPair,
    TokensFlowFinalizationSummary,
)
from powercontext_eval.web.estimation import BatchEstimate, EstimateBasis, EstimateSample, estimate_batch
from powercontext_eval.web.models import (
    ArmResponse,
    ComparisonResponse,
    EvidenceResponse,
    MetricComparison,
    ReportResponse,
    TaskRecord,
    TaskStatus,
    TreatmentEvidence,
)
from powercontext_eval.web.store import FinalizationState, TokensFlowFinalizationRecord
from powercontext_eval.web.usage import UsageSnapshot

_REPORT_JSON_LIMIT = 1024 * 1024
_REPORT_MARKDOWN_LIMIT = 4 * 1024 * 1024
_TREATMENT_LIMIT = 64 * 1024
_CONTEXT_TIMELINE_LIMIT = 64 * 1024 * 1024
_PLUGIN_ID = "powercontext@powercontext"
_COMPARABLE_STATES = {ArmState.TREATMENT_VALIDATED, ArmState.REPORTED}
_TERMINAL_STATES = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.INTERRUPTED,
    TaskStatus.CANCELLED,
}
_EXECUTION_FAILURE_STATES = {TaskStatus.FAILED, TaskStatus.INTERRUPTED}
_SECRET_SHAPED_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "secret_key",
    "token",
}


class BenchmarkCatalog(Protocol):
    @property
    def instance_ids(self) -> tuple[str, ...]: ...

    def require(self, instance_id: str) -> SweBenchProInstance: ...


class ReportingError(Exception):
    """Safe base exception for retained report access."""


class UnsafeReportPath(ReportingError):
    """The requested run is not a safe child of the configured run root."""

    def __init__(self) -> None:
        super().__init__("Evaluation run path is unsafe")


class InvalidReportArtifact(ReportingError):
    """One or more retained report artifacts failed validation."""

    def __init__(self) -> None:
        super().__init__("Evaluation report artifacts are invalid")


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _open_run(run_dir: Path, run_root: Path | None) -> tuple[int, str]:
    requested = run_dir.absolute()
    try:
        root = (requested.parent if run_root is None else run_root).resolve(strict=True)
        metadata = requested.lstat()
        if (
            requested.parent.resolve(strict=True) != root
            or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
        ):
            raise UnsafeReportPath
        descriptor = os.open(requested, _directory_flags())
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            os.close(descriptor)
            raise UnsafeReportPath
    except UnsafeReportPath:
        raise
    except (FileNotFoundError, NotADirectoryError, OSError, RuntimeError):
        raise UnsafeReportPath from None
    return descriptor, requested.name


def _open_relative_directory(root_fd: int, components: tuple[str, ...]) -> int:
    descriptor = os.dup(root_fd)
    try:
        for component in components:
            next_descriptor = os.open(component, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_bounded(root_fd: int, relative: tuple[str, ...], limit: int) -> tuple[bytes, os.stat_result]:
    parent_fd = -1
    descriptor = -1
    try:
        parent_fd = _open_relative_directory(root_fd, relative[:-1])
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(relative[-1], flags, dir_fd=parent_fd)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise InvalidReportArtifact
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            raise InvalidReportArtifact
        return data, metadata
    except InvalidReportArtifact:
        raise
    except (FileNotFoundError, NotADirectoryError, OSError):
        raise InvalidReportArtifact from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_fd >= 0:
            os.close(parent_fd)


def _load_bundle(run_fd: int) -> tuple[ReportBundle, os.stat_result]:
    raw, metadata = _read_bounded(run_fd, ("report.json",), _REPORT_JSON_LIMIT)
    try:
        bundle = ReportBundle.model_validate_json(raw, strict=True)
    except (ValidationError, ValueError, UnicodeDecodeError):
        raise InvalidReportArtifact from None
    if bundle.off.arm != "off" or bundle.on.arm != "on":
        raise InvalidReportArtifact
    return bundle, metadata


def _load_evidence(run_fd: int, arm: str) -> TreatmentEvidence:
    raw, _ = _read_bounded(
        run_fd,
        ("arms", arm, "powercontext", "treatment.json"),
        _TREATMENT_LIMIT,
    )
    try:
        return TreatmentEvidence.model_validate_json(raw, strict=True)
    except (ValidationError, ValueError, UnicodeDecodeError):
        raise InvalidReportArtifact from None


def _validate_evidence(
    bundle: ReportBundle,
    run_id: str,
    off: TreatmentEvidence,
    on: TreatmentEvidence,
) -> None:
    expected_sha = bundle.revisions.get("powercontext")
    configured_plugin_id = bundle.configuration.get("plugin_id", _PLUGIN_ID)
    configured_plugin_version = bundle.configuration.get("plugin_version", off.plugin_version)
    common = (
        expected_sha is not None
        and off.plugin_checkout_sha == expected_sha
        and on.plugin_checkout_sha == expected_sha
        and off.plugin_id == configured_plugin_id == on.plugin_id == _PLUGIN_ID
        and bool(off.plugin_version)
        and off.plugin_version == configured_plugin_version == on.plugin_version
        and off.plugin_installed
        and on.plugin_installed
        and off.server_ready
        and on.server_ready
        and off.scope_id == f"eval:{run_id}:off"
        and on.scope_id == f"eval:{run_id}:on"
    )
    activity = off.prompt_sources == 0 and off.mcp_requests == 0 and on.prompt_sources > 0 and on.mcp_requests > 0
    if not common or not activity:
        raise InvalidReportArtifact


def _arm_response(arm: Literal["off", "on"], report: ArmReport) -> ArmResponse:
    return ArmResponse(
        arm=arm,
        state=report.state,
        resolution="resolved" if report.resolved else "unresolved",
        passed=report.passed,
        treatment_valid=report.treatment_valid,
        input_tokens=report.metrics.input_tokens,
        output_tokens=report.metrics.output_tokens,
        elapsed_seconds=report.metrics.elapsed_seconds,
        patch_bytes=report.metrics.patch_bytes,
    )


def _comparison(off: float | None, on: float | None) -> MetricComparison | None:
    if off is None or on is None:
        return None
    delta = on - off
    percent = None if off == 0 else delta / off * 100
    return MetricComparison(off=off, on=on, delta=delta, percent=percent)


def _comparisons(off: ArmReport, on: ArmReport) -> ComparisonResponse:
    if (
        not off.treatment_valid
        or not on.treatment_valid
        or off.state not in _COMPARABLE_STATES
        or on.state not in _COMPARABLE_STATES
    ):
        return ComparisonResponse()
    return ComparisonResponse(
        input_tokens=_comparison(off.metrics.input_tokens, on.metrics.input_tokens),
        output_tokens=_comparison(off.metrics.output_tokens, on.metrics.output_tokens),
        elapsed_seconds=_comparison(off.metrics.elapsed_seconds, on.metrics.elapsed_seconds),
        patch_bytes=_comparison(off.metrics.patch_bytes, on.metrics.patch_bytes),
    )


def _acceptance_valid(bundle: ReportBundle) -> bool:
    lifecycle_is_comparable = bundle.off.state == bundle.on.state and bundle.off.state in _COMPARABLE_STATES
    official_outcomes_are_coherent = (
        bundle.off.passed is True and bundle.on.passed is True and bundle.off.resolved and bundle.on.resolved
    )
    return (
        bundle.off.treatment_valid
        and bundle.on.treatment_valid
        and lifecycle_is_comparable
        and official_outcomes_are_coherent
    )


def load_report(run_dir: Path, run_root: Path | None = None) -> ReportResponse:
    """Load a report only after validating its retained bundle and treatment evidence."""

    run_fd, run_id = _open_run(run_dir, run_root)
    try:
        bundle, report_metadata = _load_bundle(run_fd)
        off_evidence = _load_evidence(run_fd, "off")
        on_evidence = _load_evidence(run_fd, "on")
        _validate_evidence(bundle, run_id, off_evidence, on_evidence)
        return ReportResponse(
            task_id=run_id,
            acceptance_valid=_acceptance_valid(bundle),
            off=_arm_response("off", bundle.off),
            on=_arm_response("on", bundle.on),
            comparison=_comparisons(bundle.off, bundle.on),
            evidence=EvidenceResponse(off=off_evidence, on=on_evidence),
            gold_validation=bundle.gold_validation,
            revisions=bundle.revisions,
            configuration=bundle.configuration,
            generated_at=datetime.fromtimestamp(report_metadata.st_mtime, tz=UTC),
        )
    except (InvalidReportArtifact, UnsafeReportPath):
        raise
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise InvalidReportArtifact from None
    finally:
        os.close(run_fd)


def load_raw_report(run_dir: Path, run_root: Path | None = None) -> str:
    """Return bounded UTF-8 Markdown as literal text for a ``text/plain`` response."""

    run_fd, _ = _open_run(run_dir, run_root)
    try:
        raw, _ = _read_bounded(run_fd, ("report.md",), _REPORT_MARKDOWN_LIMIT)
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise InvalidReportArtifact from None
    finally:
        os.close(run_fd)


def _validate_batch_tasks(batch: BatchRecord, tasks: Sequence[TaskRecord]) -> None:
    if len(tasks) != batch.total_tasks:
        raise InvalidReportArtifact
    if any(task.batch_id != batch.batch_id for task in tasks):
        raise InvalidReportArtifact
    if [task.source_index for task in tasks] != list(range(batch.total_tasks)):
        raise InvalidReportArtifact
    if len({task.instance_id for task in tasks}) != batch.total_tasks:
        raise InvalidReportArtifact


def _load_batch_bundle(run_dir: Path, run_root: Path) -> ReportBundle:
    run_fd, _ = _open_run(run_dir, run_root)
    try:
        bundle, _ = _load_bundle(run_fd)
        return bundle
    finally:
        os.close(run_fd)


def _bundle_for_task(task: TaskRecord, runs_root: Path) -> ReportBundle:
    if task.status is not TaskStatus.SUCCEEDED or task.result is None:
        raise InvalidReportArtifact
    bundle = _load_batch_bundle(task_run_dir(task, runs_root), runs_root)
    if (
        bundle.off.arm != "off"
        or bundle.on.arm != "on"
        or bundle.configuration.get("instance") != task.request.instance_id
        or bundle.off.resolved != task.result.off_resolved
        or bundle.on.resolved != task.result.on_resolved
    ):
        raise InvalidReportArtifact
    return bundle


def task_run_dir(task: TaskRecord, runs_root: Path) -> Path:
    if task.result is None:
        raise InvalidReportArtifact
    if task.attempt_number == 1:
        expected_run_id = task.task_id
    else:
        expected_attempt_id = f"{task.task_id}.attempt-{task.attempt_number:04d}"
        if task.attempt_id != expected_attempt_id:
            raise InvalidReportArtifact
        expected_run_id = f"{task.task_id}-attempt-{task.attempt_number:04d}"
    artifact_dir = Path(task.result.artifact_dir)
    if artifact_dir.parts != ("runs", expected_run_id):
        raise InvalidReportArtifact
    return runs_root / expected_run_id


def _pair_category(off_resolved: bool, on_resolved: bool) -> PairCategory:
    if off_resolved and on_resolved:
        return PairCategory.BOTH_PASS
    if off_resolved:
        return PairCategory.OFF_PASS_ON_FAIL
    if on_resolved:
        return PairCategory.OFF_FAIL_ON_PASS
    return PairCategory.BOTH_FAIL


def _arm_total(arm: ArmReport) -> int | None:
    metrics = arm.metrics
    if metrics.input_tokens is None or metrics.output_tokens is None:
        return None
    return metrics.input_tokens + metrics.output_tokens


def _task_arm(arm: ArmReport) -> TaskArmSummary:
    return TaskArmSummary(
        resolved=arm.resolved,
        input_tokens=arm.metrics.input_tokens,
        output_tokens=arm.metrics.output_tokens,
        total_tokens=_arm_total(arm),
    )


def _task_item(
    task: TaskRecord,
    *,
    runs_root: Path,
    catalog: BenchmarkCatalog,
) -> BatchTaskItem:
    if task.source_index is None or task.instance_id is None:
        raise InvalidReportArtifact
    instance = catalog.require(task.instance_id)
    category: PairCategory | None = None
    off: TaskArmSummary | None = None
    on: TaskArmSummary | None = None
    tokens = TaskTokenDelta()
    if task.status is TaskStatus.SUCCEEDED:
        bundle = _bundle_for_task(task, runs_root)
        category = _pair_category(bundle.off.resolved, bundle.on.resolved)
        off = _task_arm(bundle.off)
        on = _task_arm(bundle.on)
        delta = None if off.total_tokens is None or on.total_tokens is None else on.total_tokens - off.total_tokens
        tokens = TaskTokenDelta(off=off.total_tokens, on=on.total_tokens, delta=delta)
    elif task.status in _EXECUTION_FAILURE_STATES:
        category = PairCategory.EXECUTION_FAILURE
    return BatchTaskItem(
        task_id=task.task_id,
        attempt_id=task.attempt_id,
        attempt_number=task.attempt_number,
        attempt_count=task.attempt_count,
        retryable=task.retryable,
        model=task.request.model,
        reasoning_effort=task.request.reasoning_effort,
        instance_id=task.instance_id,
        repository=instance.repo,
        source_index=task.source_index,
        status=task.status,
        pair_category=category,
        off=off,
        on=on,
        tokens=tokens,
        failure_category=task.failure_category.value if task.failure_category is not None else None,
        failure_summary=task.failure_summary,
    )


def _metric_aggregate(values: dict[str, list[int]]) -> TokenMetricAggregate:
    off = sum(values["off"])
    on = sum(values["on"])
    return TokenMetricAggregate(
        off=off,
        on=on,
        delta=on - off,
        off_measured_tasks=len(values["off"]),
        on_measured_tasks=len(values["on"]),
    )


def load_batch_estimate_samples(
    batch: BatchRecord,
    tasks: Sequence[TaskRecord],
    *,
    runs_root: Path,
    catalog: BenchmarkCatalog,
) -> tuple[EstimateSample, ...]:
    """Return only complete paired metrics compatible with the current runner schema."""

    _validate_batch_tasks(batch, tasks)
    samples: list[EstimateSample] = []
    for task in tasks:
        catalog.require(task.request.instance_id)
        if task.status is not TaskStatus.SUCCEEDED:
            continue
        bundle = _bundle_for_task(task, runs_root)
        if (
            bundle.revisions.get("dataset") != DATASET_REVISION
            or bundle.revisions.get("harness") != HARNESS_COMMIT
            or bundle.configuration.get("model") != batch.request.model
            or bundle.configuration.get("reasoning_effort") != batch.request.reasoning_effort
            or task.started_at is None
            or task.finished_at is None
        ):
            continue
        off_total = _arm_total(bundle.off)
        on_total = _arm_total(bundle.on)
        if off_total is None or on_total is None:
            continue
        samples.append(
            EstimateSample(
                tokens=off_total + on_total,
                duration_seconds=max(0, round((task.finished_at - task.started_at).total_seconds())),
            )
        )
    return tuple(samples)


def load_batch_report(
    batch: BatchRecord,
    tasks: Sequence[TaskRecord],
    *,
    runs_root: Path,
    catalog: BenchmarkCatalog,
    latest_usage: UsageSnapshot | None = None,
) -> BatchReportResponse:
    """Aggregate factual batch outcomes without producing an acceptance conclusion."""

    _validate_batch_tasks(batch, tasks)
    status_counts = Counter(task.status for task in tasks)
    categories = {category: 0 for category in PairCategory}
    comparable = 0
    execution_failures = 0
    off_resolved = 0
    on_resolved = 0
    token_values: dict[str, dict[str, list[int]]] = {
        metric: {"off": [], "on": []} for metric in ("input", "output", "total")
    }
    revisions: dict[str, str] | None = None
    configuration: dict[str, str] | None = None
    estimate_samples: list[EstimateSample] = []
    for task in tasks:
        catalog.require(task.request.instance_id)
        if task.status is TaskStatus.SUCCEEDED:
            bundle = _bundle_for_task(task, runs_root)
            category = _pair_category(bundle.off.resolved, bundle.on.resolved)
            categories[category] += 1
            comparable += 1
            off_resolved += int(bundle.off.resolved)
            on_resolved += int(bundle.on.resolved)
            metric_pairs = {
                "input": (bundle.off.metrics.input_tokens, bundle.on.metrics.input_tokens),
                "output": (bundle.off.metrics.output_tokens, bundle.on.metrics.output_tokens),
                "total": (_arm_total(bundle.off), _arm_total(bundle.on)),
            }
            for metric_name, (off_value, on_value) in metric_pairs.items():
                if off_value is not None and on_value is not None:
                    token_values[metric_name]["off"].append(off_value)
                    token_values[metric_name]["on"].append(on_value)
            paired_tokens = metric_pairs["total"]
            if (
                paired_tokens[0] is not None
                and paired_tokens[1] is not None
                and task.started_at is not None
                and task.finished_at is not None
            ):
                estimate_samples.append(
                    EstimateSample(
                        tokens=paired_tokens[0] + paired_tokens[1],
                        duration_seconds=max(0, round((task.finished_at - task.started_at).total_seconds())),
                    )
                )
            candidate_revisions = dict(bundle.revisions)
            candidate_configuration = {key: value for key, value in bundle.configuration.items() if key != "instance"}
            if revisions is None:
                revisions = candidate_revisions
                configuration = candidate_configuration
            elif revisions != candidate_revisions or configuration != candidate_configuration:
                raise InvalidReportArtifact
        elif task.status in _EXECUTION_FAILURE_STATES:
            categories[PairCategory.EXECUTION_FAILURE] += 1
            execution_failures += 1

    if revisions is None:
        revisions = {"dataset": DATASET_REVISION, "harness": HARNESS_COMMIT}
        if batch.resolved_powercontext_sha is not None:
            revisions["powercontext"] = batch.resolved_powercontext_sha
    if batch.resolved_powercontext_sha is not None and revisions.get("powercontext") != batch.resolved_powercontext_sha:
        raise InvalidReportArtifact
    if configuration is None:
        configuration = {
            "model": batch.request.model,
            "reasoning_effort": batch.request.reasoning_effort,
        }
    configuration.update(
        {
            "task_set": batch.request.task_set,
            "treatment_mode": batch.request.treatment_mode,
        }
    )
    total = batch.total_tasks
    terminal_tasks = sum(status_counts[status] for status in _TERMINAL_STATES)
    denominator = terminal_tasks if terminal_tasks > 0 else 1
    off_rate = off_resolved / denominator * 100
    on_rate = on_resolved / denominator * 100
    return BatchReportResponse(
        batch_id=batch.batch_id,
        report_revision=batch.control.version + sum(task.attempt_count * 100 + task.version for task in tasks),
        total_tasks=total,
        terminal_tasks=terminal_tasks,
        comparable_pairs=comparable,
        execution_failures=execution_failures,
        cancelled_tasks=status_counts[TaskStatus.CANCELLED],
        off=ResolutionAggregate(resolved=off_resolved, total=terminal_tasks, rate_percent=off_rate),
        on=ResolutionAggregate(resolved=on_resolved, total=terminal_tasks, rate_percent=on_rate),
        resolution_rate_delta_points=on_rate - off_rate,
        pair_categories=categories,
        task_statuses={status: status_counts[status] for status in TaskStatus},
        tokens=TokenAggregate(
            input=_metric_aggregate(token_values["input"]),
            output=_metric_aggregate(token_values["output"]),
            total=_metric_aggregate(token_values["total"]),
        ),
        control=batch.control,
        latest_usage=latest_usage,
        estimate=(
            estimate_batch(
                samples=estimate_samples,
                remaining_tasks=total - terminal_tasks,
                basis=EstimateBasis.CURRENT_BATCH,
            )
            if estimate_samples
            else BatchEstimate.unavailable(remaining_tasks=total - terminal_tasks)
        ),
        revisions=revisions,
        configuration=configuration,
    )


def load_batch_task_page(
    batch: BatchRecord,
    tasks: Sequence[TaskRecord],
    *,
    runs_root: Path,
    catalog: BenchmarkCatalog,
    category: PairCategory | None,
    query: str | None,
    sort: Literal["source", "token_delta_asc", "token_delta_desc"],
    limit: int,
    offset: int,
) -> BatchTaskPage:
    """Project, filter, and stably paginate task-pair rows."""

    _validate_batch_tasks(batch, tasks)
    if limit < 1 or offset < 0:
        raise ValueError("Task page bounds are invalid")
    items = [_task_item(task, runs_root=runs_root, catalog=catalog) for task in tasks]
    normalized_query = query.strip().casefold() if query is not None else ""
    if normalized_query:
        items = [
            item
            for item in items
            if normalized_query in item.instance_id.casefold() or normalized_query in item.repository.casefold()
        ]
    if category is not None:
        items = [item for item in items if item.pair_category is category]
    if sort == "token_delta_asc":
        items.sort(
            key=lambda item: (
                item.tokens.delta is None,
                item.tokens.delta if item.tokens.delta is not None else 0,
                item.source_index,
            )
        )
    elif sort == "token_delta_desc":
        items.sort(
            key=lambda item: (
                item.tokens.delta is None,
                -(item.tokens.delta if item.tokens.delta is not None else 0),
                item.source_index,
            )
        )
    elif sort != "source":
        raise ValueError("Task page sort is invalid")
    total = len(items)
    return BatchTaskPage(items=tuple(items[offset : offset + limit]), total=total, limit=limit, offset=offset)


def _official_group(group: TestGroupReport) -> OfficialTestGroup:
    return OfficialTestGroup(passed=group.passed, total=group.total, failed=group.failed)


def _detail_arm(arm: ArmReport) -> TaskDetailArm:
    return TaskDetailArm(
        resolved=arm.resolved,
        patch_applied=arm.patch_applied,
        fail_to_pass=_official_group(arm.fail_to_pass),
        pass_to_pass=_official_group(arm.pass_to_pass),
        log_excerpt=arm.log_excerpt,
        input_tokens=arm.metrics.input_tokens,
        output_tokens=arm.metrics.output_tokens,
        total_tokens=_arm_total(arm),
    )


def tokensflow_finalization_summary(record: TokensFlowFinalizationRecord) -> TokensFlowFinalizationSummary:
    """Project one durable job through an explicit non-secret allowlist."""

    public_state: Literal["pending", "passed", "timed_out", "capacity_evicted", "cleanup_failed"]
    if record.state in {
        FinalizationState.PENDING,
        FinalizationState.RUNNING,
        FinalizationState.CLEANUP_PENDING,
    }:
        public_state = "pending"
    elif record.state is FinalizationState.PASSED:
        public_state = "passed"
    elif record.state is FinalizationState.TIMED_OUT:
        public_state = "timed_out"
    elif record.state is FinalizationState.CAPACITY_EVICTED:
        public_state = "capacity_evicted"
    else:
        public_state = "cleanup_failed"
    return TokensFlowFinalizationSummary(
        state=public_state,
        registered_at=record.registered_at,
        deadline_at=record.deadline_at,
        finished_at=record.finished_at,
        attempts=record.attempts,
        queue_passed=record.queue_passed,
        doctor_rc=record.doctor_rc,
        error_category=record.error_category,
        reason=record.reason,
    )


def load_batch_task_detail(
    batch: BatchRecord,
    task: TaskRecord,
    *,
    runs_root: Path,
    catalog: BenchmarkCatalog,
    finalizations: Sequence[TokensFlowFinalizationRecord] = (),
) -> BatchTaskDetailResponse:
    """Return full benchmark and official result detail for one batch child."""

    if task.batch_id != batch.batch_id or task.instance_id is None:
        raise InvalidReportArtifact
    instance = catalog.require(task.instance_id)
    item = _task_item(task, runs_root=runs_root, catalog=catalog)
    off = None
    on = None
    if task.status is TaskStatus.SUCCEEDED:
        bundle = _bundle_for_task(task, runs_root)
        off = _detail_arm(bundle.off)
        on = _detail_arm(bundle.on)
    finalization_by_arm = {record.arm: tokensflow_finalization_summary(record) for record in finalizations}
    return BatchTaskDetailResponse(
        task=item,
        problem_statement=instance.problem_statement,
        required_tests=RequiredTests(
            fail_to_pass=instance.fail_to_pass,
            pass_to_pass=instance.pass_to_pass,
            selected_test_files_to_run=instance.selected_test_files_to_run,
            test_patch=instance.test_patch,
        ),
        off=off,
        on=on,
        tokensflow_finalization=TokensFlowFinalizationPair(
            off=finalization_by_arm.get("off"),
            on=finalization_by_arm.get("on"),
        ),
    )


def _reject_secret_shaped_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise InvalidReportArtifact
            normalized = key.casefold().replace("-", "_")
            if normalized in _SECRET_SHAPED_KEYS:
                raise InvalidReportArtifact
            _reject_secret_shaped_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_secret_shaped_fields(nested)


def _load_context_events(run_dir: Path, runs_root: Path, arm: Literal["off", "on"]) -> tuple[ContextEventResponse, ...]:
    run_fd, _ = _open_run(run_dir, runs_root)
    try:
        raw, _ = _read_bounded(
            run_fd,
            ("arms", arm, "context", "timeline.jsonl"),
            _CONTEXT_TIMELINE_LIMIT,
        )
    finally:
        os.close(run_fd)
    if not raw or not raw.endswith(b"\n"):
        raise InvalidReportArtifact
    events: list[ContextEventResponse] = []
    previous_time: datetime | None = None
    previous_elapsed = -1
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line:
            raise InvalidReportArtifact
        try:
            value = json.loads(line)
            _reject_secret_shaped_fields(value)
            event = ContextEventResponse.model_validate_json(line, strict=True)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
            raise InvalidReportArtifact from None
        if (
            event.sequence != line_number
            or event.arm != arm
            or (previous_time is not None and event.observed_at < previous_time)
            or event.elapsed_ms < previous_elapsed
        ):
            raise InvalidReportArtifact
        events.append(event)
        previous_time = event.observed_at
        previous_elapsed = event.elapsed_ms
    return tuple(events)


def load_context_page(
    batch: BatchRecord,
    task: TaskRecord,
    *,
    runs_root: Path,
    arm: Literal["off", "on"],
    limit: int,
    offset: int,
) -> ContextEventPage:
    """Return one bounded page of complete ordered context events."""

    if task.batch_id != batch.batch_id or task.status is not TaskStatus.SUCCEEDED:
        raise InvalidReportArtifact
    if limit < 1 or offset < 0:
        raise ValueError("Context page bounds are invalid")
    events = _load_context_events(task_run_dir(task, runs_root), runs_root, arm)
    return ContextEventPage(items=events[offset : offset + limit], total=len(events), limit=limit, offset=offset)


def load_context_event(
    batch: BatchRecord,
    task: TaskRecord,
    *,
    runs_root: Path,
    arm: Literal["off", "on"],
    sequence: int,
) -> ContextEventResponse:
    """Return the exact full event selected from an audited timeline."""

    if sequence < 1 or task.batch_id != batch.batch_id or task.status is not TaskStatus.SUCCEEDED:
        raise InvalidReportArtifact
    events = _load_context_events(task_run_dir(task, runs_root), runs_root, arm)
    if sequence > len(events):
        raise InvalidReportArtifact
    return events[sequence - 1]
