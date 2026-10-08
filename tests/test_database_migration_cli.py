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

"""The configured maintenance CLI preserves one-plan consent and read-only inspection."""

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner

from powercontext.builtin.persistence.migrations.connections import BackendIdentity, MaintenanceConnections
from powercontext.builtin.persistence.migrations.deployment import production_bundle
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.cli.app import create_cli
from powercontext.server import database_migration
from powercontext.server.cli import app


def _configure(
    tmp_path: Path, monkeypatch, *, existing: bool = False, complete: bool = False
) -> tuple[Path, list[str]]:
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    database = tmp_path / "database" / "server.db"
    environment = tmp_path / "deployment.env"
    environment.write_text(
        f"POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\nPOWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:///{database}\n",
        encoding="utf-8",
    )
    if existing:
        database.parent.mkdir(parents=True)
        bundle = production_bundle()
        schema = json.loads((bundle.snapshot_directory / "schemas/pre_dream.json").read_text())
        with sqlite3.connect(database) as connection:
            for statement in schema["sqlite"]:
                connection.execute(statement)
    if complete:

        async def initialize() -> None:
            async with SQLiteProfile.open(SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), tables=BUILTIN_TABLES):
                pass

        asyncio.run(initialize())
    return database, ["server", "db-migrate"]


def _invoke(command: list[str], action: str, tmp_path: Path, *options: str, prompt_input: str | None = None):
    return CliRunner().invoke(
        create_cli([app]),
        [*command, action, "--env-file", str(tmp_path / "deployment.env"), *options],
        input=prompt_input,
    )


def test_read_only_commands_never_create_missing_target(tmp_path, monkeypatch):
    database, command = _configure(tmp_path, monkeypatch)
    for action in ("status", "plan", "verify"):
        result = _invoke(command, action, tmp_path)
        payload = json.loads(result.output)
        if action == "verify":
            assert result.exit_code == 1
            assert payload["error"] in {"migration_required", "uninitialized"}
        else:
            assert result.exit_code == 0, result.output
            assert payload["state"] == "uninitialized"
        assert not database.parent.exists()


def test_sqlite_url_authority_is_rejected_without_creating_a_local_target(tmp_path, monkeypatch):
    _database, command = _configure(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    filename = f"pc-migration-{uuid4().hex}.db"
    private_host = "private-database-host.example"
    (tmp_path / "deployment.env").write_text(
        "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n"
        f"POWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite://{private_host}/{filename}\n",
        encoding="utf-8",
    )
    for action in ("plan", "apply"):
        options = (
            ["--yes", "--backup", "auto", "--plan-id", "unaccepted-plan", "--maintenance-confirmed"]
            if action == "apply"
            else []
        )
        result = _invoke(command, action, tmp_path, *options)
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["error"] == "unsupported_target"
        assert private_host not in result.output
        assert filename not in result.output
        assert not list(tmp_path.glob(f"{filename}*"))


@pytest.mark.parametrize(
    ("directory", "url_directory"),
    [
        pytest.param("~", "~", id="literal-tilde"),
        pytest.param("relative", "relative", id="relative-path"),
        pytest.param(
            "relative",
            "linked/../relative",
            id="lexical-parent-through-symlink",
            marks=pytest.mark.skipif(
                os.name == "nt", reason="creating symlinks may require elevated Windows privileges"
            ),
        ),
    ],
)
def test_migration_commands_use_the_configured_sqlite_url_target(tmp_path, monkeypatch, directory, url_directory):
    original_database, command = _configure(tmp_path, monkeypatch, existing=True)
    monkeypatch.chdir(tmp_path)
    filename = f"pc-migration-{uuid4().hex}.db"
    home_database = Path.home() / filename
    assert not home_database.exists()
    database = tmp_path / directory / filename
    database.parent.mkdir()
    original_database.rename(database)
    if url_directory != directory:
        link_target = tmp_path / "other" / "link-target"
        link_target.mkdir(parents=True)
        (tmp_path / "linked").symlink_to(link_target, target_is_directory=True)
    url = f"sqlite+aiosqlite:///{url_directory}/{filename}"
    (tmp_path / "deployment.env").write_text(
        f"POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\nPOWERCONTEXT_SERVER_DATABASE_URL={url}\n",
        encoding="utf-8",
    )

    planned = _invoke(command, "plan", tmp_path)
    assert planned.exit_code == 0, planned.output
    plan = json.loads(planned.output)
    assert plan["state"] == "migration_required"
    assert plan["source_revision"] == "p0001"
    applied = _invoke(
        command,
        "apply",
        tmp_path,
        "--yes",
        "--backup",
        "auto",
        "--plan-id",
        plan["plan_id"],
        "--maintenance-confirmed",
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["backup_state"] == "completed"

    async def inspect_configured_database() -> tuple[Path, str]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                target = (await connection.exec_driver_sql("PRAGMA database_list")).all()[0][2]
                revision = (await connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision")).scalar_one()
                return Path(target), revision
        finally:
            await engine.dispose()

    target, revision = asyncio.run(inspect_configured_database())
    assert target == database
    assert revision == production_bundle().head
    verified = _invoke(command, "verify", tmp_path)
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["state"] == "ready"
    assert not home_database.exists()


def test_apply_requires_exact_plan_and_explicit_noninteractive_consent(tmp_path, monkeypatch):
    database, command = _configure(tmp_path, monkeypatch)
    plan = json.loads(_invoke(command, "plan", tmp_path).output)
    denied = _invoke(command, "apply", tmp_path, "--yes", "--backup", "auto")
    assert denied.exit_code == 1
    assert json.loads(denied.output)["error"] == "confirmation_required"
    changed = _invoke(
        command,
        "apply",
        tmp_path,
        "--yes",
        "--backup",
        "auto",
        "--plan-id",
        "wrong",
        "--maintenance-confirmed",
    )
    assert changed.exit_code == 1
    assert json.loads(changed.output)["error"] == "plan_changed"
    assert not database.exists()
    applied = _invoke(
        command,
        "apply",
        tmp_path,
        "--yes",
        "--backup",
        "auto",
        "--plan-id",
        plan["plan_id"],
        "--maintenance-confirmed",
    )
    assert applied.exit_code == 0, applied.output
    payload = json.loads(applied.output)
    assert payload["state"] == "ready"
    assert payload["changed"] is True
    assert payload["backup_state"] == "not_required"
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "pc_artifacts" in tables
    assert "pc_schema_revision" in tables
    assert "pc_migration_runs" not in tables
    assert "pc_migration_steps" not in tables
    assert _invoke(command, "verify", tmp_path).exit_code == 0
    no_op = _invoke(command, "apply", tmp_path, "--manage-service")
    assert no_op.exit_code == 0, no_op.output
    assert json.loads(no_op.output)["changed"] is False
    assert not list(database.parent.glob("*backup*"))


def test_interactive_manual_backup_combines_declarations_in_one_confirmation(tmp_path, monkeypatch):
    database, command = _configure(tmp_path, monkeypatch, existing=True)
    monkeypatch.setattr(database_migration, "_interactive", lambda: True)
    result = _invoke(command, "apply", tmp_path, "--backup", "manual", prompt_input="y\n")
    assert result.exit_code == 0, result.output
    assert result.output.count("Accept this plan and the declarations above?") == 1
    assert "PC will not verify that backup" in result.output
    assert '"backup_state": "user_confirmed"' in result.output
    assert database.exists()


def test_existing_database_manual_backup_is_only_an_explicit_declaration(tmp_path, monkeypatch):
    database, command = _configure(tmp_path, monkeypatch, existing=True)
    original = database.read_bytes()
    planned = _invoke(command, "plan", tmp_path, "--backup", "manual")
    assert planned.exit_code == 0, planned.output
    plan = json.loads(planned.output)
    missing = _invoke(
        command,
        "apply",
        tmp_path,
        "--yes",
        "--backup",
        "manual",
        "--plan-id",
        plan["plan_id"],
        "--maintenance-confirmed",
    )
    assert missing.exit_code == 1
    assert json.loads(missing.output)["error"] == "confirmation_required"
    assert database.read_bytes() == original
    result = _invoke(
        command,
        "apply",
        tmp_path,
        "--yes",
        "--backup",
        "manual",
        "--plan-id",
        plan["plan_id"],
        "--maintenance-confirmed",
        "--backup-confirmed",
        "--backup-ref",
        "operator-reference",
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["backup_state"] == "user_confirmed"
    assert payload["backup_ref"] == "operator-reference"


def test_invalid_sqlite_file_reports_payload_free_json(tmp_path, monkeypatch):
    database, command = _configure(tmp_path, monkeypatch)
    database.parent.mkdir()
    private_value = "private-database-content-do-not-print"
    database.write_text(private_value, encoding="utf-8")
    result = _invoke(command, "plan", tmp_path)
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "invalid_database"
    assert private_value not in result.output
    assert "Traceback" not in result.output


def test_incomplete_external_evidence_is_a_structured_recovery_error(tmp_path, monkeypatch):
    database, command = _configure(tmp_path, monkeypatch, existing=True)
    inspected = json.loads(_invoke(command, "status", tmp_path).output)
    evidence = database.with_name(database.name + ".pc-migration-state") / "maintenance.json"
    evidence.parent.mkdir()
    evidence.write_text(
        json.dumps({"database_id": inspected["database_id"], "state": "active", "backup_policy": "auto"}),
        encoding="utf-8",
    )
    before = database.read_bytes()
    result = _invoke(command, "status", tmp_path)
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "recovery_required"
    assert "Traceback" not in result.output
    assert database.read_bytes() == before


def test_invalid_configuration_does_not_print_credentials(tmp_path, monkeypatch):
    _database, command = _configure(tmp_path, monkeypatch)
    private_value = "private-token-do-not-print"
    (tmp_path / "deployment.env").write_text(
        f"POWERCONTEXT_SERVER_DATABASE_KIND=unknown\nPOWERCONTEXT_SERVER_AUTH_TOKEN={private_value}\n",
        encoding="utf-8",
    )
    result = _invoke(command, "status", tmp_path)
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "configuration_invalid"
    assert private_value not in result.output
    assert "Traceback" not in result.output


def test_complete_server_database_is_not_silently_adopted_by_acceptance_bundle(tmp_path, monkeypatch):
    database, command = _configure(tmp_path, monkeypatch, complete=True)
    before = database.read_bytes()
    result = _invoke(command, "plan", tmp_path)
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "unknown_baseline"
    assert database.read_bytes() == before


def test_partial_bundle_rejects_business_service_management_before_writing(tmp_path, monkeypatch):
    database, command = _configure(tmp_path, monkeypatch)
    result = _invoke(command, "apply", tmp_path, "--manage-service")
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "service_unsupported"
    assert not database.exists()


def test_missing_seekdb_is_inspected_without_initializing_the_engine(tmp_path, monkeypatch):
    _database, command = _configure(tmp_path, monkeypatch)
    directory = tmp_path / "seekdb"
    (tmp_path / "deployment.env").write_text(
        f"POWERCONTEXT_SERVER_DATABASE_KIND=seekdb\nPOWERCONTEXT_SERVER_DATABASE_PATH={directory}\n",
        encoding="utf-8",
    )
    result = _invoke(command, "status", tmp_path)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["state"] == "uninitialized"
    assert json.loads(result.output)["server_ready"] is False
    assert not directory.exists()
    assert not directory.with_name(directory.name + ".pc-migration.lock").exists()


@pytest.mark.parametrize("action", ["status", "plan", "apply", "verify"])
def test_oceanbase_requires_persistent_evidence_before_connecting(tmp_path, monkeypatch, action):
    _database, command = _configure(tmp_path, monkeypatch)
    private_value = "private-password-do-not-print"
    private_host = "private-database-host.invalid"
    (tmp_path / "deployment.env").write_text(
        "POWERCONTEXT_SERVER_DATABASE_KIND=oceanbase\n"
        f"POWERCONTEXT_SERVER_DATABASE_URL=mysql+aoceanbase://operator:{private_value}@{private_host}:2881/pc_probe"
        "?charset=utf8mb4\n",
        encoding="utf-8",
    )

    def refuse_connection(*_args, **_kwargs):
        pytest.fail("Missing persistent evidence must be rejected before contacting a database")

    monkeypatch.setattr(MaintenanceConnections, "run", refuse_connection)
    result = _invoke(command, action, tmp_path)
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["error"] == "evidence_required"
    assert "--evidence-dir" in result.output
    assert private_value not in result.output
    assert private_host not in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("kind", ["seekdb", "oceanbase"])
def test_remote_cli_uses_the_configured_maintenance_identity(
    tmp_path, monkeypatch, kind: Literal["seekdb", "oceanbase"]
):
    _database, command = _configure(tmp_path, monkeypatch)
    database_name = "test" if kind == "seekdb" else "pc_probe"
    environment = f"POWERCONTEXT_SERVER_DATABASE_KIND={kind}\n"
    if kind == "seekdb":
        environment += f"POWERCONTEXT_SERVER_DATABASE_PATH={tmp_path / 'seekdb'}\n"
    else:
        environment += (
            "POWERCONTEXT_SERVER_DATABASE_URL=mysql+aoceanbase://operator:secret@database.invalid:2881/pc_probe"
            "?charset=utf8mb4\n"
        )
    (tmp_path / "deployment.env").write_text(environment, encoding="utf-8")
    identity = BackendIdentity(kind, f"simulated-{kind}-database-identity", database_name)

    def inspect_empty_database(_connections, operation, *, writable=False):
        assert not writable
        return operation(None, identity, lambda: None)

    monkeypatch.setattr(MaintenanceConnections, "run", inspect_empty_database)
    evidence = tmp_path / "persistent-evidence"
    for action in ("status", "plan"):
        options = ["--evidence-dir", str(evidence)]
        if action == "plan":
            options.extend(["--backup", "manual"])
        result = _invoke(command, action, tmp_path, *options)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["database_id"] == identity.database_id
        assert payload["state"] == "uninitialized"
        assert payload["readiness_scope"] == "registered_bundle"
        assert payload["server_ready"] is False
    assert not evidence.exists()


def test_sqlite_commands_use_the_explicit_external_evidence_directory(tmp_path, monkeypatch):
    database, command = _configure(tmp_path, monkeypatch, existing=True)
    evidence = tmp_path / "persistent-evidence"
    options = ["--evidence-dir", str(evidence)]
    before = database.read_bytes()
    inspected = _invoke(command, "status", tmp_path, *options)
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["state"] == "migration_required"
    planned = _invoke(command, "plan", tmp_path, *options, "--backup", "manual")
    assert planned.exit_code == 0, planned.output
    plan = json.loads(planned.output)
    assert not evidence.exists()
    assert database.read_bytes() == before

    applied = _invoke(
        command,
        "apply",
        tmp_path,
        *options,
        "--yes",
        "--backup",
        "manual",
        "--plan-id",
        plan["plan_id"],
        "--maintenance-confirmed",
        "--backup-confirmed",
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["backup_state"] == "user_confirmed"
    assert (evidence / "maintenance.json").is_file()
    assert not database.with_name(database.name + ".pc-migration-state").exists()
    verified = _invoke(command, "verify", tmp_path, *options)
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["revision"] == production_bundle().head

    # All read paths must use the selected journal, rather than silently
    # verifying against an empty default directory after the migration.
    (evidence / "maintenance.json").write_text('{"state":"active"}', encoding="utf-8")
    migrated = database.read_bytes()
    for action in ("status", "plan", "verify"):
        result = _invoke(command, action, tmp_path, *options)
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["error"] == "recovery_required"
        assert "Traceback" not in result.output
        assert database.read_bytes() == migrated


def test_no_backup_requires_separate_risk_consent_and_policy_is_plan_bound(tmp_path, monkeypatch):
    database, command = _configure(tmp_path, monkeypatch, existing=True)
    before = database.read_bytes()
    auto = json.loads(_invoke(command, "plan", tmp_path, "--backup", "auto").output)
    skip = json.loads(_invoke(command, "plan", tmp_path, "--backup", "skip").output)
    assert auto["plan_id"] != skip["plan_id"]
    missing = _invoke(
        command,
        "apply",
        tmp_path,
        "--yes",
        "--backup",
        "skip",
        "--plan-id",
        skip["plan_id"],
        "--maintenance-confirmed",
    )
    assert missing.exit_code == 1
    assert json.loads(missing.output)["error"] == "confirmation_required"
    assert database.read_bytes() == before
    result = _invoke(
        command,
        "apply",
        tmp_path,
        "--yes",
        "--backup",
        "skip",
        "--plan-id",
        skip["plan_id"],
        "--maintenance-confirmed",
        "--accept-no-backup",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["backup_state"] == "skipped"
