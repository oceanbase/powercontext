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

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, NamedTuple
from urllib.parse import unquote, urlparse
from uuid import uuid4

from dirhash import dirhash
from harbor.job import Job
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import DatasetConfig, JobConfig
from harbor.models.task.config import MultiStepRewardStrategy, TaskConfig
from harbor.models.task.paths import TaskPaths
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    ResourceMode,
    ServiceVolumeConfig,
)
from harbor.models.trial.config import (
    TaskConfig as HarborTrialTaskConfig,
)
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import StepResult
from powercontext.client import PowerContextClient
from powercontext.client.settings import ClientSettings
from powercontext.http import ListMemoryEntriesRequest, PrepareContextRequest

from .artifacts import write_artifacts
from .catalog import E2ETask
from .evaluation import MemoryEvaluator
from .evidence import fingerprint, load_resolved_instructions, redact, write_evaluation_report, write_evidence
from .harbor_agent import BUB_ACP_SERVER_VERSION, BUB_VERSION
from .models import (
    SHARED_TRIAL_SKIPPED_ERROR,
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
BATCH_CATEGORY_PREFIX = "batch:"
BATCH_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class TaskArtifacts(NamedTuple):
    capture_records: tuple[CaptureRecord, ...] = ()
    native_artifacts: tuple[NativeArtifact, ...] = ()
    resolved_instructions: tuple[ResolvedInstruction, ...] = ()


class ExecutionGroup(NamedTuple):
    tasks: tuple[E2ETask, ...]
    batch: str | None

    @property
    def output_id(self) -> str:
        return self.tasks[0].id if self.batch is None else f"batch-{self.batch}"


class SourceTask(NamedTuple):
    task: E2ETask
    harbor_task: HarborTask
    source_steps: tuple[str, ...]

    @property
    def runtime_steps(self) -> tuple[str, ...]:
        return tuple(f"{self.task.id}-{step}" for step in self.source_steps)


class PreparedRuntime(NamedTuple):
    task_config: HarborTrialTaskConfig
    sources: tuple[SourceTask, ...]


class SourceResult(NamedTuple):
    status: Literal["completed", "failed", "skipped"]
    errors: tuple[str, ...]
    steps: tuple[StepResult, ...]


class PreparedTask(NamedTuple):
    task: E2ETask
    run_id: str
    scope_id: str
    started_at: datetime
    memory_before: MemorySnapshot


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
    experiment = f"e2e:batch:{_task_batch(tasks[0])}"
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
    """Group only selected tasks that share an explicit batch category."""

    groups: list[ExecutionGroup] = []
    positions: dict[str, int] = {}
    for task in tasks:
        batch = _task_batch(task)
        if batch is None:
            groups.append(ExecutionGroup(tasks=(task,), batch=None))
            continue
        if batch not in positions:
            positions[batch] = len(groups)
            groups.append(ExecutionGroup(tasks=(task,), batch=batch))
            continue
        index = positions[batch]
        group = groups[index]
        groups[index] = ExecutionGroup(tasks=(*group.tasks, task), batch=group.batch)
    return tuple(
        ExecutionGroup(tasks=group.tasks, batch=group.batch if len(group.tasks) > 1 else None) for group in groups
    )


def _task_batch(task: E2ETask) -> str | None:
    batches = tuple(
        category.removeprefix(BATCH_CATEGORY_PREFIX)
        for category in task.categories
        if category.startswith(BATCH_CATEGORY_PREFIX)
    )
    if len(batches) > 1 or any(BATCH_NAME_PATTERN.fullmatch(batch) is None for batch in batches):
        raise ValueError(f"Task {task.id!r} must declare at most one valid batch category")  # noqa: TRY003
    return batches[0] if batches else None


def _powercontext_client() -> PowerContextClient:
    settings = ClientSettings()
    token = None if settings.api_token is None else settings.api_token.get_secret_value()
    return PowerContextClient(settings.server_url, token=token, timeout=settings.timeout)


async def run_task(
    task: E2ETask,
    *,
    output_dir: Path,
    settings: HarnessSettings,
) -> TaskObservation:
    run_id = f"{task.id}-{uuid4().hex[:12]}"
    scope_id = f"e2e:{run_id}"
    prepared = PreparedTask(task, run_id, scope_id, datetime.now(UTC), MemorySnapshot())
    errors: list[str] = []
    artifacts = TaskArtifacts()
    harbor = HarborTrialObservation()
    execution_status: Literal["completed", "failed"] = "failed"

    async with _powercontext_client() as client:
        try:
            await client.get_readiness()
            prepared = prepared._replace(memory_before=await memory_snapshot(client, scope_id))
            output_dir.mkdir(parents=True, exist_ok=True)
            job = await Job.create(_job_config(task, run_id, scope_id, output_dir, settings))
            result = await job.run()
            harbor, _, trial_dir = _harbor_observation(result, settings)
            if harbor.exception_type is not None:
                errors.append(f"{harbor.exception_type}: {harbor.exception_message or ''}".strip())
            else:
                execution_status = "completed"
            if trial_dir is not None:
                artifacts = _load_task_artifacts(trial_dir, task.execution.native_artifact_names, settings)
        except Exception as exc:
            errors.append(redact(f"{type(exc).__name__}: {exc}", settings))
        return await _finalize_task(client, prepared, execution_status, tuple(errors), harbor, artifacts, settings)


async def run_task_group(
    tasks: tuple[E2ETask, ...],
    *,
    output_dir: Path,
    settings: HarnessSettings,
    failure_policy: FailurePolicy = "collect-all",
) -> tuple[TaskObservation, ...]:
    if len(tasks) < 2:
        raise ValueError("A runtime batch requires at least two E2E tasks")  # noqa: TRY003
    batch = _task_batch(tasks[0])
    run_id = f"batch-{batch}-{uuid4().hex[:12]}"
    prepared = prepare_runtime_task(
        tasks,
        output_dir=output_dir,
        settings=settings,
        failure_policy=failure_policy,
        runtime_id=run_id,
    )
    task_scopes = {task.id: f"e2e:{run_id}:{task.id}" for task in tasks}
    prepared_tasks = {
        task.id: PreparedTask(task, run_id, task_scopes[task.id], datetime.now(UTC), MemorySnapshot()) for task in tasks
    }
    invocation_scopes = tuple(task_scopes[source.task.id] for source in prepared.sources for _ in source.runtime_steps)
    harbor = HarborTrialObservation()
    step_results: tuple[Any, ...] = ()
    trial_dir: Path | None = None
    execution_errors: list[str] = []

    async with _powercontext_client() as client:
        try:
            await client.get_readiness()
            for task in tasks:
                prepared_task = prepared_tasks[task.id]
                prepared_tasks[task.id] = prepared_task._replace(
                    memory_before=await memory_snapshot(client, prepared_task.scope_id)
                )
            job = await Job.create(
                _batch_job_config(
                    tasks[0],
                    run_id,
                    invocation_scopes,
                    output_dir,
                    settings,
                    task_config=prepared.task_config,
                )
            )
            result = await job.run()
            harbor, step_results, trial_dir = _harbor_observation(result, settings)
            if harbor.exception_type is not None:
                execution_errors.append(f"{harbor.exception_type}: {harbor.exception_message or ''}".strip())
        except Exception as exc:
            execution_errors.append(redact(f"{type(exc).__name__}: {exc}", settings))

        source_results = _source_results(prepared.sources, step_results, tuple(execution_errors))
        observations: list[TaskObservation] = []
        for source in prepared.sources:
            task = source.task
            source_result = source_results[task.id]
            artifacts = TaskArtifacts()
            if trial_dir is not None and source_result.status != "skipped":
                try:
                    artifacts = _load_task_artifacts(
                        trial_dir,
                        task.execution.native_artifact_names,
                        settings,
                        step_names=source.runtime_steps,
                    )
                except Exception as exc:
                    source_result = source_result._replace(
                        status="failed",
                        errors=(*source_result.errors, redact(f"{type(exc).__name__}: {exc}", settings)),
                    )
            source_harbor = _source_harbor_observation(harbor, source, source_result, settings)
            observations.append(
                await _finalize_task(
                    client,
                    prepared_tasks[task.id],
                    source_result.status,
                    source_result.errors,
                    source_harbor,
                    artifacts,
                    settings,
                )
            )
        return tuple(observations)


async def _finalize_task(
    client: PowerContextClient,
    prepared: PreparedTask,
    execution_status: Literal["completed", "failed", "skipped"],
    errors: tuple[str, ...],
    harbor: HarborTrialObservation,
    artifacts: TaskArtifacts,
    settings: HarnessSettings,
) -> TaskObservation:
    final_errors = list(errors)
    memory_after = MemorySnapshot()
    probes: tuple[RecallProbeObservation, ...] = ()
    if execution_status != "skipped":
        try:
            memory_after = await memory_snapshot(client, prepared.scope_id)
        except Exception as exc:
            final_errors.append(redact(f"{type(exc).__name__}: {exc}", settings))
        try:
            probes = await _prepared_probes(client, prepared.task, prepared.scope_id)
        except Exception as exc:
            final_errors.append(redact(f"{type(exc).__name__}: {exc}", settings))

    return TaskObservation(
        run_id=prepared.run_id,
        environment=_run_environment(prepared.task, prepared.started_at, settings),
        task=prepared.task,
        status="completed" if execution_status == "completed" and not final_errors else "failed",
        errors=tuple(final_errors),
        harbor=harbor,
        capture_records=artifacts.capture_records,
        native_artifacts=artifacts.native_artifacts,
        resolved_instructions=artifacts.resolved_instructions,
        memory_before=prepared.memory_before,
        memory_after=memory_after,
        probes=probes,
    )


def _source_results(
    sources: tuple[SourceTask, ...],
    step_results: tuple[StepResult, ...],
    execution_errors: tuple[str, ...],
) -> dict[str, SourceResult]:
    results: dict[str, SourceResult] = {}
    for index, source in enumerate(sources):
        owned = tuple(step for step in step_results if step.step_name in source.runtime_steps)
        executed = {step.step_name for step in owned}
        missing = [name for name in source.runtime_steps if name not in executed]
        if not owned:
            status: Literal["completed", "failed", "skipped"] = "failed" if index == 0 else "skipped"
            errors = ("Harbor did not execute any steps.",) if index == 0 else (SHARED_TRIAL_SKIPPED_ERROR,)
        elif missing or any(step.exception_info is not None for step in owned):
            status = "failed"
            errors = (f"Harbor did not execute steps: {missing!r}",) if missing else ()
        else:
            status = "completed"
            errors = ()
        results[source.task.id] = SourceResult(status, errors, owned)

    if execution_errors:
        source = next(
            (source for source in reversed(sources) if results[source.task.id].status != "skipped"),
            sources[0],
        )
        result = results[source.task.id]
        results[source.task.id] = SourceResult("failed", (*result.errors, *execution_errors), result.steps)
    return results


def _source_harbor_observation(
    shared: HarborTrialObservation,
    source: SourceTask,
    result: SourceResult,
    settings: HarnessSettings,
) -> HarborTrialObservation:
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
        task_checksum=source.harbor_task.checksum,
        rewards=_source_rewards(source, result.steps),
        exception_type=exception_type,
        exception_message=exception_message,
        started_at=shared.started_at,
        finished_at=shared.finished_at,
    )


def _source_rewards(source: SourceTask, steps: tuple[StepResult, ...]) -> dict[str, float | int]:
    strategy = source.harbor_task.config.multi_step_reward_strategy
    if strategy is MultiStepRewardStrategy.FINAL:
        verifier = steps[-1].verifier_result if steps else None
        return dict(verifier.rewards or {}) if verifier is not None else {}

    rewards = [step.verifier_result.rewards or {} for step in steps if step.verifier_result is not None]
    keys = {key for result in rewards for key in result}
    if not rewards or not keys:
        return {}
    return {key: sum(result.get(key, 0) for result in rewards) / len(rewards) for key in keys}


def _batch_job_config(
    task: E2ETask,
    job_name: str,
    invocation_scopes: tuple[str, ...],
    output_dir: Path,
    settings: HarnessSettings,
    *,
    task_config: HarborTrialTaskConfig,
) -> JobConfig:
    config = _job_config(task, job_name, invocation_scopes[0], output_dir, settings)
    agent = config.agents[0]
    env = dict(agent.env)
    env.pop("POWERCONTEXT_BUB_SCOPE_ID")
    return config.model_copy(
        update={
            "agents": [agent.model_copy(update={"env": env, "kwargs": {"invocation_scopes": invocation_scopes}})],
            "datasets": [],
            "tasks": [task_config],
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


def prepare_runtime_task(
    tasks: tuple[E2ETask, ...],
    *,
    output_dir: Path,
    settings: HarnessSettings,
    failure_policy: FailurePolicy,
    runtime_id: str | None = None,
) -> PreparedRuntime:
    """Assemble compatible selected source tasks into one run-local Harbor task."""

    if len(tasks) < 2:
        raise ValueError("Runtime aggregation requires at least two tasks")  # noqa: TRY003

    sources = _validate_batch_compatibility(tasks, settings)
    batch = _task_batch(tasks[0])
    if batch is None:
        raise ValueError("Runtime aggregation requires an explicit batch")  # noqa: TRY003

    runtime_task_id = runtime_id or f"batch-{batch}-{uuid4().hex[:12]}"
    runtime_root = output_dir / "harbor-runtime-dataset"
    runtime_paths = TaskPaths(runtime_root / runtime_task_id)
    runtime_paths.task_dir.mkdir(parents=True)

    first_paths = sources[0].harbor_task.paths
    for source_dir, target_dir in (
        (first_paths.environment_dir, runtime_paths.environment_dir),
        (first_paths.tests_dir, runtime_paths.tests_dir),
    ):
        if source_dir.exists():
            shutil.copytree(source_dir, target_dir)
    runtime_paths.steps_dir.mkdir()
    for source in sources:
        for source_step, runtime_step in zip(source.source_steps, source.runtime_steps, strict=True):
            shutil.copytree(
                source.harbor_task.paths.step_dir(source_step),
                runtime_paths.step_dir(runtime_step),
            )

    runtime_config = _runtime_task_config(sources, failure_policy)
    runtime_paths.config_path.write_text(runtime_config.model_dump_toml(), encoding="utf-8")

    HarborTask(runtime_paths.task_dir)
    return PreparedRuntime(HarborTrialTaskConfig(path=runtime_paths.task_dir), sources)


def _validate_batch_compatibility(
    tasks: tuple[E2ETask, ...],
    settings: HarnessSettings,
) -> tuple[SourceTask, ...]:
    batch = _task_batch(tasks[0])
    if batch is None or any(_task_batch(task) != batch for task in tasks):
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
    if harbor_task.checksum != task.dataset.checksum:
        raise ValueError(f"Source task {task.id!r} checksum changed")  # noqa: TRY003
    steps = _task_layout(task, harbor_task)
    return SourceTask(task, harbor_task, steps)


def _runtime_profile(source: SourceTask) -> dict[str, Any]:
    task = source.task
    paths = source.harbor_task.paths
    return {
        "dataset": task.dataset.model_dump(mode="json", exclude={"task_id", "checksum"}),
        "execution": task.execution.model_dump(mode="json"),
        "capture": (
            task.evaluation.capture_events,
            task.evaluation.checkpoint_every_events,
            task.evaluation.max_event_bytes,
        ),
        "harbor": source.harbor_task.config.model_dump(mode="json", exclude={"steps"}),
        "environment": _directory_checksum(paths.environment_dir),
        "tests": _directory_checksum(paths.tests_dir),
    }


def _directory_checksum(path: Path) -> str | None:
    return dirhash(path, "sha256") if path.is_dir() else None


def _runtime_task_config(sources: tuple[SourceTask, ...], failure_policy: FailurePolicy) -> TaskConfig:
    min_reward = 1.0 if failure_policy == "fail-fast" else None
    steps = [
        step.model_copy(update={"name": runtime_name, "min_reward": min_reward})
        for source in sources
        for step, runtime_name in zip(
            source.harbor_task.config.steps or (),
            source.runtime_steps,
            strict=True,
        )
    ]
    return sources[0].harbor_task.config.model_copy(update={"steps": steps})


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
    return steps


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


def _native_artifacts(
    root: Path, names: frozenset[str], *, relative_to: Path | None = None
) -> tuple[NativeArtifact, ...]:
    return tuple(
        fingerprint(path, relative_to=relative_to or root) for path in sorted(root.rglob("*")) if path.name in names
    )


def _load_task_artifacts(
    trial_dir: Path,
    native_artifact_names: frozenset[str],
    settings: HarnessSettings,
    *,
    step_names: tuple[str, ...] = (),
) -> TaskArtifacts:
    roots = tuple(TrialPaths(trial_dir).step_dir(name) for name in step_names) or (trial_dir,)
    instructions = load_resolved_instructions(trial_dir, settings)
    prefixes = tuple(f"steps/{name}/" for name in step_names)
    return TaskArtifacts(
        tuple(record for root in roots for record in _load_capture_records(root)),
        tuple(
            artifact
            for root in roots
            for artifact in _native_artifacts(root, native_artifact_names, relative_to=trial_dir)
        ),
        tuple(instruction for instruction in instructions if not prefixes or instruction.artifact.startswith(prefixes)),
    )
