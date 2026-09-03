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

"""Count a launchd attempt before importing the PowerContext application."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import stat
import sys
import tempfile
import time
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_VERSION = 1


class RetryStart(StrEnum):
    STARTED = "started"
    EXHAUSTED = "exhausted"
    STATE_ERROR = "state_error"


class FailureBudget:
    """Persist a bounded retry window without importing PowerContext."""

    def __init__(self, *, state_path: Path, token_path: Path, limit: int, window_seconds: float) -> None:
        self._state_path = state_path
        self._token_path = token_path
        self._limit = limit
        self._window_seconds = window_seconds
        self._attempts = 0

    def begin_attempt(self) -> RetryStart:
        if self._limit < 1 or self._window_seconds <= 0:
            logger.error("Personal service retry-budget configuration is invalid")
            return RetryStart.STATE_ERROR
        now = time.time()
        try:
            attempts = [
                attempt for attempt in _read_attempts(self._state_path) if now - attempt <= self._window_seconds
            ]
            if len(attempts) >= self._limit:
                self._attempts = len(attempts)
                return RetryStart.EXHAUSTED
            attempts.append(now)
            _atomic_write(
                self._state_path,
                json.dumps({"version": _STATE_VERSION, "attempts": attempts}, separators=(",", ":")).encode(),
            )
        except (OSError, ValueError):
            logger.exception("Cannot update the personal service retry budget")
            return RetryStart.STATE_ERROR
        self._attempts = len(attempts)
        return RetryStart.STARTED

    @property
    def exhausted_after_failure(self) -> bool:
        return self._attempts >= self._limit

    def stop_retrying(self, *, clear_state: bool) -> None:
        paths = (self._token_path, self._state_path) if clear_state else (self._token_path,)
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                logger.warning("Cannot remove personal service retry file %s: %s", path, error)


def main(arguments: list[str] | None = None) -> int:
    raw_arguments = list(arguments) if arguments is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="powercontext-personal-service-bootstrap")
    parser.add_argument("--retry-state", required=True, type=Path)
    parser.add_argument("--retry-token", required=True, type=Path)
    parser.add_argument("--retry-limit", required=True, type=int)
    parser.add_argument("--retry-window-seconds", required=True, type=float)
    try:
        options, launcher_arguments = parser.parse_known_args(raw_arguments)
    except SystemExit as error:
        _remove_declared_token(raw_arguments)
        return int(error.code or 2)
    if launcher_arguments[:1] == ["--"]:
        launcher_arguments = launcher_arguments[1:]

    budget = FailureBudget(
        state_path=options.retry_state,
        token_path=options.retry_token,
        limit=options.retry_limit,
        window_seconds=options.retry_window_seconds,
    )
    started = budget.begin_attempt()
    if started is RetryStart.EXHAUSTED:
        logger.error("Personal service retry budget is exhausted; run `powercontext service install` to retry")
        budget.stop_retrying(clear_state=False)
        return 0
    if started is RetryStart.STATE_ERROR:
        budget.stop_retrying(clear_state=False)
        return 1

    try:
        from powercontext.service.launcher import main as launcher_main

        exit_code = launcher_main(launcher_arguments)
    except Exception:
        logger.exception("PowerContext personal service failed before the launcher could run")
        exit_code = 1

    if exit_code == 0:
        budget.stop_retrying(clear_state=True)
        return 0
    if budget.exhausted_after_failure:
        logger.error(
            "Personal service retry budget exhausted after %d attempts; run `powercontext service install` to retry",
            options.retry_limit,
        )
        budget.stop_retrying(clear_state=False)
        return 0
    return exit_code


def _read_attempts(path: Path) -> list[float]:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return []
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or (
            os.name != "nt" and (status.st_uid != _posix_uid() or status.st_mode & (stat.S_IRWXG | stat.S_IRWXO))
        ):
            raise ValueError("unsafe retry-budget state")  # noqa: TRY003
        with os.fdopen(descriptor, encoding="utf-8") as source:
            descriptor = -1
            payload = json.load(source)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid retry-budget state") from error  # noqa: TRY003
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict) or payload.get("version") != _STATE_VERSION:
        raise ValueError("invalid retry-budget state version")  # noqa: TRY003
    attempts = payload.get("attempts")
    if not isinstance(attempts, list) or any(
        not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in attempts
    ):
        raise ValueError("invalid retry-budget state")  # noqa: TRY003
    return [float(value) for value in attempts]


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            fchmod(descriptor, 0o600)
        else:
            os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _remove_declared_token(arguments: list[str] | None) -> None:
    if arguments is None:
        return
    try:
        index = arguments.index("--retry-token")
        token = Path(arguments[index + 1])
    except (ValueError, IndexError):
        return
    try:
        token.unlink(missing_ok=True)
    except OSError:
        return


def _posix_uid() -> int:
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return -1
    return int(getuid())


if __name__ == "__main__":
    raise SystemExit(main())
