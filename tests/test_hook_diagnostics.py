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

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _load_diagnostics(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lock_holder_code() -> str:
    return r"""
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a+b") as lock_file:
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
    print("ready", flush=True)
    sys.stdin.read(1)
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
"""


@pytest.mark.parametrize(
    ("name", "relative_path"),
    [
        ("codex", "integrations/codex/plugins/powercontext/hooks/diagnostics.py"),
        ("claude_code", "integrations/claude-code/plugins/powercontext/hooks/diagnostics.py"),
    ],
)
def test_diagnostic_lock_contention_is_bounded(
    name: str,
    relative_path: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    diagnostics = _load_diagnostics(
        f"powercontext_{name}_diagnostics_contention",
        REPOSITORY_ROOT / relative_path,
    )
    state_path = tmp_path / f"{name}-diagnostics.json"
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    monkeypatch.setenv("POWERCONTEXT_DIAGNOSTIC_STATE_FILE", str(state_path))

    holder = subprocess.Popen(
        [sys.executable, "-c", _lock_holder_code(), str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ready"
        started = time.monotonic()
        assert diagnostics.should_emit("server_unavailable") is True
        elapsed = time.monotonic() - started
        assert elapsed < 1.0
    finally:
        if holder.stdin is not None:
            holder.stdin.write("\n")
            holder.stdin.flush()
        try:
            holder.wait(timeout=2)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait(timeout=2)
