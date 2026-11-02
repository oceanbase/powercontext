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

"""Atomic compatibility commands preserve managed Server migration ownership."""

import json
import sqlite3
from contextlib import closing

import pytest
from alembic import command
from sqlalchemy import create_engine
from typer.testing import CliRunner

from powercontext.builtin.persistence.migrations.deployment import deployment_runner, server_bundle
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest
from powercontext.cli.app import create_cli
from powercontext.server.cli import app
from powercontext.server.settings import ServerSettings


@pytest.fixture
def historical(tmp_path, monkeypatch):
    path = tmp_path / "historical.db"
    bundle = server_bundle()
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        config = bundle.config(connection)
        config.attributes["configuration"] = {"processing_manifest": canonical_processing_manifest(BuiltinConfig())}
        command.upgrade(config, "s1300")
        connection.exec_driver_sql("DROP TABLE pc_schema_revision")
        connection.exec_driver_sql(
            "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
            "VALUES ('scope','title','summary','scope','title','summary',1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content) "
            "VALUES ('scope','custom-family','historical',1,?)",
            (b'{"history":"unchanged"}',),
        )
        connection.commit()
    engine.dispose()
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    return path


def upgraded(path):
    runner = deployment_runner(ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}")))
    plan = runner.plan()
    runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)


def test_atomic_alias_upgrades_complete_historical_server_with_shared_plan_and_backup(historical):
    cli = create_cli([app])
    arguments = ["server", "atomic-memory-migrate"]
    original = historical.read_bytes()
    with closing(sqlite3.connect(historical)) as connection:
        original_dump = tuple(connection.iterdump())

    inspected = CliRunner().invoke(cli, [*arguments, "--action", "plan"])
    assert inspected.exit_code == 0, inspected.output
    plan = json.loads(inspected.output)
    assert plan["source_revision"] == "s1300"
    assert plan["target_revision"] == "v1_5_0_001"
    assert plan["readiness_scope"] == "server"
    assert historical.read_bytes() == original

    denied = CliRunner().invoke(cli, [*arguments, "--action", "apply", "--maintenance-confirmed"])
    assert denied.exit_code != 0
    assert historical.read_bytes() == original
    accepted = CliRunner().invoke(
        cli,
        [
            *arguments,
            "--action",
            "apply",
            "--maintenance-confirmed",
            "--plan-id",
            plan["plan_id"],
            "--backup",
            "auto",
            "--yes",
        ],
    )
    assert accepted.exit_code == 0, accepted.output
    result = json.loads(accepted.output)
    assert result["readiness_scope"] == "server" and result["backup_state"] == "completed"
    with closing(sqlite3.connect(result["backup_ref"])) as connection:
        assert tuple(connection.iterdump()) == original_dump
    with closing(sqlite3.connect(historical)) as connection:
        assert connection.execute("SELECT version_num FROM pc_schema_revision").fetchall() == [("v1_5_0_001",)]
        assert connection.execute("SELECT content FROM pc_artifacts").fetchall() == [(b'{"history":"unchanged"}',)]
    verified = CliRunner().invoke(cli, [*arguments, "--action", "verify"])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["state"] == "ready"
    after = historical.read_bytes()
    ready_plan = CliRunner().invoke(cli, [*arguments, "--action", "plan"])
    assert ready_plan.exit_code == 0, ready_plan.output
    assert json.loads(ready_plan.output)["state"] == "ready"
    assert historical.read_bytes() == after
    repeated = CliRunner().invoke(cli, [*arguments, "--action", "apply", "--maintenance-confirmed"])
    assert repeated.exit_code == 0, repeated.output
    assert json.loads(repeated.output)["changed"] is False
    assert historical.read_bytes() == after


def test_atomic_alias_rejects_unregistered_owner_decisions_on_managed_server(historical):
    upgraded(historical)
    decisions = historical.with_suffix(".json")
    decisions.write_text('{"format":"powercontext.atomic-memory-owner-decisions.v1","decisions":[]}', encoding="utf-8")
    original = historical.read_bytes()
    result = CliRunner().invoke(
        create_cli([app]),
        [
            "server",
            "atomic-memory-migrate",
            "--action",
            "apply",
            "--maintenance-confirmed",
            "--owner-decisions",
            str(decisions),
        ],
    )
    assert result.exit_code != 0
    assert "decision files" in result.output
    assert historical.read_bytes() == original
