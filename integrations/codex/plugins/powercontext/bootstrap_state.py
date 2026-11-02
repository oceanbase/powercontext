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

"""Persist only a hashed Session key and the latest bootstrap receipt identity."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from hashlib import sha256
from pathlib import Path
from uuid import uuid4


def load_receipt(session_id: str | None, scope_id: str) -> str | None:
    state = load_receipt_state(session_id)
    return state[1] if state is not None and state[0] == scope_id else None


def load_receipt_state(session_id: str | None) -> tuple[str, str] | None:
    path = _state_path(session_id)
    if path is None:
        return None
    return _read_receipt_state(path)


def _read_receipt_state(path: Path) -> tuple[str, str] | None:
    try:
        if path.is_symlink() or not path.is_file() or (os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {"version", "scope_id", "receipt_id"}:
            return None
        scope_id = value.get("scope_id")
        receipt_id = value.get("receipt_id")
        if (
            value.get("version") != 1
            or not isinstance(scope_id, str)
            or not scope_id.strip()
            or len(scope_id) > 256
            or not isinstance(receipt_id, str)
            or not 1 <= len(receipt_id) <= 64
            or not all("\x21" <= character <= "\x7e" for character in receipt_id)
        ):
            return None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return scope_id, receipt_id


def save_receipt(session_id: str | None, scope_id: str, receipt_id: str) -> None:
    path = _state_path(session_id)
    if path is None:
        raise OSError
    root = path.parent
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise OSError
    if os.name != "nt":
        root.chmod(0o700)
    payload = json.dumps(
        {"version": 1, "scope_id": scope_id, "receipt_id": receipt_id},
        separators=(",", ":"),
    ).encode()
    with _locked(path):
        temporary = root / f".{path.name}.{uuid4().hex}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        try:
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as output:
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                # Windows cannot replace the temporary file while its descriptor is open.
                os.close(descriptor)
            os.replace(temporary, path)
            if os.name != "nt":
                path.chmod(0o600)
        finally:
            with suppress(FileNotFoundError):
                temporary.unlink()


def clear_receipt(session_id: str | None) -> None:
    path = _state_path(session_id)
    if path is None or not path.parent.exists():
        return
    try:
        with _locked(path):
            if not path.is_symlink():
                path.unlink(missing_ok=True)
    except OSError:
        return


def clear_receipt_if_matches(
    session_id: str | None,
    scope_id: str,
    receipt_id: str,
) -> None:
    path = _state_path(session_id)
    if path is None or not path.parent.exists():
        return
    try:
        with _locked(path):
            if _read_receipt_state(path) == (scope_id, receipt_id):
                path.unlink(missing_ok=True)
    except OSError:
        return


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
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


def _state_path(session_id: str | None) -> Path | None:
    if session_id is None or not session_id.strip():
        return None
    configured = os.environ.get("POWERCONTEXT_BOOTSTRAP_STATE_DIR")
    plugin_data = os.environ.get("PLUGIN_DATA")
    if configured is None and plugin_data is None:
        return None
    root = Path(configured or plugin_data or "").expanduser()
    if not root.is_absolute():
        return None
    digest = sha256(session_id.strip().encode()).hexdigest()
    return root / "powercontext-bootstrap" / f"{digest}.json"
