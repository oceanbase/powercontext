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

"""Safe, deterministic child-process execution."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import unquote, urlsplit

from powercontext_eval.errors import CommandCancelled, CommandFailed, CommandNotFound, CommandTimedOut

_INHERITED_ENVIRONMENT_KEYS = frozenset(
    {
        "ALL_PROXY",
        "GIT_SSL_CAINFO",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "NO_PROXY",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "TMPDIR",
        "all_proxy",
        "https_proxy",
        "http_proxy",
        "no_proxy",
    }
)
_NOT_FOUND_RETURN_CODE = 127
_TIMEOUT_RETURN_CODE = 124
_CANCELLED_RETURN_CODE = 130
_CANCELLATION_POLL_SECONDS = 0.05
_TERMINATION_GRACE_SECONDS = 1.5
_DESCENDANT_SCAN_ATTEMPTS = 3
_PROXY_ENVIRONMENT_KEYS = frozenset(
    {
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    }
)


@dataclass(frozen=True)
class CommandResult:
    """Sanitized evidence from one child-process invocation."""

    argv: tuple[str, ...]
    cwd: str
    returncode: int
    stdout: str
    stderr: str


class ProcessRunner:
    """Run argv directly with a deliberately narrow inherited environment."""

    def __init__(self, *, default_cancel_event: threading.Event | None = None) -> None:
        self._default_cancel_event = default_cancel_event

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        secrets: Sequence[str] = (),
        input_bytes: bytes | None = None,
        stdout_sink: BinaryIO | None = None,
    ) -> CommandResult:
        """Execute a command without a shell and return redacted output."""

        validated_argv = _validate_argv(argv)
        if cancel_event is None:
            cancel_event = getattr(self, "_default_cancel_event", None)
        if input_bytes is not None and not isinstance(input_bytes, bytes):
            raise TypeError("input_bytes must be bytes or None")
        if stdout_sink is not None and not hasattr(stdout_sink, "write"):
            raise TypeError("stdout_sink must be a writable binary file or None")
        cwd_text = os.fspath(cwd)
        if "\0" in cwd_text:
            raise ValueError("cwd must not contain NUL")
        child_env = build_process_environment(env)
        redactor = _Redactor((*secrets, *_proxy_secrets(child_env)))

        try:
            process = subprocess.Popen(
                validated_argv,
                cwd=cwd_text,
                env=child_env,
                stdout=stdout_sink if stdout_sink is not None else subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE if input_bytes is not None else None,
                start_new_session=os.name == "posix",
                shell=False,
            )
        except FileNotFoundError as error:
            result = _result(
                redactor,
                validated_argv,
                cwd_text,
                _NOT_FOUND_RETURN_CODE,
                b"",
                str(error),
            )
            raise CommandNotFound(_failure_message("Command could not be started", result, redactor), result) from None
        except OSError as error:
            result = _result(
                redactor,
                validated_argv,
                cwd_text,
                _NOT_FOUND_RETURN_CODE,
                b"",
                str(error),
            )
            raise CommandNotFound(_failure_message("Command could not be started", result, redactor), result) from None

        deadline = None if timeout is None else time.monotonic() + timeout
        communicate_input = input_bytes
        while True:
            if cancel_event is not None and cancel_event.is_set():
                final_stdout, final_stderr = _terminate_owned_process(process)
                result = _result(
                    redactor,
                    validated_argv,
                    cwd_text,
                    _CANCELLED_RETURN_CODE,
                    final_stdout,
                    final_stderr,
                )
                raise CommandCancelled(_failure_message("Command cancelled", result, redactor), result) from None
            wait_timeout = None
            if deadline is not None:
                wait_timeout = max(deadline - time.monotonic(), 0.0)
            if cancel_event is not None:
                wait_timeout = (
                    _CANCELLATION_POLL_SECONDS
                    if wait_timeout is None
                    else min(wait_timeout, _CANCELLATION_POLL_SECONDS)
                )
            try:
                stdout, stderr = process.communicate(input=communicate_input, timeout=wait_timeout)
                break
            except subprocess.TimeoutExpired as error:
                communicate_input = None
                if deadline is None or time.monotonic() < deadline:
                    continue
                final_stdout, final_stderr = _terminate_owned_process(process)
                result = _result(
                    redactor,
                    validated_argv,
                    cwd_text,
                    _TIMEOUT_RETURN_CODE,
                    final_stdout or error.stdout,
                    final_stderr or error.stderr,
                )
                raise CommandTimedOut(_failure_message("Command timed out", result, redactor), result) from None

        result = _result(
            redactor,
            validated_argv,
            cwd_text,
            process.returncode,
            stdout,
            stderr,
        )
        if check and result.returncode != 0:
            raise CommandFailed(_failure_message("Command failed", result, redactor), result)
        return result


def _terminate_owned_process(process: subprocess.Popen[bytes]) -> tuple[bytes | None, bytes | None]:
    deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
    if process.poll() is not None:
        return _bounded_communicate(process, deadline)
    if os.name != "posix":
        process.kill()
        return _bounded_communicate(process, deadline)

    try:
        process_group = os.getpgid(process.pid)
    except ProcessLookupError:
        return _bounded_communicate(process, deadline)
    if process_group != process.pid:
        process.kill()
        return _bounded_communicate(process, deadline)

    _signal_process(process.pid, signal.SIGSTOP)
    descendants: set[int] = set()
    # Descendants which already reparented before the launcher is frozen cannot
    # be attributed safely. Repeated bounded scans close the spawn race for the
    # live descendant tree that still belongs to the timed-out launcher.
    for _attempt in range(_DESCENDANT_SCAN_ATTEMPTS):
        scanned = _descendant_processes(process.pid, deadline)
        new_descendants = scanned - descendants
        descendants.update(scanned)
        for pid in scanned:
            _signal_process(pid, signal.SIGSTOP)
        if not new_descendants:
            break

    for pid in sorted(descendants, reverse=True):
        _signal_process(pid, signal.SIGKILL)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return _bounded_communicate(process, deadline)


def _signal_process(pid: int, signal_number: int) -> None:
    try:
        os.kill(pid, signal_number)
    except ProcessLookupError:
        pass


def _descendant_processes(root_pid: int, deadline: float) -> set[int]:
    parent_map = _process_parent_map(deadline)
    children: dict[int, list[int]] = {}
    for pid, parent_pid in parent_map.items():
        children.setdefault(parent_pid, []).append(pid)

    descendants: set[int] = set()
    frontier = list(children.get(root_pid, ()))
    while frontier:
        pid = frontier.pop()
        if pid in descendants:
            continue
        descendants.add(pid)
        frontier.extend(children.get(pid, ()))
    return descendants


def _process_parent_map(deadline: float) -> dict[int, int]:
    proc_root = Path("/proc")
    if proc_root.is_dir():
        return _linux_process_parent_map(proc_root, deadline)

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {}
    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid="],
            capture_output=True,
            timeout=remaining,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}

    parent_map: dict[int, int] = {}
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            pid, parent_pid = (int(field) for field in fields)
        except ValueError:
            continue
        parent_map[pid] = parent_pid
    return parent_map


def _linux_process_parent_map(proc_root: Path, deadline: float) -> dict[int, int]:
    parent_map: dict[int, int] = {}
    for entry in proc_root.iterdir():
        if time.monotonic() >= deadline:
            break
        if not entry.name.isdigit():
            if time.monotonic() >= deadline:
                break
            continue
        try:
            stat_line = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            if time.monotonic() >= deadline:
                break
            continue
        if time.monotonic() >= deadline:
            break
        try:
            remainder = stat_line.rsplit(")", maxsplit=1)[1].split()
            parent_map[int(entry.name)] = int(remainder[1])
        except (IndexError, ValueError):
            continue
    return parent_map


def _bounded_communicate(
    process: subprocess.Popen[bytes],
    deadline: float,
) -> tuple[bytes | None, bytes | None]:
    remaining = max(deadline - time.monotonic(), 0.01)
    try:
        return process.communicate(timeout=remaining)
    except subprocess.TimeoutExpired as error:
        _close_controller_pipes(process)
        try:
            process.kill()
        except ProcessLookupError:
            pass
        remaining = max(deadline - time.monotonic(), 0.01)
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            pass
        return error.stdout, error.stderr
    finally:
        _close_controller_pipes(process)


def _close_controller_pipes(process: subprocess.Popen[bytes]) -> None:
    for pipe in (process.stdout, process.stderr):
        if pipe is not None and not pipe.closed:
            pipe.close()


class _Redactor:
    def __init__(self, secrets: Sequence[str]) -> None:
        invalid = [secret for secret in secrets if not isinstance(secret, str)]
        if invalid:
            raise TypeError("secrets must contain only strings")
        self._secrets = tuple(sorted({secret for secret in secrets if secret}, key=len, reverse=True))

    def __call__(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        return value


def _validate_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)) or not argv:
        raise ValueError("argv must be a non-empty sequence of strings")
    if any(not isinstance(argument, str) for argument in argv):
        raise ValueError("argv must contain only strings")
    if any("\0" in argument for argument in argv):
        raise ValueError("argv must not contain NUL")
    return tuple(argv)


def build_process_environment(overrides: Mapping[str, str] | None) -> dict[str, str]:
    """Build the restricted child environment used by every managed process."""

    environment = {
        key: value for key, value in os.environ.items() if key in _INHERITED_ENVIRONMENT_KEYS or key.startswith("LC_")
    }
    if overrides is None:
        return environment

    for key, value in overrides.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("environment overrides must map strings to strings")
        if not key or "=" in key or "\0" in key or "\0" in value:
            raise ValueError("environment overrides contain an invalid key or value")
        environment[key] = value
    return environment


def _proxy_secrets(environment: Mapping[str, str]) -> tuple[str, ...]:
    secrets: list[str] = []
    for key in _PROXY_ENVIRONMENT_KEYS:
        value = environment.get(key)
        if not value:
            continue
        secrets.append(value)
        try:
            parsed = urlsplit(value)
        except ValueError:
            continue
        for credential in (parsed.username, parsed.password):
            if credential:
                secrets.extend((credential, unquote(credential)))
    return tuple(secrets)


def _decode_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _result(
    redactor: _Redactor,
    argv: tuple[str, ...],
    cwd: str,
    returncode: int,
    stdout: bytes | str | None,
    stderr: bytes | str | None,
) -> CommandResult:
    return CommandResult(
        argv=tuple(redactor(argument) for argument in argv),
        cwd=redactor(cwd),
        returncode=returncode,
        stdout=redactor(_decode_output(stdout)),
        stderr=redactor(_decode_output(stderr)),
    )


def _failure_message(prefix: str, result: CommandResult, redactor: _Redactor) -> str:
    executable = result.argv[0] if result.argv else "<unknown>"
    return redactor(f"{prefix}: {executable!r} in {result.cwd!r} (exit {result.returncode})")


__all__ = [
    "CommandCancelled",
    "CommandFailed",
    "CommandNotFound",
    "CommandResult",
    "CommandTimedOut",
    "ProcessRunner",
    "build_process_environment",
]
