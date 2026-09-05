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

import pytest
from pydantic import SecretStr

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.cursor_secret import resolve_cursor_secret


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
