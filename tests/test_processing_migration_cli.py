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

"""Offline maintenance commands expose resumable state without settings values."""

import asyncio
import json
import os
import sqlite3

import pytest
from click import unstyle
from typer.testing import CliRunner

from powercontext.builtin.persistence.bootstrap import MissingDatabaseError
from powercontext.builtin.persistence.seekdb import LegacySeekDBDatabaseError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.cli.app import create_cli
from powercontext.server import cli as server_cli
from powercontext.server.cli import app


def test_processing_maintenance_plan_apply_verify_require_maintenance_and_reuse_receipt(tmp_path, monkeypatch):
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'maintenance.db'}")

    async def setup():
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES):
            pass

    asyncio.run(setup())
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", config.url)
    cli = create_cli([app])
    runner = CliRunner()
    command = ["server", "processing-migrate"]
    plan = runner.invoke(cli, [*command, "--action", "plan"])
    assert plan.exit_code == 0, plan.output
    assert json.loads(plan.output)["required"] is True
    denied = runner.invoke(cli, [*command, "--action", "apply"])
    assert denied.exit_code == 2
    assert "maintenance-confirmed" in unstyle(denied.output)
    applied = runner.invoke(cli, [*command, "--action", "apply", "--maintenance-confirmed", "--batch-size", "1"])
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["ready"] is True
    verified = runner.invoke(cli, [*command, "--action", "verify"])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["ready"] is True
    replay = runner.invoke(cli, [*command, "--action", "apply", "--maintenance-confirmed"])
    assert replay.exit_code == 0, replay.output
    assert config.url not in plan.output + applied.output + verified.output + replay.output


class _ReadOnlyInspection:
    """A backend profile whose read-only opener refuses to provision the database."""

    @classmethod
    def open(cls, config, *, tables):
        raise AssertionError("read-only inspection must not use the provisioning opener")  # noqa: TRY003

    @classmethod
    def open_existing(cls, config, *, tables):
        raise MissingDatabaseError("configured_db")


@pytest.mark.parametrize("action", ["plan", "verify"])
@pytest.mark.parametrize("backend", ["oceanbase", "seekdb"])
def test_processing_inspection_never_provisions_the_configured_database(tmp_path, monkeypatch, action, backend):
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    # Exercise SDK upkeep after the separate Server ownership check.
    monkeypatch.setattr(server_cli, "_managed_storage", lambda _database: False)
    monkeypatch.setattr(server_cli, "OceanBaseProfile", _ReadOnlyInspection)
    monkeypatch.setattr(server_cli, "SeekDBProfile", _ReadOnlyInspection)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", backend)
    if backend == "oceanbase":
        monkeypatch.setenv(
            "POWERCONTEXT_SERVER_DATABASE_URL",
            "mysql+aoceanbase://user:password@host:2881/configured_db?charset=utf8mb4",
        )
    else:
        monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_PATH", str(tmp_path / "seekdb"))

    result = CliRunner().invoke(create_cli([app]), ["server", "processing-migrate", "--action", action])

    assert result.exit_code == 2, result.output
    assert "require an existing database" in unstyle(result.output)
    assert "configured_db" in unstyle(result.output)


@pytest.mark.parametrize("action", ["plan", "verify"])
@pytest.mark.parametrize("target_state", ["missing", "empty", "file", "unrecognized"])
def test_processing_inspection_preserves_uninitialized_seekdb_target(tmp_path, monkeypatch, action, target_state):
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    path = tmp_path / "missing-parent" / "seekdb"
    if target_state == "empty":
        path.mkdir(parents=True)
    elif target_state == "file":
        path.parent.mkdir()
        path.write_bytes(b"retained evidence")
    elif target_state == "unrecognized":
        path.mkdir(parents=True)
        (path / "user-content.txt").write_bytes(b"retained evidence")
    original = {
        item.relative_to(tmp_path): item.read_bytes() if item.is_file() else None for item in tmp_path.rglob("*")
    }

    def unexpected_binding():
        pytest.fail("inspection must reject an uninitialized target before starting the native engine")

    monkeypatch.setattr("powercontext.builtin.persistence.seekdb.profile._load_binding", unexpected_binding)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "seekdb")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_PATH", str(path))
    result = CliRunner().invoke(create_cli([app]), ["server", "processing-migrate", "--action", action])

    if target_state in {"file", "unrecognized"}:
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["error"] == "unsupported_target"
    else:
        assert result.exit_code == 2, result.output
        assert "existing seekdb instance" in unstyle(result.output)
    # Shared admission retains its coordination lock beside the target. It must
    # not initialize native storage or change any existing target contents.
    lock = path.with_name(path.name + ".pc-migration.lock")
    assert {
        item.relative_to(tmp_path): item.read_bytes() if item.is_file() else None
        for item in tmp_path.rglob("*")
        if item != lock
    } == original


@pytest.mark.parametrize("action", ["plan", "verify", "apply"])
def test_processing_legacy_seekdb_requires_an_explicit_database_choice(tmp_path, monkeypatch, action):
    class _LegacyRefusal:
        @classmethod
        def open(cls, config, *, tables):
            raise LegacySeekDBDatabaseError(config.path)

        @classmethod
        def open_existing(cls, config, *, tables):
            raise LegacySeekDBDatabaseError(config.path)

    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(server_cli, "SeekDBProfile", _LegacyRefusal)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "seekdb")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_PATH", str(tmp_path / "seekdb"))

    result = CliRunner().invoke(
        create_cli([app]), ["server", "processing-migrate", "--action", action, "--maintenance-confirmed"]
    )

    assert result.exit_code == 2, result.output
    assert "POWERCONTEXT_SERVER_DATABASE_DATABASE" in unstyle(result.output)
    assert "Traceback" not in result.output


def _isolate_server_environment(monkeypatch, config: SQLiteConfig) -> None:
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", config.url)


def test_processing_maintenance_plan_does_not_create_a_missing_database(tmp_path, monkeypatch):
    database = tmp_path / "absent" / "maintenance.db"
    _isolate_server_environment(monkeypatch, SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))

    result = CliRunner().invoke(create_cli([app]), ["server", "processing-migrate", "--action", "plan"])

    assert result.exit_code != 0
    assert not database.parent.exists()
    assert not database.exists()


def test_processing_maintenance_plan_leaves_the_stored_journal_mode_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "maintenance.db"
    asyncio.run(_create_plain_database(SQLiteConfig(url=f"sqlite+aiosqlite:///{path}", journal_mode="DELETE")))
    # The command is configured for WAL but must not write that setting while planning.
    _isolate_server_environment(monkeypatch, SQLiteConfig(url=f"sqlite+aiosqlite:///{path}", journal_mode="WAL"))

    result = CliRunner().invoke(create_cli([app]), ["server", "processing-migrate", "--action", "plan"])

    assert result.exit_code == 0, result.output
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)


async def _create_plain_database(config: SQLiteConfig) -> None:
    async with SQLiteProfile.open(config, tables=BUILTIN_TABLES):
        pass
