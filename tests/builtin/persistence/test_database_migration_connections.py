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

"""Maintenance owns its backend lifetime without creating business schema."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import Connection, event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from powercontext.builtin.persistence import dream_schema
from powercontext.builtin.persistence.bootstrap import MissingDatabaseError
from powercontext.builtin.persistence.migrations import connections
from powercontext.builtin.persistence.migrations.connections import BackendIdentity, MaintenanceConnections
from powercontext.builtin.persistence.migrations.locking import local_migration_lock
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig


def test_missing_seekdb_inspection_creates_no_directory_or_database(tmp_path: Path) -> None:
    path = tmp_path / "missing-parent" / "database"

    def inspect(connection: Connection | None, identity: BackendIdentity, verify: Callable[[], None]):
        assert connection is None
        assert identity.product == "seekdb"
        assert identity.database_name == "powercontext"
        verify()
        return identity

    identity = MaintenanceConnections(SeekDBConfig(path=path)).run(inspect)
    assert len(identity.database_id) == 64
    assert not path.parent.exists()


def test_empty_seekdb_inspection_does_not_initialize_the_directory(tmp_path: Path) -> None:
    path = tmp_path / "empty-database"
    path.mkdir()
    assert (
        MaintenanceConnections(SeekDBConfig(path=path)).run(lambda connection, _identity, _verify: connection) is None
    )
    assert list(path.iterdir()) == []


@pytest.mark.parametrize("writable", [False, True], ids=["inspect", "apply"])
def test_unknown_nonempty_seekdb_directory_is_not_initialized(tmp_path: Path, writable: bool) -> None:
    path = tmp_path / "unrelated-directory"
    path.mkdir()
    existing = path / "user-content.txt"
    existing.write_text("preserve", encoding="utf-8")
    operation = Mock()
    with pytest.raises(MigrationError) as error:
        MaintenanceConnections(SeekDBConfig(path=path)).run(operation, writable=writable)
    assert error.value.code == "unsupported_target"
    operation.assert_not_called()
    assert list(path.iterdir()) == [existing]
    assert existing.read_text(encoding="utf-8") == "preserve"


def test_seekdb_startup_failure_is_safe_to_report_and_releases_the_lock(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "seekdb"
    private_diagnostic = "private engine diagnostic"

    async def fail_open(_path: str):
        raise RuntimeError(private_diagnostic)

    monkeypatch.setattr(connections.seekdb_profile, "_load_binding", lambda: SimpleNamespace(aopen=fail_open))
    operation = Mock()
    with pytest.raises(MigrationError) as error:
        MaintenanceConnections(SeekDBConfig(path=path)).run(operation, writable=True)
    assert error.value.code == "backend_open_failed"
    assert "private" not in str(error.value)
    operation.assert_not_called()
    with local_migration_lock(path):
        pass


def _fake_seekdb(tmp_path: Path, monkeypatch):
    path = tmp_path / "seekdb"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'maintenance.db'}")
    disposed = False

    def assert_locked() -> None:
        with pytest.raises(MigrationError) as error, local_migration_lock(path):
            pytest.fail("A second maintenance operation acquired the engine directory lock")
        assert error.value.code == "migration_locked"

    @event.listens_for(engine.sync_engine, "connect")
    def configure_database(connection, _record) -> None:
        connection.create_function("DATABASE", 0, lambda: "powercontext")

    @event.listens_for(engine.sync_engine, "before_cursor_execute", retval=True)
    def translate_transaction(_connection, _cursor, statement, parameters, _context, _many):
        # This lifecycle double uses SQLite while the real adapter speaks MySQL.
        return ("BEGIN" if statement == "START TRANSACTION" else statement), parameters

    class Instance:
        def connection_options(self):
            return {}

        def close(self) -> None:
            assert disposed
            assert_locked()

    async def open_instance(_path: str):
        assert_locked()
        path.mkdir(exist_ok=True)
        return Instance()

    dispose = engine.dispose

    async def dispose_engine() -> None:
        nonlocal disposed
        assert_locked()
        await dispose()
        disposed = True

    maintained_engine = SimpleNamespace(connect=engine.connect, dispose=dispose_engine, dialect=engine.dialect)
    monkeypatch.setattr(connections.seekdb_profile, "_load_binding", lambda: SimpleNamespace(aopen=open_instance))
    monkeypatch.setattr(connections.seekdb_profile, "_create_engine", lambda _config, _options: maintained_engine)
    return path, assert_locked


def test_seekdb_lock_outlives_callback_failure_and_engine_shutdown(tmp_path: Path, monkeypatch) -> None:
    path, assert_locked = _fake_seekdb(tmp_path, monkeypatch)

    def fail(connection: Connection | None, _identity: BackendIdentity, verify: Callable[[], None]):
        assert connection is not None
        assert_locked()
        verify()
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        MaintenanceConnections(SeekDBConfig(path=path, database="powercontext")).run(fail, writable=True)
    with local_migration_lock(path):
        pass


def test_seekdb_directory_replacement_is_rejected_before_success(tmp_path: Path, monkeypatch) -> None:
    path, _assert_locked = _fake_seekdb(tmp_path, monkeypatch)

    def replace(connection: Connection | None, _identity: BackendIdentity, verify: Callable[[], None]):
        assert connection is not None
        verify()
        path.rename(tmp_path / "original-directory")
        path.mkdir()

    with pytest.raises(MigrationError) as error:
        MaintenanceConnections(SeekDBConfig(path=path, database="powercontext")).run(replace, writable=True)
    assert error.value.code == "migration_lock_lost"
    with local_migration_lock(path):
        pass


class _RemoteSession:
    def __init__(self, cluster_id: int) -> None:
        self.connection = Mock(spec=Connection)

        def query(statement: str):
            result = Mock()
            if "ob_compatibility_mode" in statement:
                result.first.return_value = ("ob_compatibility_mode", "MYSQL")
            elif "SELECT DATABASE()" in statement:
                result.one.return_value = ("scratch", 1002)
            elif "GV$OB_PARAMETERS" in statement:
                result.scalars.return_value.all.return_value = [str(cluster_id)]
            elif "DBA_OB_TENANTS" in statement:
                result.one.return_value = (datetime(2026, 1, 1, tzinfo=UTC), "USER")
            else:
                pytest.fail(f"Unexpected identity query: {statement}")
            return result

        self.connection.exec_driver_sql.side_effect = query

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def run_sync(self, operation):
        return operation(self.connection)


def test_oceanbase_write_requires_an_explicit_single_host_coordinator() -> None:
    config = OceanBaseConfig.model_validate({
        "url": "mysql+aoceanbase://probe:secret@example.invalid:2881/scratch?charset=utf8mb4"
    })
    operation = Mock()
    with pytest.raises(MigrationError) as error:
        MaintenanceConnections(config).run(operation, writable=True)
    assert error.value.code == "coordination_required"
    operation.assert_not_called()


def test_oceanbase_routes_to_different_database_identity_before_write_are_rejected(tmp_path: Path, monkeypatch) -> None:
    sessions = iter((_RemoteSession(1), _RemoteSession(2)))
    engine = Mock()
    engine.connect.side_effect = lambda: next(sessions)

    async def dispose() -> None:
        pass

    engine.dispose = dispose
    monkeypatch.setattr(connections, "create_async_engine", lambda *_args, **_options: engine)
    operation = Mock()
    config = OceanBaseConfig.model_validate({
        "url": "mysql+aoceanbase://private-user:private-password@private-route:2881/scratch?charset=utf8mb4"
    })
    with pytest.raises(MigrationError) as error:
        MaintenanceConnections(config, evidence_directory=tmp_path, lock_coordination="single-host").run(
            operation, writable=True
        )
    assert error.value.code == "target_identity_unavailable"
    assert "private" not in str(error.value)
    operation.assert_not_called()


@pytest.mark.skipif(os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1", reason="real seekdb probe not enabled")
def test_real_seekdb_maintenance_preserves_committed_data_without_initializing_business_tables(
    short_tmp_path: Path, caplog
) -> None:
    pytest.importorskip("pylibseekdb")
    path = short_tmp_path / "seekdb"
    adapter = MaintenanceConnections(SeekDBConfig(path=path, echo=True))
    missing = adapter.run(lambda connection, identity, _verify: (connection, identity))
    assert missing[0] is None
    assert not path.exists()

    def write(connection: Connection | None, identity: BackendIdentity, verify: Callable[[], None]):
        assert connection is not None
        assert connection.exec_driver_sql("SHOW TABLES").all() == []
        with pytest.raises(MigrationError) as locked, local_migration_lock(path):
            pytest.fail("Another maintenance operation entered the live seekdb directory")
        assert locked.value.code == "migration_locked"
        verify()
        connection.exec_driver_sql("CREATE TABLE pc_maintenance_probe (id INT PRIMARY KEY, value VARCHAR(32))")
        connection.exec_driver_sql("INSERT INTO pc_maintenance_probe VALUES (1, 'preserved')")
        connection.commit()
        verify()
        return identity

    written = adapter.run(write, writable=True)

    def read(connection: Connection | None, identity: BackendIdentity, verify: Callable[[], None]):
        assert connection is not None
        verify()
        assert connection.exec_driver_sql("SHOW TABLES").all() == [("pc_maintenance_probe",)]
        assert (
            connection.exec_driver_sql("SELECT value FROM pc_maintenance_probe WHERE id = 1").scalar_one()
            == "preserved"
        )
        return identity

    assert adapter.run(read) == written == missing[1]
    assert "preserved" not in caplog.text


@pytest.mark.skipif(os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1", reason="real seekdb probe not enabled")
def test_real_seekdb_maintenance_excludes_another_process_and_reopens_after_release(short_tmp_path: Path) -> None:
    pytest.importorskip("pylibseekdb")
    path = short_tmp_path / "seekdb"
    ready = short_tmp_path / "owner-ready"
    owner_log = short_tmp_path / "owner-stderr.log"

    def owner_diagnostic() -> str:
        with owner_log.open("rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 16_384))
            return log.read().decode("utf-8", errors="replace")

    code = """
from pathlib import Path
import sys
from powercontext.builtin.persistence.migrations.connections import MaintenanceConnections
from powercontext.builtin.persistence.seekdb import SeekDBConfig

# Startup diagnostics must not block readiness even if no reader is attached.
sys.stderr.write("startup diagnostic\\n" * 65_536)
sys.stderr.flush()

def own(connection, identity, verify):
    verify()
    connection.exec_driver_sql("CREATE TABLE pc_lock_probe (id INT PRIMARY KEY, value VARCHAR(32))")
    connection.exec_driver_sql("INSERT INTO pc_lock_probe VALUES (1, 'preserved')")
    connection.commit()
    Path(sys.argv[2]).write_text("ready", encoding="utf-8")
    sys.stdin.read()
    verify()

MaintenanceConnections(SeekDBConfig(path=Path(sys.argv[1]))).run(own, writable=True)
"""
    with owner_log.open("w", encoding="utf-8") as log:
        # Native startup can emit more stderr than an unread pipe can hold.
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(path), str(ready)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=log,
            text=True,
        )
        try:
            deadline = time.monotonic() + 60
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert ready.exists(), f"The isolated seekdb owner did not become ready\n{owner_diagnostic()}"
            alias = short_tmp_path / "seekdb-alias"
            alias.symlink_to(path, target_is_directory=True)
            operation = Mock()
            with pytest.raises(MigrationError) as locked:
                MaintenanceConnections(SeekDBConfig(path=alias)).run(operation, writable=True)
            assert locked.value.code == "migration_locked"
            operation.assert_not_called()
        finally:
            try:
                process.communicate(input="", timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=10)
                pytest.fail(
                    f"The isolated seekdb owner did not release its maintenance resources\n{owner_diagnostic()}"
                )
            assert process.returncode == 0, owner_diagnostic()

    def read(connection: Connection | None, _identity: BackendIdentity, verify: Callable[[], None]) -> str:
        assert connection is not None
        verify()
        return connection.exec_driver_sql("SELECT value FROM pc_lock_probe WHERE id = 1").scalar_one()

    assert MaintenanceConnections(SeekDBConfig(path=path)).run(read) == "preserved"


@pytest.mark.skipif(
    not os.environ.get("POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL"),
    reason="dedicated real OceanBase target not configured",
)
def test_real_oceanbase_single_host_coordinator_excludes_other_maintenance_and_survives_commit(tmp_path: Path) -> None:
    """Run maintenance only on one host and evidence directory, without business DDL."""
    value = os.environ["POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL"]
    url = make_url(value)
    assert url.database and url.database.startswith("pc_migration_probe_"), (
        "Only a dedicated scratch database is allowed"
    )
    config = OceanBaseConfig.model_validate({"url": value})
    adapter = MaintenanceConnections(config, evidence_directory=tmp_path, lock_coordination="single-host")
    identity = adapter.run(lambda _connection, target, _verify: target)
    assert identity.product == "oceanbase"
    assert identity.database_name == url.database

    def diagnostic(error: MigrationError) -> str:
        details = f"code={error.code}: {error}"
        cause = error.__cause__
        if cause is not None:
            details += f"; cause={type(cause).__name__}: {cause}"
        for secret in (value, url.render_as_string(hide_password=False), url.password):
            if secret:
                details = details.replace(secret, "[redacted]")
        return details

    def maintain(connection: Connection | None, target: BackendIdentity, verify: Callable[[], None]) -> BackendIdentity:
        assert connection is not None
        assert target == identity
        verify()
        contender = MaintenanceConnections(config, evidence_directory=tmp_path, lock_coordination="single-host")
        operation = Mock()
        with ThreadPoolExecutor(max_workers=1) as executor:
            attempt = executor.submit(contender.run, operation, writable=True)
            with pytest.raises(MigrationError) as locked:
                attempt.result(timeout=30)
        assert locked.value.code == "migration_locked", diagnostic(locked.value)
        operation.assert_not_called()
        connection.commit()
        verify()
        assert connection.exec_driver_sql("SELECT DATABASE()").scalar_one() == target.database_name
        return target

    assert adapter.run(maintain, writable=True) == identity

    def reacquire(
        connection: Connection | None, target: BackendIdentity, verify: Callable[[], None]
    ) -> BackendIdentity:
        assert connection is not None
        verify()
        connection.commit()
        verify()
        return target

    assert adapter.run(reacquire, writable=True) == identity


def test_seekdb_legacy_database_refusal_is_a_maintenance_error(tmp_path: Path, monkeypatch) -> None:
    path, _assert_locked = _fake_seekdb(tmp_path, monkeypatch)

    async def refuse(_config, _options) -> None:
        from powercontext.builtin.persistence.seekdb import LegacySeekDBDatabaseError

        raise LegacySeekDBDatabaseError(path)

    monkeypatch.setattr(connections.seekdb_profile, "_require_explicit_legacy_database", refuse)

    def unexpected(connection: Connection | None, _identity: BackendIdentity, verify: Callable[[], None]):
        pytest.fail("An expected legacy refusal must not run the maintenance operation")

    with pytest.raises(MigrationError) as error:
        MaintenanceConnections(SeekDBConfig(path=path)).run(unexpected, writable=True)
    assert error.value.code == "explicit_database_required"
    assert "POWERCONTEXT_SERVER_DATABASE_DATABASE" in str(error.value)


@pytest.mark.parametrize(
    ("error_factory", "code"),
    [
        (
            lambda: connections.DatabaseAccessError("custom-db", "CREATE DATABASE IF NOT EXISTS `custom-db`"),
            "database_access_denied",
        ),
        (
            lambda: connections.DatabaseCreationError("custom-db", "CREATE DATABASE IF NOT EXISTS `custom-db`"),
            "database_creation_required",
        ),
    ],
)
def test_seekdb_provisioning_failure_is_a_maintenance_error(tmp_path: Path, monkeypatch, error_factory, code) -> None:
    path, _assert_locked = _fake_seekdb(tmp_path, monkeypatch)

    async def refuse(*_args, **_options):
        raise error_factory()

    monkeypatch.setattr(connections, "ensure_database", refuse)

    def unexpected(connection: Connection | None, _identity: BackendIdentity, verify: Callable[[], None]):
        pytest.fail("a provisioning failure must not run the maintenance operation")

    with pytest.raises(MigrationError) as error:
        MaintenanceConnections(SeekDBConfig(path=path, database="custom-db")).run(unexpected, writable=True)
    assert error.value.code == code
    assert "administrator" in str(error.value)


@pytest.mark.parametrize(
    ("error_factory", "code"),
    [
        (lambda: MissingDatabaseError("custom-db"), "database_missing"),
        (
            lambda: connections.DatabaseAccessError("custom-db", "CREATE DATABASE IF NOT EXISTS `custom-db`"),
            "database_access_denied",
        ),
    ],
)
def test_seekdb_read_only_inspection_reports_selected_database_error(
    tmp_path: Path, monkeypatch, error_factory, code
) -> None:
    path, _assert_locked = _fake_seekdb(tmp_path, monkeypatch)
    path.mkdir()
    (path / "fixture").touch()
    monkeypatch.setattr(connections.seekdb_profile, "has_native_storage", lambda _path: True)

    async def refuse(_engine, *, name: str) -> None:
        assert name == "custom-db"
        raise error_factory()

    monkeypatch.setattr(connections, "require_existing_database", refuse)
    operation = Mock()
    with pytest.raises(MigrationError) as error:
        MaintenanceConnections(SeekDBConfig(path=path, database="custom-db")).run(operation)
    assert error.value.code == code
    assert "custom-db" in str(error.value)
    operation.assert_not_called()


def _statement_snapshot_adapter(target):
    engine = create_async_engine(f"sqlite+aiosqlite:///{target}")

    class NativeReadConnection:
        dialect = SimpleNamespace(name="mysql")

        def __init__(self):
            self.connection = engine.connect()

        async def __aenter__(self):
            await self.connection.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.connection.__aexit__(*args)

        async def exec_driver_sql(self, statement):
            translated = {
                "START TRANSACTION WITH CONSISTENT SNAPSHOT": "BEGIN",
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ": "SELECT 1",
                "START TRANSACTION": "SELECT 1",
            }.get(statement, statement)
            return await self.connection.exec_driver_sql(translated)

        async def run_sync(self, operation, *args, **kwargs):
            return await self.connection.run_sync(operation, *args, **kwargs)

        async def invalidate(self):
            await self.connection.invalidate()

    @event.listens_for(engine.sync_engine, "connect")
    def database_name(connection, _record):
        connection.create_function("DATABASE", 0, lambda: "test")

    @event.listens_for(engine.sync_engine, "before_cursor_execute", retval=True)
    def ordinary_transaction(_connection, _cursor, statement, parameters, _context, _many):
        # An ordinary READ COMMITTED transaction does not pin a snapshot.
        return ("SELECT 1" if statement == "START TRANSACTION" else statement), parameters

    return SimpleNamespace(connect=NativeReadConnection, dispose=engine.dispose)


@pytest.mark.parametrize("entry", ["profile", "oceanbase", "seekdb"])
def test_native_read_admission_keeps_one_snapshot(tmp_path, monkeypatch, entry):
    # A real SQLite WAL file models MySQL READ COMMITTED's statement snapshots.
    # Only the connection/protocol boundary is substituted; the native E2E
    # suite proves these semantics on OceanBase and embedded seekDB themselves.
    target = tmp_path / "read-consistency.db"
    with sqlite3.connect(target) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE snapshot_values (value INTEGER NOT NULL)")
        writer.execute("INSERT INTO snapshot_values VALUES (1)")
    adapter = _statement_snapshot_adapter(target)

    def read_twice(connection, *_args, **_kwargs):
        first = connection.exec_driver_sql("SELECT value FROM snapshot_values").scalar_one()
        with sqlite3.connect(target) as writer:
            writer.execute("UPDATE snapshot_values SET value=2")
        second = connection.exec_driver_sql("SELECT value FROM snapshot_values").scalar_one()
        assert (first, second) == (1, 1)

    if entry == "profile":
        monkeypatch.setattr(dream_schema, "check_dream_schema", read_twice)
        try:
            asyncio.run(dream_schema.assert_dream_schema_ready(adapter))
        finally:
            asyncio.run(adapter.dispose())
    elif entry == "oceanbase":
        monkeypatch.setattr(connections, "create_async_engine", lambda *_args, **_kwargs: adapter)
        monkeypatch.setattr(
            connections, "_oceanbase_identity", lambda _connection: BackendIdentity("oceanbase", "isolated", "test")
        )
        config = OceanBaseConfig.model_validate({"url": "mysql+aoceanbase://test@localhost:2881/test?charset=utf8mb4"})
        MaintenanceConnections(config).run(read_twice)
    else:
        path = tmp_path / "seekdb"
        path.mkdir()
        (path / "fixture").touch()

        async def open_instance(_path):
            return SimpleNamespace(connection_options=lambda: {}, close=lambda: None)

        monkeypatch.setattr(connections.seekdb_profile, "has_native_storage", lambda _path: True)
        monkeypatch.setattr(connections.seekdb_profile, "_load_binding", lambda: SimpleNamespace(aopen=open_instance))
        monkeypatch.setattr(connections.seekdb_profile, "_create_engine", lambda *_args: adapter)
        MaintenanceConnections(SeekDBConfig(path=path, database="test")).run(read_twice)
    with sqlite3.connect(target) as writer:
        assert writer.execute("SELECT value FROM snapshot_values").fetchone() == (2,)
