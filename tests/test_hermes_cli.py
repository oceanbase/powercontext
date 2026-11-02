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
import shutil
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from typer.testing import CliRunner

import powercontext.cli.hermes as hermes_cli
from powercontext.cli.app import create_cli
from powercontext.cli.system import doctor_app, setup_app


def _write_plugin(root: Path, *, guidance: bool = True) -> Path:
    plugins_root = root / "integrations" / "hermes" / "plugins"
    plugin = plugins_root / "powercontext"
    command_plugin = plugins_root / "powercontext-command"
    plugin.mkdir(parents=True)
    command_plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("def register(): pass\n", encoding="utf-8")
    (plugin / "plugin.yaml").write_text("name: powercontext\n", encoding="utf-8")
    (command_plugin / "__init__.py").write_text("def register(ctx): pass\n", encoding="utf-8")
    (command_plugin / "plugin.yaml").write_text(
        "name: powercontext-command\nkind: standalone\n",
        encoding="utf-8",
    )
    if guidance:
        shutil.copytree(
            Path(__file__).parents[1] / "integrations/hermes/plugins/powercontext/skills", plugin / "skills"
        )
    return plugin


def _write_installed_plugins(hermes_home: Path) -> Path:
    plugins_root = hermes_home / "plugins"
    plugin = plugins_root / "powercontext"
    command_plugin = plugins_root / "powercontext-command"
    plugin.mkdir(parents=True)
    command_plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("def register(): pass\n", encoding="utf-8")
    (plugin / "plugin.yaml").write_text("name: powercontext\n", encoding="utf-8")
    (command_plugin / "__init__.py").write_text("def register(ctx): pass\n", encoding="utf-8")
    (command_plugin / "plugin.yaml").write_text(
        "name: powercontext-command\nkind: standalone\n",
        encoding="utf-8",
    )
    return plugin


def _successful_hermes_run(command: list[str], **_kwargs) -> CompletedProcess[str]:
    if command[1:] == ["--version"]:
        return CompletedProcess(command, 0, stdout="Hermes Agent v0.20.4 (2026.8.18)\n", stderr="")
    if command[1:3] == ["plugins", "doctor"]:
        return CompletedProcess(command, 0, stdout="Plugin Doctor: OK\n", stderr="")
    raise AssertionError(command)


def test_setup_hermes_copies_provider_from_a_local_checkout(
    tmp_path: Path, monkeypatch, installed_agent_checks, hermes_activation
) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    hermes_home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")
    old_plugin = hermes_home / "plugins" / "powercontext"
    old_plugin.mkdir(parents=True)
    (old_plugin / "removed_module.py").write_text("stale\n", encoding="utf-8")
    unrelated_plugin = hermes_home / "plugins" / "other-plugin"
    unrelated_plugin.mkdir()
    (unrelated_plugin / "keep.txt").write_text("keep\n", encoding="utf-8")
    monkeypatch.setattr(hermes_cli.subprocess, "run", _successful_hermes_run)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "hermes", "--source", str(checkout), "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["hosts"]["hermes"]["result"] == {
        "plugin": "powercontext",
        "plugin_path": str(hermes_home / "plugins" / "powercontext"),
        "hermes_home": str(hermes_home),
        "data_dir": str(tmp_path / "data"),
        "command_plugin_path": str(hermes_home / "plugins" / "powercontext-command"),
        "skill_path": str(hermes_home / "skills/powercontext-project-context"),
    }
    assert (hermes_home / "plugins" / "powercontext" / "plugin.yaml").is_file()
    assert (hermes_home / "plugins" / "powercontext-command" / "plugin.yaml").is_file()
    assert not (hermes_home / "plugins" / "powercontext" / "removed_module.py").exists()
    assert (unrelated_plugin / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_setup_hermes_preserves_customizations_when_moving_skill_to_native_directory(
    tmp_path: Path, monkeypatch, installed_agent_checks, hermes_activation
) -> None:
    checkout = tmp_path / "source"
    package = _write_plugin(checkout)
    home = tmp_path / "hermes"
    installed = _write_installed_plugins(home)
    shutil.copytree(package / "skills", installed / "skills")
    relative = Path("skills/powercontext-project-context")
    previous = installed / relative
    instructions = b"\r\nKeep my plugin-local instructions.\r\n"
    (previous / "SKILL.md").write_bytes((previous / "SKILL.md").read_bytes() + instructions)
    (previous / "personal.txt").write_bytes(b"Keep my notes\r\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_cli.subprocess, "run", _successful_hermes_run)
    arguments = ["setup", "hermes", "--source", str(checkout), "--json"]

    result = CliRunner().invoke(create_cli([setup_app]), arguments)
    assert result.exit_code == 0, result.output
    skill = home / relative
    assert (skill / "SKILL.md").read_bytes().endswith(instructions)
    assert (skill / "personal.txt").read_bytes() == b"Keep my notes\r\n"

    instructions = b"\r\nUse my current native Skill instructions.\r\n"
    (skill / "SKILL.md").write_bytes((package / relative / "SKILL.md").read_bytes() + instructions)
    (skill / "personal.txt").write_bytes(b"Updated native notes\r\n")
    result = CliRunner().invoke(create_cli([setup_app]), arguments)
    assert result.exit_code == 0, result.output
    assert (skill / "SKILL.md").read_bytes().endswith(instructions)
    assert (skill / "personal.txt").read_bytes() == b"Updated native notes\r\n"


def test_setup_hermes_refuses_package_omitting_installed_guidance(
    tmp_path: Path, monkeypatch, installed_agent_checks, hermes_activation
) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout, guidance=False)
    home = tmp_path / "hermes"
    installed = _write_installed_plugins(home)
    guidance = installed / "skills/powercontext-project-context/SKILL.md"
    guidance.parent.mkdir(parents=True)
    original = b"Preserve these installed instructions and notes.\r\n"
    guidance.write_bytes(original)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_cli.subprocess, "run", _successful_hermes_run)

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "hermes", "--source", str(checkout)])

    assert result.exit_code == 1
    assert "omits existing guidance" in result.output
    assert guidance.read_bytes() == original
    assert sorted(path.name for path in (home / "plugins").iterdir()) == ["powercontext", "powercontext-command"]


def test_setup_hermes_restores_both_plugins_when_second_replace_fails(
    tmp_path: Path, monkeypatch, installed_agent_checks, hermes_activation
) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    hermes_home = tmp_path / "hermes"
    old_provider = _write_installed_plugins(hermes_home)
    old_command = hermes_home / "plugins" / "powercontext-command"
    (old_provider / "version.txt").write_text("old provider\n", encoding="utf-8")
    (old_command / "version.txt").write_text("old command\n", encoding="utf-8")
    unrelated_plugin = hermes_home / "plugins" / "other-plugin"
    unrelated_plugin.mkdir()
    (unrelated_plugin / "keep.txt").write_text("keep\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_cli.subprocess, "run", _successful_hermes_run)

    original_replace = hermes_cli.os.replace
    failed = False

    def fail_second_replace(source, destination):
        nonlocal failed
        if not failed and Path(destination) == old_command and Path(source).name == old_command.name:
            failed = True
            raise OSError
        original_replace(source, destination)

    monkeypatch.setattr(hermes_cli.os, "replace", fail_second_replace)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "hermes", "--source", str(checkout)],
    )

    assert result.exit_code == 1
    assert failed
    assert (old_provider / "version.txt").read_text(encoding="utf-8") == "old provider\n"
    assert (old_command / "version.txt").read_text(encoding="utf-8") == "old command\n"
    assert (unrelated_plugin / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert not list((hermes_home / "plugins").glob(".powercontext*"))


def test_setup_hermes_keeps_both_plugins_when_validation_fails(
    tmp_path: Path, monkeypatch, installed_agent_checks, hermes_activation
) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    hermes_home = tmp_path / "hermes"
    old_provider = _write_installed_plugins(hermes_home)
    old_command = hermes_home / "plugins" / "powercontext-command"
    (old_provider / "version.txt").write_text("old provider\n", encoding="utf-8")
    (old_command / "version.txt").write_text("old command\n", encoding="utf-8")
    unrelated_plugin = hermes_home / "plugins" / "other-plugin"
    unrelated_plugin.mkdir()
    (unrelated_plugin / "keep.txt").write_text("keep\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_cli.subprocess, "run", _successful_hermes_run)

    (checkout / "integrations/hermes/plugins/powercontext-command/plugin.yaml").unlink()

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "hermes", "--source", str(checkout)],
    )

    assert result.exit_code == 1
    assert (old_provider / "version.txt").read_text(encoding="utf-8") == "old provider\n"
    assert (old_command / "version.txt").read_text(encoding="utf-8") == "old command\n"
    assert (unrelated_plugin / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert not list((hermes_home / "plugins").glob(".powercontext*"))


def test_setup_hermes_reports_missing_cli(
    tmp_path: Path, monkeypatch, installed_agent_checks, hermes_activation
) -> None:
    monkeypatch.setattr(hermes_cli, "which", lambda _name: None)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "hermes", "--source", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "Hermes CLI is not installed" in result.output


def test_doctor_hermes_reports_an_installed_provider(tmp_path: Path, monkeypatch, server_checks) -> None:
    hermes_home = tmp_path / "hermes"
    _write_installed_plugins(hermes_home)
    (hermes_home / "config.yaml").write_text(
        "memory:\n  provider: powercontext\nplugins:\n  enabled: [powercontext-command]\n"
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_cli.subprocess, "run", _successful_hermes_run)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "hermes", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)["hosts"]["hermes"]
    assert payload["ok"] is True
    assert payload["checks"]["hermes"] == {
        "ok": True,
        "status": "ok",
        "detail": "/usr/bin/hermes (Hermes Agent v0.20.4)",
    }
    assert payload["checks"]["plugin"] == {
        "ok": True,
        "status": "ok",
        "detail": "powercontext passed Hermes plugin doctor",
    }
    assert payload["checks"]["command_plugin"] == {
        "ok": True,
        "status": "ok",
        "detail": "powercontext-command passed Hermes plugin doctor",
    }


def test_doctor_hermes_reports_missing_provider(tmp_path: Path, monkeypatch, server_checks) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_cli.subprocess, "run", _successful_hermes_run)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "hermes"])

    assert result.exit_code == 1
    assert "plugin: failed - PowerContext Hermes plugin is not installed" in result.output


def test_doctor_hermes_rejects_a_broken_provider(tmp_path: Path, monkeypatch, server_checks) -> None:
    hermes_home = tmp_path / "hermes"
    _write_installed_plugins(hermes_home)
    (hermes_home / "config.yaml").write_text(
        "memory:\n  provider: powercontext\nplugins:\n  enabled: [powercontext-command]\n"
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")

    def failed_plugin_doctor(command: list[str], **_kwargs) -> CompletedProcess[str]:
        if command[1:] == ["--version"]:
            return CompletedProcess(command, 0, stdout="Hermes Agent v0.20.4\n", stderr="")
        return CompletedProcess(command, 1, stdout="", stderr="ImportError: cannot import client\n")

    monkeypatch.setattr(hermes_cli.subprocess, "run", failed_plugin_doctor)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "hermes", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)["hosts"]["hermes"]
    assert payload["ok"] is False
    assert payload["checks"]["plugin"]["status"] == "failed"
    assert "ImportError" in payload["checks"]["plugin"]["detail"]


def test_doctor_hermes_rejects_an_unsupported_version(monkeypatch, server_checks) -> None:
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")

    def old_hermes_run(command: list[str], **_kwargs) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, stdout="Hermes Agent v0.20.3\n", stderr="")

    monkeypatch.setattr(hermes_cli.subprocess, "run", old_hermes_run)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "hermes", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)["hosts"]["hermes"]
    assert payload["checks"]["hermes"]["status"] == "failed"
    assert "requires Hermes Agent v0.20.4 or newer" in payload["checks"]["hermes"]["detail"]
    assert payload["checks"]["plugin"]["status"] == "skipped"


@pytest.mark.parametrize("provider", ["", "other-provider"])
def test_setup_preserves_hermes_provider_and_companion_disablement(
    tmp_path, monkeypatch, hermes_activation, server_checks, provider
):
    checkout = tmp_path / "source"
    _write_plugin(checkout)
    home = tmp_path / "hermes"
    home.mkdir()
    config = home / "config.yaml"
    original = f"memory:\n  provider: '{provider}'\nplugins:\n  disabled: [powercontext-command]\n"
    config.write_text(original)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_cli.subprocess, "run", _successful_hermes_run)
    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "hermes", "--source", str(checkout), "--json"])
    assert result.exit_code == 1, result.output
    row = json.loads(result.output)["hosts"]["hermes"]
    assert row["setup"] == "complete"
    assert row["checks"]["provider"]["status"] == "failed"
    assert row["checks"]["command_enabled"]["status"] == "failed"
    assert config.read_text() == original


def test_failed_hermes_configuration_can_finish_activation_on_retry(
    tmp_path, monkeypatch, hermes_activation, server_checks
):
    from powercontext.cli import installation
    from powercontext.cli.transport import client_config_file

    checkout = tmp_path / "source"
    _write_plugin(checkout)
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_cli.subprocess, "run", _successful_hermes_run)
    write = installation.write_bytes

    def failed_connection(path, content):
        if path == client_config_file():
            raise OSError("cannot write connection")  # noqa: TRY003
        write(path, content)

    arguments = ["setup", "hermes", "--source", str(checkout), "--json"]
    with monkeypatch.context() as failure:
        failure.setattr(installation, "write_bytes", failed_connection)
        first = CliRunner().invoke(create_cli([setup_app]), arguments)
    assert first.exit_code == 1
    assert not (home / "config.yaml").exists()
    retried = CliRunner().invoke(create_cli([setup_app]), arguments)
    assert retried.exit_code == 0, retried.output
    assert json.loads(retried.output)["hosts"]["hermes"]["checks"]["provider"]["ok"]
