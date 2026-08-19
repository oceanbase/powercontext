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

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.cli.system import SetupError, doctor_app, setup_app


def _write_plugin(root: Path, *, built: bool = True) -> Path:
    plugin = root / "integrations" / "dsh" / "plugins" / "powercontext"
    plugin.mkdir(parents=True)
    (plugin / "package.json").write_text('{"name": "powercontext-dsh"}', encoding="utf-8")
    if built:
        (plugin / "lib").mkdir()
        (plugin / "lib" / "index.js").write_text("export const name = 'powercontext-dsh'\n", encoding="utf-8")
    return plugin


def test_dsh_executable_prefers_the_windows_cmd_shim(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.dsh as dsh_cli

    cmd = tmp_path / "dsh.cmd"
    cmd.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(dsh_cli.os, "name", "nt")
    monkeypatch.setattr(dsh_cli, "which", lambda name: str(cmd) if name == "dsh.cmd" else None)

    assert dsh_cli.dsh_executable() == str(cmd)


def test_setup_dsh_rejects_plugin_without_bundle(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.dsh as dsh_cli

    checkout = tmp_path / "powercontext"
    _write_plugin(checkout, built=False)
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(dsh_cli, "which", lambda _name: "/usr/bin/dsh")
    run_dsh = Mock(return_value="id: powercontext-dsh\n")
    monkeypatch.setattr(dsh_cli, "_run_dsh", run_dsh)

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "dsh", "--source", str(checkout)])

    assert result.exit_code == 1
    assert "lib/index.js" in result.output
    run_dsh.assert_not_called()


def test_setup_dsh_rejects_a_ref_that_escapes_the_checkout_root(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.dsh as dsh_cli

    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    with pytest.raises(SetupError, match="invalid DeepSeek Harness ref"):
        dsh_cli.resolve_dsh_plugin_dir(source="oceanbase/powercontext", ref="../../etc")


def test_setup_dsh_clones_a_github_url_and_replaces_a_broken_checkout(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.dsh as dsh_cli

    home = tmp_path / "data"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(home))
    stale = home / "checkouts" / "dsh" / "master"
    stale.mkdir(parents=True)
    (stale / "README").write_text("incomplete", encoding="utf-8")
    captured: list[list[str]] = []

    def fake_run(command, **_kwargs):
        captured.append(command)
        checkout = Path(command[-1])
        _write_plugin(checkout)
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(dsh_cli.subprocess, "run", fake_run)

    plugin = dsh_cli.resolve_dsh_plugin_dir(
        source="https://github.com/oceanbase/powercontext",
        ref="master",
    )

    assert plugin == stale / "integrations" / "dsh" / "plugins" / "powercontext"
    assert captured[0][:4] == ["git", "clone", "--depth", "1"]
    assert captured[0][4:6] == ["--branch", "master"]
    assert captured[0][6] == "https://github.com/oceanbase/powercontext.git"
    assert not (stale / "README").exists()


def test_doctor_dsh_requires_the_plugin_id_field(monkeypatch) -> None:
    import powercontext.cli.dsh as dsh_cli

    monkeypatch.setattr(dsh_cli, "which", lambda _name: "/usr/bin/dsh")
    monkeypatch.setattr(dsh_cli, "_run_dsh", lambda *_args: "name: powercontext-dsh\n")

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "dsh"])

    assert result.exit_code == 1
    assert "plugin: failed - PowerContext DSH plugin is not installed" in result.output


def test_doctor_dsh_reports_an_installed_plugin(monkeypatch) -> None:
    import powercontext.cli.dsh as dsh_cli

    monkeypatch.setattr(dsh_cli, "which", lambda _name: "/usr/bin/dsh")
    monkeypatch.setattr(
        dsh_cli,
        "_run_dsh",
        lambda *_args: "- id: powercontext-dsh\n  name: powercontext-dsh\n",
    )

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "dsh", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["checks"]["plugin"] == {
        "ok": True,
        "status": "ok",
        "detail": "powercontext-dsh is installed",
    }
