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

import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from pydantic import SecretStr

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.cursor_secret import _creation_lock, resolve_cursor_secret

# Starter A reads the key (missing) and pauses after that read; starter B then
# runs to completion. A real second process is used because the window under
# test is the one between A's read and the cleanup that follows it, and holding
# two starters in one process would also hold one lock twice.
_STARTER_SCRIPT = """
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[4])
import powercontext.server.cursor_secret as cursor_secret

mode = sys.argv[1]
key_path = Path(sys.argv[2])
ready = Path(sys.argv[3])


def load():
    return cursor_secret._load_or_create(key_path).hex()


if mode == "a":
    real_read = cursor_secret._read
    state = {"first": True}

    def paused_read(path):
        secret = real_read(path)
        if state["first"]:
            state["first"] = False
            print("paused", flush=True)
            deadline = time.monotonic() + 30
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(1.5)
        return secret

    cursor_secret._read = paused_read
    print(load(), flush=True)
else:
    ready.write_text("1")
    print(load(), flush=True)
"""

# The holder locks byte 0 of an otherwise empty lock file and keeps that lock
# until the test releases it. A second process is required because Windows
# byte-range locks are enforced between processes, not within one.
_LOCK_HOLDER_SCRIPT = """
import msvcrt
import os
import sys
import time
from pathlib import Path

lock_path = Path(sys.argv[1])
ready = Path(sys.argv[2])
release = Path(sys.argv[3])

descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
os.lseek(descriptor, 0, os.SEEK_SET)
msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
ready.write_text("1")
deadline = time.monotonic() + 30
while not release.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
os.close(descriptor)
"""


@pytest.fixture
def generated_secret(monkeypatch) -> bytes:
    # Include LF and other control bytes so Windows text translation cannot hide
    # behind a randomly generated key that happens not to contain a newline.
    secret = bytes(range(32))
    monkeypatch.setattr("powercontext.server.cursor_secret.secrets.token_bytes", lambda size: secret[:size])
    return secret


def test_file_backed_sqlite_reuses_private_cursor_secret(tmp_path, generated_secret) -> None:
    database_path = tmp_path / "powercontext.db"
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}")

    first = resolve_cursor_secret(config, None)
    second = resolve_cursor_secret(config, None)

    key_path = tmp_path / ".powercontext.db.cursor-key"
    assert first == second == generated_secret
    assert key_path.read_bytes() == generated_secret
    if os.name != "nt":
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_explicit_cursor_secret_overrides_database_storage(tmp_path) -> None:
    database_path = tmp_path / "powercontext.db"
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}")

    secret = resolve_cursor_secret(config, "configured-secret-with-at-least-32-bytes")

    assert secret == b"configured-secret-with-at-least-32-bytes"
    assert not (tmp_path / ".powercontext.db.cursor-key").exists()


def test_remote_database_without_override_reuses_local_persisted_secret(
    tmp_path, monkeypatch, generated_secret
) -> None:
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path))
    config = OceanBaseConfig(url=SecretStr("mysql+aoceanbase://user:password@127.0.0.1:2881/test?charset=utf8mb4"))

    first = resolve_cursor_secret(config, None)
    second = resolve_cursor_secret(config, None)

    key_path = tmp_path / "cursor-signing.key"
    assert first == second == generated_secret
    assert key_path.read_bytes() == generated_secret
    if os.name != "nt":
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_transient_short_key_write_completes_the_key(tmp_path, monkeypatch, generated_secret) -> None:
    database_path = tmp_path / "powercontext.db"
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}")
    key_path = tmp_path / ".powercontext.db.cursor-key"
    real_write = os.write

    # POSIX allows write() to accept fewer bytes than requested. Accept five
    # bytes once, then accept the rest, the way a resumed write would.
    seen_short_write = {"done": False}

    def limited_write(descriptor: int, data: bytes) -> int:
        payload = bytes(data)
        if len(payload) < 32:
            # The creation lock writes a one-byte marker on Windows; only the
            # key write itself is under test here.
            return real_write(descriptor, payload)
        if seen_short_write["done"]:
            return real_write(descriptor, payload)
        seen_short_write["done"] = True
        return real_write(descriptor, payload[:5])

    with monkeypatch.context() as patch:
        patch.setattr(os, "write", limited_write)
        secret = resolve_cursor_secret(config, None)

    assert secret == generated_secret
    assert key_path.read_bytes() == generated_secret
    assert resolve_cursor_secret(config, None) == generated_secret


def test_failed_key_write_leaves_no_unreadable_key_behind(tmp_path, monkeypatch, generated_secret) -> None:
    database_path = tmp_path / "powercontext.db"
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}")
    key_path = tmp_path / ".powercontext.db.cursor-key"
    real_write = os.write
    retries = {"count": 0}

    def stalled_write(descriptor: int, data: bytes) -> int:
        payload = bytes(data)
        if len(payload) < 32:
            return real_write(descriptor, payload)
        retries["count"] += 1
        if retries["count"] > 64:
            raise AssertionError("a stalled write was retried instead of rejected")  # noqa: TRY003
        real_write(descriptor, payload[:3])
        return 0

    with monkeypatch.context() as patch:
        patch.setattr(os, "write", stalled_write)
        with pytest.raises(OSError):
            resolve_cursor_secret(config, None)

    # A failed attempt must not publish a key it never finished writing; the
    # next start still has to be able to create one.
    assert not key_path.exists()
    assert resolve_cursor_secret(config, None) == generated_secret


def test_key_write_without_progress_fails_instead_of_retrying(tmp_path, monkeypatch, generated_secret) -> None:
    database_path = tmp_path / "powercontext.db"
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}")
    real_write = os.write
    calls = {"count": 0}

    # Accept nothing on the first attempt. Treating a zero-byte write as
    # progress would loop forever, and a later write that does succeed must not
    # hide that the first one stalled.
    def stalled_write(descriptor: int, data: bytes) -> int:
        payload = bytes(data)
        if len(payload) < 32:
            return real_write(descriptor, payload)
        calls["count"] += 1
        if calls["count"] > 64:
            raise AssertionError("a zero-byte write was retried instead of rejected")  # noqa: TRY003
        if calls["count"] == 1:
            return 0
        return real_write(descriptor, payload)

    with monkeypatch.context() as patch:
        patch.setattr(os, "write", stalled_write)
        with pytest.raises(OSError):
            resolve_cursor_secret(config, None)


@pytest.mark.parametrize("length", [0, 5, 31])
def test_key_left_by_an_interrupted_creation_is_replaced(tmp_path, generated_secret, length) -> None:
    database_path = tmp_path / "powercontext.db"
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}")
    key_path = tmp_path / ".powercontext.db.cursor-key"

    # A process killed between os.open and os.write leaves a key of the wrong
    # length and never returns it, so no cursor was signed with it.
    key_path.write_bytes(b"\x00" * length)

    secret = resolve_cursor_secret(config, None)

    assert secret == generated_secret
    assert key_path.read_bytes() == generated_secret
    assert resolve_cursor_secret(config, None) == generated_secret


def test_existing_complete_key_is_never_regenerated(tmp_path, generated_secret) -> None:
    database_path = tmp_path / "powercontext.db"
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}")
    key_path = tmp_path / ".powercontext.db.cursor-key"
    existing = b"k" * 32

    key_path.write_bytes(existing)

    assert resolve_cursor_secret(config, None) == existing
    assert key_path.read_bytes() == existing


def test_starters_that_race_on_a_missing_key_agree_on_the_persisted_one(tmp_path) -> None:
    key_path = tmp_path / ".powercontext.db.cursor-key"
    ready = tmp_path / "b-ready"
    script = tmp_path / "starter.py"
    script.write_text(_STARTER_SCRIPT, encoding="utf-8")
    source = str(Path(__file__).resolve().parents[1] / "src")

    def start(mode: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, str(script), mode, str(key_path), str(ready), source],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    # A must be paused between its missing-key read and the cleanup that
    # follows; only then does B get to publish a complete key in that window.
    first = start("a")
    processes = [first]
    try:
        assert first.stdout is not None
        assert first.stdout.readline().strip() == "paused"
        second = start("b")
        processes.append(second)
        second_out, second_err = second.communicate(timeout=90)
        first_out, first_err = first.communicate(timeout=90)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()

    assert first.returncode == 0, first_err
    assert second.returncode == 0, second_err
    keys = {first_out.strip().splitlines()[-1], second_out.strip().splitlines()[-1]}
    assert len(keys) == 1, "starters published different keys; one of them lost cursors signed with it"
    assert key_path.read_bytes().hex() in keys


@pytest.mark.skipif(os.name != "nt", reason="only Windows rejects a write into a locked byte range")
def test_lock_byte_is_not_seeded_while_another_starter_holds_it(tmp_path) -> None:
    key_path = tmp_path / ".powercontext.db.cursor-key"
    lock_path = key_path.with_name(f"{key_path.name}.lock")
    ready = tmp_path / "holder-ready"
    release = tmp_path / "holder-release"
    script = tmp_path / "lock-holder.py"
    script.write_text(_LOCK_HOLDER_SCRIPT, encoding="utf-8")

    holder = subprocess.Popen(
        [sys.executable, str(script), str(lock_path), str(ready), str(release)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "the helper process never took the lock"

        # Entering the critical section while another starter holds byte 0 is the
        # window under test: seeding the file first writes into a byte Windows has
        # locked, and that error escapes before the retry loop.
        timer = threading.Timer(1.0, release.write_text, args=("1",))
        timer.start()
        started = time.monotonic()
        try:
            with _creation_lock(key_path):
                pass
        finally:
            timer.cancel()
            release.write_text("1", encoding="utf-8")
        # A lock that excludes nothing would let this return immediately while the
        # holder still owns byte 0, so the wait itself is part of the contract.
        assert time.monotonic() - started >= 0.5
        _, holder_err = holder.communicate(timeout=60)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.communicate()

    assert holder.returncode == 0, holder_err
