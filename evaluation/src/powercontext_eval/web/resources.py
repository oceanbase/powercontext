"""Filesystem admission and successful-workspace lifecycle management."""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import stat
import tarfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from powercontext_eval.artifacts import ArtifactStore
from powercontext_eval.errors import CommandError
from powercontext_eval.paths import EvaluationPaths
from powercontext_eval.process import CommandResult, ProcessRunner
from powercontext_eval.web.config import WebConfig
from powercontext_eval.web.models import TaskRecord
from powercontext_eval.web.reporting import load_report
from powercontext_eval.web.store import AttemptCleanupCandidate, TaskStore

_LOGGER = logging.getLogger(__name__)
_ATTEMPT_CLEANUP_RETRY_SECONDS = 30


def _chmod_nofollow(path: Path, mode: int, *, directory: bool) -> None:
    """Apply exact permissions through a no-follow file descriptor."""

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise OSError("Platform does not support no-follow incident permissions")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    if directory:
        flags |= os.O_DIRECTORY
    else:
        flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        expected_type = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
        if not expected_type:
            raise OSError("Private incident path has an unexpected type")
        os.fchmod(descriptor, mode)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != mode:
            raise OSError("Private incident permissions did not converge")
    finally:
        os.close(descriptor)


class ResourceUnavailable(RuntimeError):
    """The filesystem resource state cannot be observed safely."""


@dataclass(frozen=True)
class FilesystemCapacity:
    """One allowlisted statvfs snapshot used by claims and health reporting."""

    free_bytes: int
    total_bytes: int
    free_inodes: int
    total_inodes: int

    def admission_open(self, *, min_free_bytes: int, min_free_inodes: int) -> bool:
        """Return whether both configured hard reserves remain available."""

        return self.free_bytes >= min_free_bytes and self.free_inodes >= min_free_inodes


class ResourceProbe(Protocol):
    def read(self) -> FilesystemCapacity: ...


class DependencyProbe(Protocol):
    def check(self) -> None: ...


class CommandRunner(Protocol):
    def run(self, *args: Any, **kwargs: Any) -> CommandResult: ...


class DockerDependencyProbe:
    """Confirm the Docker daemon can serve new evaluations without mutating it."""

    def __init__(self, run_root: Path, *, runner: ProcessRunner | None = None) -> None:
        self._run_root = run_root
        self._runner = runner or ProcessRunner()

    def check(self) -> None:
        try:
            result = self._runner.run(
                ("docker", "info", "--format", "{{.ServerVersion}}"),
                cwd=FilesystemResourceProbe._nearest_existing_ancestor(self._run_root),
                timeout=10,
                check=False,
            )
        except (CommandError, OSError):
            raise ResourceUnavailable("Docker dependency is unavailable") from None
        if result.returncode != 0 or not result.stdout.strip():
            raise ResourceUnavailable("Docker dependency is unavailable")


class FilesystemResourceProbe:
    """Read capacity from the filesystem containing the configured run root."""

    def __init__(self, run_root: Path) -> None:
        self._run_root = run_root

    def read(self) -> FilesystemCapacity:
        target = self._nearest_existing_ancestor(self._run_root)
        try:
            observed = os.statvfs(target)
        except OSError:
            raise ResourceUnavailable("Evaluation filesystem capacity is unavailable") from None
        fragment_size = observed.f_frsize or observed.f_bsize
        values = (
            observed.f_bavail * fragment_size,
            observed.f_blocks * fragment_size,
            observed.f_favail,
            observed.f_files,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ResourceUnavailable("Evaluation filesystem capacity is invalid")
        return FilesystemCapacity(*values)

    @staticmethod
    def _nearest_existing_ancestor(path: Path) -> Path:
        candidate = path
        while not candidate.exists():
            parent = candidate.parent
            if parent == candidate:
                raise ResourceUnavailable("Evaluation filesystem path is unavailable")
            candidate = parent
        return candidate


class WorkspaceReclaimStore(Protocol):
    def list_succeeded_tasks_for_workspace_reclaim(self, *, limit: int, offset: int) -> list[TaskRecord]: ...


ArtifactValidator = Callable[[Path, Path], object]


class SucceededWorkspaceReclaimer:
    """Delete only reproducible scratch after durable success evidence is complete."""

    def __init__(
        self,
        store: WorkspaceReclaimStore,
        run_root: Path,
        *,
        interval_seconds: float,
        batch_size: int = 256,
        max_reclaims_per_cycle: int = 1,
        artifact_validator: ArtifactValidator = load_report,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Workspace reclaim interval must be positive")
        if batch_size < 1:
            raise ValueError("Workspace reclaim batch size must be positive")
        if max_reclaims_per_cycle < 1:
            raise ValueError("Workspace reclaim limit must be positive")
        self._store = store
        self._run_root = run_root
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._max_reclaims_per_cycle = max_reclaims_per_cycle
        self._artifact_validator = artifact_validator
        self._offset = 0

    def run_once(self) -> int:
        """Reclaim one bounded snapshot and retain every unsafe or failed candidate."""

        reclaimed = 0
        tasks = self._store.list_succeeded_tasks_for_workspace_reclaim(
            limit=self._batch_size,
            offset=self._offset,
        )
        processed = 0
        for task in tasks:
            processed += 1
            try:
                if self._reclaim_task(task):
                    reclaimed += 1
            except Exception as error:  # noqa: BLE001 - reclamation must fail closed without affecting task truth
                _LOGGER.warning(
                    "Evaluation workspace reclaim retained candidate (task_id=%s error_type=%s)",
                    task.task_id,
                    type(error).__name__,
                )
            if reclaimed >= self._max_reclaims_per_cycle:
                break
        self._offset = 0 if processed == len(tasks) and len(tasks) < self._batch_size else self._offset + processed
        return reclaimed

    def run_forever(self, stop: threading.Event) -> None:
        """Continuously reclaim bounded success snapshots until Worker shutdown."""

        while not stop.is_set():
            try:
                self.run_once()
            except Exception as error:  # noqa: BLE001 - a maintenance fault must not stop evaluation slots
                _LOGGER.warning(
                    "Evaluation workspace reclaimer poll failed (error_type=%s)",
                    type(error).__name__,
                )
            # Deleting Git workspaces can release millions of inodes and put sustained
            # metadata pressure on the same filesystem used by active evaluations.  A
            # maintenance success is therefore still rate limited; it must never turn
            # into a tight historical-cleanup loop beside the Worker slots.
            stop.wait(self._interval_seconds)

    def _reclaim_task(self, task: TaskRecord) -> bool:
        if task.result is None or task.attempt_id is None:
            raise ValueError("Succeeded workspace candidate is incomplete")
        run_id = task.task_id if task.attempt_number == 1 else f"{task.task_id}-attempt-{task.attempt_number:04d}"
        layout = EvaluationPaths(self._run_root, run_id)
        expected_artifact_dir = Path("runs") / run_id
        expected_report_path = expected_artifact_dir / "report.md"
        if (
            Path(task.result.artifact_dir) != expected_artifact_dir
            or Path(task.result.report_path) != expected_report_path
        ):
            raise ValueError("Succeeded workspace candidate has an unexpected artifact path")
        workspace = self._run_root / "work" / run_id
        try:
            metadata = workspace.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OSError("Succeeded workspace candidate is not a real directory")
        self._artifact_validator(layout.run_artifacts, self._run_root / "runs")
        self._remove_exact_workspace(run_id)
        return True

    def _remove_exact_workspace(self, run_id: str) -> None:
        work_root = self._run_root / "work"
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(work_root, flags)
        try:
            metadata = os.stat(run_id, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise OSError("Succeeded workspace candidate changed before reclaim")
            if not shutil.rmtree.avoids_symlink_attacks:
                raise OSError("Platform does not support symlink-safe workspace reclaim")
            shutil.rmtree(run_id, dir_fd=descriptor)
        finally:
            os.close(descriptor)


class AttemptLifecycleCleaner:
    """Export bounded failure metadata, then remove only exact attempt-owned resources."""

    def __init__(
        self,
        store: TaskStore,
        run_root: Path,
        *,
        runner: CommandRunner | None = None,
        clock: Callable[[], datetime] | None = None,
        interval_seconds: float = 5.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("attempt cleanup interval must be positive")
        self._store = store
        self._run_root = run_root
        self._runner = runner or ProcessRunner()
        self._clock = clock
        self._interval_seconds = interval_seconds

    def run_once(self) -> int:
        """Settle a bounded FIFO snapshot; one failure never blocks later candidates."""

        now = self._clock() if self._clock is not None else datetime.now(UTC)
        settled = 0
        for candidate in self._store.list_attempt_cleanup_candidates(limit=16, now=now):
            try:
                self._export_incident(candidate)
                self._export_private_incident(candidate)
                self._store.mark_attempt_evidence_exported(candidate.attempt_id)
                self._cleanup_exact_resources(candidate)
                self._store.complete_attempt_cleanup_and_schedule_retry(candidate.attempt_id, now=now)
                settled += 1
            except Exception as error:  # noqa: BLE001 - cleanup is independently retryable and sanitized
                _LOGGER.warning(
                    "Evaluation attempt cleanup deferred (attempt_id=%s error_type=%s)",
                    candidate.attempt_id,
                    type(error).__name__,
                )
                try:
                    self._store.defer_attempt_cleanup(
                        candidate.attempt_id,
                        error_code="cleanup_failed",
                        retry_seconds=_ATTEMPT_CLEANUP_RETRY_SECONDS,
                        now=now,
                    )
                except Exception as defer_error:  # noqa: BLE001 - retain the original candidate for a later scan
                    _LOGGER.warning(
                        "Evaluation attempt cleanup defer failed (attempt_id=%s error_type=%s)",
                        candidate.attempt_id,
                        type(defer_error).__name__,
                    )
        return settled

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.run_once()
            except Exception as error:  # noqa: BLE001 - maintenance retries must survive a poll-wide fault
                _LOGGER.warning(
                    "Evaluation attempt cleanup poll failed (error_type=%s)",
                    type(error).__name__,
                )
            stop.wait(self._interval_seconds)

    def _export_incident(self, candidate: AttemptCleanupCandidate) -> None:
        store = ArtifactStore(self._run_root / "runs" / candidate.run_id)
        store.write_json(
            "incident/manifest.json",
            {
                "schema_version": 1,
                "attempt_id": candidate.attempt_id,
                "task_id": candidate.task_id,
                "batch_id": candidate.batch_id,
                "run_id": candidate.run_id,
                "attempt_number": candidate.attempt_number,
                "failure_code": candidate.failure_code.value,
                "failure_phase": candidate.failure_phase.value if candidate.failure_phase is not None else None,
                "failure_summary": candidate.failure_summary,
            },
        )

    def _export_private_incident(self, candidate: AttemptCleanupCandidate) -> None:
        private_root = self._run_root / "private-incidents"
        private_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _chmod_nofollow(private_root, 0o700, directory=True)
        incident_root = private_root / candidate.run_id
        incident_root.mkdir(mode=0o700, exist_ok=True)
        _chmod_nofollow(incident_root, 0o700, directory=True)
        for path in (private_root, incident_root):
            metadata = path.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise OSError("private incident path is unsafe")

        target = incident_root / "tokensflow-spool.tar.gz"
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None:
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise OSError("private incident archive is unsafe")
            _chmod_nofollow(target, 0o600, directory=False)
            return

        workspace = self._run_root / "work" / candidate.run_id
        temporary = incident_root / f".tokensflow-spool-{secrets.token_hex(8)}.tmp"

        def retain_regular_entries(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
            return info if info.isfile() or info.isdir() or info.issym() or info.islnk() else None

        try:
            with tarfile.open(temporary, mode="x:gz", dereference=False) as archive:
                for arm in ("off", "on"):
                    runtime = workspace / arm / "runtime"
                    for source in (runtime / "tokensflow-home", runtime / "tokensflow-recovery.json"):
                        try:
                            source.lstat()
                        except FileNotFoundError:
                            continue
                        archive.add(
                            source,
                            arcname=os.fspath(source.relative_to(workspace)),
                            recursive=True,
                            filter=retain_regular_entries,
                        )
            _chmod_nofollow(temporary, 0o600, directory=False)
            os.replace(temporary, target)
            directory_fd = os.open(incident_root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _cleanup_exact_resources(self, candidate: AttemptCleanupCandidate) -> None:
        cwd = self._nearest_existing(self._run_root)
        label = f"powercontext-eval.run={candidate.run_id}"
        listed = self._runner.run(
            ("docker", "ps", "-aq", "--filter", f"label={label}"),
            cwd=cwd,
            timeout=30,
            check=False,
        )
        if listed.returncode != 0:
            raise RuntimeError("attempt container inventory failed")
        for container_id in tuple(line.strip() for line in listed.stdout.splitlines() if line.strip()):
            owner = self._runner.run(
                ("docker", "inspect", "--format", '{{ index .Config.Labels "powercontext-eval.run" }}', container_id),
                cwd=cwd,
                timeout=30,
                check=False,
            )
            if owner.returncode != 0 or owner.stdout.strip() != candidate.run_id:
                raise RuntimeError("attempt container ownership changed")
            removed = self._runner.run(
                ("docker", "rm", "-f", container_id),
                cwd=cwd,
                timeout=60,
                check=False,
            )
            if removed.returncode != 0:
                raise RuntimeError("attempt container cleanup failed")

        network_name = f"powercontext-eval-{candidate.run_id}"
        networks = self._runner.run(
            (
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                f"label={label}",
                "--filter",
                f"name=^{network_name}$",
            ),
            cwd=cwd,
            timeout=30,
            check=False,
        )
        if networks.returncode != 0:
            raise RuntimeError("attempt network inventory failed")
        for network_id in tuple(line.strip() for line in networks.stdout.splitlines() if line.strip()):
            inspected = self._runner.run(
                (
                    "docker",
                    "network",
                    "inspect",
                    "--format",
                    '{{ .Name }}|{{ index .Labels "powercontext-eval.run" }}',
                    network_id,
                ),
                cwd=cwd,
                timeout=30,
                check=False,
            )
            if inspected.returncode != 0 or inspected.stdout.strip() != f"{network_name}|{candidate.run_id}":
                raise RuntimeError("attempt network ownership changed")
            removed = self._runner.run(
                ("docker", "network", "rm", network_id),
                cwd=cwd,
                timeout=30,
                check=False,
            )
            if removed.returncode != 0:
                raise RuntimeError("attempt network cleanup failed")

        self._remove_exact_workspace(candidate.run_id)

    def _remove_exact_workspace(self, run_id: str) -> None:
        work_root = self._run_root / "work"
        try:
            root_fd = os.open(
                work_root,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            )
        except FileNotFoundError:
            return
        try:
            try:
                metadata = os.stat(run_id, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise OSError("attempt workspace is unsafe")
            if not shutil.rmtree.avoids_symlink_attacks:
                raise OSError("platform does not support symlink-safe cleanup")
            shutil.rmtree(run_id, dir_fd=root_fd)
        finally:
            os.close(root_fd)

    @staticmethod
    def _nearest_existing(path: Path) -> Path:
        candidate = path
        while not candidate.exists():
            if candidate.parent == candidate:
                raise OSError("evaluation root is unavailable")
            candidate = candidate.parent
        return candidate


def default_workspace_reclaimer(config: WebConfig, store: TaskStore) -> SucceededWorkspaceReclaimer:
    """Construct the production reclaimer without widening the public Worker surface."""

    return SucceededWorkspaceReclaimer(
        store,
        config.run_root,
        interval_seconds=config.workspace_reclaim_interval_seconds,
    )
