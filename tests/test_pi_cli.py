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

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.cli.system import SetupError, doctor_app, setup_app


def _write_pi_package(root: Path) -> Path:
    package = root / "integrations" / "pi" / "plugins" / "powercontext"
    (package / "extensions").mkdir(parents=True)
    (package / "skills" / "project-context").mkdir(parents=True)
    (package / "package.json").write_text(
        '{"name": "powercontext-pi", "pi": {"extensions": ["./extensions/powercontext.ts"]}}',
        encoding="utf-8",
    )
    (package / "extensions" / "powercontext.ts").write_text("export default () => {}\n", encoding="utf-8")
    (package / "skills" / "project-context" / "SKILL.md").write_text("# Project Context\n", encoding="utf-8")
    return package


def test_setup_pi_installs_the_native_package_and_reports_configuration(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.pi as pi_cli

    checkout = tmp_path / "powercontext"
    package = _write_pi_package(checkout)
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(pi_cli, "which", lambda _name: "/usr/bin/pi")

    monkeypatch.setattr(pi_cli, "_run_pi", lambda *_arguments: f"User packages:\n  {package}\n    {package}\n")

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "pi", "--source", str(checkout)])

    assert result.exit_code == 0
    assert "PowerContext Pi setup complete." in result.output
    assert str(package) in result.output


def test_setup_pi_refreshes_remote_source_and_replaces_previous_package(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.pi as pi_cli

    data_dir = tmp_path / "data"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(data_dir))
    monkeypatch.setattr(pi_cli, "which", lambda _name: "/usr/bin/pi")

    legacy_package = _write_pi_package(data_dir / "checkouts" / "pi" / "v0.0.1")
    current_root = pi_cli.checkout_target("master")
    current_package = _write_pi_package(current_root)
    (current_root / "source.txt").write_text("stale/source@master", encoding="utf-8")
    installed_packages = {str(legacy_package), str(current_package)}

    def fake_pi(*arguments: str) -> str:
        command, *values = arguments
        if command == "list":
            lines = ["User packages:"]
            for package in sorted(installed_packages):
                lines.extend((f"  {package}", f"    {package}"))
            return "\n".join(lines)
        if command == "remove":
            installed_packages.discard(values[0])
            return ""
        if command == "install":
            installed_packages.add(values[0])
            return ""
        raise AssertionError

    def clone(source: str, ref: str, target: Path) -> None:
        _write_pi_package(target)
        (target / "source.txt").write_text(f"{source}@{ref}", encoding="utf-8")

    monkeypatch.setattr(pi_cli, "_run_pi", fake_pi)
    monkeypatch.setattr(pi_cli, "_clone_github_source", clone)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "pi", "--source", "another/powercontext", "--ref", "master"],
    )

    refreshed_package = current_root / "integrations" / "pi" / "plugins" / "powercontext"
    assert result.exit_code == 0
    assert installed_packages == {str(refreshed_package)}
    assert (current_root / "source.txt").read_text(encoding="utf-8") == "another/powercontext@master"


def test_setup_pi_keeps_the_existing_package_when_refresh_installation_fails(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.pi as pi_cli

    data_dir = tmp_path / "data"
    checkout = tmp_path / "replacement"
    _write_pi_package(checkout)
    existing = _write_pi_package(tmp_path / "existing")
    installed_packages = {str(existing)}

    monkeypatch.setenv("POWERCONTEXT_HOME", str(data_dir))
    monkeypatch.setattr(pi_cli, "which", lambda _name: "/usr/bin/pi")

    def fake_pi(*arguments: str) -> str:
        command, *values = arguments
        if command == "list":
            return "\n".join(["User packages:", f"  {existing}", f"    {existing}"])
        if command == "install":
            raise SetupError.command_failed(["pi", "install"], "simulated Pi installation failure")
        if command == "remove":
            installed_packages.discard(values[0])
            return ""
        raise AssertionError

    monkeypatch.setattr(pi_cli, "_run_pi", fake_pi)

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "pi", "--source", str(checkout)])

    assert result.exit_code == 1
    assert "simulated Pi installation failure" in result.output
    assert installed_packages == {str(existing)}


def test_setup_pi_does_not_echo_source_credentials(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.pi as pi_cli

    marker = "redacted-value"
    source = f"https://{marker}@github.com/oceanbase/powercontext"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    with pytest.raises(SetupError) as raised:
        pi_cli.resolve_pi_plugin_dir(source=source, ref="master")

    assert marker not in str(raised.value)


def test_setup_pi_does_not_echo_git_clone_output(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.pi as pi_cli

    marker = "redacted-value"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(
        "subprocess.run",
        lambda command, **_kwargs: CompletedProcess(command, 1, "", f"fatal: https://{marker}@github.com/failed"),
    )

    with pytest.raises(SetupError) as raised:
        pi_cli.resolve_pi_plugin_dir(source="oceanbase/powercontext", ref="master")

    assert marker not in str(raised.value)


def test_doctor_pi_reports_a_missing_cli(monkeypatch) -> None:
    import powercontext.cli.pi as pi_cli

    monkeypatch.setattr(pi_cli, "which", lambda _name: None)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "pi"])

    assert result.exit_code == 1
    assert "pi: failed - Pi CLI is not installed or is not on PATH" in result.output
    assert "package: skipped - not checked because Pi CLI is unavailable" in result.output


def test_doctor_pi_reports_an_installed_package_as_json(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.pi as pi_cli

    package = _write_pi_package(tmp_path / "powercontext")
    monkeypatch.setattr(pi_cli, "which", lambda _name: "/usr/bin/pi")

    monkeypatch.setattr(pi_cli, "_run_pi", lambda *_arguments: f"User packages:\n  {package}\n    {package}\n")

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "pi", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["checks"]["package"] == {
        "ok": True,
        "status": "ok",
        "detail": "powercontext-pi is installed",
    }
