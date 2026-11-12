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
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import Mock

import httpx
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


@pytest.fixture
def pet_setup_boundary(setup_boundary, monkeypatch):
    from powercontext.cli import dsh_pet, system
    from powercontext.client import PowerContextClient

    plugin, native, inspect = setup_boundary
    adapter = plugin.parent / "dsh-pet"
    (adapter / "lib").mkdir(parents=True)
    (adapter / "lib/index.js").touch()
    (adapter / "package.json").write_text('{"name":"powercontext-dsh-pet","version":"0.1.0"}')
    packages = {"dsh-pet": {"entry": "enabled", "version": "0.4.8"}}
    inspect.return_value = {"petPackages": packages, "memoryProtocol": "atomic-artifact-v1"}
    monkeypatch.setattr(dsh_pet, "read_dsh_settings", inspect)

    def run_dsh(*args, **_):
        if args == ("--version",):
            return "0.2.2-rc.1\n"
        if args == ("plugin", "--profile", "web", "add", str(adapter)):
            packages["powercontext-dsh-pet"] = {"entry": "enabled", "version": "0.1.0"}
        return "id: powercontext-dsh\n"

    native.side_effect = run_dsh
    monkeypatch.setattr(system, "stdin_is_tty", lambda: True)
    scopes = [
        {
            "scope_id": scope_id,
            "title": "Pet",
            "summary": "Fixture",
            "version": 1,
            "context_references": [],
            "external_references": [],
        }
        for scope_id in ("one", "two")
    ]
    paths = []

    def respond(request):
        paths.append(request.url.path)
        if request.url.path == "/v1/scopes":
            return httpx.Response(200, json={"items": scopes})
        if request.url.path == "/v1/connection/identity":
            return httpx.Response(
                200,
                json={
                    "server_id": "fixture",
                    "authentication": "disabled",
                    "access_mode": "disabled",
                    "principal": None,
                },
            )
        if request.url.path == "/v1/scopes/two":
            return httpx.Response(200, json=scopes[1])
        return httpx.Response(404)

    @asynccontextmanager
    async def destination(server_url):
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client,
            PowerContextClient(server_url, http_client=http_client) as client,
        ):
            yield client

    monkeypatch.setattr(dsh_pet, "_destination_client", destination)
    args = ["setup", "dsh", "--profile", "web", "--source", str(plugin), "--server-url", "http://127.0.0.1:8000"]
    settings = Path(os.environ["DSH_HOME"]) / "profiles/web/.powercontext-dsh-pet/bindings.json"
    return args, packages, settings, paths


@pytest.mark.parametrize("scope_choice", ["2\n", "0\ntwo\n"])
def test_terminal_setup_offers_pet_memory_and_saves_the_selected_scope(pet_setup_boundary, scope_choice):
    args, packages, settings, _ = pet_setup_boundary
    runner = CliRunner()
    cli = create_cli([setup_app])
    result = runner.invoke(cli, args, input="y\n\n" + scope_choice)

    assert result.exit_code == 0, result.output
    assert "Enable dsh-pet long-term memory in the web profile?" in result.output
    assert "'Pet' (ID: 'one')" in result.output and "'Pet' (ID: 'two')" in result.output
    assert "dsh_pet_memory: enabled" in result.output
    assert packages["powercontext-dsh-pet"]["entry"] == "enabled"
    config = json.loads(settings.read_text(encoding="utf-8"))
    assert config["enabled"] is True
    assert [(row["pet_id"], row["scope_id"]) for row in config["bindings"]] == [("main", "two")]

    rerun = runner.invoke(cli, args)
    assert rerun.exit_code == 0, rerun.output
    assert "Enable dsh-pet long-term memory" not in rerun.output
    assert json.loads(settings.read_text(encoding="utf-8")) == config


@pytest.mark.parametrize("case", ["decline", "skip_scope", "json", "pipe", "incompatible", "unknown_scope"])
def test_setup_without_an_authorized_pet_binding_leaves_memory_off(pet_setup_boundary, monkeypatch, case):
    args, packages, settings, paths = pet_setup_boundary
    answers = {
        "decline": "\n",
        "skip_scope": "y\n\n\n",
        "json": "y\n\n2\n",
        "pipe": "y\n\n2\n",
        "incompatible": "y\n\n2\n",
        "unknown_scope": "y\n\n0\nunknown\n",
    }
    if case == "json":
        args = [*args, "--json"]
    if case == "pipe":
        monkeypatch.setattr("powercontext.cli.system.stdin_is_tty", lambda: False)
    if case == "incompatible":
        packages["dsh-pet"]["version"] = "0.4.7"
    result = CliRunner().invoke(create_cli([setup_app]), args, input=answers[case])

    assert result.exit_code == 0, result.output
    assert ("Enable dsh-pet long-term memory" in result.output) == (case in {"decline", "skip_scope", "unknown_scope"})
    assert ("/v1/scopes" in paths) == (case in {"skip_scope", "unknown_scope"})
    assert ("server_identity_scope_or_permissions_unverified" in result.output) == (case == "unknown_scope")
    assert "powercontext-dsh-pet" not in packages
    assert not settings.exists()


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
