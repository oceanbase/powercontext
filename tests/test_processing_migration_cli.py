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

from click import unstyle
from typer.testing import CliRunner

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.cli.app import create_cli
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
