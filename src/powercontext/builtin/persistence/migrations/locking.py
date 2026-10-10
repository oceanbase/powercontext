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

"""Locks are held for the complete operation, including backup and verification."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from filelock import BaseFileLock, FileLock, SoftFileLock, Timeout

from .models import MigrationError


def _require_os_backed_lock(lock: BaseFileLock) -> None:
    if isinstance(lock, SoftFileLock):
        raise MigrationError("migration_lock_unsupported", "This platform does not provide an OS-backed file lock.")


@contextmanager
def local_migration_lock(target: Path) -> Iterator[None]:
    """Lock a canonical database file or embedded-engine directory before opening it.

    Keep the lock inode after release: removing it permits waiters on the old
    inode and newcomers on its replacement to enter concurrently.
    """
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(target.with_name(target.name + ".pc-migration.lock")), timeout=0, mode=0o600)
    _require_os_backed_lock(lock)
    try:
        lock.acquire()
    except Timeout as error:
        raise MigrationError("migration_locked", "Another process holds the migration lock.") from error
    try:
        # filelock can switch to SoftFileLock during acquire when native locking is unsupported.
        _require_os_backed_lock(lock)
        yield
    except BaseException:
        # Cleanup must not replace an unsupported-lock, migration, or interruption error.
        with suppress(BaseException):
            lock.release()
        raise
    else:
        lock.release()
