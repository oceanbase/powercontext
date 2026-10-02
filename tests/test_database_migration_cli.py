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

from typer.testing import CliRunner

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


def test_remote_backend_is_not_reported_ready_without_acceptance(tmp_path, monkeypatch):
    _database, command = _configure(tmp_path, monkeypatch)
    directory = tmp_path / "seekdb"
    (tmp_path / "deployment.env").write_text(
        f"POWERCONTEXT_SERVER_DATABASE_KIND=seekdb\nPOWERCONTEXT_SERVER_DATABASE_PATH={directory}\n",
        encoding="utf-8",
    )
    result = _invoke(command, "status", tmp_path)
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "unsupported_backend"
    assert not directory.exists()


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
