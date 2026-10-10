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


def test_obsolete_development_projection_requires_explicit_rebuild(tmp_path, monkeypatch, maintenance_cli):
    from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexError
    from powercontext.builtin.records import ArtifactWrite
    from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts

    database = tmp_path / "obsolete-current.db"
    database_config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")
    config = BuiltinConfig(database=database_config)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", database_config.url)

    async def seed():
        async with open_builtin_contexts(config) as contexts:
            await contexts.get("project")
            created = await contexts.records.create_artifact(
                "project", "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": "Retain exact history."})
            )
            memory = contexts.atomic_memory.for_scope("project")
            return await memory.get(created.artifact_id), await memory.search("exact history", mode="text")

    original, baseline_search = asyncio.run(seed())
    with closing(sqlite3.connect(database)) as connection:
        authority = {
            name: connection.execute(f"SELECT * FROM {name}").fetchall()  # noqa: S608 - fixed authority table names
            for name in [
                "pc_artifacts",
                "pc_artifact_heads",
                "pc_atomic_memory_states",
                "pc_access_owners",
                "pc_access_relationships",
            ]
        }
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'pc_atomic_memory_current'"
        ).fetchone()[0]
        columns = [row[1] for row in connection.execute("PRAGMA table_info(pc_atomic_memory_current)")]
        rows = connection.execute("SELECT * FROM pc_atomic_memory_current").fetchall()
        # Reproduce the prior development table: copied grants were required and had no default.
        connection.execute("DROP TABLE pc_atomic_memory_current")
        connection.execute(definition.replace("PRIMARY KEY", "read_grants TEXT NOT NULL, PRIMARY KEY", 1))
        names = ", ".join([*columns, "read_grants"])
        values = ", ".join("?" for _ in [*columns, "read_grants"])
        connection.executemany(
            f"INSERT INTO pc_atomic_memory_current ({names}) VALUES ({values})",  # noqa: S608 - local schema columns
            [(*row, "[]") for row in rows],
        )
        connection.commit()

    async def rejected():
        with pytest.raises(AtomicMemoryIndexError, match="atomic-memory-rebuild-projection --maintenance-confirmed"):
            async with open_builtin_contexts(config):
                pytest.fail("The obsolete current table must not be accepted for normal writes")

    asyncio.run(rejected())
    result = CliRunner().invoke(
        maintenance_cli, ["server", "atomic-memory-rebuild-projection", "--maintenance-confirmed"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ready"] is True
    with closing(sqlite3.connect(database)) as connection:
        for name, retained in authority.items():
            assert connection.execute(f"SELECT * FROM {name}").fetchall() == retained  # noqa: S608 - fixed table names

    async def read_and_write():
        async with open_builtin_contexts(config) as contexts:
            memory = contexts.atomic_memory.for_scope("project")
            assert await memory.get(original.ref.artifact_id, revision=1) == original
            assert await memory.search("exact history", mode="text") == baseline_search
            created = await contexts.records.create_artifact(
                "project", "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": "New searchable fact."})
            )
            result = await memory.search("searchable", mode="text")
            assert [hit.hit.artifact_ref.artifact_id for hit in result.hits] == [created.artifact_id]

    asyncio.run(read_and_write())
