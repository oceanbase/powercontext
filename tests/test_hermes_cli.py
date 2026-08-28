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

from typer.testing import CliRunner

import powercontext.cli.hermes as hermes_cli
from powercontext.cli.app import create_cli
from powercontext.cli.system import SetupError, doctor_app, setup_app


def _write_plugin(root: Path) -> Path:
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
    if command[1:3] == ["plugins", "enable"]:
        return CompletedProcess(command, 0, stdout="Plugin enabled\n", stderr="")
    raise AssertionError(command)


def test_setup_hermes_copies_provider_from_a_local_checkout(tmp_path: Path, monkeypatch) -> None:
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
    assert json.loads(result.output) == {
        "plugin": "powercontext",
        "plugin_path": str(hermes_home / "plugins" / "powercontext"),
        "hermes_home": str(hermes_home),
        "data_dir": str(tmp_path / "data"),
        "command_plugin_path": str(hermes_home / "plugins" / "powercontext-command"),
    }
    assert (hermes_home / "plugins" / "powercontext" / "plugin.yaml").is_file()
    assert (hermes_home / "plugins" / "powercontext-command" / "plugin.yaml").is_file()
    assert not (hermes_home / "plugins" / "powercontext" / "removed_module.py").exists()
    assert (unrelated_plugin / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_setup_hermes_restores_both_plugins_when_second_replace_fails(tmp_path: Path, monkeypatch) -> None:
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
        if not failed and Path(destination) == old_command and Path(source).name.startswith(f".{old_command.name}-"):
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


def test_setup_hermes_restores_both_plugins_when_enable_fails(tmp_path: Path, monkeypatch) -> None:
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

    enabled = False

    def fail_enable(_executable: str, _name: str) -> None:
        nonlocal enabled
        enabled = True
        raise SetupError

    monkeypatch.setattr(hermes_cli, "_enable_hermes_plugin", fail_enable)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "hermes", "--source", str(checkout)],
    )

    assert result.exit_code == 1
    assert enabled
    assert (old_provider / "version.txt").read_text(encoding="utf-8") == "old provider\n"
    assert (old_command / "version.txt").read_text(encoding="utf-8") == "old command\n"
    assert (unrelated_plugin / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert not list((hermes_home / "plugins").glob(".powercontext*"))


def test_setup_hermes_reports_missing_cli(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(hermes_cli, "which", lambda _name: None)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "hermes", "--source", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "Hermes CLI is not installed" in result.output


def test_doctor_hermes_reports_an_installed_provider(tmp_path: Path, monkeypatch) -> None:
    hermes_home = tmp_path / "hermes"
    _write_installed_plugins(hermes_home)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_cli.subprocess, "run", _successful_hermes_run)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "hermes", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
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


def test_doctor_hermes_reports_missing_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")
    monkeypatch.setattr(hermes_cli.subprocess, "run", _successful_hermes_run)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "hermes"])

    assert result.exit_code == 1
    assert "hermes: ok - /usr/bin/hermes (Hermes Agent v0.20.4)" in result.output
    assert "plugin: failed - PowerContext Hermes plugin is not installed" in result.output


def test_doctor_hermes_rejects_a_broken_provider(tmp_path: Path, monkeypatch) -> None:
    hermes_home = tmp_path / "hermes"
    _write_installed_plugins(hermes_home)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")

    def failed_plugin_doctor(command: list[str], **_kwargs) -> CompletedProcess[str]:
        if command[1:] == ["--version"]:
            return CompletedProcess(command, 0, stdout="Hermes Agent v0.20.4\n", stderr="")
        return CompletedProcess(command, 1, stdout="", stderr="ImportError: cannot import client\n")

    monkeypatch.setattr(hermes_cli.subprocess, "run", failed_plugin_doctor)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "hermes", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["checks"]["plugin"]["status"] == "failed"
    assert "ImportError" in payload["checks"]["plugin"]["detail"]


def test_doctor_hermes_rejects_an_unsupported_version(monkeypatch) -> None:
    monkeypatch.setattr(hermes_cli, "which", lambda _name: "/usr/bin/hermes")
    calls: list[list[str]] = []

    def old_hermes_run(command: list[str], **_kwargs) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, stdout="Hermes Agent v0.20.3\n", stderr="")

    monkeypatch.setattr(hermes_cli.subprocess, "run", old_hermes_run)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "hermes", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["checks"]["hermes"]["status"] == "failed"
    assert "requires Hermes Agent v0.20.4 or newer" in payload["checks"]["hermes"]["detail"]
    assert payload["checks"]["plugin"]["status"] == "skipped"
    assert len(calls) == 1


def test_setup_hermes_remote_checkout_uses_the_requested_ref(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    captured: list[list[str]] = []

    def fake_run(command, **_kwargs):
        captured.append(command)
        _write_plugin(Path(command[-1]))
        return Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hermes_cli.subprocess, "run", fake_run)

    source = "oceanbase/powercontext"
    ref = "v0.0.2"
    plugin = hermes_cli.resolve_hermes_plugin_dir(source=source, ref=ref)

    assert plugin == hermes_cli.checkout_target(source, ref) / "integrations" / "hermes" / "plugins" / "powercontext"
    assert captured[0][:6] == ["git", "clone", "--depth", "1", "--branch", "v0.0.2"]
    assert captured[0][6] == "https://github.com/oceanbase/powercontext.git"


def test_remote_checkout_is_source_scoped_and_refreshes_mutable_refs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    captured: list[list[str]] = []

    def fake_run(command, **_kwargs):
        captured.append(command)
        _write_plugin(Path(command[-1]))
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(hermes_cli.subprocess, "run", fake_run)

    first = hermes_cli.resolve_hermes_plugin_dir(source="oceanbase/powercontext", ref="master")
    refreshed = hermes_cli.resolve_hermes_plugin_dir(source="oceanbase/powercontext", ref="master")
    other_source = hermes_cli.resolve_hermes_plugin_dir(source="other/project", ref="master")

    assert first == refreshed
    assert first != other_source
    assert len(captured) == 3
