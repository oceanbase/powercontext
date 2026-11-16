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

"""Managed alias schema changes use explicit, registered maintenance."""

import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import create_engine

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.migrations.deployment import deployment_runner, server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.mysql_schema import MySQLSchema, _parse_table
from powercontext.builtin.persistence.migrations.server_readiness import check_server_layout
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.settings import ServerSettings
from tests.builtin.persistence.test_portable_storage_migration import configuration, previous_bundle


def test_managed_alias_schema_is_installed_by_explicit_maintenance(tmp_path):
    path = tmp_path / "managed-alias.db"
    settings = ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
    runner = deployment_runner(settings)
    plan = runner.plan()
    runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    with closing(sqlite3.connect(path)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "pc_atomic_memory_alias" in tables
        assert connection.execute("SELECT COUNT(*) FROM pc_atomic_memory_alias").fetchone() == (0,)
        assert connection.execute("SELECT version_num FROM pc_schema_revision").fetchone() == (server_bundle().head,)
    assert runner.verify().state == "ready"


@pytest.mark.parametrize("dimension", [None, 3, 1536])
def test_registered_mysql_alias_layout_uses_configured_vector_type(dimension):
    profile = (
        None if dimension is None else EmbeddingProfile(profile_id="alias-layout", model="fixture", dimension=dimension)
    )
    bundle = server_bundle(embedding_profile=profile)
    schema = MySQLSchema(bundle)
    (statement,) = schema.statements("v1_5_0_006")
    name, table = _parse_table(statement)
    assert name == "pc_atomic_memory_alias"
    assert table == schema.required(bundle.head)["tables"][name]
    expected = ["blob", []] if dimension is None else ["vector", [str(dimension)]]
    assert table["columns"]["embedding"]["type"] == expected


def test_existing_managed_database_requires_explicit_alias_schema_upgrade(tmp_path):
    path = tmp_path / "previous-head.db"
    bundle = server_bundle()
    previous = previous_bundle(tmp_path / "previous", None, through="v1_5_0_005")
    old = SQLiteMigrationRunner(path, previous, configuration=configuration(None))
    old_plan = old.plan()
    old.apply(plan_id=old_plan.plan_id, accepted=True, maintenance_confirmed=True)
    engine = create_engine(f"sqlite:///{path}")
    before = path.read_bytes()
    with engine.connect() as connection, pytest.raises(MigrationError, match="migration_required"):
        check_server_layout(connection)
    assert path.read_bytes() == before
    engine.dispose()
    settings = ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
    runner = deployment_runner(settings)
    plan = runner.plan()
    assert plan.source_revision == "v1_5_0_005"
    assert plan.target_revision == bundle.head
    runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM pc_atomic_memory_alias").fetchone() == (0,)
    assert runner.verify().state == "ready"
