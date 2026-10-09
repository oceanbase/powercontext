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

"""Recovery point behavior; simulated feature versions do not certify a backend."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import Connection

from powercontext.builtin.persistence.migrations.backup import (
    BackupContext,
    BackupRef,
    ForkBackupProvider,
    ForkWorkflowAcceptance,
    SQLiteBackupProvider,
    identify_fork_server,
)
from powercontext.builtin.persistence.migrations.models import MigrationError, digest


def context(*, writers_stopped: bool = True) -> BackupContext:
    return BackupContext(
        database_id="scratch-database",
        source_revision="p0001",
        bundle_checksum="package-checksum",
        maintenance_window_id="window-1",
        objects=("pc_artifacts",),
        writers_stopped=writers_stopped,
    )


def test_sqlite_online_backup_restores_committed_wal_data_and_keeps_original_point(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    with closing(sqlite3.connect(database)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE pc_artifacts (id INTEGER PRIMARY KEY, content TEXT)")
        writer.execute("INSERT INTO pc_artifacts VALUES (1, 'original')")
        writer.commit()
        assert database.with_name(database.name + "-wal").stat().st_size > 0
        provider = SQLiteBackupProvider(database)
        ref = provider.create_backup(context())
        assert provider.inspect_backup(ref).state == "completed"
        assert provider.inspect_backup(ref).check_level == "integrity_checked"
        assert not provider.inspect_backup(ref).recovery_verified

        writer.execute("UPDATE pc_artifacts SET content='changed'")
        writer.execute("ALTER TABLE pc_artifacts ADD COLUMN changed INTEGER")
        writer.commit()
        restored = tmp_path / "restored.db"
        with closing(sqlite3.connect(ref.location)) as source, closing(sqlite3.connect(restored)) as destination:
            source.backup(destination)
            assert destination.execute("SELECT content FROM pc_artifacts").fetchall() == [("original",)]

        assert provider.create_backup(context()) == ref
        with pytest.raises(MigrationError) as changed_plan:
            provider.create_backup(context().model_copy(update={"bundle_checksum": "different-package"}))
        assert changed_plan.value.code == "backup_failed"
        assert tuple(provider.directory.glob("*.sqlite3")) == (Path(ref.location),)
        second = provider.create_backup(context().model_copy(update={"maintenance_window_id": "window-2"}))
        assert second.location != ref.location
        assert provider.inspect_backup(ref).state == "completed"
        assert provider.restore_plan(ref).automatic is False
        assert Path(ref.location).is_file()


def test_sqlite_rejects_unstopped_writers_before_creating_backup_files(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("CREATE TABLE pc_artifacts (id INTEGER)")
    provider = SQLiteBackupProvider(database)
    with pytest.raises(MigrationError) as error:
        provider.create_backup(context(writers_stopped=False))
    assert error.value.code == "active_writers"
    assert not provider.directory.exists()


def test_sqlite_changed_original_recovery_point_blocks_inspection_and_restore(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("CREATE TABLE pc_artifacts (id INTEGER)")
    provider = SQLiteBackupProvider(database)
    ref = provider.create_backup(context())
    with Path(ref.location).open("ab") as changed:
        changed.write(b"modified")
    assert provider.inspect_backup(ref).state == "failed"
    with pytest.raises(MigrationError) as error:
        provider.restore_plan(ref)
    assert error.value.code == "backup_failed"
    assert Path(ref.location).is_file()


def test_sqlite_missing_original_manifest_blocks_retry_without_replacing_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("CREATE TABLE pc_artifacts (id INTEGER)")
    provider = SQLiteBackupProvider(database)
    ref = provider.create_backup(context())
    original = Path(ref.location).read_bytes()
    (provider.directory / f"{ref.ref_id}.json").unlink()
    with pytest.raises(MigrationError) as error:
        provider.create_backup(context())
    assert error.value.code == "backup_failed"
    assert Path(ref.location).read_bytes() == original


def test_sqlite_invalid_target_reports_unsupported_without_creating_anything(tmp_path: Path) -> None:
    database = tmp_path / "invalid.db"
    database.write_text("not a database", encoding="utf-8")
    provider = SQLiteBackupProvider(database)
    assert not provider.capabilities(context()).available
    with pytest.raises(MigrationError) as error:
        provider.create_backup(context())
    assert error.value.code == "backup_unsupported"
    assert not provider.directory.exists()


@pytest.mark.parametrize(
    ("version", "comment", "product", "expected"),
    [
        ("5.7.25-OceanBase seekdb-v1.4.0.0", "OceanBase 4.3.5.3 seekdb (r1.4.0.0)", "seekdb", (1, 4, 0)),
        ("5.7.25", "OceanBase seekdb (r1.1.0.0)", "seekdb", (1, 1, 0)),
        ("5.7.25-OceanBase_CE-v4.6.2.0", "OceanBase", "oceanbase_ai", (4, 6, 2)),
        ("8.0.36", "MySQL Community Server", None, None),
        ("5.7.25-OceanBase", "OceanBase seekdb", "seekdb", None),
    ],
)
def test_server_identity_uses_product_version_instead_of_mysql_handshake_version(
    version: str, comment: str, product: str | None, expected: tuple[int, int, int] | None
) -> None:
    assert identify_fork_server(version, comment) == (product, expected)


class _ReadResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self.rows

    def one(self) -> tuple[Any, ...]:
        assert len(self.rows) == 1
        return self.rows[0]

    def scalar_one(self) -> Any:
        return self.one()[0]


class _ReadOnlyServer:
    """Simulated server responses exercise refusal, not successful Fork support."""

    class dialect:
        name = "oceanbase"

    def __init__(self, version: str, comment: str) -> None:
        self.version = version
        self.comment = comment
        self.queries: list[str] = []

    def exec_driver_sql(self, query: str) -> _ReadResult:
        self.queries.append(query)
        if query == "SELECT VERSION()":
            return _ReadResult([(self.version,)])
        if query == "SELECT @@version_comment":
            return _ReadResult([(self.comment,)])
        if query == "SELECT DATABASE()":
            return _ReadResult([("test",)])
        if query == "SHOW VARIABLES LIKE 'ob_compatibility_mode'":
            return _ReadResult([("ob_compatibility_mode", "MYSQL")])
        if query.startswith("SHOW CREATE TABLE"):
            return _ReadResult([("pc_artifacts", "CREATE TABLE pc_artifacts (id INT PRIMARY KEY)")])
        raise AssertionError(query)

    def execute(self, query: object, parameters: object) -> _ReadResult:
        sql = str(query)
        self.queries.append(sql)
        if "TABLE_NAME, TABLE_TYPE" in sql:
            return _ReadResult([("pc_artifacts", "BASE TABLE")])
        if "COUNT(*)" in sql:
            return _ReadResult([(0,)])
        raise AssertionError(sql)


@pytest.mark.parametrize(
    ("product", "version", "table_available", "database_available"),
    [
        ("seekdb", "1.0.0", False, False),
        ("seekdb", "1.1.0", True, False),
        ("seekdb", "1.2.0", True, True),
        ("oceanbase_ai", "4.6.1", False, False),
        ("oceanbase_ai", "4.6.2", True, True),
    ],
)
def test_product_specific_fork_minimum_never_enables_unaccepted_recovery(
    tmp_path: Path, product: str, version: str, table_available: bool, database_available: bool
) -> None:
    marker = "seekdb" if product == "seekdb" else "OceanBase_CE"
    server = _ReadOnlyServer(f"5.7.25-{marker}-v{version}", marker)
    provider = ForkBackupProvider(
        cast(Connection, server),
        database_name="test",
        expected_product=cast(Any, product),
        directory=tmp_path / "manifests",
    )
    capability = provider.capabilities(context())
    assert capability.table_fork_available == table_available
    assert capability.database_fork_available == database_available
    assert not capability.available
    with pytest.raises(MigrationError) as error:
        provider.create_backup(context())
    assert error.value.code == "backup_unsupported"
    assert all(query.startswith(("SELECT", "SHOW")) for query in server.queries)
    assert not provider.directory.exists()


@pytest.mark.skipif(os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1", reason="real seekdb probe not enabled")
def test_real_seekdb_reports_engine_fork_version_without_claiming_recovery_acceptance(short_tmp_path: Path) -> None:
    from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile

    async def scenario() -> None:
        async with (
            SeekDBProfile.open(SeekDBConfig(path=short_tmp_path / "seekdb"), tables=()) as profile,
            profile.database.engine.connect() as connection,
        ):
            await connection.exec_driver_sql("CREATE TABLE pc_artifacts (id INT PRIMARY KEY)")
            await connection.commit()

            def inspect_provider(sync: Connection) -> None:
                provider = ForkBackupProvider(
                    sync,
                    database_name="test",
                    expected_product="seekdb",
                    directory=short_tmp_path / "manifests",
                )
                capability = provider.capabilities(context())
                assert capability.product == "seekdb"
                assert capability.server_version is not None
                assert not capability.available
                with pytest.raises(MigrationError) as error:
                    provider.create_backup(context())
                assert error.value.code == "backup_unsupported"
                assert not provider.directory.exists()

            await connection.run_sync(inspect_provider)

    asyncio.run(scenario())


@pytest.mark.skipif(os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1", reason="real seekdb probe not enabled")
def test_real_seekdb_fork_simple_table_survives_ddl_restart_and_original_name_restore(short_tmp_path: Path) -> None:
    """Acceptance for one scratch-table shape, not the complete PC schema."""
    from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile

    async def scenario() -> None:
        path = short_tmp_path / "seekdb"
        config = SeekDBConfig(path=path)
        async with (
            SeekDBProfile.open(config, tables=()) as profile,
            profile.database.engine.connect() as connection,
        ):
            await connection.exec_driver_sql("CREATE TABLE pc_artifacts (id INT PRIMARY KEY, content VARCHAR(40))")
            await connection.exec_driver_sql("INSERT INTO pc_artifacts VALUES (1, 'original')")
            await connection.commit()
            version = str((await connection.exec_driver_sql("SELECT VERSION()")).scalar_one())
            comment = str((await connection.exec_driver_sql("SELECT @@version_comment")).scalar_one())
            number = identify_fork_server(version, comment)[1]
            assert number is not None
            definition = str((await connection.exec_driver_sql("SHOW CREATE TABLE test.pc_artifacts")).one()[1])
            # This test performs every claimed DDL/restart/restore check below.
            # It does not install this evidence in any production adapter.
            acceptance = ForkWorkflowAcceptance(
                product="seekdb",
                server_version=".".join(str(part) for part in number),
                server_identity_digest=digest([version, comment]),
                method="fork_database",
                schema_fingerprint=digest({"pc_artifacts": definition}),
                bundle_checksum=context().bundle_checksum,
                covered_objects=("pc_artifacts",),
                evidence_reference="scratch-table acceptance executed by this integration test",
                restore_steps=(
                    "While stopped, Fork the retained table into a new table in the original test database.",
                    "Rename the current table to a retained name, then atomically give the restored table its original name.",
                    "Verify original data and revision; preserve the original Fork database and failed table.",
                ),
                ddl_verified=True,
                restart_verified=True,
                restore_to_original_database=True,
                events_not_supported=True,
            )

            def create_provider_backup(sync: Connection) -> BackupRef:
                provider = ForkBackupProvider(
                    sync,
                    database_name="test",
                    expected_product="seekdb",
                    directory=short_tmp_path / "manifests",
                    acceptance=acceptance,
                )
                assert provider.capabilities(context()).available
                return provider.create_backup(context())

            ref = await connection.run_sync(create_provider_backup)
            await connection.exec_driver_sql("ALTER TABLE pc_artifacts ADD COLUMN changed INT")
            await connection.exec_driver_sql("UPDATE pc_artifacts SET content='changed'")
            await connection.commit()

        async with (
            SeekDBProfile.open(config, tables=()) as reopened,
            reopened.database.engine.connect() as connection,
        ):

            def inspect_provider_backup(sync: Connection) -> None:
                provider = ForkBackupProvider(
                    sync,
                    database_name="test",
                    expected_product="seekdb",
                    directory=short_tmp_path / "manifests",
                    acceptance=acceptance,
                )
                assert provider.inspect_backup(ref).state == "completed"
                assert provider.inspect_backup(ref).check_level == "metadata_checked"
                assert not provider.inspect_backup(ref).recovery_verified
                assert not provider.restore_plan(ref).automatic
                # Source DDL and process restart must not replace the original
                # point if the executor lost its own post-backup receipt.
                assert provider.create_backup(context()) == ref

            await connection.run_sync(inspect_provider_backup)
            # The generated name is hexadecimal; it is not user SQL input.
            await connection.exec_driver_sql(f"FORK TABLE `{ref.location}`.`pc_artifacts` TO `test`.`pc_restore_probe`")
            await connection.exec_driver_sql(
                "RENAME TABLE test.pc_artifacts TO test.pc_failed_retained,test.pc_restore_probe TO test.pc_artifacts"
            )
            await connection.commit()
            assert (await connection.exec_driver_sql("SELECT id,content FROM test.pc_artifacts")).all() == [
                (1, "original")
            ]
            assert (await connection.exec_driver_sql("SELECT content FROM test.pc_failed_retained")).all() == [
                ("changed",)
            ]
            assert (
                await connection.exec_driver_sql(
                    f"SELECT id,content FROM `{ref.location}`.`pc_artifacts`"  # noqa: S608 -- generated hexadecimal name
                )
            ).all() == [(1, "original")]

    asyncio.run(scenario())


def test_sqlite_backup_rejects_unrecorded_wal_even_when_main_checksum_matches(tmp_path: Path) -> None:
    import hashlib
    import subprocess
    import sys

    database = tmp_path / "source.db"
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("CREATE TABLE pc_artifacts (id INTEGER)")
    provider = SQLiteBackupProvider(database)
    ref = provider.create_backup(context())
    # Simulate an older WAL-mode backup and record its main-file checksum.
    with closing(sqlite3.connect(ref.location)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
    ref = ref.model_copy(update={"checksum": hashlib.sha256(Path(ref.location).read_bytes()).hexdigest()})
    (provider.directory / f"{ref.ref_id}.json").write_text(ref.model_dump_json())
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
            "c.execute('INSERT INTO pc_artifacts VALUES (7)'); c.commit(); os._exit(0)",
            ref.location,
        ],
        check=True,
    )
    assert hashlib.sha256(Path(ref.location).read_bytes()).hexdigest() == ref.checksum
    assert provider.inspect_backup(ref).state == "failed"
    with pytest.raises(MigrationError, match="backup_failed"):
        provider.restore_plan(ref)
