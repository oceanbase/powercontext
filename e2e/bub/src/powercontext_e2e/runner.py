"""Run every end-to-end workload through Harbor, ACP, and Bub."""

from __future__ import annotations

import json
import shutil
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse
from uuid import uuid4

from harbor.job import Job
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import DatasetConfig, JobConfig
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, ResourceMode, ServiceVolumeConfig, VerifierConfig
from powercontext.client import PowerContextClient
from powercontext.client.settings import ClientSettings
from powercontext.http import (
    ListMemoryEntriesRequest,
    PrepareContextRequest,
)

from .artifacts import write_artifacts
from .catalog import E2ETask
from .evaluation import MemoryEvaluator
from .evidence import fingerprint, load_resolved_instructions, redact
from .harbor_agent import BUB_ACP_SERVER_VERSION, BUB_VERSION
from .models import (
    CaptureRecord,
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
from .settings import (
    HarnessSettings,
    ModelNotConfiguredError,
    bub_environment,
    codex_auth_path,
    powercontext_bub_environment,
)

REMOTE_HANDOFF_DIR = "/handoff"
REMOTE_WORKSPACE_ARCHIVE = f"{REMOTE_HANDOFF_DIR}/workspace.tar"


@dataclass(frozen=True)
class TaskRunOptions:
    run_id: str | None = None
    scope_id: str | None = None
    prompt: Literal["task", "continue"] = "task"
    handoff_after_steps: int | None = None
    segment_max_steps: int | None = None
    workspace_snapshot_dir: Path | None = None
    restore_workspace: bool = False
    save_workspace: bool = False
    disable_verifier: bool = False

    def __post_init__(self) -> None:
        if self.handoff_after_steps is not None and self.handoff_after_steps < 1:
            raise ValueError("handoff_after_steps must be positive")  # noqa: TRY003
        if self.segment_max_steps is not None and self.segment_max_steps < 1:
            raise ValueError("segment_max_steps must be positive")  # noqa: TRY003
        if (self.restore_workspace or self.save_workspace) and self.workspace_snapshot_dir is None:
            raise ValueError("Workspace restore and snapshot require workspace_snapshot_dir")  # noqa: TRY003


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


async def run_tasks(
    tasks: tuple[E2ETask, ...],
    *,
    output_dir: Path,
    settings: HarnessSettings,
) -> bool:
    model_workload_ids = tuple(task.id for task in tasks if task.execution.model)
    if model_workload_ids and "BUB_MODEL" not in bub_environment():
        raise ModelNotConfiguredError(model_workload_ids)

    accepted = True
    for task in tasks:
        task_accepted = await evaluate_task(task, output_dir=output_dir / task.id, settings=settings)
        accepted = task_accepted and accepted
    return accepted


async def run_task(
    task: E2ETask,
    *,
    output_dir: Path,
    settings: HarnessSettings,
    options: TaskRunOptions | None = None,
) -> TaskObservation:
    options = options or TaskRunOptions()
    started_at = datetime.now(UTC)
    run_id = options.run_id or f"{task.id}-{uuid4().hex[:12]}"
    scope_id = options.scope_id or f"e2e:{run_id}"
    errors: list[str] = []
    capture_records: tuple[CaptureRecord, ...] = ()
    native_artifacts: tuple[NativeArtifact, ...] = ()
    resolved_instructions: tuple[ResolvedInstruction, ...] = ()
    harbor_observation = HarborTrialObservation()
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
            if options.workspace_snapshot_dir is not None:
                options.workspace_snapshot_dir.mkdir(parents=True, exist_ok=True)
            job = await Job.create(_job_config(task, run_id, scope_id, output_dir, settings, options))
            result = await job.run()
            harbor_observation, trial_dir = _harbor_observation(result, settings)
            if harbor_observation.exception_type is not None:
                errors.append(
                    f"{harbor_observation.exception_type}: {harbor_observation.exception_message or ''}".strip()
                )
            if trial_dir is not None:
                capture_records = _load_capture_records(trial_dir)
                native_artifacts = _native_artifacts(trial_dir, task.execution.native_artifact_names)
                resolved_instructions = load_resolved_instructions(trial_dir, settings)
                if options.save_workspace and options.workspace_snapshot_dir is not None:
                    _persist_workspace_snapshot(trial_dir, options.workspace_snapshot_dir)

            memory_after = await memory_snapshot(client, scope_id)
            probe_observations: list[RecallProbeObservation] = []
            for probe in task.evaluation.probes:
                probe_observations.append(
                    RecallProbeObservation(
                        id=probe.id,
                        query=probe.query,
                        prepared_context=await prepared_context(client, scope_id, probe.query),
                    )
                )
            probes = tuple(probe_observations)
        except Exception as exc:
            errors.append(redact(f"{type(exc).__name__}: {exc}", settings))
            with suppress(Exception):
                memory_after = await memory_snapshot(client, scope_id)

    return TaskObservation(
        run_id=run_id,
        environment=RunEnvironment(
            commit=settings.commit_id(),
            database=settings.database,
            adapter_version=BUB_VERSION,
            adapter_protocol_version=BUB_ACP_SERVER_VERSION,
            agent_model=bub_environment().get("BUB_MODEL") if task.execution.model else None,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        ),
        task=task,
        status="completed" if not errors else "failed",
        errors=tuple(errors),
        harbor=harbor_observation,
        capture_records=capture_records,
        native_artifacts=native_artifacts,
        resolved_instructions=resolved_instructions,
        memory_before=memory_before,
        memory_after=memory_after,
        probes=probes,
    )


def _job_config(  # noqa: C901
    task: E2ETask,
    run_id: str,
    scope_id: str,
    output_dir: Path,
    settings: HarnessSettings,
    options: TaskRunOptions | None = None,
) -> JobConfig:
    options = options or TaskRunOptions()
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
    if options.restore_workspace and options.workspace_snapshot_dir is not None:
        workspace_archive = options.workspace_snapshot_dir.resolve() / "workspace.tar"
        if not workspace_archive.is_file():
            raise FileNotFoundError(f"Workspace snapshot does not exist: {workspace_archive}")  # noqa: TRY003
        mounts.append({
            "type": "bind",
            "source": str(workspace_archive),
            "target": REMOTE_WORKSPACE_ARCHIVE,
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
    if options.handoff_after_steps is not None:
        agent_env["POWERCONTEXT_E2E_SCENARIO_HANDOFF_AFTER_STEPS"] = str(options.handoff_after_steps)
    if options.segment_max_steps is not None:
        agent_env["POWERCONTEXT_E2E_SCENARIO_MAX_STEPS"] = str(options.segment_max_steps)
    if options.prompt == "continue":
        agent_env["POWERCONTEXT_E2E_SCENARIO_PROMPT"] = "continue"
    if options.restore_workspace:
        agent_env["POWERCONTEXT_E2E_WORKSPACE_ARCHIVE"] = REMOTE_WORKSPACE_ARCHIVE
    elif options.save_workspace:
        agent_env["POWERCONTEXT_E2E_WORKSPACE_ARCHIVE"] = "/logs/agent/workspace.tar"
    if options.restore_workspace:
        agent_env["POWERCONTEXT_E2E_RESTORE_WORKSPACE"] = "true"
    if options.save_workspace:
        agent_env["POWERCONTEXT_E2E_SAVE_WORKSPACE"] = "true"
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
        verifier=VerifierConfig(disable=options.disable_verifier),
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


def _harbor_observation(result: Any, settings: HarnessSettings) -> tuple[HarborTrialObservation, Path | None]:
    if not result.trial_results:
        return HarborTrialObservation(job_id=str(result.id)), None
    trial = result.trial_results[0]
    trial_dir = _trial_dir(trial.trial_uri)
    agent_sessions, agent_prompts = _agent_session_evidence(trial_dir)
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
            agent_sessions=agent_sessions,
            agent_prompts=agent_prompts,
        ),
        trial_dir,
    )


def _trial_dir(trial_uri: str) -> Path | None:
    parsed = urlparse(trial_uri)
    return Path(unquote(parsed.path)) if parsed.scheme == "file" else None


def _agent_session_evidence(trial_dir: Path | None) -> tuple[int, tuple[str, ...]]:
    if trial_dir is None:
        return 0, ()
    summary_paths = sorted(trial_dir.rglob("acp-summary.json"))
    for summary_path in summary_paths:
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        scenario_count = payload.get("scenario_session_count") if isinstance(payload, dict) else None
        if isinstance(scenario_count, int) and scenario_count > 0:
            segments = payload.get("segments")
            prompts = (
                tuple(
                    instruction
                    for segment in segments
                    if isinstance(segment, dict) and isinstance((instruction := segment.get("instruction")), str)
                )
                if isinstance(segments, list)
                else ()
            )
            return scenario_count, prompts
    prompts: list[str] = []
    for summary_path in summary_paths:
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        instruction = payload.get("instruction") if isinstance(payload, dict) else None
        if isinstance(instruction, str):
            prompts.append(instruction)
    return len(summary_paths), tuple(prompts)


def _load_capture_records(trial_dir: Path) -> tuple[CaptureRecord, ...]:
    records: list[CaptureRecord] = []
    for path in sorted(trial_dir.rglob("powercontext-capture.jsonl")):
        records.extend(
            CaptureRecord.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return tuple(records)


def _persist_workspace_snapshot(trial_dir: Path, snapshot_dir: Path) -> None:
    candidates = sorted(trial_dir.rglob("workspace.tar"))
    if len(candidates) != 1:
        raise ValueError(  # noqa: TRY003
            f"Expected one workspace snapshot below {trial_dir}, found {len(candidates)}"
        )
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidates[0], snapshot_dir / "workspace.tar")


def _native_artifacts(trial_dir: Path, names: frozenset[str]) -> tuple[NativeArtifact, ...]:
    return tuple(
        fingerprint(path, relative_to=trial_dir) for path in sorted(trial_dir.rglob("*")) if path.name in names
    )
