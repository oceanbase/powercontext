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

"""Migration callbacks require an OS-backed lock even after runtime fallback."""

from __future__ import annotations

import errno
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from filelock import FileLock, SoftFileLock

from powercontext.builtin.persistence.migrations.locking import local_migration_lock
from powercontext.builtin.persistence.migrations.models import MigrationError


@pytest.mark.parametrize("release_fails", [False, True])
def test_runtime_soft_lock_fallback_blocks_the_operation_and_preserves_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, release_fails: bool
) -> None:
    target = tmp_path / "database"
    operation = Mock()
    original_release = SoftFileLock.release

    def fallback(lock, *args, **kwargs):
        lock.__class__ = SoftFileLock
        return SoftFileLock.acquire(lock, *args, **kwargs)

    def release(lock, force: bool = False) -> None:
        was_locked = lock.is_locked
        original_release(lock, force)
        if was_locked and release_fails:
            message = "Release failed after closing the lock."
            raise OSError(message)

    with monkeypatch.context() as patch:
        patch.setattr(FileLock, "acquire", fallback)
        patch.setattr(SoftFileLock, "release", release)
        with pytest.raises(MigrationError) as error, local_migration_lock(target):
            operation()

    assert error.value.code == "migration_lock_unsupported"
    operation.assert_not_called()
    assert not target.exists()
    assert not target.with_name(target.name + ".pc-migration.lock").exists()
    with local_migration_lock(target):
        pass


@pytest.mark.skipif(sys.platform == "win32", reason="flock capability detection is a Unix filesystem boundary")
def test_native_flock_unsupported_does_not_enter_a_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    target = tmp_path / "database"
    operation = Mock()

    def unsupported_flock(_descriptor: int, _operation: int) -> None:
        raise OSError(errno.ENOSYS, "Native flock is unavailable on this filesystem.")

    with monkeypatch.context() as patch:
        patch.setattr(fcntl, "flock", unsupported_flock)
        with (
            pytest.warns(UserWarning, match="falling back to SoftFileLock"),
            pytest.raises(MigrationError) as error,
            local_migration_lock(target),
        ):
            operation()

    assert error.value.code == "migration_lock_unsupported"
    operation.assert_not_called()
    assert not target.exists()
    assert not target.with_name(target.name + ".pc-migration.lock").exists()
    with local_migration_lock(target):
        pass


def test_cleanup_failure_does_not_replace_the_migration_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "database"
    original_release = FileLock.release
    failure = MigrationError("verification_failed", "Migration verification failed.")

    def release(lock, force: bool = False) -> None:
        was_locked = lock.is_locked
        original_release(lock, force)
        if was_locked:
            message = "Release failed after closing the lock."
            raise OSError(message)

    with monkeypatch.context() as patch:
        patch.setattr(FileLock, "release", release)
        with pytest.raises(MigrationError) as error, local_migration_lock(target):
            raise failure

    assert error.value is failure
    with local_migration_lock(target):
        pass
