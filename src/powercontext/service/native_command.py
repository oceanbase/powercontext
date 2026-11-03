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

"""Observe bounded native mutation processes without recording command arguments."""

from __future__ import annotations

import errno
import json
import os
import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from powercontext.service.model import ServiceError

_observer: ContextVar[Callable[[dict[str, object]], None] | None] = ContextVar("native_command_observer", default=None)


@contextmanager
def observe_native_commands(callback: Callable[[dict[str, object]], None]) -> Iterator[None]:
    """Persist command boundaries in the owning operation's existing journal."""
    previous = _observer.get()

    def observe(event: dict[str, object]) -> None:
        # The outer service mutation owns the evidence. Inner Ops observers
        # checkpoint their projection after the owning boundary succeeds.
        if previous is not None:
            previous(event)
        callback(event)

    token = _observer.set(observe)
    try:
        yield
    finally:
        _observer.reset(token)


def native_command_evidence(path: Path) -> dict[str, object] | None:
    """Read service-owned process evidence without creating or repairing state."""
    from powercontext.service.environment import read_protected_file

    if not path.exists() and not path.is_symlink():
        return None
    try:
        record = json.loads(read_protected_file(path).content)
    except (OSError, ValueError) as error:
        raise ServiceError("the native command evidence cannot be verified") from error  # noqa: TRY003
    if not isinstance(record, dict) or type(record.get("format_version")) is not int or record["format_version"] != 1:
        raise ServiceError("the native command evidence format is unsupported")  # noqa: TRY003
    phase = record.get("phase")
    without_pid = phase in ("dispatching", "spawn_failed")
    expected = {"format_version", "phase"} if without_pid else {"format_version", "phase", "pid"}
    if (
        set(record) != expected
        or not isinstance(phase, str)
        or phase not in {"dispatching", "running", "completed", "spawn_failed"}
    ):
        raise ServiceError("the native command evidence is invalid")  # noqa: TRY003
    if not without_pid and (type(record["pid"]) is not int or record["pid"] <= 0):
        raise ServiceError("the native command process identity is invalid")  # noqa: TRY003
    return record


@contextmanager
def native_command_boundary(path: Path) -> Iterator[None]:
    """Exclude unfinished native commands while holding the existing service lock."""
    from powercontext.service.environment import write_protected_file

    record = native_command_evidence(path)
    if record is not None and (
        record["phase"] == "dispatching"
        or (record["phase"] == "running" and not native_process_exited(int(str(record["pid"]))))
    ):
        raise ServiceError("a previous native service command has not been confirmed to have exited")  # noqa: TRY003

    def checkpoint(event: dict[str, object]) -> None:
        try:
            write_protected_file(path, json.dumps({"format_version": 1, **event}, sort_keys=True).encode("utf-8"))
        except (OSError, ValueError) as error:
            raise ServiceError("the native command evidence could not be recorded") from error  # noqa: TRY003

    with observe_native_commands(checkpoint):
        yield


def run_native_command(
    command: Sequence[str], *, timeout: float, text: bool = False, creationflags: int = 0
) -> subprocess.CompletedProcess[Any]:
    """Run one native mutation and positively reap its process before completion.

    A lost parent after ``dispatching`` but before the PID checkpoint is
    deliberately uncertain. Callers must never infer child exit from release
    of the parent process's service lock.
    """
    observer = _observer.get()
    if observer is None:
        return subprocess.run(  # noqa: S603
            command, capture_output=True, text=text, creationflags=creationflags, timeout=timeout, check=False
        )
    observer({"phase": "dispatching"})
    # An interruption inside Popen can occur after OS process creation but
    # before Python returns its handle. Preserve dispatching without guessing.
    try:
        child = subprocess.Popen(  # noqa: S603
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text, creationflags=creationflags
        )
    except OSError as error:
        # These launch errors mean CreateProcess failed on Windows, or that
        # Popen reaped a child whose exec failed on POSIX. Other failures (for
        # example a pipe error after creation) cannot prove child absence.
        if error.errno in {errno.ENOENT, errno.EACCES, errno.EPERM, errno.ENOTDIR, errno.ENOEXEC, errno.E2BIG}:
            try:
                observer({"phase": "spawn_failed"})
            except BaseException as checkpoint_error:
                error.add_note(
                    f"native command launch failure could not be recorded: {type(checkpoint_error).__name__}"
                )
        raise
    try:
        observer({"phase": "running", "pid": child.pid})
        stdout, stderr = child.communicate(timeout=timeout)
    except BaseException as error:
        try:
            child.kill()
            stdout, stderr = child.communicate()
            if isinstance(error, subprocess.TimeoutExpired):
                error.output = stdout.encode() if isinstance(stdout, str) else stdout
                error.stderr = stderr.encode() if isinstance(stderr, str) else stderr
        except BaseException as cleanup_error:
            error.add_note(f"native command exit could not be confirmed: {type(cleanup_error).__name__}")
        else:
            try:
                observer({"phase": "completed", "pid": child.pid})
            except BaseException as checkpoint_error:
                error.add_note(f"native command completion could not be recorded: {type(checkpoint_error).__name__}")
        raise
    observer({"phase": "completed", "pid": child.pid})
    return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)


def native_process_exited(pid: int) -> bool:
    """Return true only when the observed PID is positively absent or terminated.

    Reused PIDs and inaccessible processes stay unknown; neither permits a new
    mutation. Windows ``os.kill(pid, 0)`` is deliberately never used.
    """
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        if pid > 0xFFFFFFFF:
            return False
        return _windows_process_exited(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (OSError, OverflowError, ValueError):
        return False
    return False


def _windows_process_exited(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    win_dll = getattr(ctypes, "WinDLL")  # noqa: B009
    get_last_error = getattr(ctypes, "get_last_error")  # noqa: B009
    kernel32 = win_dll("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, no terminate permission.
    if not handle:
        return get_last_error() == 87  # ERROR_INVALID_PARAMETER: no such process.
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0  # WAIT_OBJECT_0, not WAIT_TIMEOUT/WAIT_FAILED.
    finally:
        kernel32.CloseHandle(handle)
