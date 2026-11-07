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

"""Native restart/recovery release gates; no additional instance lease is held."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path

import pytest
from sqlalchemy import Connection, text

from powercontext.builtin.persistence.migrations.connections import BackendIdentity, MaintenanceConnections
from powercontext.builtin.persistence.migrations.deployment import production_bundle
from powercontext.builtin.persistence.seekdb import SeekDBConfig, profile
from tests.seekdb_support import RestartingInstance

pytestmark = pytest.mark.skipif(
    os.environ.get("POWERCONTEXT_TEST_SEEKDB_RESTART_RECOVERY") != "1",
    reason="native seekdb restart/recovery release gate not enabled",
)


@pytest.fixture(autouse=True)
def native_restarts(monkeypatch: pytest.MonkeyPatch) -> None:
    original = profile._open_instance

    async def open_generation(binding, path: Path):
        instance = await original(binding, path)
        try:
            pid = int((path / "run/seekdb.pid").read_text().strip())
        except (OSError, ValueError):
            instance.close()
            raise
        return RestartingInstance(instance, pid)

    monkeypatch.setattr(profile, "_open_instance", open_generation)


@pytest.fixture
def recovery_path() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="pc-"))
    try:
        yield path
    finally:
        # The probe owns the parent directory and removes successful attempts.
        # Keep failures even when the client timeout lets pytest exit normally.
        if os.environ.get("POWERCONTEXT_SEEKDB_KEEP_FAILED_STORAGE") != "1":
            shutil.rmtree(path)


def test_metadata_inspection_survives_last_client_restarts(recovery_path: Path) -> None:
    import_module("pylibseekdb")
    connections = MaintenanceConnections(SeekDBConfig(path=recovery_path / "seekdb"))
    bundle = production_bundle()
    schema = json.loads((bundle.snapshot_directory / "schemas/pre_dream.json").read_text())

    def seed(connection: Connection | None, _identity: BackendIdentity, _guard: Callable[[], None]) -> None:
        assert connection is not None
        for statement in schema["mysql"]:
            connection.exec_driver_sql(statement)
        connection.execute(
            text(
                "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content) "
                "VALUES ('s','memory','m',1,:content)"
            ),
            {"content": b'{"content":"preserved"}'},
        )
        connection.exec_driver_sql(
            "INSERT INTO pc_artifact_heads (scope_id,family,artifact_id,revision) VALUES ('s','memory','m',1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO pc_artifact_tags "
            "(scope_id,family,artifact_id,target_type,target_id,tag_key_hash,tag_key,tag,assigned_at) "
            "VALUES ('s','memory','m','artifact','m',UNHEX(REPEAT('00',32)),'keep','Keep','2026-09-28 12:00:00')"
        )
        connection.commit()

    def inspect(connection: Connection | None, _identity: BackendIdentity, _guard: Callable[[], None]) -> None:
        assert connection is not None
        assert connection.exec_driver_sql("SHOW TRIGGERS").all() == []
        connection.commit()

    def adopt(connection: Connection | None, _identity: BackendIdentity, _guard: Callable[[], None]) -> None:
        assert connection is not None
        inspect(connection, _identity, _guard)
        connection.exec_driver_sql("START TRANSACTION")
        connection.exec_driver_sql(
            "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) NOT NULL, "
            "CONSTRAINT pc_schema_revision_pkc PRIMARY KEY (version_num))"
        )
        connection.exec_driver_sql("INSERT INTO pc_schema_revision VALUES ('p0001')")
        connection.commit()

    def verify(connection: Connection | None, _identity: BackendIdentity, _guard: Callable[[], None]) -> None:
        assert connection is not None
        assert connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").all() == [("p0001",)]
        assert connection.exec_driver_sql("SELECT content FROM pc_artifacts").all() == [(b'{"content":"preserved"}',)]
        assert connection.exec_driver_sql("SELECT tag FROM pc_artifact_tags").all() == [("Keep",)]

    connections.run(seed, writable=True)
    connections.run(inspect)
    connections.run(inspect)
    connections.run(adopt, writable=True)
    connections.run(verify)


@contextmanager
def _native_generation(binding, path: Path):
    instance = asyncio.run(profile._open_instance(binding, path))
    try:
        yield instance
    finally:
        instance.close()


def test_saved_fault_recovers_and_remains_usable_after_reopen(recovery_path: Path) -> None:
    binding = import_module("pylibseekdb")
    pymysql = import_module("pymysql")
    configured = os.environ.get("POWERCONTEXT_SEEKDB_RECOVERY_FIXTURE")
    if not configured:
        pytest.fail("enabled recovery acceptance requires an immutable native fault fixture directory", pytrace=False)
    source = Path(configured).resolve()
    assert (source / "etc/seekdb.data_version.bin").is_file(), "unrecognized native fault fixture"
    assert (source / "store/sstable/meta.db").is_file(), "unrecognized native fault fixture"
    path = recovery_path / "seekdb"
    # Open only the disposable clone, including for startup/recovery writes.
    shutil.copytree(
        source,
        path,
        ignore=lambda directory, _names: ["run"] if Path(directory) == source else [],
    )

    def verify(connection) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM pc_schema_revision")
            assert cursor.fetchall() == (("p0001",),)
            cursor.execute("SELECT content FROM pc_artifacts")
            assert cursor.fetchall() == ((b'{"content":"preserved"}',),)
            cursor.execute("SELECT tag FROM pc_artifact_tags")
            assert cursor.fetchall() == (("Keep",),)

    with (
        _native_generation(binding, path) as instance,
        pymysql.connect(**instance.connection_options(), database="powercontext", read_timeout=15) as connection,
    ):
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT pc_schema_revision_pkc PRIMARY KEY (version_num))"
            )
            cursor.execute("INSERT INTO pc_schema_revision VALUES ('p0001')")
        connection.commit()
        verify(connection)

    with (
        _native_generation(binding, path) as instance,
        pymysql.connect(**instance.connection_options(), database="powercontext", read_timeout=15) as connection,
    ):
        verify(connection)
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE pc_recovery_write_check (id INT PRIMARY KEY)")
            cursor.execute("INSERT INTO pc_recovery_write_check VALUES (1)")
        connection.commit()

    with (
        _native_generation(binding, path) as instance,
        pymysql.connect(**instance.connection_options(), database="powercontext", read_timeout=15) as connection,
    ):
        verify(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM pc_recovery_write_check")
            assert cursor.fetchall() == ((1,),)
