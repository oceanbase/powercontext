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

"""Serialize remote maintenance on an explicitly selected coordinator host.

This is an OS lock, not a database-wide or cross-host advisory lock. All
migration commands for the deployment must use the same host and evidence
directory. A durable run receipt fences recovery after any unclean exit.
Releasing a local OS lock does not establish that a request sent through ODP
has finished; only an explicit operator acknowledgment permits a retry.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import re
import socket
import stat
import tempfile
import uuid
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import MigrationError


class _OSLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None
        self.parent_identity: tuple[int, int] | None = None

    def acquire(self) -> None:
        if self.path.parent.is_symlink():
            raise MigrationError("migration_lock_unsupported", "The lock directory must not be a symbolic link.")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.parent_identity = _inode(self.path.parent)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            self.fd = fd
            self._lock_descriptor(fd)
            self.verify()
        except BaseException as error:
            try:
                self.release()
            except OSError:
                error.add_note("The failed coordinator lock descriptor could not be closed.")
            if isinstance(error, OSError):
                if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    failure = MigrationError(
                        "migration_locked", "Another coordinator process holds the migration lock."
                    )
                else:
                    failure = MigrationError("migration_lock_unsupported", "The coordinator OS lock is unavailable.")
                for note in getattr(error, "__notes__", []):
                    failure.add_note(note)
                raise failure from error
            raise

    def _lock_descriptor(self, fd: int) -> None:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or self.path.is_symlink():
            raise MigrationError("migration_lock_unsupported", "The lock must be a regular file.")
        if os.name == "posix":
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
                os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            raise MigrationError("migration_lock_unsupported", "This platform has no supported OS lock.")

    def verify(self) -> None:
        if self.fd is None:
            raise MigrationError("migration_lock_lost", "The coordinator lock was released.")
        try:
            opened = os.fstat(self.fd)
            visible = self.path.lstat()
            if (
                not stat.S_ISREG(visible.st_mode)
                or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
                or _inode(self.path.parent) != self.parent_identity
            ):
                raise MigrationError("migration_lock_lost", "The coordinator lock file or directory changed.")
        except OSError as error:
            raise MigrationError("migration_lock_lost", "The coordinator lock file is unavailable.") from error

    def release(self) -> None:
        fd, self.fd = self.fd, None
        if fd is not None:
            # Closing the only descriptor releases the OS lock. Keep its inode
            # so that existing waiters and new processes lock the same file.
            os.close(fd)


def _inode(path: Path) -> tuple[int, int]:
    information = path.stat()
    return information.st_dev, information.st_ino


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_symlink():
            raise MigrationError("recovery_required", "A coordinator record must not be a symbolic link.")
        with path.open(encoding="utf-8") as source:
            record = json.load(source)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeError) as error:
        raise MigrationError("recovery_required", "A durable coordinator record could not be read.") from error
    if not isinstance(record, dict):
        raise MigrationError("recovery_required", "A durable coordinator record has an unknown format.")
    return record


def _sync_directory(directory: Path) -> None:
    if os.name == "posix":
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write_record(path: Path, record: dict[str, Any]) -> None:
    temporary: Path | None = None
    try:
        if path.is_symlink():
            raise MigrationError("recovery_required", "A coordinator record must not be a symbolic link.")
        with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as output:
            temporary = Path(output.name)
            json.dump(record, output, ensure_ascii=False, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
        _sync_directory(path.parent)
    except OSError as error:
        raise MigrationError("recovery_required", "The coordinator record could not be saved durably.") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class _HostBinding:
    directory: Path
    hostname: str
    directory_identity: tuple[int, int]

    def verify(self) -> None:
        try:
            if _inode(self.directory) != self.directory_identity:
                raise MigrationError("migration_lock_lost", "The evidence directory changed.")
        except OSError as error:
            raise MigrationError("migration_lock_lost", "The evidence directory is unavailable.") from error
        if _read_record(self.directory / "coordinator.json") != {"version": 1, "hostname": self.hostname}:
            raise MigrationError("recovery_required", "The evidence directory belongs to a different coordinator host.")


def _bind_host(directory: Path) -> _HostBinding:
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    host = socket.gethostname().casefold()
    if not host:
        raise MigrationError("migration_lock_unsupported", "The coordinator hostname is unavailable.")
    binding = _HostBinding(directory, host, _inode(directory))
    lock = _OSLock(directory / "coordinator.lock")
    lock.acquire()
    try:
        path = directory / "coordinator.json"
        if _read_record(path) is None:
            _write_record(path, {"version": 1, "hostname": host})
        binding.verify()
    finally:
        lock.release()
    return binding


def _validate_database_id(database_id: str) -> None:
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", database_id) is None:
        raise MigrationError("unsupported_target", "The maintenance database identity is not a safe directory name.")


def _run_receipt(path: Path, *, database_id: str, hostname: str) -> dict[str, Any] | None:
    receipt = _read_record(path)
    if receipt is not None and (
        set(receipt) != {"version", "database_id", "hostname", "token", "previous_run"}
        or type(receipt["version"]) is not int
        or receipt["version"] != 1
        or receipt["database_id"] != database_id
        or receipt["hostname"] != hostname
        or not isinstance(receipt["token"], str)
        or re.fullmatch(r"[0-9a-f]{32}", receipt["token"]) is None
        or (
            receipt["previous_run"] is not None
            and (
                not isinstance(receipt["previous_run"], str)
                or re.fullmatch(r"[0-9a-f]{32}", receipt["previous_run"]) is None
                or receipt["previous_run"] == receipt["token"]
            )
        )
    ):
        raise MigrationError("recovery_required", "The previous run receipt has an unknown identity or format.")
    return receipt


@dataclass(frozen=True)
class _ActiveRun:
    directory: Path
    database_id: str
    own_token: str
    prior_token: str | None


_active_run: ContextVar[_ActiveRun | None] = ContextVar("oceanbase_active_migration_run", default=None)


def pending_run(directory: Path, database_id: str) -> str | None:
    """Read an unresolved run token without creating files or querying the DB.

    Within an admitted operation this returns its predecessor's token, so that
    rechecking the reviewed plan does not treat its own receipt as a new crash.
    Independent callers continue to see the current operation's durable token.
    """
    _validate_database_id(database_id)
    directory = directory.expanduser().resolve()
    hostname = socket.gethostname().casefold()
    binding = _read_record(directory / "coordinator.json")
    if binding is not None and binding != {"version": 1, "hostname": hostname}:
        raise MigrationError("recovery_required", "The evidence directory belongs to a different coordinator host.")
    receipt = _run_receipt(directory / database_id / "active-run.json", database_id=database_id, hostname=hostname)
    active = _active_run.get()
    if active is not None and active.directory == directory and active.database_id == database_id:
        if receipt is None or receipt["token"] != active.own_token or receipt["previous_run"] != active.prior_token:
            raise MigrationError("recovery_required", "The admitted run receipt changed; inspect it first.")
        return active.prior_token
    return receipt["token"] if receipt is not None else None


@dataclass(frozen=True)
class CoordinatorLock:
    connection: AsyncConnection
    name: str
    _os_lock: _OSLock
    _host: _HostBinding
    _receipt_path: Path
    _receipt: dict[str, Any]

    def _verify_local(self) -> None:
        self._os_lock.verify()
        self._host.verify()
        if _read_record(self._receipt_path) != self._receipt:
            raise MigrationError("recovery_required", "The active run receipt changed; inspect it first.")

    def verify_sync(self, connection: Connection) -> None:
        """Check local coordination without relying on a physical ODP session."""
        self._verify_local()
        if connection.closed or connection.invalidated:
            raise MigrationError("migration_lock_lost", "The dedicated maintenance connection was lost.")

    async def verify(self) -> None:
        self._verify_local()
        if self.connection.closed or self.connection.invalidated:
            raise MigrationError("migration_lock_lost", "The dedicated maintenance connection was lost.")


def _clear_receipt(path: Path, receipt: dict[str, Any], lock: _OSLock, host: _HostBinding) -> None:
    lock.verify()
    host.verify()
    if _read_record(path) != receipt:
        raise MigrationError("recovery_required", "The active run receipt changed; retain it for inspection.")
    path.unlink()
    _sync_directory(path.parent)


async def _finish_cleanup(operation: Awaitable[None]) -> None:
    task = asyncio.ensure_future(operation)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancellation = cancellation or error
    task.result()
    if cancellation is not None:
        raise cancellation


def _start_run(path: Path, database_id: str, hostname: str, acknowledgment: str | None) -> dict[str, Any]:
    previous = _run_receipt(path, database_id=database_id, hostname=hostname)
    prior_token = previous["token"] if previous is not None else None
    if prior_token is not None and acknowledgment != prior_token:
        raise MigrationError(
            "recovery_required", "Confirm the previous remote operation has ended and acknowledge its run token."
        )
    if prior_token is None and acknowledgment is not None:
        raise MigrationError("stale_ack", "There is no previous run matching the supplied acknowledgment.")
    receipt = {
        "version": 1,
        "database_id": database_id,
        "hostname": hostname,
        "token": uuid.uuid4().hex,
        "previous_run": prior_token,
    }
    _write_record(path, receipt)
    return receipt


@asynccontextmanager
async def oceanbase_migration_lock(
    connection: AsyncConnection,
    *,
    database_id: str,
    directory: Path,
    acknowledged_run: str | None = None,
) -> AsyncIterator[CoordinatorLock]:
    """Hold one fixed host's lock and receipt through primary connection close.

    A normal callback and successful close clear the receipt. Every unclean run
    keeps it, even when local close succeeds. A matching acknowledgment declares
    that the operator verified the previous remote operation has ended; the
    coordinator cannot establish that fact from ODP physical session IDs.
    """
    _validate_database_id(database_id)
    host = _bind_host(directory)
    lock = _OSLock(host.directory / database_id / "migration.lock")
    lock.acquire()
    path = lock.path.parent / "active-run.json"
    original: BaseException | None = None
    completed = False
    context_token = None
    receipt: dict[str, Any] | None = None
    try:
        receipt = _start_run(path, database_id, host.hostname, acknowledged_run)
        context_token = _active_run.set(
            _ActiveRun(host.directory, database_id, receipt["token"], receipt["previous_run"])
        )
        held = CoordinatorLock(connection, str(lock.path), lock, host, path, receipt)
        await held.verify()
        yield held
        await held.verify()
        completed = True
    except BaseException as error:
        original = error
        raise
    finally:
        cleanup_error: BaseException | None = None
        try:
            # Close in a separate shielded task, but clear only after the caller
            # has also returned normally from waiting for that task. Cancellation
            # during close must retain evidence even if close eventually succeeds.
            await _finish_cleanup(connection.close())
            if completed and receipt is not None:
                _clear_receipt(path, receipt, lock, host)
        except BaseException as error:
            if original is None:
                cleanup_error = error
            else:
                original.add_note(
                    f"Coordinator cleanup did not complete safely ({type(error).__name__}); retain the run receipt."
                )
        finally:
            if context_token is not None:
                _active_run.reset(context_token)
        try:
            lock.release()
        except OSError as error:
            if original is not None:
                original.add_note("The coordinator lock descriptor could not be closed.")
            elif cleanup_error is not None:
                cleanup_error.add_note("The coordinator lock descriptor could not be closed.")
            else:
                cleanup_error = MigrationError(
                    "recovery_required", "The coordinator lock descriptor could not be closed."
                )
                cleanup_error.__cause__ = error
        if cleanup_error is not None:
            raise cleanup_error
