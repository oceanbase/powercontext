from __future__ import annotations

import os
import stat
import tarfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from powercontext_eval.process import CommandResult
from powercontext_eval.web.batches import BatchCreate
from powercontext_eval.web.config import WebConfig
from powercontext_eval.web.models import FailureCategory, FailureCode, SafeFailure, TaskPhase, TaskResult, TaskStatus
from powercontext_eval.web.resources import (
    AttemptLifecycleCleaner,
    FilesystemResourceProbe,
    SucceededWorkspaceReclaimer,
)
from powercontext_eval.web.store import TaskStore, TokensFlowFinalizationCreate

NOW = datetime(2026, 8, 12, tzinfo=UTC)


def _store(root: Path) -> tuple[WebConfig, TaskStore]:
    config = WebConfig.for_root(root, tokensflow_egress_network="bridge", proxy_url="http://127.0.0.1:8081")
    store = TaskStore(config.database_path, lease_duration=timedelta(seconds=config.lease_seconds))
    store.initialize()
    return config, store


def _succeed(store: TaskStore, key: str) -> str:
    batch, _ = store.create_batch(
        BatchCreate(
            powercontext_ref="latest",
            benchmark="swebench-pro",
            task_set="swebench-pro-public-v2",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            treatment_mode="off_on",
            idempotency_key=f"resource-{key}",
        ),
        (f"instance_{key}",),
        now=NOW,
    )
    created = store.list_batch_tasks(batch.batch_id)[0]
    claimed = store.claim_next("resource-worker", now=NOW + timedelta(seconds=1))
    assert claimed is not None and claimed.task_id == created.task_id
    store.succeed(
        created.task_id,
        "resource-worker",
        TaskResult(
            artifact_dir=f"runs/{created.task_id}",
            report_path=f"runs/{created.task_id}/report.md",
            off_resolved=False,
            on_resolved=True,
        ),
        now=NOW + timedelta(seconds=2),
    )
    return created.task_id


def _fail(store: TaskStore, key: str) -> tuple[str, str]:
    batch, _ = store.create_batch(
        BatchCreate(
            powercontext_ref="latest",
            benchmark="swebench-pro",
            task_set="swebench-pro-public-v2",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            treatment_mode="off_on",
            idempotency_key=f"resource-failure-{key}",
        ),
        (f"instance_{key}",),
        now=NOW,
    )
    task = store.list_batch_tasks(batch.batch_id)[0]
    claimed = store.claim_next("resource-worker", now=NOW + timedelta(seconds=1))
    assert claimed is not None and claimed.task_id == task.task_id
    failed = store.fail(
        task.task_id,
        "resource-worker",
        SafeFailure(
            category=FailureCategory.CODEX_EXECUTION,
            failure_code=FailureCode.CODEX_EXECUTION,
            phase=TaskPhase.RUNNING_OFF,
            summary="Codex execution infrastructure failed.",
        ),
        now=NOW + timedelta(seconds=2),
    )
    assert failed.attempt_id is not None
    return batch.batch_id, task.task_id


class EmptyDockerRunner:
    def __init__(self, *, fail_network_inventory: bool = False) -> None:
        self.fail_network_inventory = fail_network_inventory
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout: float | None = None,
        check: bool = True,
    ) -> CommandResult:
        del timeout, check
        self.calls.append(argv)
        returncode = 70 if self.fail_network_inventory and argv[1:3] == ("network", "ls") else 0
        return CommandResult(argv=argv, cwd=os.fspath(cwd), returncode=returncode, stdout="", stderr="")


def test_filesystem_probe_uses_available_blocks_and_inodes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed_paths: list[Path] = []

    def statvfs(path: Path) -> object:
        observed_paths.append(path)
        return SimpleNamespace(f_frsize=4096, f_bsize=1024, f_bavail=7, f_blocks=11, f_favail=13, f_files=17)

    monkeypatch.setattr(os, "statvfs", statvfs)
    capacity = FilesystemResourceProbe(tmp_path / "not-created" / "work").read()

    assert observed_paths == [tmp_path]
    assert capacity.free_bytes == 7 * 4096
    assert capacity.total_bytes == 11 * 4096
    assert capacity.free_inodes == 13
    assert capacity.total_inodes == 17


def test_reclaimer_removes_only_verified_success_workspace_and_retains_runs(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    task_id = _succeed(store, "success")
    workspace = config.run_root / "work" / task_id
    retained = config.run_root / "runs" / task_id
    workspace.mkdir(parents=True)
    (workspace / "many-small-files").write_text("scratch")
    retained.mkdir(parents=True)
    (retained / "report.json").write_text("retained")
    validations: list[tuple[Path, Path]] = []

    reclaimer = SucceededWorkspaceReclaimer(
        store,
        config.run_root,
        interval_seconds=1,
        artifact_validator=lambda run, root: validations.append((run, root)),
    )

    assert reclaimer.run_once() == 1
    assert not workspace.exists()
    assert (retained / "report.json").read_text() == "retained"
    assert validations == [(retained, config.run_root / "runs")]
    assert reclaimer.run_once() == 0


def test_open_finalization_blocks_workspace_reclaim(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    task_id = _succeed(store, "finalizing")
    task = store.get(task_id)
    assert task.attempt_id is not None
    store.register_tokensflow_finalization(
        TokensFlowFinalizationCreate(
            attempt_id=task.attempt_id,
            task_id=task_id,
            batch_id=task.batch_id,
            arm="off",
            run_id=task_id,
            container_name=f"powercontext-eval-{task_id}-off",
            runtime_path=f"work/{task_id}/off/runtime",
            wrapper_path=f"work/{task_id}/off/wrapper",
            egress_network="bridge",
            daemon_pid_file="/runtime/tokensflow.pid",
            evidence_sha256="0" * 64,
            evidence_bytes=1,
        ),
        now=NOW + timedelta(seconds=3),
        timeout_seconds=600,
    )
    workspace = config.run_root / "work" / task_id
    retained = config.run_root / "runs" / task_id
    workspace.mkdir(parents=True)
    retained.mkdir(parents=True)

    reclaimer = SucceededWorkspaceReclaimer(
        store,
        config.run_root,
        interval_seconds=1,
        artifact_validator=lambda *_: None,
    )

    assert reclaimer.run_once() == 0
    assert workspace.is_dir()


def test_reclaimer_retains_symlink_candidate_and_never_touches_target(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    task_id = _succeed(store, "symlink")
    target = tmp_path / "outside"
    target.mkdir()
    (target / "keep").write_text("evidence")
    work = config.run_root / "work"
    work.mkdir()
    (work / task_id).symlink_to(target, target_is_directory=True)
    (config.run_root / "runs" / task_id).mkdir(parents=True)

    reclaimer = SucceededWorkspaceReclaimer(
        store,
        config.run_root,
        interval_seconds=1,
        artifact_validator=lambda *_: None,
    )

    assert reclaimer.run_once() == 0
    assert (work / task_id).is_symlink()
    assert (target / "keep").read_text() == "evidence"


def test_failed_task_workspace_is_not_a_reclaim_candidate(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    batch, _ = store.create_batch(
        BatchCreate(
            powercontext_ref="latest",
            benchmark="swebench-pro",
            task_set="swebench-pro-public-v2",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            treatment_mode="off_on",
            idempotency_key="resource-failed",
        ),
        ("instance_failed",),
        now=NOW,
    )
    created = store.list_batch_tasks(batch.batch_id)[0]
    workspace = config.run_root / "work" / created.task_id
    workspace.mkdir(parents=True)

    assert store.list_succeeded_tasks_for_workspace_reclaim(limit=10) == []
    assert (
        SucceededWorkspaceReclaimer(
            store,
            config.run_root,
            interval_seconds=1,
            artifact_validator=lambda *_: None,
        ).run_once()
        == 0
    )
    assert workspace.is_dir()
    assert store.get(created.task_id).status is TaskStatus.QUEUED


def test_reclaimer_pages_past_old_successes_with_absent_workspaces(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    task_ids = [_succeed(store, f"page-{index}") for index in range(3)]
    retained_task = task_ids[-1]
    retained_workspace = config.run_root / "work" / retained_task
    retained_workspace.mkdir(parents=True)
    (config.run_root / "runs" / retained_task).mkdir(parents=True)
    reclaimer = SucceededWorkspaceReclaimer(
        store,
        config.run_root,
        interval_seconds=1,
        batch_size=2,
        artifact_validator=lambda *_: None,
    )

    assert reclaimer.run_once() == 0
    assert retained_workspace.is_dir()
    assert reclaimer.run_once() == 1
    assert not retained_workspace.exists()


def test_reclaimer_default_scan_skips_absent_history_but_deletes_only_one_workspace(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    task_ids = [_succeed(store, f"default-scan-{index}") for index in range(5)]
    first_workspace = config.run_root / "work" / task_ids[-2]
    second_workspace = config.run_root / "work" / task_ids[-1]
    for task_id, workspace in ((task_ids[-2], first_workspace), (task_ids[-1], second_workspace)):
        workspace.mkdir(parents=True)
        (config.run_root / "runs" / task_id).mkdir(parents=True)

    reclaimer = SucceededWorkspaceReclaimer(
        store,
        config.run_root,
        interval_seconds=1,
        artifact_validator=lambda *_: None,
    )

    assert reclaimer.run_once() == 1
    assert not first_workspace.exists()
    assert second_workspace.is_dir()
    assert reclaimer.run_once() == 1
    assert not second_workspace.exists()


def test_reclaimer_waits_after_each_successful_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, store = _store(tmp_path)
    reclaimer = SucceededWorkspaceReclaimer(store, config.run_root, interval_seconds=7)
    runs = 0

    def run_once() -> int:
        nonlocal runs
        runs += 1
        return 1

    class StopAfterWait:
        def __init__(self) -> None:
            self.stopped = False
            self.waits: list[float] = []

        def is_set(self) -> bool:
            return self.stopped

        def wait(self, seconds: float) -> None:
            self.waits.append(seconds)
            self.stopped = True

    stop = StopAfterWait()
    monkeypatch.setattr(reclaimer, "run_once", run_once)

    reclaimer.run_forever(stop)  # type: ignore[arg-type]

    assert runs == 1
    assert stop.waits == [7]


def test_attempt_cleaner_exports_private_spool_then_reclaims_and_schedules_retry(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    batch_id, task_id = _fail(store, "settled")
    workspace = config.run_root / "work" / task_id
    tokensflow_home = workspace / "off" / "runtime" / "tokensflow-home"
    tokensflow_home.mkdir(parents=True)
    (tokensflow_home / "queue.db").write_bytes(b"private diagnostic state")
    runner = EmptyDockerRunner()

    cleaner = AttemptLifecycleCleaner(store, config.run_root, runner=runner, clock=lambda: NOW + timedelta(seconds=3))

    assert cleaner.run_once() == 1
    assert not workspace.exists()
    public = config.run_root / "runs" / task_id / "incident" / "manifest.json"
    private = config.run_root / "private-incidents" / task_id / "tokensflow-spool.tar.gz"
    assert public.is_file()
    assert private.is_file()
    assert stat.S_IMODE(private.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    with tarfile.open(private, mode="r:gz") as archive:
        member = archive.extractfile("off/runtime/tokensflow-home/queue.db")
        assert member is not None
        assert member.read() == b"private diagnostic state"
    attempts = store.list_task_attempts(batch_id, task_id)
    assert [attempt.status for attempt in attempts] == [TaskStatus.FAILED, TaskStatus.QUEUED]
    assert attempts[1].eligible_at == NOW + timedelta(seconds=33)
    assert any(call[1:3] == ("network", "ls") for call in runner.calls)


def test_attempt_cleaner_does_not_require_chmod_follow_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, store = _store(tmp_path)
    batch_id, task_id = _fail(store, "no-chmod-follow-symlinks")
    workspace = config.run_root / "work" / task_id
    tokensflow_home = workspace / "off" / "runtime" / "tokensflow-home"
    tokensflow_home.mkdir(parents=True)
    (tokensflow_home / "queue.db").write_bytes(b"private diagnostic state")

    def unsupported_chmod(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise NotImplementedError("chmod: follow_symlinks unavailable on this platform")

    monkeypatch.setattr(os, "chmod", unsupported_chmod)
    cleaner = AttemptLifecycleCleaner(
        store,
        config.run_root,
        runner=EmptyDockerRunner(),
        clock=lambda: NOW + timedelta(seconds=3),
    )

    assert cleaner.run_once() == 1
    private = config.run_root / "private-incidents" / task_id / "tokensflow-spool.tar.gz"
    assert stat.S_IMODE(private.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert [attempt.status for attempt in store.list_task_attempts(batch_id, task_id)] == [
        TaskStatus.FAILED,
        TaskStatus.QUEUED,
    ]


def test_attempt_cleaner_retries_cleanup_without_creating_an_early_attempt(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    batch_id, task_id = _fail(store, "deferred")
    workspace = config.run_root / "work" / task_id
    workspace.mkdir(parents=True)
    failing_runner = EmptyDockerRunner(fail_network_inventory=True)
    cleaner = AttemptLifecycleCleaner(
        store,
        config.run_root,
        runner=failing_runner,
        clock=lambda: NOW + timedelta(seconds=3),
    )

    assert cleaner.run_once() == 0
    assert workspace.is_dir()
    assert [attempt.status for attempt in store.list_task_attempts(batch_id, task_id)] == [TaskStatus.FAILED]
    assert (config.run_root / "runs" / task_id / "incident" / "manifest.json").is_file()
    assert (config.run_root / "private-incidents" / task_id / "tokensflow-spool.tar.gz").is_file()

    recovered = AttemptLifecycleCleaner(
        store,
        config.run_root,
        runner=EmptyDockerRunner(),
        clock=lambda: NOW + timedelta(seconds=34),
    )
    assert recovered.run_once() == 1
    assert not workspace.exists()
    assert [attempt.status for attempt in store.list_task_attempts(batch_id, task_id)] == [
        TaskStatus.FAILED,
        TaskStatus.QUEUED,
    ]


def test_attempt_cleaner_poll_survives_a_store_wide_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, store = _store(tmp_path)
    cleaner = AttemptLifecycleCleaner(store, config.run_root, interval_seconds=1)
    calls = 0
    stop = threading.Event()

    def run_once() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("private database detail")
        stop.set()
        return 0

    monkeypatch.setattr(cleaner, "run_once", run_once)

    cleaner.run_forever(stop)

    assert calls == 2
