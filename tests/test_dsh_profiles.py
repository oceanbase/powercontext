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

"""Profile selection and Desktop prerequisites through the public CLI."""

import json
import os
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.cli.dsh_runtime import DshProfile, desktop_runtime, resolve_dsh_target
from powercontext.cli.system import doctor_app, setup_app


@pytest.fixture
def desktop_command(tmp_path):
    resources = tmp_path / "DeepSeek Harness" / "resources"
    command = resources / "runtime/cli/bin/dsh.cmd"
    command.parent.mkdir(parents=True)
    command.write_text("@echo off\n")
    (resources / "app.asar").touch()
    (resources.parent / "DeepSeek Harness.exe").touch()
    return command


@pytest.fixture
def setup_boundary(tmp_path, monkeypatch):
    import powercontext.cli.dsh as dsh
    import powercontext.cli.dsh_transport as transport

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    plugin = tmp_path / "plugin"
    (plugin / "lib").mkdir(parents=True)
    (plugin / "package.json").write_text('{"name":"powercontext-dsh"}')
    (plugin / "lib/index.js").write_text("export const name = 'powercontext-dsh'\n")
    shutil.copytree(
        Path(__file__).parents[1] / "integrations/dsh/plugins/powercontext/skills",
        plugin / "skills",
    )
    monkeypatch.setattr(dsh, "dsh_executable", lambda: "npm-dsh")
    inspect = Mock(return_value={})
    monkeypatch.setattr(transport, "read_dsh_settings", inspect)
    run = Mock(return_value="id: powercontext-dsh\n")
    monkeypatch.setattr(dsh, "_run_dsh", run)
    return plugin, run, inspect


@pytest.mark.parametrize("profile", ["web", "desktop"])
def test_setup_and_default_doctor_reuse_the_selected_native_target(
    tmp_path, monkeypatch, desktop_command, setup_boundary, profile, server_checks
):
    plugin, _, inspect = setup_boundary
    inspect.return_value = {"memoryProtocol": "atomic-artifact-v1"}
    desktop_dir = Path(os.environ["DSH_HOME"]) / "profiles/desktop"
    desktop_dir.mkdir(parents=True)
    (desktop_dir / "package.json").write_text("{}")
    result = CliRunner().invoke(
        create_cli(),
        [
            "setup",
            "dsh",
            "--source",
            str(plugin),
            "--dsh-command",
            str(desktop_command),
            "--profile",
            profile,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    installed = json.loads(result.stdout)["hosts"]["dsh"]["result"]
    assert installed["profile"] == profile
    assert installed["profile_dir"] == str(Path(os.environ["DSH_HOME"]) / "profiles" / profile)
    original = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    doctor = CliRunner().invoke(create_cli(), ["doctor", "--json"])
    assert doctor.exit_code == 0, doctor.output
    plugin_check = json.loads(doctor.output)["hosts"]["dsh"]["checks"]["plugin"]
    assert ("Desktop" if profile == "desktop" else "Web") in plugin_check["detail"]
    assert plugin_check["checks"]["running_host_configuration"] == "not_observed"
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == original
    configured = CliRunner().invoke(create_cli(), ["config", "agent", "dsh", "--json"])
    assert configured.exit_code == 0, configured.output


def test_uninitialized_desktop_fails_before_installation(tmp_path, desktop_command, setup_boundary):
    plugin, run, inspect = setup_boundary
    result = CliRunner().invoke(
        create_cli([setup_app]),
        [
            "setup",
            "dsh",
            "--profile",
            "desktop",
            "--dsh-command",
            str(desktop_command),
            "--source",
            str(plugin),
        ],
    )
    assert result.exit_code == 1
    assert "Open Desktop once" in result.output
    assert not (Path(os.environ["DSH_HOME"]) / "profiles/desktop").exists()
    assert not (tmp_path / "data").exists()
    run.assert_not_called()
    inspect.assert_not_called()


@pytest.mark.parametrize(
    ("profile", "available_command", "error"),
    [
        ("web", False, "does not exist"),
        ("desktop", False, "does not exist"),
        ("desktop", True, "Open Desktop once"),
    ],
)
def test_doctor_keeps_plugin_check_when_target_is_unavailable(
    tmp_path, desktop_command, profile, available_command, error
):
    command = desktop_command if available_command else tmp_path / "missing-dsh.cmd"
    result = CliRunner().invoke(
        create_cli([doctor_app]),
        ["doctor", "dsh", "--profile", profile, "--dsh-command", str(command), "--json"],
    )
    assert result.exit_code == 1
    report = json.loads(result.stdout)["hosts"]["dsh"]
    assert report["status"] == "failed"
    assert report["checks"]["verification"]["status"] == "failed"
    assert error in report["checks"]["verification"]["detail"]
    assert not (Path(os.environ["DSH_HOME"]) / "profiles").exists()


def test_desktop_rejects_npm_command_without_writes(tmp_path, setup_boundary):
    plugin, run, _ = setup_boundary
    command = tmp_path / "dsh.cmd"
    command.write_text("@echo off\n")
    result = CliRunner().invoke(
        create_cli([setup_app]),
        [
            "setup",
            "dsh",
            "--profile",
            "desktop",
            "--dsh-command",
            str(command),
            "--source",
            str(plugin),
        ],
    )
    assert result.exit_code == 1
    assert "Desktop-installed dsh" in result.output
    assert "Manage dsh Command" in result.output
    run.assert_not_called()


def test_invalid_profile_never_installs(setup_boundary):
    _, run, _ = setup_boundary
    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "dsh", "--profile", "../other"])
    assert result.exit_code == 2
    run.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows installed command layout")
def test_desktop_searches_path_past_npm_shim(tmp_path, monkeypatch, desktop_command):
    npm = tmp_path / "npm"
    npm.mkdir()
    (npm / "dsh.cmd").write_text("@echo off\n")
    monkeypatch.setenv("PATH", os.pathsep.join([str(npm), str(desktop_command.parent)]))
    profile = Path(os.environ["DSH_HOME"]) / "profiles/desktop"
    profile.mkdir(parents=True)
    (profile / "package.json").write_text("{}")
    assert resolve_dsh_target(DshProfile.DESKTOP).command == str(desktop_command)


def test_macos_launcher_resolves_its_signed_runtime(tmp_path):
    resources = tmp_path / "DeepSeek Harness.app/Contents/Resources"
    command = resources / "runtime/cli/bin/dsh"
    command.parent.mkdir(parents=True)
    command.touch()
    electron = resources.parent / "MacOS/DeepSeek Harness"
    electron.parent.mkdir()
    electron.touch()
    (resources / "app.asar").touch()
    runtime = desktop_runtime(str(command))
    assert runtime is not None
    assert runtime.electron == electron
    assert str(runtime.anchor).endswith(str(Path("app.asar/dsh/node_modules/@deepseek-ai/dsh/package.json")))
