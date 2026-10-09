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

"""Atomic Memory inspection preserves the configured SQLite database."""

import asyncio
import json
import os
import sqlite3
from contextlib import closing

import pytest
from click import unstyle
from typer.testing import CliRunner

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.cli.app import create_cli
from powercontext.server.authz import PrincipalRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.cli import app


@pytest.fixture
def maintenance_cli(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
    return create_cli([app])


@pytest.mark.parametrize("action", ["plan", "verify"])
@pytest.mark.parametrize("uri", [False, True])
def test_inspection_preserves_sqlite_bytes_and_delete_journal_mode(tmp_path, monkeypatch, maintenance_cli, action, uri):
    database = tmp_path / "legacy database%.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE user_data(value TEXT)")
        connection.execute("INSERT INTO user_data VALUES ('retained evidence')")
        connection.commit()
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    original = database.read_bytes()
    url = f"sqlite+aiosqlite:///{database.as_uri()}?mode=rw&uri=true" if uri else f"sqlite+aiosqlite:///{database}"
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", url)

    result = CliRunner().invoke(maintenance_cli, ["server", "atomic-memory-migrate", "--action", action])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ready"] is True
    assert database.read_bytes() == original
    assert set(tmp_path.iterdir()) == {database}
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("SELECT value FROM user_data").fetchone()[0] == "retained evidence"


@pytest.mark.parametrize("action", ["plan", "verify"])
def test_inspection_rejects_missing_sqlite_without_creating_parent(tmp_path, monkeypatch, maintenance_cli, action):
    database = tmp_path / "missing-parent" / "missing.db"
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", f"sqlite+aiosqlite:///{database}")

    result = CliRunner().invoke(maintenance_cli, ["server", "atomic-memory-migrate", "--action", action])

    assert result.exit_code == 2, result.output
    assert "existing SQLite database" in unstyle(result.output)
    assert not database.parent.exists()


@pytest.mark.parametrize("action", ["plan", "verify"])
@pytest.mark.parametrize(
    "url",
    [
        "sqlite+aiosqlite:///:memory:",
        "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
        "sqlite+aiosqlite:///file:named-memory?mode=memory&cache=shared&uri=true",
        "sqlite+aiosqlite:///file:?uri=true",
    ],
)
def test_inspection_rejects_process_and_named_memory_sqlite(tmp_path, monkeypatch, maintenance_cli, action, url):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", url)

    result = CliRunner().invoke(maintenance_cli, ["server", "atomic-memory-migrate", "--action", action])

    assert result.exit_code == 2, result.output
    assert "persistent database" in unstyle(result.output)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("action", ["plan", "verify"])
@pytest.mark.parametrize("uri_options", [None, "immutable=1&nolock=1"])
def test_inspection_reads_committed_legacy_data_from_active_wal(
    tmp_path, monkeypatch, maintenance_cli, action, uri_options
):
    database = tmp_path / "wal.db"
    url = (
        f"sqlite+aiosqlite:///{database.as_uri()}?mode=rw&uri=true&{uri_options}"
        if uri_options
        else f"sqlite+aiosqlite:///{database}"
    )
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", url)
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute(
            "CREATE TABLE pc_artifacts(scope_id TEXT, family TEXT, artifact_id TEXT, revision INTEGER, "
            "content BLOB, memory_citations BLOB)"
        )
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute(
            "INSERT INTO pc_artifacts VALUES (?, ?, ?, ?, ?, ?)",
            ("scope", "memory", "legacy", 1, b"{}", b"[]"),
        )
        writer.commit()
        assert database.with_name("wal.db-wal").stat().st_size > 0

        result = CliRunner().invoke(maintenance_cli, ["server", "atomic-memory-migrate", "--action", action])

        assert result.exit_code == 1, result.output
        report = json.loads(result.output)
        assert report["ready"] is False
        assert any("legacy migration tables are absent" in error for error in report["errors"])
        assert writer.execute("SELECT COUNT(*) FROM pc_artifacts").fetchone()[0] == 1


def test_apply_and_rebuild_retain_writable_sqlite_initialization(tmp_path, monkeypatch, maintenance_cli):
    database = tmp_path / "new-parent" / "maintenance.db"
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    runner = CliRunner()

    applied = runner.invoke(
        maintenance_cli,
        ["server", "atomic-memory-migrate", "--action", "apply", "--maintenance-confirmed"],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["ready"] is True
    assert database.is_file()

    async def initialize_authority():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES):
            pass
        async with open_builtin_access_control(
            config, bootstrap_administrators=(PrincipalRef(type="user", id="owner"),)
        ):
            pass

    asyncio.run(initialize_authority())
    rebuilt = runner.invoke(maintenance_cli, ["server", "atomic-memory-rebuild-projection", "--maintenance-confirmed"])
    assert rebuilt.exit_code == 0, rebuilt.output
    assert json.loads(rebuilt.output)["ready"] is True
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"pc_atomic_memory_states", "pc_atomic_memory_current", "pc_atomic_memory_current_fts"} <= tables
