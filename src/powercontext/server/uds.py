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

"""Own a private HTTP Unix socket without replacing another listener or file."""

# ruff: noqa: TRY003 - keep actionable configuration errors beside their validation.

from __future__ import annotations

import errno
import json
import os
import socket
import stat
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TextIO

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


class UnixSocketExternalScheme:
    """Apply an operator-declared proxy scheme without trusting forwarded peer headers."""

    def __init__(self, app: ASGIApp, scheme: Literal["http", "https"]) -> None:
        self.app = app
        self.scheme = scheme

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            scheme = self.scheme if scope["type"] == "http" else {"http": "ws", "https": "wss"}[self.scheme]
            scope = {**scope, "scheme": scheme}
        await self.app(scope, receive, send)


class UnixSocketError(ValueError):
    """A Unix listener could not safely acquire its configured path."""


def validate_socket_path(value: object) -> Path:
    """Validate before any filesystem mutation, including on unsupported platforms."""

    if sys.platform not in {"linux", "darwin"}:
        raise UnixSocketError("UDS listeners are supported only on Linux and macOS")
    if not isinstance(value, (str, Path)):
        raise UnixSocketError("HTTP_UDS must be an absolute filesystem path")
    path = Path(value)
    if not path.is_absolute() or not path.name or ".." in path.parts or "\0" in str(value):
        raise UnixSocketError("HTTP_UDS must be an absolute socket path without '..' or NUL")
    # sockaddr_un.sun_path is 104 bytes on macOS, including the terminating NUL.
    if len(os.fsencode(path)) > 103:
        raise UnixSocketError("HTTP_UDS must be at most 103 filesystem-encoded bytes; choose a shorter run directory")
    return path


def _private_directory(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise UnixSocketError("UDS requires a real directory owned by the current user with no group or other access")


def _socket_identity(path: Path) -> tuple[int, int, int] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
        raise UnixSocketError("UDS path is occupied by a file, symlink, or socket owned by another user")
    return info.st_dev, info.st_ino, info.st_ctime_ns


def _reclaim_socket(path: Path, lock: TextIO) -> None:
    identity = _socket_identity(path)
    if identity is None:
        return
    try:
        record = json.loads(lock.read(1024))
    except (ValueError, UnicodeError):
        record = None
    if record != {"device": identity[0], "inode": identity[1], "ctime_ns": identity[2]}:
        raise UnixSocketError("UDS path is occupied by an unrecognized socket; refusing to remove it")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        try:
            probe.connect(str(path))
        except OSError as error:
            if error.errno != errno.ECONNREFUSED:
                raise UnixSocketError("UDS socket liveness could not be established; refusing to remove it") from error
        else:
            raise UnixSocketError("UDS socket is already accepting connections")
    if _socket_identity(path) != identity:
        raise UnixSocketError("UDS socket changed during startup; refusing to remove it")
    path.unlink()


def _close_socket(listener: socket.socket, path: Path, identity: tuple[int, int]) -> None:
    # An operator's replacement file or socket must survive shutdown.
    listener.close()
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) == identity and stat.S_ISSOCK(current.st_mode):
        path.unlink()


@contextmanager
def unix_socket_listener(path: Path) -> Iterator[socket.socket]:
    """Bind one owned socket; retain a lock record for safe recovery after a crash.

    The lock file deliberately survives shutdown: deleting it would let a competing
    process lock a different inode while another process still holds the old lock.
    """

    path = validate_socket_path(path)
    import fcntl

    with ExitStack() as resources:
        try:
            _private_directory(path.parent)
            lock_path = path.with_name(path.name + ".lock")
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            lock = resources.enter_context(os.fdopen(fd, "r+", encoding="utf-8"))
            info = os.fstat(lock.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_nlink != 1
            ):
                raise UnixSocketError("UDS lock must be a private regular file owned by the current user")
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise UnixSocketError("UDS listener is already running or starting") from error
            _reclaim_socket(path, lock)
            listener = resources.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
            listener.bind(str(path))
            info = path.lstat()
            identity = info.st_dev, info.st_ino
            resources.callback(_close_socket, listener, path, identity)
            path.chmod(0o600)
            info = path.lstat()
            lock.seek(0)
            lock.truncate()
            # Include inode change time so a recycled inode is not mistaken for
            # the previous instance's socket after a normal shutdown.
            json.dump({"device": info.st_dev, "inode": info.st_ino, "ctime_ns": info.st_ctime_ns}, lock)
            lock.flush()
            os.fsync(lock.fileno())
        except OSError as error:
            raise UnixSocketError(
                "Cannot acquire the UDS listener; check directory, socket, and lock ownership/permissions"
            ) from error
        yield listener


__all__ = ["UnixSocketError", "UnixSocketExternalScheme", "unix_socket_listener", "validate_socket_path"]
