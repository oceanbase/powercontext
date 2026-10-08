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

"""Opt-in real seekDB/OceanBase DDL probes; a skip is not backend acceptance.

OceanBase requires an empty dedicated database whose name starts with
pc_migration_probe_. The test leaves its schema for inspection and never drops
an existing database. Recreate the scratch database before running it again.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import platform
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Connection, inspect, text
from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.migrations import MigrationBundle
from powercontext.builtin.persistence.migrations.connections import MaintenanceConnections
from powercontext.builtin.persistence.migrations.locking import local_migration_lock
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/database_migrations"


def exercise_revisions(connection: Connection, bundle: MigrationBundle) -> None:
    assert inspect(connection).get_table_names() == [], "Use an empty disposable database"
    assert inspect(connection).get_view_names() == [], "Use an empty disposable database"
    print(
        json.dumps({
            "python": platform.python_version(),
            "sqlalchemy": importlib.metadata.version("sqlalchemy"),
            "alembic": importlib.metadata.version("alembic"),
            "pyobvector": importlib.metadata.version("pyobvector"),
            "driver": connection.dialect.driver,
            "server": connection.exec_driver_sql("SELECT VERSION()").scalar_one(),
        })
    )
    command.upgrade(bundle.config(connection), "p0001")
    connection.execute(
        text(
            "INSERT INTO pc_artifacts (scope_id, family, artifact_id, revision, content) VALUES ('s','memory','m',1,:data)"
        ),
        {"data": b'{"content":"preserve"}'},
    )
    connection.exec_driver_sql(
        "INSERT INTO pc_artifact_heads (scope_id,family,artifact_id,revision) VALUES ('s','memory','m',1)"
    )
    connection.exec_driver_sql(
        "INSERT INTO pc_artifact_tags (scope_id,family,artifact_id,target_type,target_id,tag_key_hash,tag_key,tag,assigned_at) "
        "VALUES ('s','memory','m','artifact','m',UNHEX(REPEAT('00',32)),'keep','Keep','2026-09-28 12:00:00')"
    )
    connection.commit()
    command.upgrade(bundle.config(connection), "p0002")
    connection.commit()
    command.upgrade(bundle.config(connection), "p0003")
    connection.commit()
    command.upgrade(bundle.config(connection), "head")
    assert connection.exec_driver_sql("SELECT content,memory_citations FROM pc_artifacts").all() == [
        (b'{"content":"preserve"}', None)
    ]
    assert connection.exec_driver_sql("SELECT tag FROM pc_artifact_tags").all() == [("Keep",)]
    names = {row["name"] for row in inspect(connection).get_check_constraints("pc_artifact_tags")}
    assert "ck_pc_artifact_tags_family" not in names
    assert "ck_pc_artifact_tags_target" in names
    assert {row["name"] for row in inspect(connection).get_indexes("pc_artifact_tags")} >= {
        "ix_pc_artifact_tags_family_key",
        "ix_pc_artifact_tags_key",
    }


@pytest.mark.skipif(os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1", reason="real seekDB probe not enabled")
def test_real_seekdb_revision_operations(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "seekdb"
        # The OS lock precedes engine startup and outlives engine shutdown.
        with local_migration_lock(path):
            async with SeekDBProfile.open(SeekDBConfig(path=path), tables=()) as profile:
                async with profile.database.engine.connect() as connection:
                    await connection.run_sync(lambda sync: exercise_revisions(sync, MigrationBundle(FIXTURE)))

    asyncio.run(scenario())


@pytest.mark.skipif(
    not os.environ.get("POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL"),
    reason="dedicated real OceanBase target not configured",
)
def test_real_oceanbase_revision_operations_and_single_host_coordination(tmp_path: Path) -> None:
    value = os.environ["POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL"]
    url = make_url(value)
    assert url.database and url.database.startswith("pc_migration_probe_"), (
        "Only a dedicated scratch database is allowed"
    )
    config = OceanBaseConfig.model_validate({"url": value})

    adapter = MaintenanceConnections(config, evidence_directory=tmp_path / "evidence", lock_coordination="single-host")

    def exercise(connection, _identity, verify):
        assert connection is not None
        verify()
        exercise_revisions(connection, MigrationBundle(FIXTURE))
        verify()

    adapter.run(exercise, writable=True)
