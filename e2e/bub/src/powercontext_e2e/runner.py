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

"""Run every end-to-end workload through Harbor, ACP, and Bub."""

from __future__ import annotations

import hashlib
import re
import shutil
import tomllib
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, NamedTuple
from urllib.parse import unquote, urlparse
from uuid import uuid4

from harbor.job import Job
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import DatasetConfig, JobConfig
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, ResourceMode, ServiceVolumeConfig
from powercontext.client import PowerContextClient
from powercontext.client.settings import ClientSettings
from powercontext.http import ListMemoryEntriesRequest, PrepareContextRequest

from .artifacts import write_artifacts
from .catalog import E2ETask
from .evaluation import MemoryEvaluator
from .evidence import fingerprint, load_resolved_instructions, redact, write_evaluation_report, write_evidence
from .harbor_agent import BUB_ACP_SERVER_VERSION, BUB_VERSION
from .models import (
    CaptureRecord,
    EvaluationReport,
    HarborTrialObservation,
    MemoryEntrySnapshot,
    MemorySnapshot,
    NativeArtifact,
    PreparedContextSnapshot,
    RecallProbeObservation,
    ResolvedInstruction,
    RunEnvironment,
    SourceReferenceSnapshot,
    TaskObservation,
)
from .report import render_evaluation_summary
from .settings import (
    HarnessSettings,
    ModelNotConfiguredError,
    bub_environment,
    codex_auth_path,
    powercontext_bub_environment,
)

FailurePolicy = Literal["fail-fast", "collect-all"]


class TaskArtifacts(NamedTuple):
    capture_records: tuple[CaptureRecord, ...]
    native_artifacts: tuple[NativeArtifact, ...]
    resolved_instructions: tuple[ResolvedInstruction, ...]


class ExecutionGroup(NamedTuple):
    tasks: tuple[E2ETask, ...]
    batch: str | None

    @property
    def output_id(self) -> str:
        return self.tasks[0].id if self.batch is None else f"batch-{self.batch}"


class SourceTask(NamedTuple):
    task: E2ETask
    path: Path
    checksum: str
    source_steps: tuple[str, ...]

    @property
    def runtime_steps(self) -> tuple[str, ...]:
        return tuple(f"{self.task.id}-{step}" for step in self.source_steps)


class PreparedRuntime(NamedTuple):
    dataset_config: DatasetConfig
    sources: tuple[SourceTask, ...]
    runtime_checksum: str


class SourceResult(NamedTuple):
    status: Literal["completed", "failed", "skipped"]
    errors: tuple[str, ...]
    steps: tuple[Any, ...]


async def evaluate_task(
    task: E2ETask,
    *,
    output_dir: Path,
    settings: HarnessSettings,
) -> bool:
    observation = await run_task(task, output_dir=output_dir, settings=settings)
    report = MemoryEvaluator.evaluate(observation, experiment=f"e2e:{task.id}")
    write_artifacts(observation, report, output_dir, settings=settings)
    return report.accepted


async def evaluate_tasks(
    tasks: tuple[E2ETask, ...],
    *,
    output_dir: Path,
    settings: HarnessSettings,
    failure_policy: FailurePolicy = "collect-all",
) -> bool:
    if len(tasks) == 1:
        return await evaluate_task(tasks[0], output_dir=output_dir, settings=settings)
    observations = await run_task_group(
        tasks,
        output_dir=output_dir,
        settings=settings,
        failure_policy=failure_policy,
    )
    experiment = f"e2e:batch:{tasks[0].batch}"
    reports = tuple(
        MemoryEvaluator.evaluate(observation, experiment=f"e2e:{observation.task.id}") for observation in observations
    )
    for observation, report in zip(observations, reports, strict=True):
        write_artifacts(observation, report, output_dir / "tasks" / observation.task.id, settings=settings)
    aggregate = EvaluationReport(
        experiment=experiment, cases=tuple(case for report in reports for case in report.cases)
    )
    _write_batch_summary(aggregate, output_dir, settings)
    return aggregate.accepted


def _write_batch_summary(report: EvaluationReport, output_dir: Path, settings: HarnessSettings) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_evaluation_report(output_dir / "eval-report.json", report=report, settings=settings)
    write_evidence(output_dir / "report.md", render_evaluation_summary(report), settings)


async def run_tasks(
    tasks: tuple[E2ETask, ...],
    *,
    output_dir: Path,
    settings: HarnessSettings,
    failure_policy: FailurePolicy = "collect-all",
) -> bool:
    model_workload_ids = tuple(task.id for task in tasks if task.execution.model)
    if model_workload_ids and "BUB_MODEL" not in bub_environment():
        raise ModelNotConfiguredError(model_workload_ids)

    accepted = True
    for group in group_tasks(tasks):
        group_accepted = await evaluate_tasks(
            group.tasks,
            output_dir=output_dir / group.output_id,
            settings=settings,
            failure_policy=failure_policy,
        )
        accepted = group_accepted and accepted
    return accepted


def group_tasks(tasks: tuple[E2ETask, ...]) -> tuple[ExecutionGroup, ...]:
    """Group only selected tasks that explicitly declare the same batch."""

    groups: list[ExecutionGroup] = []
    positions: dict[str, int] = {}
    for task in tasks:
        if task.batch is None:
            groups.append(ExecutionGroup(tasks=(task,), batch=None))
            continue
        if task.batch not in positions:
            positions[task.batch] = len(groups)
            groups.append(ExecutionGroup(tasks=(task,), batch=task.batch))
            continue
        index = positions[task.batch]
        group = groups[index]
        groups[index] = ExecutionGroup(tasks=(*group.tasks, task), batch=group.batch)
    return tuple(
        ExecutionGroup(tasks=group.tasks, batch=group.batch if len(group.tasks) > 1 else None) for group in groups
    )


async def run_task(
    task: E2ETask,
    *,
    output_dir: Path,
    settings: HarnessSettings,
) -> TaskObservation:
    started_at = datetime.now(UTC)
    run_id = f"{task.id}-{uuid4().hex[:12]}"
    scope_id = f"e2e:{run_id}"
    errors: list[str] = []
    capture_records: tuple[CaptureRecord, ...] = ()
    native_artifacts: tuple[NativeArtifact, ...] = ()
    resolved_instructions: tuple[ResolvedInstruction, ...] = ()
    harbor = HarborTrialObservation()
    memory_before = MemorySnapshot()
    memory_after = MemorySnapshot()
    probes: tuple[RecallProbeObservation, ...] = ()
    client_settings = ClientSettings()
    client_token = None if client_settings.api_token is None else client_settings.api_token.get_secret_value()

    async with PowerContextClient(
        client_settings.server_url,
        token=client_token,
        timeout=client_settings.timeout,
    ) as client:
        try:
            await client.get_readiness()
            memory_before = await memory_snapshot(client, scope_id)
            output_dir.mkdir(parents=True, exist_ok=True)
            job = await Job.create(_job_config(task, run_id, scope_id, output_dir, settings))
            result = await job.run()
            harbor, _, trial_dir = _harbor_observation(result, settings)
            if harbor.exception_type is not None:
                errors.append(f"{harbor.exception_type}: {harbor.exception_message or ''}".strip())
            if trial_dir is not None:
                capture_records = _load_capture_records(trial_dir)
                native_artifacts = _native_artifacts(trial_dir, task.execution.native_artifact_names)
                resolved_instructions = load_resolved_instructions(trial_dir, settings)
            memory_after = await memory_snapshot(client, scope_id)
            probes = await _prepared_probes(client, task, scope_id)
        except Exception as exc:
            errors.append(redact(f"{type(exc).__name__}: {exc}", settings))
            with suppress(Exception):
                memory_after = await memory_snapshot(client, scope_id)

    return TaskObservation(
        run_id=run_id,
        scope_id=scope_id,
        environment=_run_environment(task, started_at, settings),
        task=task,
        status="completed" if not errors else "failed",
        errors=tuple(errors),
        harbor=harbor,
        capture_records=capture_records,
        native_artifacts=native_artifacts,
        resolved_instructions=resolved_instructions,
        memory_before=memory_before,
        memory_after=memory_after,
        probes=probes,
    )


async def run_task_group(  # noqa: C901 - one shared trial owns the client and evidence lifecycle
    tasks: tuple[E2ETask, ...],
    *,
    output_dir: Path,
    settings: HarnessSettings,
    failure_policy: FailurePolicy = "collect-all",
) -> tuple[TaskObservation, ...]:
    if len(tasks) < 2:
        raise ValueError("A runtime batch requires at least two E2E tasks")  # noqa: TRY003
    prepared = prepare_runtime_dataset(
        tasks,
        output_dir=output_dir,
        settings=settings,
        failure_policy=failure_policy,
    )
    started_at = datetime.now(UTC)
    batch = tasks[0].batch
    run_id = f"batch-{batch}-{uuid4().hex[:12]}"
    task_scopes = {task.id: f"e2e:{run_id}:{task.id}" for task in tasks}
    invocation_scopes = tuple(task_scopes[source.task.id] for source in prepared.sources for _ in source.runtime_steps)
    memory_before = {task.id: MemorySnapshot() for task in tasks}
    memory_after = {task.id: MemorySnapshot() for task in tasks}
    harbor = HarborTrialObservation()
    step_results: tuple[Any, ...] = ()
    trial_dir: Path | None = None
    execution_errors: list[str] = []
    provenance_error: str | None = None
    client_settings = ClientSettings()
    client_token = None if client_settings.api_token is None else client_settings.api_token.get_secret_value()

    async with PowerContextClient(
        client_settings.server_url,
        token=client_token,
        timeout=client_settings.timeout,
    ) as client:
        try:
            await client.get_readiness()
            for task in tasks:
                memory_before[task.id] = await memory_snapshot(client, task_scopes[task.id])
            job = await Job.create(
                _batch_job_config(
                    tasks[0],
                    run_id,
                    invocation_scopes,
                    output_dir,
                    settings,
                    dataset_config=prepared.dataset_config,
                )
            )
            result = await job.run()
            harbor, step_results, trial_dir = _harbor_observation(result, settings)
            if harbor.task_checksum != prepared.runtime_checksum:
                provenance_error = (
                    f"Runtime Harbor task checksum changed: expected {prepared.runtime_checksum}, "
                    f"observed {harbor.task_checksum}"
                )
            if harbor.exception_type is not None:
                execution_errors.append(f"{harbor.exception_type}: {harbor.exception_message or ''}".strip())
        except Exception as exc:
            execution_errors.append(redact(f"{type(exc).__name__}: {exc}", settings))

        source_results = _source_results(
            prepared.sources,
            step_results,
            tuple(execution_errors),
            provenance_error,
            settings,
        )
        artifacts_by_task = (
            collect_task_artifacts(prepared.sources, trial_dir, tasks[0].execution.native_artifact_names, settings)
            if trial_dir is not None
            else {task.id: TaskArtifacts((), (), ()) for task in tasks}
        )
        environment = _run_environment(tasks[0], started_at, settings)
        observations: list[TaskObservation] = []
        for source in prepared.sources:
            task = source.task
            scope_id = task_scopes[task.id]
            source_result = source_results[task.id]
            errors = list(source_result.errors)
            if source_result.status != "skipped":
                try:
                    memory_after[task.id] = await memory_snapshot(client, scope_id)
                except Exception as exc:
                    errors.append(redact(f"{type(exc).__name__}: {exc}", settings))

            probes: list[RecallProbeObservation] = []
            if source_result.status != "skipped":
                try:
                    probes.extend(await _prepared_probes(client, task, scope_id))
                except Exception as exc:
                    errors.append(redact(f"{type(exc).__name__}: {exc}", settings))

            status = source_result.status
            if status == "completed" and errors:
                status = "failed"
            artifacts = artifacts_by_task[task.id]
            observations.append(
                TaskObservation(
                    run_id=run_id,
                    scope_id=scope_id,
                    environment=environment,
                    task=task,
                    status=status,
                    errors=tuple(errors),
                    harbor=_source_harbor_observation(harbor, source, source_result, settings),
                    capture_records=artifacts.capture_records,
                    native_artifacts=artifacts.native_artifacts,
                    resolved_instructions=artifacts.resolved_instructions,
                    memory_before=memory_before[task.id],
                    memory_after=memory_after[task.id],
                    probes=tuple(probes),
                )
            )
    environment = environment.model_copy(update={"finished_at": datetime.now(UTC)})
    return tuple(observation.model_copy(update={"environment": environment}) for observation in observations)


def _source_results(
    sources: tuple[SourceTask, ...],
    step_results: tuple[Any, ...],
    execution_errors: tuple[str, ...],
    provenance_error: str | None,
    settings: HarnessSettings,
) -> dict[str, SourceResult]:
    results: dict[str, SourceResult] = {}
    for source in sources:
        owned_steps = tuple(step for step in step_results if step.step_name in source.runtime_steps)
        executed_names = {step.step_name for step in owned_steps}
        errors = [_step_failure_message(step, settings) for step in owned_steps if _step_failed(step)]
        missing_steps = [step for step in source.runtime_steps if step not in executed_names]
        if missing_steps and owned_steps:
            errors.append(f"Harbor did not execute steps: {missing_steps!r}")
        if not owned_steps:
            status: Literal["completed", "failed", "skipped"] = "skipped"
        else:
            status = "failed" if errors else "completed"
        results[source.task.id] = SourceResult(status, tuple(errors), owned_steps)

    if provenance_error is not None:
        return {
            source.task.id: SourceResult(
                "failed",
                (*results[source.task.id].errors, provenance_error),
                results[source.task.id].steps,
            )
            for source in sources
        }
    if not step_results and not execution_errors:
        execution_errors = ("Harbor did not execute any steps.",)
    if execution_errors:
        error_index = next(
            (
                index
                for index, source in enumerate(sources)
                if results[source.task.id].status != "completed"
                and all(results[previous.task.id].status == "completed" for previous in sources[:index])
            ),
            max((index for index, source in enumerate(sources) if results[source.task.id].steps), default=0),
        )
        source = sources[error_index]
        result = results[source.task.id]
        results[source.task.id] = SourceResult("failed", (*result.errors, *execution_errors), result.steps)

    failed_index = next(
        (index for index, source in enumerate(sources) if results[source.task.id].status == "failed"),
        None,
    )
    if failed_index is not None:
        for source in sources[failed_index + 1 :]:
            result = results[source.task.id]
            if result.status == "skipped" and not result.errors:
                results[source.task.id] = SourceResult(
                    "skipped",
                    ("Skipped after an earlier task stopped the shared Harbor trial.",),
                    (),
                )
    return results


def _step_failed(step: Any) -> bool:
    rewards = step.verifier_result.rewards if step.verifier_result is not None else {}
    return step.exception_info is not None or any(float(value) < 1 for value in (rewards or {}).values())


def _step_failure_message(step: Any, settings: HarnessSettings) -> str:
    exception = step.exception_info
    if exception is not None:
        detail = redact(exception.exception_message or "", settings)
    else:
        rewards = step.verifier_result.rewards if step.verifier_result is not None else {}
        detail = f"rewards={rewards or {}!r}"
    return f"Harbor step {step.step_name!r} failed: {detail}"


def _source_harbor_observation(
    shared: HarborTrialObservation,
    source: SourceTask,
    result: SourceResult,
    settings: HarnessSettings,
) -> HarborTrialObservation:
    step_rewards = [
        step.verifier_result.rewards
        for step in result.steps
        if step.verifier_result is not None and step.verifier_result.rewards
    ]
    reward_names = {name for rewards in step_rewards for name in rewards}
    rewards = {
        name: sum(float(item.get(name, 0)) for item in step_rewards) / len(step_rewards) for name in reward_names
    }
    step_exception = next((step.exception_info for step in result.steps if step.exception_info is not None), None)
    exception_type = None if step_exception is None else step_exception.exception_type
    exception_message = None if step_exception is None else redact(step_exception.exception_message or "", settings)
    if result.status == "failed" and step_exception is None and shared.exception_type is not None:
        exception_type = shared.exception_type
        exception_message = shared.exception_message
    return HarborTrialObservation(
        job_id=shared.job_id,
        trial_name=shared.trial_name,
        trial_uri=shared.trial_uri,
        task_checksum=shared.task_checksum,
        source_task_checksum=source.checksum,
        rewards=rewards,
        exception_type=exception_type,
        exception_message=exception_message,
        started_at=shared.started_at,
        finished_at=shared.finished_at,
    )


def _batch_job_config(
    task: E2ETask,
    job_name: str,
    invocation_scopes: tuple[str, ...],
    output_dir: Path,
    settings: HarnessSettings,
    *,
    dataset_config: DatasetConfig,
) -> JobConfig:
    config = _job_config(task, job_name, invocation_scopes[0], output_dir, settings)
    agent = config.agents[0]
    env = dict(agent.env)
    env.pop("POWERCONTEXT_BUB_SCOPE_ID")
    return config.model_copy(
        update={
            "agents": [agent.model_copy(update={"env": env, "kwargs": {"invocation_scopes": invocation_scopes}})],
            "datasets": [dataset_config],
        }
    )


def _job_config(
    task: E2ETask,
    run_id: str,
    scope_id: str,
    output_dir: Path,
    settings: HarnessSettings,
) -> JobConfig:
    repository = settings.repository_path()
    mounts: list[ServiceVolumeConfig] = [
        {
            "type": "bind",
            "source": str(repository),
            "target": "/opt/powercontext/source",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    ]
    if task.execution.model and (auth_path := codex_auth_path()).is_file():
        mounts.append({
            "type": "bind",
            "source": str(auth_path),
            "target": "/run/powercontext/codex-auth.json",
            "read_only": True,
            "bind": {"create_host_path": False},
        })

    agent_env = powercontext_bub_environment()
    if task.execution.model:
        agent_env.update(bub_environment())
    else:
        agent_env.update({"BUB_API_KEY": "null", "BUB_FALLBACK_MODELS": "null"})
    agent_env.update({
        "BUB_HOME": "/installed-agent/bub-home",
        "BUB_MAX_STEPS": str(task.execution.max_steps),
        "BUB_MAX_TOKENS": str(task.execution.max_tokens),
        "CODEX_HOME": "/installed-agent/codex",
        "POWERCONTEXT_BUB_CAPTURE_CHECKPOINT_EVERY": str(task.evaluation.checkpoint_every_events),
        "POWERCONTEXT_BUB_CAPTURE_EVENTS": str(task.evaluation.capture_events).lower(),
        "POWERCONTEXT_BUB_CAPTURE_LOG": "/logs/agent/powercontext-capture.jsonl",
        "POWERCONTEXT_BUB_CAPTURE_MAX_BYTES": str(task.evaluation.max_event_bytes),
        "POWERCONTEXT_BUB_SCOPE_ID": scope_id,
    })
    if settings.agent_proxy_url is not None:
        proxy_url = settings.agent_proxy_url.get_secret_value()
        agent_env.update({
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "NO_PROXY": "127.0.0.1,localhost,host-gateway,powercontext",
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            "no_proxy": "127.0.0.1,localhost,host-gateway,powercontext",
        })

    return JobConfig(
        job_name=run_id,
        jobs_dir=output_dir / "harbor-jobs",
        n_attempts=1,
        n_concurrent_trials=1,
        quiet=True,
        environment=EnvironmentConfig(
            type=EnvironmentType.DOCKER,
            delete=True,
            cpu_enforcement_policy=ResourceMode.IGNORE,
            memory_enforcement_policy=ResourceMode.IGNORE,
            extra_docker_compose=[repository / "e2e" / "bub" / "harbor-task-overlay.yaml"],
            mounts=mounts,
        ),
        agents=[
            AgentConfig(
                import_path="powercontext_e2e.harbor_agent:PowerContextBubAcpAgent",
                env=agent_env,
            )
        ],
        datasets=[_dataset_config(task, repository)],
    )


def _dataset_config(task: E2ETask, repository: Path) -> DatasetConfig:
    dataset = task.dataset
    if dataset.path is not None:
        return DatasetConfig(path=repository / dataset.path, task_names=[dataset.task_id])
    return DatasetConfig(name=dataset.name, version=dataset.version, task_names=[dataset.task_id])


def prepare_runtime_dataset(
    tasks: tuple[E2ETask, ...],
    *,
    output_dir: Path,
    settings: HarnessSettings,
    failure_policy: FailurePolicy,
) -> PreparedRuntime:
    """Assemble compatible selected source tasks into one run-local Harbor task."""

    if len(tasks) < 2:
        raise ValueError("Runtime aggregation requires at least two tasks")  # noqa: TRY003

    sources = _validate_batch_compatibility(tasks, settings)
    batch = tasks[0].batch
    if batch is None:
        raise ValueError("Runtime aggregation requires an explicit batch")  # noqa: TRY003

    runtime_task_id = f"batch-{batch}"
    runtime_root = output_dir / "harbor-runtime-dataset"
    runtime_task_dir = runtime_root / runtime_task_id
    if runtime_task_dir.exists():
        raise FileExistsError(f"Runtime Harbor task already exists: {runtime_task_dir}")  # noqa: TRY003
    runtime_task_dir.mkdir(parents=True)

    first_source_dir = sources[0].path
    for shared_dir in ("environment", "tests"):
        source = first_source_dir / shared_dir
        if source.exists():
            shutil.copytree(source, runtime_task_dir / shared_dir)
    runtime_steps_dir = runtime_task_dir / "steps"
    runtime_steps_dir.mkdir()
    for source in sources:
        for source_step, runtime_step in zip(source.source_steps, source.runtime_steps, strict=True):
            shutil.copytree(source.path / "steps" / source_step, runtime_steps_dir / runtime_step)

    _combine_task_toml(sources, runtime_task_dir)
    if failure_policy == "fail-fast":
        _add_fail_fast_thresholds(runtime_task_dir / "task.toml")

    harbor_task = HarborTask(runtime_task_dir)
    runtime_checksum = harbor_task.checksum
    expected_steps = tuple(step for source in sources for step in source.runtime_steps)
    if tuple(step.name for step in harbor_task.config.steps or ()) != expected_steps:
        raise ValueError("Runtime Harbor steps do not match the selected source tasks")  # noqa: TRY003
    return PreparedRuntime(
        dataset_config=DatasetConfig(path=runtime_root, task_names=[runtime_task_id]),
        sources=sources,
        runtime_checksum=runtime_checksum,
    )


def _validate_batch_compatibility(
    tasks: tuple[E2ETask, ...],
    settings: HarnessSettings,
) -> tuple[SourceTask, ...]:
    batch = tasks[0].batch
    if batch is None or any(task.batch != batch for task in tasks):
        raise ValueError("Runtime aggregation requires one explicit shared batch")  # noqa: TRY003
    if any(task.dataset.path is None for task in tasks):
        raise ValueError(f"Batch {batch!r} requires local Harbor datasets")  # noqa: TRY003

    if len({task.id for task in tasks}) != len(tasks):
        raise ValueError(f"Batch {batch!r} task IDs must be unique")  # noqa: TRY003
    repository = settings.repository_path()
    sources = tuple(_load_source_task(task, repository) for task in tasks)
    first_profile = _runtime_profile(sources[0])
    for source in sources[1:]:
        profile = _runtime_profile(source)
        if incompatible := [name for name in first_profile if profile[name] != first_profile[name]]:
            raise ValueError(  # noqa: TRY003
                f"Source task {source.task.id!r} has incompatible batch settings: {incompatible!r}"
            )
    steps = [step for source in sources for step in source.runtime_steps]
    if len(steps) != len(set(steps)):
        raise ValueError(f"Batch {batch!r} step names must be unique")  # noqa: TRY003
    return sources


def _load_source_task(task: E2ETask, repository: Path) -> SourceTask:
    dataset_path = task.dataset.path
    if dataset_path is None:
        raise ValueError(f"Source task {task.id!r} does not use a local Harbor dataset")  # noqa: TRY003
    task_dir = repository / dataset_path / task.dataset.task_id
    try:
        harbor_task = HarborTask(task_dir)
    except Exception as exc:
        raise ValueError(f"Source task {task.id!r} cannot be loaded from {task_dir}") from exc  # noqa: TRY003
    unexpected = {item.name for item in task_dir.iterdir()} - {"environment", "steps", "task.toml", "tests"}
    if unexpected:
        raise ValueError(f"Source task {task.id!r} has unsupported runtime inputs: {sorted(unexpected)!r}")  # noqa: TRY003
    if harbor_task.checksum != task.dataset.checksum:
        raise ValueError(f"Source task {task.id!r} checksum changed")  # noqa: TRY003
    steps = _task_layout(task, harbor_task)
    return SourceTask(task, task_dir, harbor_task.checksum, steps)


def _runtime_profile(source: SourceTask) -> dict[str, Any]:
    task = source.task
    return {
        "dataset": task.dataset.model_dump(mode="json", exclude={"task_id", "checksum"}),
        "execution": task.execution.model_dump(mode="json"),
        "capture": _capture_profile(task),
        "harbor": _shared_harbor_config(HarborTask(source.path)),
        "environment": _directory_snapshot(source.path / "environment"),
        "tests": _directory_snapshot(source.path / "tests"),
    }


def _capture_profile(task: E2ETask) -> tuple[bool, int, int]:
    evaluation = task.evaluation
    return (
        evaluation.capture_events,
        evaluation.checkpoint_every_events,
        evaluation.max_event_bytes,
    )


def _shared_harbor_config(task: HarborTask) -> dict[str, Any]:
    return task.config.model_dump(mode="json", exclude={"steps"})


def _directory_snapshot(path: Path) -> tuple[tuple[str, int, str], ...]:
    if not path.is_dir():
        return ()
    snapshot = []
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"Shared Harbor directory cannot contain symlinks: {item}")  # noqa: TRY003
        if item.is_file():
            snapshot.append((
                item.relative_to(path).as_posix(),
                item.stat().st_mode & 0o777,
                hashlib.sha256(item.read_bytes()).hexdigest(),
            ))
    return tuple(snapshot)


def _combine_task_toml(sources: tuple[SourceTask, ...], runtime_task_dir: Path) -> None:
    source_texts = tuple(source.path.joinpath("task.toml").read_text(encoding="utf-8") for source in sources)
    header, marker, _ = source_texts[0].partition("[[steps]]")
    if not marker:
        raise ValueError(f"Source Harbor task has no steps: {sources[0].path / 'task.toml'}")  # noqa: TRY003
    step_sections = []
    for source, source_text in zip(sources, source_texts, strict=True):
        blocks = source_text.split("[[steps]]")
        if len(blocks) - 1 != len(source.source_steps):
            raise ValueError(f"Source Harbor task step blocks changed: {source.path / 'task.toml'}")  # noqa: TRY003
        for source_step, runtime_step, block in zip(
            source.source_steps,
            source.runtime_steps,
            blocks[1:],
            strict=True,
        ):
            parsed_steps = tomllib.loads(f"[[steps]]{block}").get("steps")
            if (
                not isinstance(parsed_steps, list)
                or len(parsed_steps) != 1
                or parsed_steps[0].get("name") != source_step
            ):
                raise ValueError(f"Source Harbor task step order changed: {source.path / 'task.toml'}")  # noqa: TRY003
            name_matches = tuple(re.finditer(r"(?m)^[ \t]*name[ \t]*=.*$", block))
            if len(name_matches) != 1:
                raise ValueError(f"Source Harbor task step requires one name field: {source.path / 'task.toml'}")  # noqa: TRY003
            rewritten = f'{block[: name_matches[0].start()]}name = "{runtime_step}"{block[name_matches[0].end() :]}'
            step_sections.append(f"[[steps]]{rewritten.rstrip()}\n")
    (runtime_task_dir / "task.toml").write_text(f"{header.rstrip()}\n\n{''.join(step_sections)}", encoding="utf-8")


def _task_layout(task: E2ETask, harbor_task: HarborTask) -> tuple[str, ...]:
    steps = tuple(step.name for step in harbor_task.config.steps or ())
    if not steps:
        raise ValueError(f"Source task {task.id!r} has no Harbor steps")  # noqa: TRY003
    if len(steps) != len(set(steps)):
        raise ValueError(f"Source task {task.id!r} step names must be unique")  # noqa: TRY003
    paths = tuple(PurePosixPath(step) for step in steps)
    if any(
        "\\" in step or path.is_absolute() or len(path.parts) != 1 or path.parts[0] == ".." or path.as_posix() != step
        for step, path in zip(steps, paths, strict=True)
    ):
        raise ValueError(f"Source task {task.id!r} step names must be single path components")  # noqa: TRY003
    steps_dir = harbor_task.task_dir / "steps"
    step_entries = {path.name: path.is_dir() for path in steps_dir.iterdir()}
    if step_entries != dict.fromkeys(steps, True):
        raise ValueError(f"Source task {task.id!r} step directories do not match task.toml")  # noqa: TRY003
    if harbor_task.step_instruction(steps[0]).strip() != ",tape.reset":
        raise ValueError(f"Task {task.id!r} must start with a tape.reset step")  # noqa: TRY003
    return steps


def _add_fail_fast_thresholds(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    payload = tomllib.loads(source)
    steps = payload.get("steps", [])
    if not isinstance(steps, list) or not steps:
        raise ValueError("Fail-fast requires a multi-step Harbor task")  # noqa: TRY003

    blocks = source.split("[[steps]]")
    updated = [blocks[0]]
    for block in blocks[1:]:
        if "min_reward" not in block.split("[[", 1)[0]:
            lines = block.splitlines(keepends=True)
            name_index = next((index for index, line in enumerate(lines) if line.strip().startswith("name =")), None)
            if name_index is None:
                raise ValueError("Every Harbor step requires a name")  # noqa: TRY003
            lines.insert(name_index + 1, "min_reward = 1.0\n")
            block = "".join(lines)
        updated.extend(("[[steps]]", block))
    path.write_text("".join(updated), encoding="utf-8")


async def memory_snapshot(client: PowerContextClient, scope_id: str) -> MemorySnapshot:
    response = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope_id))
    return MemorySnapshot(
        entries=tuple(
            MemoryEntrySnapshot(
                entry_id=entry.citation.entry_id,
                entry_version_id=entry.citation.entry_version_id,
                version=entry.version,
                kind=entry.kind,
                text=entry.text,
                state=entry.state.value,
                source_refs=tuple(
                    SourceReferenceSnapshot(name=source.name, source_id=source.source_id)
                    for source in entry.source_refs
                ),
            )
            for entry in response.entries
        )
    )


async def prepared_context(client: PowerContextClient, scope_id: str, query: str) -> PreparedContextSnapshot:
    prepared = await client.prepare_context(PrepareContextRequest(scope_id=scope_id, query=query))
    return PreparedContextSnapshot(status=prepared.status.value, content=prepared.content or "")


async def _prepared_probes(
    client: PowerContextClient,
    task: E2ETask,
    scope_id: str,
) -> tuple[RecallProbeObservation, ...]:
    observations = []
    for probe in task.evaluation.probes:
        observations.append(
            RecallProbeObservation(
                id=probe.id,
                query=probe.query,
                prepared_context=await prepared_context(client, scope_id, probe.query),
            )
        )
    return tuple(observations)


def _run_environment(task: E2ETask, started_at: datetime, settings: HarnessSettings) -> RunEnvironment:
    return RunEnvironment(
        commit=settings.commit_id(),
        database=settings.database,
        adapter_version=BUB_VERSION,
        adapter_protocol_version=BUB_ACP_SERVER_VERSION,
        agent_model=bub_environment().get("BUB_MODEL") if task.execution.model else None,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


def _harbor_observation(
    result: Any,
    settings: HarnessSettings,
) -> tuple[HarborTrialObservation, tuple[Any, ...], Path | None]:
    if not result.trial_results:
        return (
            HarborTrialObservation(
                job_id=str(result.id),
                exception_type="HarborJobError",
                exception_message="Harbor job returned no trial results.",
            ),
            (),
            None,
        )
    trial = result.trial_results[0]
    rewards = trial.verifier_result.rewards if trial.verifier_result is not None else {}
    exception = trial.exception_info
    return (
        HarborTrialObservation(
            job_id=str(result.id),
            trial_name=trial.trial_name,
            trial_uri=trial.trial_uri,
            task_checksum=trial.task_checksum,
            rewards=rewards or {},
            exception_type=None if exception is None else exception.exception_type,
            exception_message=None if exception is None else redact(exception.exception_message, settings),
            started_at=trial.started_at,
            finished_at=trial.finished_at,
        ),
        tuple(trial.step_results or ()),
        _trial_dir(trial.trial_uri),
    )


def _trial_dir(trial_uri: str) -> Path | None:
    parsed = urlparse(trial_uri)
    return Path(unquote(parsed.path)) if parsed.scheme == "file" else None


def _load_capture_records(trial_dir: Path) -> tuple[CaptureRecord, ...]:
    records: list[CaptureRecord] = []
    for path in sorted(trial_dir.rglob("powercontext-capture.jsonl")):
        records.extend(
            CaptureRecord.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return tuple(records)


def _native_artifacts(trial_dir: Path, names: frozenset[str]) -> tuple[NativeArtifact, ...]:
    return tuple(
        fingerprint(path, relative_to=trial_dir) for path in sorted(trial_dir.rglob("*")) if path.name in names
    )


def collect_task_artifacts(
    sources: tuple[SourceTask, ...],
    trial_dir: Path,
    native_artifact_names: frozenset[str],
    settings: HarnessSettings,
) -> dict[str, TaskArtifacts]:
    """Collect shared-trial artifacts below each source task's Harbor steps."""

    instructions = load_resolved_instructions(trial_dir, settings)
    result: dict[str, TaskArtifacts] = {}
    for source in sources:
        step_roots = tuple(trial_dir / "steps" / step for step in source.runtime_steps)
        capture_records = tuple(
            CaptureRecord.model_validate_json(line)
            for root in step_roots
            for path in sorted(root.rglob("powercontext-capture.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        native_artifacts = tuple(
            fingerprint(path, relative_to=trial_dir)
            for root in step_roots
            for path in sorted(root.rglob("*"))
            if path.name in native_artifact_names
        )
        prefixes = tuple(f"steps/{step}/" for step in source.runtime_steps)
        resolved_instructions = tuple(
            instruction for instruction in instructions if instruction.artifact.startswith(prefixes)
        )
        result[source.task.id] = TaskArtifacts(capture_records, native_artifacts, resolved_instructions)
    return result
