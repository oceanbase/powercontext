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
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, NamedTuple
from urllib.parse import unquote, urlparse
from uuid import uuid4

from dirhash import dirhash
from harbor.environments.definition import environment_content_hash
from harbor.job import Job
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import DatasetConfig, JobConfig
from harbor.models.task.config import MultiStepRewardStrategy, TaskConfig
from harbor.models.task.paths import TaskPaths
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, ResourceMode, ServiceVolumeConfig
from harbor.models.trial.config import TaskConfig as HarborTrialTaskConfig
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import StepResult
from powercontext.client import PowerContextClient
from powercontext.client.settings import ClientSettings
from powercontext.http import CreateScopeRequest, ListMemoryEntriesRequest, PrepareContextRequest

from .artifacts import write_artifacts
from .catalog import E2ETask, MemoryEvaluationSpec, OutcomeEvaluationSpec
from .evaluation import evaluate_observation, matches_forbidden_context
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
TaskStatus = Literal["completed", "failed", "skipped"]
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


class TaskRun(NamedTuple):
    task: E2ETask
    run_id: str
    scope_id: str
    started_at: datetime
    memory_before: MemorySnapshot


async def evaluate_tasks(
    tasks: tuple[E2ETask, ...],
    *,
    output_dir: Path,
    settings: HarnessSettings,
    failure_policy: FailurePolicy = "collect-all",
) -> bool:
    observations = await run_task_group(
        tasks,
        output_dir=output_dir,
        settings=settings,
        failure_policy=failure_policy,
    )
    reports = tuple(
        evaluate_observation(
            observation,
            experiment=f"e2e:{observation.task.id}",
            failure_policy=failure_policy,
        )
        for observation in observations
    )
    if len(observations) == 1:
        write_artifacts(observations[0], reports[0], output_dir, settings=settings)
        return reports[0].accepted

    for observation, report in zip(observations, reports, strict=True):
        write_artifacts(observation, report, output_dir / "tasks" / observation.task.id, settings=settings)
    aggregate = EvaluationReport(
        experiment=f"e2e:batch:{_task_batch(tasks[0])}",
        cases=tuple(case for report in reports for case in report.cases),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_evaluation_report(output_dir / "eval-report.json", report=aggregate, settings=settings)
    write_evidence(output_dir / "report.md", render_evaluation_summary(aggregate), settings)
    return aggregate.accepted


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
        accepted &= await evaluate_tasks(
            group.tasks,
            output_dir=output_dir / group.output_id,
            settings=settings,
            failure_policy=failure_policy,
        )
    return accepted


def group_tasks(tasks: tuple[E2ETask, ...]) -> tuple[ExecutionGroup, ...]:
    """Group selected tasks only when they share an explicit batch category."""

    groups: list[ExecutionGroup] = []
    positions: dict[str, int] = {}
    for task in tasks:
        batch = _task_batch(task)
        if batch is None:
            groups.append(ExecutionGroup((task,), None))
        elif batch not in positions:
            positions[batch] = len(groups)
            groups.append(ExecutionGroup((task,), batch))
        else:
            index = positions[batch]
            groups[index] = ExecutionGroup((*groups[index].tasks, task), batch)
    return tuple(ExecutionGroup(group.tasks, group.batch if len(group.tasks) > 1 else None) for group in groups)


def _task_batch(task: E2ETask) -> str | None:
    batches = tuple(
        category.removeprefix(BATCH_CATEGORY_PREFIX)
        for category in task.categories
        if category.startswith(BATCH_CATEGORY_PREFIX)
    )
    if len(batches) > 1 or any(BATCH_NAME_PATTERN.fullmatch(batch) is None for batch in batches):
        raise ValueError(f"Task {task.id!r} must declare at most one valid batch category")  # noqa: TRY003
    return batches[0] if batches else None


async def run_task_group(
    tasks: tuple[E2ETask, ...],
    *,
    output_dir: Path,
    settings: HarnessSettings,
    failure_policy: FailurePolicy = "collect-all",
) -> tuple[TaskObservation, ...]:
    if not tasks:
        raise ValueError("At least one E2E task is required")  # noqa: TRY003

    runtime = (
        prepare_runtime_task(tasks, output_dir=output_dir, settings=settings, failure_policy=failure_policy)
        if len(tasks) > 1
        else None
    )
    run_id = _run_id(tasks)
    harbor = HarborTrialObservation()
    step_results: tuple[StepResult, ...] = ()
    trial_dir: Path | None = None
    execution_errors: list[str] = []

    async with _powercontext_client() as client:
        memory_tasks = tuple(task for task in tasks if isinstance(task.evaluation, MemoryEvaluationSpec))
        if memory_tasks:
            await client.get_readiness()
        started_at = datetime.now(UTC)
        scopes = {
            task.id: (
                await client.create_scope(
                    CreateScopeRequest(
                        title=f"E2E workload: {task.id}",
                        summary=f"Isolated Scope for the {task.id} workload in E2E run {run_id}.",
                        idempotency_key=f"e2e:{run_id}:{task.id}",
                    )
                )
            ).scope_id
            for task in tasks
        }
        runs = {task.id: TaskRun(task, run_id, scopes[task.id], started_at, MemorySnapshot()) for task in tasks}
        invocation_scopes = (
            tuple(scopes[source.task.id] for source in runtime.sources for _ in source.runtime_steps) if runtime else ()
        )
        job_config = _job_config(
            tasks[0],
            run_id,
            scopes[tasks[0].id],
            output_dir,
            settings,
            runtime=runtime,
            invocation_scopes=invocation_scopes,
        )
        try:
            for task in memory_tasks:
                run = runs[task.id]
                runs[task.id] = run._replace(memory_before=await memory_snapshot(client, run.scope_id))

            output_dir.mkdir(parents=True, exist_ok=True)
            harbor, step_results, trial_dir = _harbor_observation(await (await Job.create(job_config)).run(), settings)
            if harbor.exception_type is not None:
                execution_errors.append(f"{harbor.exception_type}: {harbor.exception_message or ''}".strip())
        except Exception as exc:
            execution_errors.append(redact(f"{type(exc).__name__}: {exc}", settings))

        if runtime is None:
            artifacts = _collect_task_artifacts(trial_dir, tasks[0], settings, errors=execution_errors)
            reward_failed = _outcome_reward_failed(tasks[0], harbor.rewards)
            status: TaskStatus = "failed" if execution_errors or reward_failed else "completed"
            return (
                await _finalize_task(
                    client,
                    runs[tasks[0].id],
                    status,
                    tuple(execution_errors),
                    harbor,
                    artifacts,
                    settings,
                ),
            )

        error_owner = next(
            (
                source.task.id
                for source in reversed(runtime.sources)
                if any(step.step_name in source.runtime_steps for step in step_results)
            ),
            runtime.sources[0].task.id,
        )
        observations: list[TaskObservation] = []
        for source in runtime.sources:
            owned = tuple(step for step in step_results if step.step_name in source.runtime_steps)
            missing = [step for step in source.runtime_steps if step not in {result.step_name for result in owned}]
            verifier = owned[-1].verifier_result if owned else None
            reward_failed = _outcome_reward_failed(
                source.task,
                None if verifier is None else verifier.rewards,
            )
            status: TaskStatus = (
                "skipped"
                if not owned and source.task.id != error_owner
                else "failed"
                if missing
                or any(step.exception_info is not None for step in owned)
                or reward_failed
                or (source.task.id == error_owner and bool(execution_errors))
                else "completed"
            )
            errors = list(execution_errors) if source.task.id == error_owner else []
            if missing and owned:
                errors.append(f"Harbor did not execute steps: {missing!r}")
            artifacts = _collect_task_artifacts(
                trial_dir,
                source.task,
                settings,
                errors=errors,
                step_names=source.runtime_steps if status != "skipped" else (),
                whole_trial=False,
            )
            if errors:
                status = "failed"
            observations.append(
                await _finalize_task(
                    client,
                    runs[source.task.id],
                    status,
                    tuple(errors),
                    _source_harbor_observation(harbor, source, status, owned, settings),
                    artifacts,
                    settings,
                )
            )
        return tuple(observations)


def _run_id(tasks: tuple[E2ETask, ...]) -> str:
    name = tasks[0].id if len(tasks) == 1 else f"batch-{_task_batch(tasks[0])}"
    return f"{name}-{uuid4().hex[:12]}"


def _outcome_reward_failed(task: E2ETask, rewards: Mapping[str, float | int] | None) -> bool:
    reward = None if rewards is None else rewards.get("reward")
    return isinstance(task.evaluation, OutcomeEvaluationSpec) and reward is not None and float(reward) < 1


def _powercontext_client() -> PowerContextClient:
    settings = ClientSettings()
    token = None if settings.api_token is None else settings.api_token.get_secret_value()
    return PowerContextClient(settings.server_url, token=token, timeout=settings.timeout)


async def _finalize_task(
    client: PowerContextClient,
    run: TaskRun,
    status: TaskStatus,
    errors: tuple[str, ...],
    harbor: HarborTrialObservation,
    artifacts: TaskArtifacts,
    settings: HarnessSettings,
) -> TaskObservation:
    final_errors = list(errors)
    memory_after = MemorySnapshot()
    probes: tuple[RecallProbeObservation, ...] = ()
    if status != "skipped" and isinstance(run.task.evaluation, MemoryEvaluationSpec):
        try:
            memory_after = await memory_snapshot(client, run.scope_id)
            probes = await _prepared_probes(client, run.task.evaluation, run.scope_id)
        except Exception as exc:
            final_errors.append(redact(f"{type(exc).__name__}: {exc}", settings))
    if final_errors and status == "completed":
        status = "failed"

    return TaskObservation(
        run_id=run.run_id,
        environment=_run_environment(run.task, run.started_at, settings),
        task=run.task,
        status=status,
        errors=tuple(final_errors),
        harbor=harbor,
        capture_records=artifacts.capture_records,
        native_artifacts=artifacts.native_artifacts,
        resolved_instructions=artifacts.resolved_instructions,
        memory_before=run.memory_before,
        memory_after=memory_after,
        probes=probes,
    )


def _source_harbor_observation(
    shared: HarborTrialObservation,
    source: SourceTask,
    status: TaskStatus,
    steps: tuple[StepResult, ...],
    settings: HarnessSettings,
) -> HarborTrialObservation:
    step_exception = next((step.exception_info for step in steps if step.exception_info is not None), None)
    verifier = steps[-1].verifier_result if steps else None
    updates: dict[str, Any] = {
        "source_task_checksum": source.harbor_task.checksum,
        "rewards": dict(verifier.rewards or {}) if verifier is not None else {},
        "exception_type": None if step_exception is None else step_exception.exception_type,
        "exception_message": (
            None if step_exception is None else redact(step_exception.exception_message or "", settings)
        ),
    }
    if status == "failed" and step_exception is None and shared.exception_type is not None:
        updates.update(exception_type=shared.exception_type, exception_message=shared.exception_message)
    return shared.model_copy(update=updates)


def _job_config(
    task: E2ETask,
    run_id: str,
    scope_id: str,
    output_dir: Path,
    settings: HarnessSettings,
    *,
    runtime: PreparedRuntime | None = None,
    invocation_scopes: tuple[str, ...] = (),
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

    evaluation = task.evaluation
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
        "POWERCONTEXT_BUB_CAPTURE_CHECKPOINT_EVERY": str(
            evaluation.checkpoint_every_events if isinstance(evaluation, MemoryEvaluationSpec) else 5
        ),
        "POWERCONTEXT_BUB_CAPTURE_EVENTS": str(
            evaluation.capture_events if isinstance(evaluation, MemoryEvaluationSpec) else False
        ).lower(),
        "POWERCONTEXT_BUB_CAPTURE_LOG": "/logs/agent/powercontext-capture.jsonl",
        "POWERCONTEXT_BUB_CAPTURE_MAX_BYTES": str(
            evaluation.max_event_bytes if isinstance(evaluation, MemoryEvaluationSpec) else 8192
        ),
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
    agent_kwargs: dict[str, Any] = {}
    if runtime is not None:
        agent_env.pop("POWERCONTEXT_BUB_SCOPE_ID")
        agent_kwargs["invocation_scopes"] = invocation_scopes

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
                kwargs=agent_kwargs,
            )
        ],
        datasets=[] if runtime else [_dataset_config(task, repository)],
        tasks=[runtime.task_config] if runtime else [],
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
) -> PreparedRuntime:
    """Assemble compatible source tasks into one run-local Harbor task."""

    sources = _validate_batch_compatibility(tasks, settings)
    batch = _task_batch(tasks[0])
    if len(tasks) < 2 or batch is None:
        raise ValueError("Runtime aggregation requires at least two tasks in one explicit batch")  # noqa: TRY003

    runtime_paths = TaskPaths(output_dir / "harbor-runtime-dataset" / f"batch-{batch}-{uuid4().hex[:12]}")
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
            shutil.copytree(source.harbor_task.paths.step_dir(source_step), runtime_paths.step_dir(runtime_step))

    config = _runtime_task_config(sources, failure_policy)
    runtime_paths.config_path.write_text(config.model_dump_toml(), encoding="utf-8")
    HarborTask(runtime_paths.task_dir)
    return PreparedRuntime(HarborTrialTaskConfig(path=runtime_paths.task_dir), sources)


def _validate_batch_compatibility(tasks: tuple[E2ETask, ...], settings: HarnessSettings) -> tuple[SourceTask, ...]:
    batch = _task_batch(tasks[0])
    if batch is None or any(_task_batch(task) != batch for task in tasks):
        raise ValueError("Runtime aggregation requires one explicit shared batch")  # noqa: TRY003
    if any(task.dataset.path is None for task in tasks):
        raise ValueError(f"Batch {batch!r} requires local Harbor datasets")  # noqa: TRY003
    if len({task.id for task in tasks}) != len(tasks):
        raise ValueError(f"Batch {batch!r} task IDs must be unique")  # noqa: TRY003

    repository = settings.repository_path()
    sources = tuple(_load_source_task(task, repository) for task in tasks)
    if any(
        source.harbor_task.config.multi_step_reward_strategy is not MultiStepRewardStrategy.FINAL for source in sources
    ):
        raise ValueError(f"Batch {batch!r} requires Harbor's final multi-step reward strategy")  # noqa: TRY003
    first_profile = _runtime_profile(sources[0])
    for source in sources[1:]:
        profile = _runtime_profile(source)
        if incompatible := [name for name, value in first_profile.items() if profile[name] != value]:
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
    return SourceTask(task, harbor_task, _task_layout(task, harbor_task))


def _runtime_profile(source: SourceTask) -> dict[str, Any]:
    task = source.task
    paths = source.harbor_task.paths
    evaluation = task.evaluation
    return {
        "dataset": task.dataset.model_dump(mode="json", exclude={"task_id", "checksum"}),
        "execution": task.execution.model_dump(mode="json"),
        "capture": (
            evaluation.capture_events,
            evaluation.checkpoint_every_events,
            evaluation.max_event_bytes,
        )
        if isinstance(evaluation, MemoryEvaluationSpec)
        else (False, 5, 8192),
        "harbor": source.harbor_task.config.model_dump(mode="json", exclude={"steps"}),
        "environment": environment_content_hash(
            paths.environment_dir,
            docker_image=source.harbor_task.config.environment.docker_image,
        ),
        "tests": dirhash(paths.tests_dir, "sha256") if paths.tests_dir.is_dir() else None,
    }


def _runtime_task_config(sources: tuple[SourceTask, ...], failure_policy: FailurePolicy) -> TaskConfig:
    min_reward = 1.0 if failure_policy == "fail-fast" else None
    steps = [
        step.model_copy(update={"name": runtime_name, "min_reward": min_reward})
        for source in sources
        for step, runtime_name in zip(source.harbor_task.config.steps or (), source.runtime_steps, strict=True)
    ]
    return sources[0].harbor_task.config.model_copy(update={"steps": steps})


def _task_layout(task: E2ETask, harbor_task: HarborTask) -> tuple[str, ...]:
    steps = tuple(step.name for step in harbor_task.config.steps or ())
    paths = tuple(PurePosixPath(step) for step in steps)
    if not steps or len(steps) != len(set(steps)):
        raise ValueError(f"Source task {task.id!r} must have unique Harbor steps")  # noqa: TRY003
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
    evaluation: MemoryEvaluationSpec,
    scope_id: str,
) -> tuple[RecallProbeObservation, ...]:
    probes = []
    for probe in evaluation.probes:
        context = await prepared_context(client, scope_id, probe.query)
        forbidden_context_matched = context.status == "ready" and matches_forbidden_context(
            context.content,
            probe.forbidden_context,
        )
        if forbidden_context_matched:
            context = context.model_copy(update={"content": ""})
        probes.append(
            RecallProbeObservation(
                id=probe.id,
                query=probe.query,
                prepared_context=context,
                forbidden_context_matched=forbidden_context_matched,
            )
        )
    return tuple(probes)


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
    result: Any, settings: HarnessSettings
) -> tuple[HarborTrialObservation, tuple[StepResult, ...], Path | None]:
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


def _task_artifacts(
    trial_dir: Path | None,
    task: E2ETask,
    settings: HarnessSettings,
    *,
    step_names: tuple[str, ...] = (),
    whole_trial: bool = True,
) -> TaskArtifacts:
    if trial_dir is None:
        return TaskArtifacts()
    roots = (trial_dir,) if whole_trial else tuple(TrialPaths(trial_dir).step_dir(name) for name in step_names)
    prefixes = tuple(f"steps/{name}/" for name in step_names)
    instructions = load_resolved_instructions(trial_dir, settings)
    return TaskArtifacts(
        tuple(record for root in roots for record in _load_capture_records(root)),
        tuple(
            artifact
            for root in roots
            for artifact in _native_artifacts(root, task.execution.native_artifact_names, relative_to=trial_dir)
        ),
        tuple(instruction for instruction in instructions if whole_trial or instruction.artifact.startswith(prefixes)),
    )


def _collect_task_artifacts(
    trial_dir: Path | None,
    task: E2ETask,
    settings: HarnessSettings,
    *,
    errors: list[str],
    step_names: tuple[str, ...] = (),
    whole_trial: bool = True,
) -> TaskArtifacts:
    try:
        return _task_artifacts(trial_dir, task, settings, step_names=step_names, whole_trial=whole_trial)
    except Exception as exc:
        errors.append(redact(f"{type(exc).__name__}: {exc}", settings))
        return TaskArtifacts()


def _load_capture_records(root: Path) -> tuple[CaptureRecord, ...]:
    records: list[CaptureRecord] = []
    for path in sorted(root.rglob("powercontext-capture.jsonl")):
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
