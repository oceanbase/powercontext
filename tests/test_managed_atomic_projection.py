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

"""Managed projection repair must preserve authority and shared maintenance ownership."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.migrations.locking import local_migration_lock
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.cli.app import create_cli
from powercontext.server.cli import app
from powercontext.server.factory import create_server_app
from powercontext.server.settings import ServerSettings


@pytest.fixture
def damaged(tmp_path, monkeypatch):
    path = tmp_path / "managed.db"
    settings = ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
    runner = deployment_runner(settings)
    plan = runner.plan()
    runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
            "VALUES ('scope','title','summary','scope','title','summary',1)"
        )
        connection.execute(
            "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content) "
            "VALUES ('scope','atomic-memory','fact',1,?)",
            (
                AtomicMemoryContent(kind="fact", text="Preserve immutable nebula evidence.")
                .model_dump_json(by_alias=True)
                .encode(),
            ),
        )
        connection.execute(
            "INSERT INTO pc_artifact_heads (scope_id,family,artifact_id,revision) "
            "VALUES ('scope','atomic-memory','fact',1)"
        )
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    return path, settings


def authority(path):
    with closing(sqlite3.connect(path)) as connection:
        return tuple(
            (table, tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1,2,3").fetchall()))  # noqa: S608 -- Fixed fixture tables.
            for table in ("pc_artifacts", "pc_artifact_heads", "pc_artifact_tags", "pc_access_owners")
        )


@pytest.mark.parametrize("damage", ["missing_current", "helper_only", "helper_identity"])
def test_managed_repair_uses_shared_plan_backup_and_lock_without_changing_authority(damaged, damage):
    path, settings = damaged
    cli = create_cli([app])
    arguments = ["server", "atomic-memory-rebuild-projection"]
    if damage != "missing_current":
        initial = CliRunner().invoke(cli, [*arguments, "--action", "plan"])
        assert initial.exit_code == 0, initial.output
        first_plan = json.loads(initial.output)
        repaired = CliRunner().invoke(
            cli,
            [
                *arguments,
                "--maintenance-confirmed",
                "--plan-id",
                first_plan["plan_id"],
                "--backup",
                "auto",
                "--yes",
            ],
        )
        assert repaired.exit_code == 0, repaired.output
        with closing(sqlite3.connect(path)) as connection, connection:
            if damage == "helper_only":
                connection.execute("DELETE FROM pc_atomic_memory_current_fts")
            else:
                connection.execute("UPDATE pc_atomic_memory_current_fts SET identity_token='broken'")
    original_authority = authority(path)
    original = path.read_bytes()
    for operation in ("status", "plan", "verify"):
        rejected = CliRunner().invoke(cli, ["server", "db-migrate", operation])
        assert rejected.exit_code != 0, rejected.output
        assert json.loads(rejected.output)["error"] == "projection_verification_failed"

    async def reject_runtime():
        with pytest.raises(MigrationError, match="projection_verification_failed"):
            async with open_builtin_contexts(BuiltinConfig(database=settings.database)):
                pytest.fail("Incomplete projections must block runtime admission")

    asyncio.run(reject_runtime())
    server = create_server_app(settings=settings)
    with pytest.raises(MigrationError, match="projection_verification_failed"), TestClient(server):
        pytest.fail("Incomplete projections must block Server startup")
    assert path.read_bytes() == original
    inspected = CliRunner().invoke(cli, [*arguments, "--action", "plan"])
    assert inspected.exit_code == 0, inspected.output
    plan = json.loads(inspected.output)
    assert plan["state"] == "migration_required" and plan["readiness_scope"] == "server"
    assert path.read_bytes() == original
    denied = CliRunner().invoke(cli, [*arguments, "--maintenance-confirmed"])
    assert denied.exit_code != 0 and path.read_bytes() == original
    accepted = [
        *arguments,
        "--maintenance-confirmed",
        "--plan-id",
        plan["plan_id"],
        "--backup",
        "auto",
        "--yes",
    ]
    with local_migration_lock(path):
        locked = CliRunner().invoke(cli, accepted)
    assert locked.exit_code != 0 and "migration_locked" in locked.output
    assert path.read_bytes() == original
    repaired = CliRunner().invoke(cli, accepted)
    assert repaired.exit_code == 0, repaired.output
    result = json.loads(repaired.output)
    assert result["backup_state"] == "completed" and Path(result["backup_ref"]).is_file()
    assert authority(Path(result["backup_ref"])) == original_authority == authority(path)
    assert deployment_runner(settings).verify().state == "ready"
    verified = CliRunner().invoke(cli, [*arguments, "--action", "verify"])
    assert verified.exit_code == 0, verified.output
    after = path.read_bytes()
    repeated = CliRunner().invoke(cli, [*arguments, "--maintenance-confirmed"])
    assert repeated.exit_code == 0, repeated.output
    assert json.loads(repeated.output)["changed"] is False and path.read_bytes() == after

    async def search():
        async with open_builtin_contexts(BuiltinConfig(database=settings.database)) as contexts:
            result = await contexts.atomic_memory.for_scope("scope").search("nebula", mode="text")
            assert [hit.hit.artifact_ref.artifact_id for hit in result.hits] == ["fact"]

    asyncio.run(search())


def test_managed_repair_rejects_broken_authority_before_backup_or_writes(damaged):
    path, _settings = damaged
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DELETE FROM pc_artifact_heads WHERE artifact_id='fact'")
    original = path.read_bytes()
    result = CliRunner().invoke(create_cli([app]), ["server", "atomic-memory-rebuild-projection", "--action", "plan"])
    assert result.exit_code != 0 and path.read_bytes() == original
    assert json.loads(result.output)["error"] == "invalid_authority"
    assert not list(path.parent.glob("*.pc-migration-state/atomic_projection/*/maintenance.json"))


def test_projection_repair_rejects_a_processing_configuration_switch(damaged, monkeypatch):
    path, _settings = damaged
    original = path.read_bytes()
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_SUPERVISOR_MODE", "dedicated")
    result = CliRunner().invoke(create_cli([app]), ["server", "atomic-memory-rebuild-projection", "--action", "plan"])
    assert result.exit_code != 0, result.output
    assert json.loads(result.output)["error"] == "incompatible_processing_configuration"
    assert path.read_bytes() == original


def test_process_exit_during_projection_repair_rolls_back_and_reuses_original_backup(damaged):
    path, settings = damaged
    runner = deployment_runner(settings, bundle="atomic-projection", batch_size=1)
    plan = runner.plan()
    original_authority = authority(path)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("POWERCONTEXT_SERVER_")}
    environment.update(
        POWERCONTEXT_SERVER_DATABASE_KIND="sqlite",
        POWERCONTEXT_SERVER_DATABASE_URL=f"sqlite+aiosqlite:///{path}",
    )
    interrupted = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os
from sqlalchemy import Engine, event
from powercontext.cli.app import main
@event.listens_for(Engine, "after_cursor_execute")
def interrupt(connection, cursor, statement, parameters, context, executemany):
    if statement.startswith("INSERT INTO pc_atomic_memory_current "):
        os._exit(73)
main()
""",
            "server",
            "db-migrate",
            "apply",
            "--bundle",
            "atomic-projection",
            "--batch-size",
            "1",
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
    journal = next(path.parent.glob("*.pc-migration-state/atomic_projection/*/maintenance.json"))
    original = json.loads(journal.read_text())
    assert original["state"] == "active"
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM pc_atomic_memory_current").fetchone() == (0,)
    recovery = runner.plan()
    assert recovery.state == "recovery_required"
    result = runner.apply(plan_id=recovery.plan_id, accepted=True, maintenance_confirmed=True)
    finished = json.loads(journal.read_text())
    assert finished["state"] == "complete"
    assert finished["maintenance_window_id"] == original["maintenance_window_id"]
    assert result.backup_ref == original["backup"]["location"]
    assert authority(path) == original_authority
    assert deployment_runner(settings).verify().state == "ready"
