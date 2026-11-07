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

import errno
import os
import stat
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


def assert_no_local_migration(target: Path) -> None:
    """Probe an existing migration lock without creating or changing its file.

    This observes active maintenance, not future migration attempts or existing
    writers. Deployment orchestration must still prevent restarts during maintenance.
    """
    target = target.resolve()
    path = target.with_name(target.name + ".pc-migration.lock")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return
    except OSError as error:
        raise MigrationError("migration_lock_unsupported", "Cannot inspect the maintenance lock.") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise MigrationError("migration_lock_unsupported", "The maintenance lock is not a regular file.")
        if os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        elif os.name == "nt":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_NBRLCK, 1)
        else:
            raise MigrationError("migration_lock_unsupported", "This platform has no supported lock inspection.")
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            raise MigrationError("migration_locked", "Another process holds the migration lock.") from error
        raise MigrationError("migration_lock_unsupported", "Cannot inspect the maintenance lock.") from error
    finally:
        os.close(descriptor)
