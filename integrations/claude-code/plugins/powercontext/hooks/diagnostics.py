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

"""Best-effort cross-process throttling for host-visible hook diagnostics."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_FAILURE_OUTCOMES = frozenset({"authentication_failed", "version_mismatch", "server_unavailable", "invalid_response"})
_COOLDOWN_SECONDS = 60.0


def _state_path() -> Path:
    configured = os.environ.get("POWERCONTEXT_DIAGNOSTIC_STATE_FILE")
    if configured and configured.strip():
        return Path(configured)
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return root / "powercontext" / "claude-code-diagnostics.json"


@contextmanager
def _locked(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def should_emit(outcome: str) -> bool:
    """Return whether a diagnostic should be shown to the host user."""

    if outcome not in _FAILURE_OUTCOMES:
        return True

    try:
        now = time.time()
        state_path = _state_path()
        with _locked(state_path.with_name(f"{state_path.name}.lock")):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
                state = {}
            if not isinstance(state, dict):
                state = {}
            previous = state.get(outcome)
            if (
                isinstance(previous, (int, float))
                and not isinstance(previous, bool)
                and 0 <= now - previous < _COOLDOWN_SECONDS
            ):
                return False

            state[outcome] = now
            temporary_path = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
            temporary_path.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
            os.replace(temporary_path, state_path)
    except (OSError, TypeError, ValueError):
        # Diagnostics must never make a hook invocation fail.
        return True
    return True
