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

"""Supervised task-pair orchestration for queued evaluations."""

from __future__ import annotations

import fcntl
import logging
import os
import sqlite3
import stat
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from powercontext_eval.artifacts import ArtifactError
from powercontext_eval.benchmarks.base import GoldCheckFailed
from powercontext_eval.benchmarks.swebench_pro.adapter import DatasetSchemaError, SweBenchProInstance
from powercontext_eval.benchmarks.swebench_pro.catalog import CatalogError, SweBenchProCatalog
from powercontext_eval.benchmarks.swebench_pro.evaluator import OfficialResultError
from powercontext_eval.benchmarks.swebench_pro.prediction import BinaryPatchError
from powercontext_eval.codex import CodexCapacityError, CodexInfrastructureError, UnsafeCodexInvocation
from powercontext_eval.errors import CommandCancelled, CommandError, GitSourceError, PowerContextEvalError
from powercontext_eval.git_source import GitSource
from powercontext_eval.models import PowerContextRef
from powercontext_eval.paths import EvaluationPaths
from powercontext_eval.powercontext_sut import (
    InvalidTreatment,
    PluginInspectionFailure,
    ReadinessFailure,
    UnsafeSutConfiguration,
)
from powercontext_eval.process import ProcessRunner
from powercontext_eval.report import InvalidReportBundle
from powercontext_eval.runner import (
    MinimalRunConfig,
    MinimalRunResult,
    RunConfig,
    RunPhase,
    run_minimal_swebench_pro,
    run_swebench_pro_instance,
)
from powercontext_eval.tokensflow import TokensFlowFinalizationDescriptor, TokensFlowFinalizationRegistrar
from powercontext_eval.web.claiming import ClaimCoordinator, PeriodicUsageRefresher
from powercontext_eval.web.config import WebConfig
from powercontext_eval.web.finalization import DockerFinalizationRuntime, TokensFlowFinalizer
from powercontext_eval.web.models import (
    FailureCategory,
    FailureCode,
    RetryDisposition,
    SafeFailure,
    TaskPhase,
    TaskRecord,
    TaskResult,
)
from powercontext_eval.web.reporting import ReportingError, load_report
from powercontext_eval.web.resources import AttemptLifecycleCleaner, ResourceProbe, default_workspace_reclaimer
from powercontext_eval.web.revision import RUNTIME_SCHEMA_VERSION, current_build_revision
from powercontext_eval.web.store import (
    TaskConflict,
    TaskOwnershipError,
    TaskStore,
    TokensFlowFinalizationCreate,
)
from powercontext_eval.web.usage import CodexUsageProbe, UsageSnapshot

_INTERNAL_SUMMARY = "The evaluation worker failed unexpectedly. Inspect the retained worker logs."
_REPORT_SUMMARY = "Evaluation report validation failed."
_TREATMENT_FAILURE_SUMMARIES = {
    "Treatment evidence is malformed": "Treatment evidence was malformed.",
    "Treatment evidence does not match the requested arm": "Treatment evidence did not match the requested arm.",
    "PowerContext source HEAD does not match the configured commit": (
        "PowerContext source revision did not match the pinned batch."
    ),
    "PowerContext source checkout must be clean": "PowerContext source checkout was not clean.",
    "PowerContext plugin manifest is invalid": "PowerContext plugin manifest was invalid.",
    "PowerContext plugin manifest version does not match configuration": (
        "PowerContext plugin manifest version did not match the evaluation configuration."
    ),
    "PowerContext plugin lockfile is missing": "PowerContext plugin lockfile was missing.",
    "PowerContext plugin lockfile is invalid": "PowerContext plugin lockfile was invalid.",
    "Isolated Codex home does not contain the exact expected plugin": (
        "Isolated Codex home did not contain the pinned PowerContext plugin."
    ),
    "Codex CLI version does not match the pinned experiment": (
        "Codex CLI version did not match the pinned experiment."
    ),
    "PowerContext SQLite evidence is malformed": "PowerContext SQLite treatment evidence was malformed.",
}
_LOGGER = logging.getLogger(__name__)


class ThreadLike(Protocol):
    def start(self) -> None: ...

    def join(self) -> None: ...


ThreadFactory = Callable[..., ThreadLike]
Runner = Callable[..., MinimalRunResult]


class SourceResolver(Protocol):
    def resolve(self, source: str | Path, requested: PowerContextRef) -> object: ...


class Catalog(Protocol):
    def require(self, instance_id: str) -> SweBenchProInstance: ...


class UsageProbe(Protocol):
    def read(self, *, now: datetime) -> UsageSnapshot: ...


class FinalizerSupervisor(Protocol):
    def run_forever(self, stop: threading.Event, poll_seconds: float) -> None: ...


class WorkspaceReclaimerSupervisor(Protocol):
    def run_forever(self, stop: threading.Event) -> None: ...


class AttemptLifecycleSupervisor(Protocol):
    def run_once(self) -> int: ...

    def run_forever(self, stop: threading.Event) -> None: ...


class UsageRefreshSupervisor(Protocol):
    def run_forever(self, stop: threading.Event, poll_seconds: float) -> None: ...


class TaskPairWorker:
    """Claim and execute complete OFF/ON task pairs in one isolated slot."""

    def __init__(
        self,
        config: WebConfig,
        store: TaskStore,
        *,
        coordinator: ClaimCoordinator | None = None,
        usage_probe: UsageProbe | None = None,
        runner: Runner | None = None,
        source: SourceResolver | None = None,
        catalog: Catalog | None = None,
        worker_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        thread_factory: ThreadFactory = threading.Thread,
    ) -> None:
        self._config = config
        self._store = store
        self._usage_probe = usage_probe or CodexUsageProbe(
            codex_binary=config.codex_binary,
            auth_json=config.auth_json,
            codex_config=config.codex_config,
            proxy_url=config.proxy_url,
            timeout_seconds=config.usage_probe_timeout_seconds,
        )
        self._batch_runner: Runner = runner or run_swebench_pro_instance
        self._legacy_runner: Runner = runner or run_minimal_swebench_pro
        self._source = source or GitSource(
            cache_root=config.run_root / "cache" / "powercontext-git",
            runner=ProcessRunner(),
        )
        self._catalog = catalog
        self._worker_id = worker_id or f"worker-{uuid4().hex}"
        self._clock = clock or (lambda: datetime.now(UTC))
        self._coordinator = coordinator or ClaimCoordinator(
            config, store, usage_probe=self._usage_probe, clock=self._clock
        )
        self._stop = threading.Event()
        self._sleep = sleep or self._stop.wait
        self._thread_factory = thread_factory

    def stop(self) -> None:
        """Request shutdown after the active evaluation returns."""
        self._coordinator.stop()
        self._stop.set()

    def run_once(self) -> bool:
        """Run the next task, returning whether one was claimed."""
        task = self._coordinator.claim(self._worker_id)
        if task is None:
            return False
        return self._run_claimed(task)

    def _run_claimed(self, task: TaskRecord) -> bool:
        ownership_lost = threading.Event()
        heartbeat_stop = threading.Event()
        heartbeat: ThreadLike | None = None
        heartbeat_started = False
        phase: TaskPhase | None = None
        attempt_finished = False
        try:
            heartbeat = self._thread_factory(
                target=self._heartbeat,
                daemon=True,
                name=f"evaluation-heartbeat-{task.task_id}",
                args=(task.task_id, heartbeat_stop, ownership_lost),
            )
            heartbeat.start()
            heartbeat_started = True
            run_id = _execution_run_id(task)
            layout = EvaluationPaths(self._config.run_root, run_id)
            work_dir = self._config.run_root / "work" / run_id
            if os.path.lexists(layout.run_artifacts) or os.path.lexists(work_dir):
                attempt_finished = self._fail(
                    task,
                    SafeFailure(
                        category=FailureCategory.REPORT_GENERATION,
                        failure_code=FailureCode.ARTIFACTS_ALREADY_EXIST,
                        summary="Evaluation artifacts already exist; refusing to overwrite them.",
                        retry_disposition=RetryDisposition.TERMINAL,
                    ),
                    ownership_lost,
                )
                return True

            def on_phase(run_phase: RunPhase) -> None:
                nonlocal phase
                mapped = TaskPhase(run_phase.value)
                try:
                    self._store.set_phase(task.task_id, self._worker_id, mapped, now=self._clock())
                except (TaskOwnershipError, TaskConflict):
                    ownership_lost.set()
                    return
                phase = mapped

            result = self._invoke_runner(task, on_phase, ownership_lost)
            if ownership_lost.is_set():
                return True
            task_result = self._validated_result(task, result)
            load_report(layout.run_artifacts, self._config.run_root / "runs")
            self._store.succeed(task.task_id, self._worker_id, task_result, now=self._clock())
            attempt_finished = True
        except (TaskOwnershipError, TaskConflict):
            ownership_lost.set()
        except Exception as error:  # noqa: BLE001 - the worker boundary must sanitize every runner failure
            command_kind, returncode = _safe_command_failure_fields(error)
            _LOGGER.error(
                "Evaluation task execution failed "
                "(task_id=%s attempt_number=%s phase=%s error_type=%s command_kind=%s returncode=%s)",
                task.task_id,
                task.attempt_number,
                phase.value if phase is not None else "none",
                type(error).__name__,
                command_kind,
                returncode,
            )
            failure = _safe_failure(error, phase)
            attempt_finished = self._fail(task, failure, ownership_lost)
        finally:
            heartbeat_stop.set()
            if heartbeat_started and heartbeat is not None:
                heartbeat.join()
            if attempt_finished and task.batch_id is not None and not ownership_lost.is_set():
                self._finalize_batch(task.batch_id)
        return True

    def run_forever(self, stop: threading.Event | None = None) -> None:
        """Poll until stopped; process startup exclusively owns predecessor recovery."""
        stop_event = stop or self._stop
        while not stop_event.is_set():
            if not self.run_once():
                self._sleep(self._config.poll_seconds)

    def _invoke_runner(
        self,
        task: TaskRecord,
        on_phase: Callable[[RunPhase], None],
        cancel_event: threading.Event,
    ) -> MinimalRunResult:
        if task.batch_id is None:
            return self._legacy_runner(self._legacy_run_config(task, cancel_event), on_phase=on_phase)
        if task.instance_id is None:
            raise DatasetSchemaError("Batch child is missing an instance ID")
        catalog = self._catalog
        if catalog is None:
            catalog = SweBenchProCatalog.load(self._config.dataset_path)
            self._catalog = catalog
        return self._batch_runner(
            self._batch_run_config(task, cancel_event),
            instance=catalog.require(task.instance_id),
            on_phase=on_phase,
        )

    def _batch_run_config(self, task: TaskRecord, cancel_event: threading.Event | None = None) -> RunConfig:
        if task.batch_id is None:
            raise ValueError("Batch run configuration requires a batch child")
        powercontext_ref = self._pinned_batch_ref(task.batch_id)
        return RunConfig(
            root=self._config.run_root,
            powercontext_source=self._config.powercontext_source,
            powercontext_ref=powercontext_ref,
            harness_root=self._config.harness_root,
            harness_python=self._config.harness_python,
            codex_binary=self._config.codex_binary,
            tokensflow_enabled=self._config.tokensflow_enabled,
            tokensflow_binary=self._config.tokensflow_binary,
            tokensflow_user_home=self._config.tokensflow_user_home,
            tokensflow_egress_network=self._config.tokensflow_egress_network,
            uv_binary=self._config.uv_binary,
            registry_binary=self._config.registry_binary,
            auth_json=self._config.auth_json,
            codex_config=self._config.codex_config,
            proxy_url=self._config.proxy_url,
            docker_network_pool=self._config.docker_network_pool,
            extra_no_proxy_hosts=self._config.extra_no_proxy_hosts,
            run_id=_execution_run_id(task),
            model=task.request.model,
            reasoning_effort=task.request.reasoning_effort,
            finalization_registrar=self._finalization_registrar(task),
            container_env=dict(task.request.container_env),
            cancel_event=cancel_event,
        )

    def _legacy_run_config(self, task: TaskRecord, cancel_event: threading.Event | None = None) -> MinimalRunConfig:
        return MinimalRunConfig(
            root=self._config.run_root,
            powercontext_source=self._config.powercontext_source,
            powercontext_ref=task.request.powercontext_ref,
            harness_root=self._config.harness_root,
            harness_python=self._config.harness_python,
            raw_sample_path=self._config.raw_sample_path,
            codex_binary=self._config.codex_binary,
            tokensflow_enabled=self._config.tokensflow_enabled,
            tokensflow_binary=self._config.tokensflow_binary,
            tokensflow_user_home=self._config.tokensflow_user_home,
            tokensflow_egress_network=self._config.tokensflow_egress_network,
            uv_binary=self._config.uv_binary,
            registry_binary=self._config.registry_binary,
            auth_json=self._config.auth_json,
            codex_config=self._config.codex_config,
            proxy_url=self._config.proxy_url,
            docker_network_pool=self._config.docker_network_pool,
            extra_no_proxy_hosts=self._config.extra_no_proxy_hosts,
            run_id=_execution_run_id(task),
            model=task.request.model,
            reasoning_effort=task.request.reasoning_effort,
            finalization_registrar=self._finalization_registrar(task),
            cancel_event=cancel_event,
        )

    def _finalization_registrar(self, task: TaskRecord) -> TokensFlowFinalizationRegistrar | None:
        if not self._config.tokensflow_enabled:
            return None
        attempt_id = task.attempt_id
        if attempt_id is None:
            raise ValueError("TokensFlow finalization requires an attempt ID")

        def register(descriptor: TokensFlowFinalizationDescriptor) -> None:
            try:
                runtime_path = descriptor.runtime.relative_to(self._config.run_root)
                wrapper_path = descriptor.wrapper.relative_to(self._config.run_root)
            except ValueError:
                raise ValueError("TokensFlow finalization paths escaped the run root") from None
            self._store.register_tokensflow_finalization(
                TokensFlowFinalizationCreate(
                    attempt_id=attempt_id,
                    task_id=task.task_id,
                    batch_id=task.batch_id,
                    arm=descriptor.arm.value,
                    run_id=descriptor.run_id,
                    container_name=descriptor.container_name,
                    runtime_path=os.fspath(runtime_path),
                    wrapper_path=os.fspath(wrapper_path),
                    egress_network=descriptor.egress_network,
                    daemon_pid_file=descriptor.daemon_pid_file,
                    evidence_sha256=descriptor.evidence_sha256,
                    evidence_bytes=descriptor.evidence_bytes,
                ),
                now=self._clock(),
                timeout_seconds=self._config.tokensflow_finalizer_timeout_seconds,
            )

        return register

    def _pinned_batch_ref(self, batch_id: str) -> str:
        batch = self._store.get_batch(batch_id)
        if batch.resolved_powercontext_sha is not None:
            return f"commit:{batch.resolved_powercontext_sha}"
        requested = PowerContextRef.parse(batch.request.powercontext_ref)
        if requested.kind == "commit":
            assert requested.value is not None
            sha = requested.value.lower()
        else:
            resolved = self._source.resolve(self._config.powercontext_source, requested)
            sha = getattr(resolved, "sha", None)
            if not isinstance(sha, str):
                raise GitSourceError("PowerContext source resolver returned no immutable SHA")
        try:
            pinned = self._store.pin_batch_revision(batch_id, sha)
        except TaskConflict as error:
            raise GitSourceError("PowerContext batch revision conflicted with its persisted pin") from error
        assert pinned.resolved_powercontext_sha is not None
        return f"commit:{pinned.resolved_powercontext_sha}"

    def _heartbeat(
        self,
        task_id: str,
        stop: threading.Event,
        ownership_lost: threading.Event,
    ) -> None:
        interval = self._config.lease_seconds / 3
        while not stop.wait(interval):
            try:
                self._store.heartbeat(task_id, self._worker_id, now=self._clock())
            except (TaskOwnershipError, TaskConflict) as error:
                _LOGGER.warning(
                    "Evaluation heartbeat lost ownership (error_type=%s)",
                    type(error).__name__,
                )
                ownership_lost.set()
                return
            except sqlite3.OperationalError as error:
                _LOGGER.warning(
                    "Evaluation heartbeat database operation failed transiently; retrying (error_type=%s)",
                    type(error).__name__,
                )
            except Exception as error:  # noqa: BLE001 - unexpected renewal failures must fail closed
                _LOGGER.warning(
                    "Evaluation heartbeat failed closed (error_type=%s)",
                    type(error).__name__,
                )
                ownership_lost.set()
                return

    def _validated_result(self, task: TaskRecord, result: MinimalRunResult) -> TaskResult:
        run_id = _execution_run_id(task)
        layout = EvaluationPaths(self._config.run_root, run_id)
        if result.run_id != run_id:
            raise InvalidReportBundle("Runner returned a mismatched run ID")
        expected_report = layout.run_artifacts / "report.md"
        try:
            if result.report_path != expected_report:
                raise InvalidReportBundle("Runner returned an unexpected report path")
            metadata = expected_report.lstat()
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise InvalidReportBundle("Runner report is not a regular file")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(expected_report, flags)
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise InvalidReportBundle("Runner report changed during validation")
            finally:
                os.close(descriptor)
        except (FileNotFoundError, OSError, RuntimeError, ValueError, InvalidReportBundle):
            raise InvalidReportBundle("Runner returned an unsafe report path") from None
        return TaskResult(
            artifact_dir=os.fspath(layout.run_artifacts.relative_to(self._config.run_root)),
            report_path=os.fspath(expected_report.relative_to(self._config.run_root)),
            off_resolved=result.off_resolved,
            on_resolved=result.on_resolved,
        )

    def _fail(self, task: TaskRecord, failure: SafeFailure, ownership_lost: threading.Event) -> bool:
        if ownership_lost.is_set():
            return False
        try:
            self._store.fail(task.task_id, self._worker_id, failure, now=self._clock())
        except (TaskOwnershipError, TaskConflict):
            ownership_lost.set()
            return False
        return True

    def _finalize_batch(self, batch_id: str) -> None:
        self._coordinator.refresh_after_attempt(batch_id)


class EvaluationWorker:
    """Own one Worker process and supervise configured task-pair slots."""

    def __init__(
        self,
        config: WebConfig,
        store: TaskStore,
        *,
        usage_probe: UsageProbe | None = None,
        runner: Runner | None = None,
        source: SourceResolver | None = None,
        catalog: Catalog | None = None,
        worker_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        thread_factory: ThreadFactory = threading.Thread,
        finalizer: FinalizerSupervisor | None = None,
        workspace_reclaimer: WorkspaceReclaimerSupervisor | None = None,
        attempt_lifecycle: AttemptLifecycleSupervisor | None = None,
        usage_refresher: UsageRefreshSupervisor | None = None,
        resource_probe: ResourceProbe | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._stop = threading.Event()
        shared_probe = usage_probe or CodexUsageProbe(
            codex_binary=config.codex_binary,
            auth_json=config.auth_json,
            codex_config=config.codex_config,
            proxy_url=config.proxy_url,
            timeout_seconds=config.usage_probe_timeout_seconds,
        )
        coordinator = ClaimCoordinator(
            config,
            store,
            usage_probe=shared_probe,
            clock=self._clock,
            resource_probe=resource_probe,
            deployment_gate=store.deployment_admission_open,
        )
        self._coordinator = coordinator
        self._finalizer: FinalizerSupervisor | None = None
        if config.tokensflow_enabled:
            self._finalizer = finalizer or TokensFlowFinalizer(
                store,
                DockerFinalizationRuntime(config.run_root, cancel_event=self._stop),
                clock=self._clock,
                task_parallelism=config.task_parallelism,
            )
        self._workspace_reclaimer = workspace_reclaimer or default_workspace_reclaimer(config, store)
        self._attempt_lifecycle = attempt_lifecycle or AttemptLifecycleCleaner(
            store,
            config.run_root,
            clock=self._clock,
        )
        self._usage_refresher = usage_refresher or PeriodicUsageRefresher(coordinator)
        base_worker_id = worker_id or f"worker-{uuid4().hex}"

        def default_sleep(seconds: float) -> None:
            self._stop.wait(seconds)

        self._slots = tuple(
            TaskPairWorker(
                config,
                store,
                coordinator=coordinator,
                usage_probe=shared_probe,
                runner=runner,
                source=source,
                catalog=catalog,
                worker_id=(base_worker_id if config.task_parallelism == 1 else f"{base_worker_id}-slot-{index + 1}"),
                clock=self._clock,
                sleep=sleep or default_sleep,
                thread_factory=thread_factory,
            )
            for index in range(config.task_parallelism)
        )

    def stop(self) -> None:
        """Stop claiming replacements after active task pairs finish."""

        self._coordinator.stop()
        self._stop.set()

    def run_once(self) -> bool:
        """Run one slot once while retaining the process-owner compatibility boundary."""

        with _nonblocking_worker_lock(self._config.database_path) as locked:
            if not locked:
                return False
            startup_now = self._clock()
            self._publish_runtime_revision(now=startup_now)
            recovered = self._store.begin_startup_recovery(now=startup_now)
            if recovered:
                self._attempt_lifecycle.run_once()
            ran = self._slots[0].run_once()
            self._attempt_lifecycle.run_once()
            return ran

    def run_forever(self) -> None:
        """Own the process lock and supervise all configured task-pair slots."""

        with _nonblocking_worker_lock(self._config.database_path) as locked:
            if not locked:
                return
            startup_now = self._clock()
            self._publish_runtime_revision(now=startup_now)
            self._store.begin_startup_recovery(now=startup_now)
            self._attempt_lifecycle.run_once()
            self._store.record_worker_capacity(self._config.task_parallelism, now=self._clock())
            failures: list[BaseException] = []
            failures_lock = threading.Lock()

            def run_slot(slot: TaskPairWorker) -> None:
                try:
                    slot.run_forever(self._stop)
                except Exception as error:  # noqa: BLE001 - a slot failure must stop the whole supervisor
                    with failures_lock:
                        failures.append(error)
                    self.stop()

            def run_usage_refresher() -> None:
                try:
                    self._usage_refresher.run_forever(self._stop, self._config.usage_probe_seconds)
                except Exception as error:  # noqa: BLE001 - usage gating must fail closed on supervisor defects
                    _LOGGER.warning(
                        "Account usage refresher stopped unexpectedly (error_type=%s)",
                        type(error).__name__,
                    )
                    self.stop()

            threads = tuple(
                threading.Thread(
                    target=run_slot,
                    args=(slot,),
                    daemon=False,
                    name=f"evaluation-slot-{index + 1}",
                )
                for index, slot in enumerate(self._slots)
            )
            started: list[threading.Thread] = []
            usage_refresh_thread: threading.Thread | None = None
            finalizer_thread: threading.Thread | None = None
            workspace_reclaimer_thread: threading.Thread | None = None
            attempt_lifecycle_thread: threading.Thread | None = None
            try:
                attempt_lifecycle_thread = threading.Thread(
                    target=self._attempt_lifecycle.run_forever,
                    args=(self._stop,),
                    daemon=False,
                    name="attempt-lifecycle-cleaner",
                )
                attempt_lifecycle_thread.start()
                for thread in threads:
                    thread.start()
                    started.append(thread)
            except Exception:  # noqa: BLE001 - partial startup must stop and join every started slot
                self.stop()
                for thread in started:
                    thread.join()
                if attempt_lifecycle_thread is not None:
                    attempt_lifecycle_thread.join()
                raise RuntimeError("Evaluation worker slot failed") from None
            try:
                usage_refresh_thread = threading.Thread(
                    target=run_usage_refresher,
                    daemon=False,
                    name="account-usage-refresher",
                )
                usage_refresh_thread.start()
            except Exception as error:  # noqa: BLE001 - claims remain fail closed if refreshing cannot start
                usage_refresh_thread = None
                _LOGGER.warning(
                    "Account usage refresher failed to start (error_type=%s)",
                    type(error).__name__,
                )
                self.stop()
            if self._finalizer is not None:
                try:
                    finalizer_thread = threading.Thread(
                        target=self._finalizer.run_forever,
                        args=(self._stop, self._config.tokensflow_finalizer_poll_seconds),
                        daemon=False,
                        name="tokensflow-finalizer",
                    )
                    finalizer_thread.start()
                except Exception as error:  # noqa: BLE001 - durable rows remain recoverable while evaluation continues
                    finalizer_thread = None
                    _LOGGER.warning(
                        "TokensFlow finalizer supervisor failed to start (error_type=%s)",
                        type(error).__name__,
                    )
            try:
                workspace_reclaimer_thread = threading.Thread(
                    target=self._workspace_reclaimer.run_forever,
                    args=(self._stop,),
                    daemon=False,
                    name="succeeded-workspace-reclaimer",
                )
                workspace_reclaimer_thread.start()
            except Exception as error:  # noqa: BLE001 - capacity gate remains fail closed if maintenance cannot start
                workspace_reclaimer_thread = None
                _LOGGER.warning(
                    "Evaluation workspace reclaimer failed to start (error_type=%s)",
                    type(error).__name__,
                )
            for thread in started:
                thread.join()
            if usage_refresh_thread is not None:
                usage_refresh_thread.join()
            if finalizer_thread is not None:
                finalizer_thread.join()
            if workspace_reclaimer_thread is not None:
                workspace_reclaimer_thread.join()
            if attempt_lifecycle_thread is not None:
                attempt_lifecycle_thread.join()
            if failures:
                raise RuntimeError("Evaluation worker slot failed") from None

    def _publish_runtime_revision(self, *, now: datetime) -> None:
        self._store.record_runtime_revision(
            "worker",
            build_revision=current_build_revision(),
            schema_version=RUNTIME_SCHEMA_VERSION,
            now=now,
        )


def _execution_run_id(task: TaskRecord) -> str:
    if task.attempt_number == 1:
        return task.task_id
    if task.attempt_id is None:
        raise ValueError("Retried task is missing an attempt ID")
    expected_attempt_id = f"{task.task_id}.attempt-{task.attempt_number:04d}"
    if task.attempt_id != expected_attempt_id:
        raise ValueError("Retried task has an invalid attempt ID")
    return f"{task.task_id}-attempt-{task.attempt_number:04d}"


@contextmanager
def _nonblocking_worker_lock(database_path: Path) -> Iterator[bool]:
    lock_path = database_path.with_name(f"{database_path.name}.worker.lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    locked = False
    try:
        metadata = lock_path.lstat()
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise RuntimeError("Evaluation worker lock is unsafe")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            try:
                os.fchmod(descriptor, 0o600)
            except OSError:
                raise RuntimeError("Evaluation worker lock is unsafe") from None
        metadata = lock_path.lstat()
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise RuntimeError("Evaluation worker lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        locked = True
        yield True
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _safe_failure(error: Exception, phase: TaskPhase | None) -> SafeFailure:
    if isinstance(error, CommandCancelled):
        return SafeFailure(
            category=FailureCategory.WORKER_INTERRUPTION,
            failure_code=FailureCode.WORKER_INTERRUPTION,
            phase=phase,
            summary="Evaluation execution was interrupted after worker ownership changed.",
        )
    if isinstance(error, CodexCapacityError):
        return SafeFailure(
            category=FailureCategory.CODEX_CAPACITY,
            failure_code=FailureCode.CODEX_CAPACITY,
            phase=phase,
            summary="The upstream Codex model was at capacity.",
        )
    fixed: tuple[FailureCategory, FailureCode, str, RetryDisposition]
    if isinstance(error, GitSourceError):
        fixed = (
            FailureCategory.SOURCE_RESOLUTION,
            FailureCode.SOURCE_RESOLUTION,
            "PowerContext source resolution failed.",
            RetryDisposition.RETRY,
        )
    elif isinstance(error, CatalogError):
        fixed = (
            FailureCategory.ENVIRONMENT_PREPARATION,
            FailureCode.CATALOG,
            "Evaluation benchmark catalog is invalid.",
            RetryDisposition.TERMINAL,
        )
    elif isinstance(error, DatasetSchemaError):
        fixed = (
            FailureCategory.ENVIRONMENT_PREPARATION,
            FailureCode.DATASET_SCHEMA,
            "Evaluation dataset schema is invalid.",
            RetryDisposition.TERMINAL,
        )
    elif isinstance(error, UnsafeSutConfiguration):
        fixed = (
            FailureCategory.ENVIRONMENT_PREPARATION,
            FailureCode.UNSAFE_SUT_CONFIGURATION,
            "Evaluation SUT configuration is unsafe.",
            RetryDisposition.TERMINAL,
        )
    elif isinstance(error, GoldCheckFailed):
        fixed = (
            FailureCategory.GOLD_VALIDATION,
            FailureCode.GOLD_VALIDATION,
            "Gold patch validation failed.",
            RetryDisposition.RETRY,
        )
    elif isinstance(error, CodexInfrastructureError):
        fixed = (
            FailureCategory.CODEX_EXECUTION,
            FailureCode.CODEX_EXECUTION,
            "Codex execution infrastructure failed.",
            RetryDisposition.RETRY,
        )
    elif isinstance(error, UnsafeCodexInvocation):
        fixed = (
            FailureCategory.CODEX_EXECUTION,
            FailureCode.UNSAFE_CODEX_INVOCATION,
            "Codex invocation was unsafe.",
            RetryDisposition.TERMINAL,
        )
    elif isinstance(error, BinaryPatchError):
        fixed = (
            FailureCategory.CODEX_EXECUTION,
            FailureCode.CODEX_EXECUTION,
            "Codex patch did not satisfy the evaluation contract.",
            RetryDisposition.RETRY,
        )
    elif isinstance(error, ReadinessFailure):
        fixed = (
            FailureCategory.TREATMENT_VALIDATION,
            FailureCode.READINESS,
            error.safe_summary,
            RetryDisposition.RETRY,
        )
    elif isinstance(error, PluginInspectionFailure):
        fixed = (
            FailureCategory.TREATMENT_VALIDATION,
            FailureCode.PLUGIN_INSPECTION,
            error.safe_summary,
            RetryDisposition.RETRY,
        )
    elif isinstance(error, InvalidTreatment):
        fixed = (
            FailureCategory.TREATMENT_VALIDATION,
            FailureCode.INVALID_TREATMENT_CONTRACT,
            _TREATMENT_FAILURE_SUMMARIES.get(str(error), "Treatment validation failed."),
            RetryDisposition.TERMINAL,
        )
    elif isinstance(error, OfficialResultError):
        fixed = (
            FailureCategory.OFFICIAL_EVALUATOR,
            FailureCode.OFFICIAL_EVALUATOR,
            "Official evaluation failed.",
            RetryDisposition.RETRY,
        )
    elif isinstance(error, (ReportingError, InvalidReportBundle, ArtifactError)):
        fixed = (
            FailureCategory.REPORT_GENERATION,
            FailureCode.REPORT_GENERATION,
            _REPORT_SUMMARY,
            RetryDisposition.RETRY,
        )
    elif isinstance(error, (CommandError, PowerContextEvalError)):
        category, summary = _phase_failure(phase)
        fixed = category, _phase_failure_code(phase), summary, RetryDisposition.RETRY
    else:
        fixed = FailureCategory.INTERNAL, FailureCode.INTERNAL, _INTERNAL_SUMMARY, RetryDisposition.RETRY
    return SafeFailure(
        category=fixed[0],
        failure_code=fixed[1],
        phase=phase,
        summary=fixed[2],
        retry_disposition=fixed[3],
    )


def _safe_command_failure_fields(error: Exception) -> tuple[str, str]:
    """Return fixed-shape diagnostics without exposing arguments, paths, or output."""

    if not isinstance(error, CommandError):
        return "none", "none"
    argv = error.result.argv
    command_kind = "unknown"
    if argv:
        executable = Path(argv[0]).name
        if executable in {"docker", "git"}:
            operation = argv[1] if len(argv) > 1 and argv[1].isalpha() else "unknown"
            command_kind = f"{executable}.{operation}"
    return command_kind, str(error.result.returncode)


def _phase_failure(phase: TaskPhase | None) -> tuple[FailureCategory, str]:
    return {
        TaskPhase.PREPARING: (FailureCategory.ENVIRONMENT_PREPARATION, "Evaluation environment preparation failed."),
        TaskPhase.VALIDATING_GOLD: (FailureCategory.GOLD_VALIDATION, "Gold patch validation failed."),
        TaskPhase.RUNNING_OFF: (FailureCategory.CODEX_EXECUTION, "Codex execution failed."),
        TaskPhase.RUNNING_ON: (FailureCategory.CODEX_EXECUTION, "Codex execution failed."),
        TaskPhase.OFFICIAL_EVALUATION: (FailureCategory.OFFICIAL_EVALUATOR, "Official evaluation failed."),
        TaskPhase.GENERATING_REPORT: (FailureCategory.REPORT_GENERATION, _REPORT_SUMMARY),
        None: (FailureCategory.INTERNAL, _INTERNAL_SUMMARY),
    }[phase]


def _phase_failure_code(phase: TaskPhase | None) -> FailureCode:
    return {
        TaskPhase.PREPARING: FailureCode.INTERNAL,
        TaskPhase.VALIDATING_GOLD: FailureCode.GOLD_VALIDATION,
        TaskPhase.RUNNING_OFF: FailureCode.CODEX_EXECUTION,
        TaskPhase.RUNNING_ON: FailureCode.CODEX_EXECUTION,
        TaskPhase.OFFICIAL_EVALUATION: FailureCode.OFFICIAL_EVALUATOR,
        TaskPhase.GENERATING_REPORT: FailureCode.REPORT_GENERATION,
        None: FailureCode.INTERNAL,
    }[phase]
