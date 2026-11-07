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

"""Offline configuration changes must share managed database maintenance."""

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine
from typer.testing import CliRunner

from powercontext.builtin.persistence.migrations.deployment import deployment_runner, server_bundle
from powercontext.builtin.persistence.migrations.locking import local_migration_lock
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import BuiltinConfig, RuntimeConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest
from powercontext.cli.app import create_cli
from powercontext.server.cli import app
from powercontext.server.settings import ServerSettings


@pytest.fixture
def managed(tmp_path):
    path = tmp_path / "managed.db"
    settings = ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
    runner = deployment_runner(settings)
    plan = runner.plan()
    runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_artifact_processing_leases "
            "(supervisor_group,holder_id,supervisor_generation,lease_expires_at) VALUES ('global','old-worker',7,NULL)"
        )
    return path, settings


def switched(settings):
    return settings.model_copy(update={"runtime": RuntimeConfig(artifact_processing_supervisor_mode="dedicated")})


def test_registered_mode_switch_backs_up_preserves_revision_and_fences_old_worker(managed):
    path, settings = managed
    runner = deployment_runner(switched(settings), bundle="processing")
    plan = runner.plan()
    assert plan.state == "migration_required"
    with closing(sqlite3.connect(path)) as connection:
        assert (
            json.loads(connection.execute("SELECT config_manifest FROM pc_artifact_processing_schema").fetchone()[0])[
                "mode"
            ]
            == "global"
        )

    result = runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)

    assert result.backup_state == "completed"
    assert result.backup_ref and Path(result.backup_ref).is_file()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT version_num FROM pc_schema_revision").fetchall() == [("v1_5_0_003",)]
        assert (
            json.loads(connection.execute("SELECT config_manifest FROM pc_artifact_processing_schema").fetchone()[0])[
                "mode"
            ]
            == "dedicated"
        )
        assert connection.execute("SELECT supervisor_generation FROM pc_artifact_processing_leases").fetchone()[0] > 7
        after = tuple(connection.iterdump())
    with closing(sqlite3.connect(result.backup_ref)) as connection:
        assert (
            json.loads(connection.execute("SELECT config_manifest FROM pc_artifact_processing_schema").fetchone()[0])[
                "mode"
            ]
            == "global"
        )
    assert deployment_runner(switched(settings)).verify().state == "ready"
    assert not runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True).changed
    with closing(sqlite3.connect(path)) as connection:
        assert tuple(connection.iterdump()) == after


@pytest.mark.parametrize(
    ("bundle", "missing", "recorded"),
    [
        ("processing", "pc_retained_dream_candidate_heads", True),
        ("atomic-projection", "pc_retained_dream_candidate_versions", False),
    ],
)
def test_partial_maintenance_rejects_missing_required_history(managed, bundle, missing, recorded):
    path, settings = managed
    full = deployment_runner(settings)
    journal = path.with_name(path.name + ".pc-migration-state") / "maintenance.json"
    evidence = json.loads(journal.read_text())
    assert missing in evidence["retained_objects"]
    if not recorded:
        # The source-to-head transition still requires retention when its final
        # inventory has not been recorded in the external journal.
        del evidence["retained_objects"]
        journal.write_text(json.dumps(evidence))
    partial = deployment_runner(switched(settings) if bundle == "processing" else settings, bundle=bundle)
    reviewed = partial.plan()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"DROP TABLE {missing}")
    original = path.read_bytes()
    original_evidence = journal.read_bytes()
    with pytest.raises(MigrationError, match="recovery_required"):
        full.verify()

    for operation in (
        partial.plan,
        partial.verify,
        lambda: partial.apply(plan_id=reviewed.plan_id, accepted=True, maintenance_confirmed=True),
    ):
        with pytest.raises(MigrationError, match="recovery_required"):
            operation()
    assert path.read_bytes() == original
    assert journal.read_bytes() == original_evidence
    assert not list(journal.parent.glob("*/*/maintenance.json"))
    assert not list(path.parent.glob("*.pc-migration-backups/*"))


@pytest.mark.parametrize("bundle", ["processing", "atomic-projection"])
def test_partial_maintenance_accepts_current_storage_without_external_evidence(managed, bundle):
    path, settings = managed
    full = deployment_runner(settings)
    (path.with_name(path.name + ".pc-migration-state") / "maintenance.json").unlink()
    # A current-layout database without an upgrade journal has no recorded
    # historical retention obligation, matching the full runner's admission.
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP TABLE pc_retained_dream_candidate_heads")
        connection.execute("DROP TABLE pc_retained_dream_candidate_versions")
    original = path.read_bytes()
    assert full.verify().state == "ready"
    assert deployment_runner(settings, bundle=bundle).verify().state == "ready"
    assert path.read_bytes() == original


def test_mode_switch_uses_the_whole_database_migration_lock(managed):
    path, settings = managed
    runner = deployment_runner(switched(settings), bundle="processing")
    plan = runner.plan()
    with local_migration_lock(path), pytest.raises(MigrationError, match="migration_locked"):
        runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    assert deployment_runner(settings).verify().state == "ready"


def test_reused_mode_switch_id_is_rejected_before_creating_new_recovery_evidence(managed):
    path, settings = managed
    first = deployment_runner(switched(settings), bundle="processing", migration_id="first-switch")
    plan = first.plan()
    first.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    journal = next(path.parent.glob("*.pc-migration-state/processing_configuration/*/maintenance.json"))
    completed = journal.read_bytes()
    reused = deployment_runner(settings, bundle="processing", migration_id="first-switch")
    with pytest.raises(MigrationError, match="migration_id_reused"):
        reused.plan()
    assert journal.read_bytes() == completed
    reverse = deployment_runner(settings, bundle="processing", migration_id="second-switch")
    plan = reverse.plan()
    reverse.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    assert deployment_runner(settings).verify().state == "ready"


def test_process_exit_during_mode_switch_rolls_back_and_reuses_original_backup(managed):
    path, settings = managed
    runner = deployment_runner(switched(settings), bundle="processing")
    plan = runner.plan()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("POWERCONTEXT_SERVER_")}
    environment.update(
        POWERCONTEXT_SERVER_DATABASE_KIND="sqlite",
        POWERCONTEXT_SERVER_DATABASE_URL=f"sqlite+aiosqlite:///{path}",
        POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_SUPERVISOR_MODE="dedicated",
    )
    interrupted = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os
from sqlalchemy import event, Engine
from powercontext.cli.app import main
@event.listens_for(Engine, "after_cursor_execute")
def interrupt(connection, cursor, statement, parameters, context, executemany):
    if statement.startswith("UPDATE pc_artifact_processing_leases "):
        os._exit(73)
main()
""",
            "server",
            "db-migrate",
            "apply",
            "--bundle",
            "processing",
            "--plan-id",
            plan.plan_id,
            "--backup",
            "auto",
            "--yes",
            "--maintenance-confirmed",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert interrupted.returncode == 73, interrupted.stdout + interrupted.stderr
    journal = next(path.parent.glob("*.pc-migration-state/processing_configuration/*/maintenance.json"))
    original = json.loads(journal.read_text())
    assert original["state"] == "active"
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT supervisor_generation FROM pc_artifact_processing_leases").fetchone() == (7,)
        assert (
            json.loads(connection.execute("SELECT config_manifest FROM pc_artifact_processing_schema").fetchone()[0])[
                "mode"
            ]
            == "global"
        )
    recovery = runner.plan()
    assert recovery.state == "recovery_required"
    result = runner.apply(plan_id=recovery.plan_id, accepted=True, maintenance_confirmed=True)
    finished = json.loads(journal.read_text())
    assert finished["state"] == "complete"
    assert finished["maintenance_window_id"] == original["maintenance_window_id"]
    assert result.backup_ref == original["backup"]["location"]
    assert deployment_runner(switched(settings)).verify().state == "ready"


def test_processing_alias_delegates_with_reviewed_plan_and_preserves_operator_migration_id(managed, monkeypatch):
    path, _settings = managed
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_SUPERVISOR_MODE", "dedicated")
    command = ["server", "processing-migrate", "--migration-id", "operator-switch"]
    cli = create_cli([app])
    result = CliRunner().invoke(cli, [*command, "--action", "plan"])
    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)
    assert plan["domain_steps"] == ["processing_configuration"]
    denied = CliRunner().invoke(cli, [*command, "--action", "apply", "--maintenance-confirmed"])
    assert denied.exit_code != 0
    applied = CliRunner().invoke(
        cli,
        [
            *command,
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
    assert applied.exit_code == 0, applied.output
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT migration_id FROM pc_artifact_processing_schema").fetchone() == (
            "operator-switch",
        )


def test_processing_alias_does_not_modify_an_unversioned_complete_historical_server(tmp_path, monkeypatch):
    path = tmp_path / "historical.db"
    bundle = server_bundle()
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        config = bundle.config(connection)
        config.attributes["configuration"] = {"processing_manifest": canonical_processing_manifest(BuiltinConfig())}
        command.upgrade(config, "s1300")
        connection.exec_driver_sql("DROP TABLE pc_schema_revision")
        connection.commit()
    engine.dispose()
    original = path.read_bytes()
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    result = CliRunner().invoke(
        create_cli([app]),
        [
            "server",
            "processing-migrate",
            "--action",
            "apply",
            "--maintenance-confirmed",
            "--migration-id",
            "wrong-owner",
        ],
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["error"] == "migration_required"
    assert path.read_bytes() == original


def test_processing_alias_inspection_and_execution_use_the_same_discovered_environment(managed, monkeypatch):
    path, _settings = managed
    monkeypatch.chdir(path.parent)
    (path.parent / ".env").write_text(
        f'POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\nPOWERCONTEXT_SERVER_DATABASE_URL="sqlite+aiosqlite:///{path.as_posix()}"\n'
        "POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_SUPERVISOR_MODE=dedicated\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
    other = path.parent / "other.db"
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", f"sqlite+aiosqlite:///{other}")
    result = CliRunner().invoke(create_cli([app]), ["server", "processing-migrate", "--action", "plan"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["domain_steps"] == ["processing_configuration"]
    assert not other.exists()
