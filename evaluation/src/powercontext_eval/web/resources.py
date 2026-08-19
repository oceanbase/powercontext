"""Filesystem admission and successful-workspace lifecycle management."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from powercontext_eval.paths import EvaluationPaths
from powercontext_eval.web.config import WebConfig
from powercontext_eval.web.models import TaskRecord
from powercontext_eval.web.reporting import load_report
from powercontext_eval.web.store import TaskStore

_LOGGER = logging.getLogger(__name__)


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


def default_workspace_reclaimer(config: WebConfig, store: TaskStore) -> SucceededWorkspaceReclaimer:
    """Construct the production reclaimer without widening the public Worker surface."""

    return SucceededWorkspaceReclaimer(
        store,
        config.run_root,
        interval_seconds=config.workspace_reclaim_interval_seconds,
    )
