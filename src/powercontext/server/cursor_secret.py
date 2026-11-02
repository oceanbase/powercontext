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

"""Resolve a stable signing secret for stateless REST cursors."""

from __future__ import annotations

import contextlib
import os
import secrets
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import DatabaseConfig
from powercontext.paths import powercontext_data_dir

_CURSOR_SECRET_BYTES = 32
_KEY_CREATE_ATTEMPTS = 3
_KEY_LOCK_TIMEOUT_SECONDS = 10.0


def resolve_cursor_secret(database: DatabaseConfig, configured_secret: str | None, /) -> bytes | None:
    """Use an explicit secret, or persist one in the local data location."""

    if configured_secret is not None:
        return configured_secret.encode()
    if isinstance(database, SQLiteConfig):
        if not database.is_persistent:
            return None
        database_name = make_url(database.url).database
        if database_name:
            database_path = Path(database_name).expanduser().resolve(strict=False)
            return _load_or_create(database_path.with_name(f".{database_path.name}.cursor-key"))
    return _load_or_create(powercontext_data_dir() / "cursor-signing.key")


def _load_or_create(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Deciding that a key is debris and publishing its replacement is one
    # critical section. Without it, a starter that read a missing key can wake
    # up after another starter published a complete one, and delete it.
    with _creation_lock(path):
        return _create_or_reuse_locked(path)


def _create_or_reuse_locked(path: Path) -> bytes:
    for _ in range(_KEY_CREATE_ATTEMPTS):
        secret = _read(path)
        if secret is not None:
            return secret

        # Only inside the lock can a key of the wrong length be assumed to come
        # from a creation that stopped before the writer returned it: every
        # writer holds this lock, so no other starter is midway through writing.
        # Outside the lock, a short file may belong to a starter that is about
        # to publish the key it will sign cursors with.
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)

        generated = secrets.token_bytes(_CURSOR_SECRET_BYTES)
        try:
            # Windows text descriptors translate LF bytes even when using os.write.
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            # Another starter won the race; use the key it publishes.
            continue
        completed = False
        try:
            _write_key(descriptor, generated, path)
            os.fsync(descriptor)
            completed = True
        finally:
            os.close(descriptor)
            if not completed:
                # Do not leave a half-written key behind: the next start would
                # have to discard it before it could create a usable one.
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
        return generated

    raise OSError(f"failed to create cursor signing key: {path}")  # noqa: TRY003


@contextmanager
def _creation_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive cross-process lock for one cursor key location."""

    # Keep the lock inode after release: removing it lets a waiter on the old
    # inode and a newcomer on the replacement enter at the same time.
    lock_path = path.with_name(f"{path.name}.lock")
    if os.name == "nt":
        with _windows_creation_lock(lock_path):
            yield
        return

    import fcntl

    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        deadline = time.monotonic() + _KEY_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise OSError(  # noqa: TRY003
                        f"timed out waiting for the cursor signing key lock: {path}"
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _windows_creation_lock(lock_path: Path) -> Iterator[None]:
    import msvcrt

    # These Windows-only members are missing from the stdlib type stubs.
    msvcrt_members = vars(msvcrt)
    locking = cast(Callable[[int, int, int], None], msvcrt_members["locking"])
    lock_nonblocking = cast(int, msvcrt_members["LK_NBLCK"])
    lock_unlock = cast(int, msvcrt_members["LK_UNLCK"])
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        # Take the lock without seeding the file first. `_locking` accepts a range
        # past the end of the file, and writing a marker before the lock is held is
        # itself a race: a starter that read the size before another starter locked
        # byte 0 would write into that byte, Windows rejects the write with errno 13,
        # and the error escapes the retry loop below, so the second starter never
        # starts even though the persisted key is valid.
        deadline = time.monotonic() + _KEY_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                locking(descriptor, lock_nonblocking, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise OSError(  # noqa: TRY003
                        f"timed out waiting for the cursor signing key lock: {lock_path}"
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        with contextlib.suppress(OSError):
            os.lseek(descriptor, 0, os.SEEK_SET)
            locking(descriptor, lock_unlock, 1)
        os.close(descriptor)


def _write_key(descriptor: int, secret: bytes, path: Path) -> None:
    """Write every byte: os.write may accept fewer bytes than requested."""

    remaining = memoryview(secret)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError(f"failed to write complete cursor signing key: {path}")  # noqa: TRY003
        remaining = remaining[written:]


def _read(path: Path) -> bytes | None:
    """Return the stored key, or None when there is no complete key on disk.

    "No complete key" covers both a missing file and a file of the wrong
    length. Treating the second as debris is only safe inside `_creation_lock`:
    a short file may also be a key that another starter is still writing and
    about to sign cursors with.
    """

    try:
        secret = path.read_bytes()
    except FileNotFoundError:
        return None
    if len(secret) != _CURSOR_SECRET_BYTES:
        return None
    return secret
