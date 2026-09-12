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

import asyncio

from click.utils import strip_ansi
from typer.testing import CliRunner

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.cli.app import create_cli
from powercontext.cli.archive import archive_app
from powercontext.paths import default_database_path, sqlite_url


def test_archive_cli_exposes_a_safe_restore_confirmation_and_dry_run() -> None:
    cli = create_cli([archive_app])
    runner = CliRunner()

    help_result = runner.invoke(cli, ["archive", "--help"], terminal_width=160)
    restore_help = runner.invoke(cli, ["archive", "restore", "--help"], terminal_width=160)

    help_output = strip_ansi(help_result.output)
    restore_output = strip_ansi(restore_help.output)

    assert help_result.exit_code == 0
    assert all(command in help_output for command in ("export", "inspect", "restore"))
    assert restore_help.exit_code == 0
    assert "--dry-run" in restore_output
    assert "--yes" in restore_output


def test_archive_cli_can_export_inspect_and_dry_run_a_scope(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    output = tmp_path / "scope.pcb"
    cli = create_cli([archive_app])
    runner = CliRunner()

    async def default_scope_id() -> str:
        config = BuiltinConfig(database=SQLiteConfig(url=sqlite_url(default_database_path())))
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.default_scope()
            assert scope is not None
            return scope.scope_id

    scope_id = asyncio.run(default_scope_id())
    exported = runner.invoke(cli, ["archive", "export", "--scope-id", scope_id, "--output", str(output)])
    inspected = runner.invoke(cli, ["archive", "inspect", str(output)])
    validated = runner.invoke(cli, ["archive", "restore", str(output), "--dry-run"])

    assert exported.exit_code == 0, exported.output
    assert output.is_file()
    assert inspected.exit_code == 0, inspected.output
    assert validated.exit_code == 0, validated.output


def test_archive_cli_reports_corrupt_or_missing_bundles_without_a_traceback() -> None:
    result = CliRunner().invoke(create_cli([archive_app]), ["archive", "inspect", "missing.pcb"])

    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
    assert "error: archive validation failed: cannot read portable bundle" in result.output
    assert "Traceback" not in result.output
