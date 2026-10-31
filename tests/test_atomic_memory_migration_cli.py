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

from powercontext.builtin.persistence.bootstrap import MissingDatabaseError
from powercontext.builtin.persistence.seekdb import LegacySeekDBDatabaseError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.cli.app import create_cli
from powercontext.server import cli as server_cli
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


@pytest.mark.parametrize("action", ["plan", "apply", "verify"])
@pytest.mark.parametrize("uri", [False, True])
@pytest.mark.parametrize("access_mode", ["disabled", "enforced"])
def test_snapshot_budget_rejection_preserves_existing_database(
    tmp_path, monkeypatch, maintenance_cli, action, uri, access_mode
):
    from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
    from powercontext.builtin.sources.content import ContentSource
    from powercontext.sources import SourceMaterialization

    database = tmp_path / "snapshot budget%.db"

    async def seed():
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
        ) as contexts:
            await contexts.get("scope")
            async with contexts.database.transaction() as connection:
                for index in range(8):
                    await contexts.repositories.sources.add(
                        connection,
                        "scope",
                        ContentSource(
                            name=f"source-{index}",
                            content="x" * 65536,
                            materialization=SourceMaterialization.CAPTURED,
                        ),
                    )

    asyncio.run(seed())
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"
    original = database.read_bytes()
    url = f"sqlite+aiosqlite:///{database.as_uri()}?mode=rw&uri=true" if uri else f"sqlite+aiosqlite:///{database}"
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", url)
    monkeypatch.setenv("POWERCONTEXT_SERVER_ACCESS_MODE", access_mode)
    arguments = ["server", "atomic-memory-migrate", "--action", action]
    if action == "apply":
        arguments.append("--maintenance-confirmed")

    result = CliRunner().invoke(maintenance_cli, [*arguments, "--snapshot-budget-mib", "1"])

    assert result.exit_code == 1, result.output
    report = json.loads(result.output)
    assert report["ready"] is False
    assert report["processing_snapshot_hash"] is None
    assert any("snapshot" in error and "budget" in error for error in report["errors"])
    assert database.read_bytes() == original
    assert set(tmp_path.iterdir()) == {database}
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("SELECT COUNT(*) FROM pc_sources").fetchone()[0] == 8

    retried = CliRunner().invoke(maintenance_cli, [*arguments, "--snapshot-budget-mib", "8"])
    assert retried.exit_code == 0, retried.output
    accepted = json.loads(retried.output)
    assert accepted["ready"] is True
    assert accepted["processing_snapshot_hash"] is not None


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
        assert {"pc_atomic_memory_current", "pc_atomic_memory_current_fts"} <= tables
        assert "pc_atomic_memory_states" not in tables


def test_owner_decision_file_is_loaded_and_unknown_entry_is_rejected(tmp_path, monkeypatch, maintenance_cli):
    database = tmp_path / "maintenance.db"
    with closing(sqlite3.connect(database)):
        pass
    decisions = tmp_path / "owners.json"
    decisions.write_text(
        json.dumps({
            "format": "powercontext.atomic-memory-owner-decisions.v1",
            "decisions": [
                {
                    "scope_id": "scope-a",
                    "memory_id": "memory-a",
                    "entry_id": "entry-a",
                    "action": "establish",
                    "owner": {"type": "user", "id": "owner-a"},
                    "idempotency_key": "recover-owner:entry-a",
                }
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", f"sqlite+aiosqlite:///{database}")

    result = CliRunner().invoke(
        maintenance_cli,
        ["server", "atomic-memory-migrate", "--action", "plan", "--owner-decisions", str(decisions)],
    )

    assert result.exit_code == 1, result.output
    assert "owner decision targets unknown legacy entry scope-a/memory-a/entry-a" in result.output


def test_rebuild_upgrades_old_revision_schema_without_backfilling_history(tmp_path, monkeypatch, maintenance_cli):
    from powercontext.builtin.records import ArtifactWrite
    from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts

    database = tmp_path / "old-revisions.db"
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
    metadata_columns = ("created_at", "created_by", "restored_from_revision", "rollback_reason")
    with closing(sqlite3.connect(database)) as connection:
        # Reproduce storage written before revision metadata, with a projection needing repair.
        for column in metadata_columns:
            connection.execute(f"ALTER TABLE pc_artifacts DROP COLUMN {column}")
        connection.execute("DELETE FROM pc_atomic_memory_current")
        connection.commit()

    runner = CliRunner()
    unconfirmed = runner.invoke(maintenance_cli, ["server", "atomic-memory-rebuild-projection"])
    assert unconfirmed.exit_code == 2, unconfirmed.output
    assert "--maintenance-confirmed" in unstyle(unconfirmed.output)
    for action in ("plan", "verify"):
        inspected = runner.invoke(maintenance_cli, ["server", "atomic-memory-migrate", "--action", action])
        assert inspected.exit_code == 0, inspected.output
    with closing(sqlite3.connect(database)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(pc_artifacts)")}
        assert not columns.intersection(metadata_columns)

    # Maintenance must work before ordinary startup, including a repeated run.
    for _ in range(2):
        rebuilt = runner.invoke(
            maintenance_cli, ["server", "atomic-memory-rebuild-projection", "--maintenance-confirmed"]
        )
        assert rebuilt.exit_code == 0, rebuilt.exception or rebuilt.output
        report = json.loads(rebuilt.output)
        assert report["ready"] is True
        assert report["counts"]["rebuilt_rows"] == 1
    with closing(sqlite3.connect(database)) as connection:
        metadata = connection.execute(
            "SELECT created_at, created_by, restored_from_revision, rollback_reason "
            "FROM pc_artifacts WHERE family = 'atomic-memory'"
        ).fetchall()
        assert metadata == [(None, None, None, None)]

    async def read_and_search():
        async with open_builtin_contexts(config) as contexts:
            memory = contexts.atomic_memory.for_scope("project")
            assert await memory.get(original.ref.artifact_id, revision=1) == original
            assert await memory.search("exact history", mode="text") == baseline_search

    asyncio.run(read_and_search())


@pytest.mark.parametrize("action", ["plan", "verify"])
def test_inspection_rejects_unreleased_atomic_state_table_without_writes(
    tmp_path, monkeypatch, maintenance_cli, action
):
    database = tmp_path / "unreleased.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE pc_atomic_memory_states(artifact_id TEXT, state TEXT)")
        connection.execute("INSERT INTO pc_atomic_memory_states VALUES ('retained', 'merged')")
        connection.commit()
    original = database.read_bytes()
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", f"sqlite+aiosqlite:///{database}")

    result = CliRunner().invoke(maintenance_cli, ["server", "atomic-memory-migrate", "--action", action])

    assert result.exit_code == 1, result.output
    report = json.loads(result.output)
    assert report["ready"] is False
    assert any("unsupported unreleased" in error and "pc_atomic_memory_states" in error for error in report["errors"])
    assert database.read_bytes() == original


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


class _ReadOnlyInspection:
    """A backend profile whose read-only opener refuses to provision the database."""

    @classmethod
    def open(cls, config, *, tables):
        raise AssertionError("read-only inspection must not use the provisioning opener")  # noqa: TRY003

    @classmethod
    def open_existing(cls, config, *, tables):
        raise MissingDatabaseError("configured_db")


@pytest.mark.parametrize("action", ["plan", "verify"])
@pytest.mark.parametrize("backend", ["oceanbase", "seekdb"])
def test_inspection_never_provisions_the_configured_database(tmp_path, monkeypatch, maintenance_cli, action, backend):
    # Exercise SDK upkeep after the separate Server ownership check.
    monkeypatch.setattr(server_cli, "_managed_storage", lambda _database, **_kwargs: False)
    monkeypatch.setattr(server_cli, "OceanBaseProfile", _ReadOnlyInspection)
    monkeypatch.setattr(server_cli, "SeekDBProfile", _ReadOnlyInspection)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", backend)
    if backend == "oceanbase":
        monkeypatch.setenv(
            "POWERCONTEXT_SERVER_DATABASE_URL",
            "mysql+aoceanbase://user:password@host:2881/configured_db?charset=utf8mb4",
        )
    else:
        monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_PATH", str(tmp_path / "seekdb"))

    result = CliRunner().invoke(maintenance_cli, ["server", "atomic-memory-migrate", "--action", action])

    assert result.exit_code == 2, result.output
    assert "require an existing database" in unstyle(result.output)
    assert "configured_db" in unstyle(result.output)


@pytest.mark.parametrize("action", ["plan", "verify"])
@pytest.mark.parametrize("target_state", ["missing", "empty", "file", "unrecognized"])
def test_inspection_preserves_uninitialized_seekdb_target(tmp_path, monkeypatch, maintenance_cli, action, target_state):
    path = tmp_path / "missing-parent" / "seekdb"
    if target_state == "empty":
        path.mkdir(parents=True)
    elif target_state == "file":
        path.parent.mkdir()
        path.write_bytes(b"retained evidence")
    elif target_state == "unrecognized":
        path.mkdir(parents=True)
        (path / "user-content.txt").write_bytes(b"retained evidence")
    original = {
        item.relative_to(tmp_path): item.read_bytes() if item.is_file() else None for item in tmp_path.rglob("*")
    }

    def unexpected_binding():
        pytest.fail("inspection must reject an uninitialized target before starting the native engine")

    monkeypatch.setattr("powercontext.builtin.persistence.seekdb.profile._load_binding", unexpected_binding)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "seekdb")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_PATH", str(path))
    result = CliRunner().invoke(maintenance_cli, ["server", "atomic-memory-migrate", "--action", action])

    if target_state in {"file", "unrecognized"}:
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["error"] == "unsupported_target"
    else:
        assert result.exit_code == 2, result.output
        assert "existing seekdb instance" in unstyle(result.output)
    # Shared admission retains its coordination lock beside the target. It must
    # not initialize native storage or change any existing target contents.
    lock = path.with_name(path.name + ".pc-migration.lock")
    assert {
        item.relative_to(tmp_path): item.read_bytes() if item.is_file() else None
        for item in tmp_path.rglob("*")
        if item != lock
    } == original


@pytest.mark.parametrize(
    ("command", "action"),
    [
        ("atomic-memory-migrate", "plan"),
        ("atomic-memory-migrate", "verify"),
        ("atomic-memory-migrate", "apply"),
        ("atomic-memory-rebuild-projection", None),
    ],
)
def test_legacy_seekdb_requires_an_explicit_database_choice(tmp_path, monkeypatch, maintenance_cli, command, action):
    class _LegacyRefusal:
        @classmethod
        def open(cls, config, *, tables):
            raise LegacySeekDBDatabaseError(config.path)

        @classmethod
        def open_existing(cls, config, *, tables):
            raise LegacySeekDBDatabaseError(config.path)

    monkeypatch.setattr(server_cli, "SeekDBProfile", _LegacyRefusal)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "seekdb")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_PATH", str(tmp_path / "seekdb"))

    arguments = ["server", command, "--maintenance-confirmed"]
    if action is not None:
        arguments.extend(["--action", action])
    result = CliRunner().invoke(maintenance_cli, arguments)

    assert result.exit_code == 2, result.output
    assert "POWERCONTEXT_SERVER_DATABASE_DATABASE" in unstyle(result.output)
    assert "Traceback" not in result.output
