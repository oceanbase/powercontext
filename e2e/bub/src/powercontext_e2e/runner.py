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

from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

from harbor.job import Job
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import DatasetConfig, JobConfig
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, ResourceMode, ServiceVolumeConfig
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


async def run_task(task: E2ETask, *, output_dir: Path, settings: HarnessSettings) -> TaskObservation:
    started_at = datetime.now(UTC)
    run_id = f"{task.id}-{uuid4().hex[:12]}"
    scope_id = f"e2e:{run_id}"
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
            job = await Job.create(_job_config(task, run_id, scope_id, output_dir, settings))
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
