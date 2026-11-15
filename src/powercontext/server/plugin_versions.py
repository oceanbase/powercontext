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

"""Read-only version snapshots of plugin directories.

Deployers may change a plugin directory at any time, so no process imports from it.
Once a directory's content digest is stable, core copies it to
``<root>/<process>/<plugin>/<digest>/`` and every check, host process, and Worker imports
from that read-only copy, so one call never mixes two versions of the code.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import uuid4

from filelock import BaseFileLock, FileLock, Timeout

from powercontext.builtin.plugins.manifest import code_fingerprint

# Files are read-only so a snapshot never changes; directories stay writable by the owner so
# deleting the data directory with ordinary tools still works.
_FILE_MODE = 0o444
_DIRECTORY_MODE = 0o755
_LOCK_SUFFIX = ".lock"


def content_digest(directory: Path) -> str:
    return code_fingerprint(directory)


def make_snapshot(source: Path, root: Path, name: str, digest: str) -> Path | None:
    """Copy ``source`` into a read-only snapshot named by ``digest``.

    Returns None when the directory changed while it was copied; the next scan retries.
    """

    target = root / name / digest
    if target.is_dir():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".tmp-{uuid4().hex}"
    # Symbolic links stay links: page serving still refuses those that lead outside the plugin.
    shutil.copytree(source, staging, ignore=shutil.ignore_patterns("__pycache__"), symlinks=True)
    if code_fingerprint(staging) != digest:
        remove_snapshot(staging)
        return None
    for path in sorted(staging.rglob("*"), reverse=True):
        # chmod follows links, which may lead outside the snapshot.
        if not path.is_symlink():
            path.chmod(_DIRECTORY_MODE if path.is_dir() else _FILE_MODE)
    staging.chmod(_DIRECTORY_MODE)
    try:
        staging.rename(target)
    except OSError:
        # The same version was copied concurrently and finished first.
        remove_snapshot(staging)
        if not target.is_dir():
            raise
    return target


def remove_snapshot(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


class ProcessSnapshots:
    """This Server process's own snapshot directory, ``<root>/<process>/``.

    Several Server processes on one machine, such as an ``api`` and a ``background`` process, share
    the data directory. Each copies the versions it uses into its own directory and holds a lock
    file next to it for its lifetime, so no process deletes snapshots another one still uses. A
    directory whose lock nobody holds belongs to a process that exited without cleaning up.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory: Path | None = None
        self._lock: BaseFileLock | None = None

    def open(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        name = f"{os.getpid()}-{uuid4().hex[:12]}"
        # The lock exists before the directory, so another process never takes the directory for abandoned.
        lock = FileLock(str(self.root / f"{name}{_LOCK_SUFFIX}"), timeout=0)
        lock.acquire()
        self._lock = lock
        directory = self.root / name
        directory.mkdir()
        self.directory = directory
        self._remove_abandoned()
        return directory

    def close(self) -> None:
        if self.directory is not None:
            remove_snapshot(self.directory)
        if self._lock is not None:
            self._lock.release()
            Path(self._lock.lock_file).unlink(missing_ok=True)

    def _remove_abandoned(self) -> None:
        for entry in self.root.iterdir():
            if entry == self.directory or self._lock is None or str(entry) == self._lock.lock_file:
                continue
            try:
                if entry.name.endswith(_LOCK_SUFFIX):
                    _remove_if_abandoned(entry)
                elif entry.is_dir() and not entry.with_name(entry.name + _LOCK_SUFFIX).exists():
                    # Left by a process that crashed while removing its own directory.
                    remove_snapshot(entry)
            except OSError:
                continue


def _remove_if_abandoned(lock_path: Path) -> None:
    lock = FileLock(str(lock_path), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        return
    try:
        remove_snapshot(lock_path.with_name(lock_path.name.removesuffix(_LOCK_SUFFIX)))
    finally:
        lock.release()
    lock_path.unlink(missing_ok=True)
