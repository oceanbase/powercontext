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

from powercontext.cli import git_source
from powercontext.cli.app import create_cli
from powercontext.cli.git_source import resolve_plugin_source
from powercontext.cli.system import SetupError, doctor_app, setup_app


def _write_pi_package(root: Path) -> Path:
    package = root / "integrations" / "pi" / "plugins" / "powercontext"
    (package / "extensions").mkdir(parents=True)
    (package / "skills" / "powercontext-project-context").mkdir(parents=True)
    (package / "package.json").write_text(
        '{"name": "powercontext-pi", "powercontext": {"memoryProtocol": "atomic-artifact-v1"}, "pi": {"extensions": ["./extensions/powercontext.ts"]}}',
        encoding="utf-8",
    )
    (package / "extensions" / "powercontext.ts").write_text("export default () => {}\n", encoding="utf-8")
    (package / "skills" / "powercontext-project-context" / "SKILL.md").write_text(
        "# Project Context\n", encoding="utf-8"
    )
    return package


def test_setup_pi_installs_the_native_package_and_reports_configuration(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    import powercontext.cli.pi as pi_cli

    checkout = tmp_path / "powercontext"
    package = _write_pi_package(checkout)
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(pi_cli, "which", lambda _name: "/usr/bin/pi")

    monkeypatch.setattr(pi_cli, "_run_pi", lambda *_arguments: f"User packages:\n  {package}\n    {package}\n")

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "pi", "--source", str(checkout)])

    assert result.exit_code == 0
    assert "pi: ok" in result.output
    assert str(tmp_path / "data/plugins/pi") in result.output


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:18000", "https://remote.example/proxy"])
def test_setup_pi_binds_runtime_and_credentials_to_selected_endpoint(
    tmp_path: Path, monkeypatch, endpoint: str, installed_agent_checks
) -> None:
    """A real installer persists one endpoint for runtime and URL-bound credentials."""
    import powercontext.cli.pi as pi_cli
    from powercontext.cli.authorization import credential_path, read_stored_authorization
    from powercontext.client.transport_policy import load_client_settings

    package = _write_pi_package(tmp_path / "checkout")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    monkeypatch.setenv("POWERCONTEXT_PI_AUTHORIZATION", "test-token")
    monkeypatch.setattr(pi_cli, "which", lambda _name: "/usr/bin/pi")
    monkeypatch.setattr(pi_cli, "_run_pi", lambda *_args: f"User packages:\n  {package}\n    {package}\n")
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "pi", "--source", str(package), "--server-url", endpoint]
    )
    assert result.exit_code == 0, result.output
    assert load_client_settings("pi")["server_url"] == endpoint
    assert read_stored_authorization(credential_path("pi"), server_url=endpoint).authorization == "Bearer test-token"
    assert read_stored_authorization(credential_path("pi"), server_url="https://other.example").status == "url_mismatch"


def test_setup_pi_reuses_its_owned_path_and_preserves_other_registrations(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    import powercontext.cli.pi as pi_cli

    data_dir = tmp_path / "data"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(data_dir))
    monkeypatch.setattr(pi_cli, "which", lambda _name: "/usr/bin/pi")

    legacy_package = _write_pi_package(data_dir / "checkouts" / "pi" / "v0.0.1")
    current_root = data_dir / "checkouts/pi/current"
    current_package = _write_pi_package(current_root)
    (current_root / "source.txt").write_text("stale/source@master", encoding="utf-8")
    unrelated = _write_pi_package(tmp_path / "unrelated")
    (unrelated / "package.json").write_text('{"name":"another-plugin"}', encoding="utf-8")
    project_package = _write_pi_package(tmp_path / "project")
    installed_packages = {str(legacy_package), str(current_package), str(unrelated)}

    def fake_pi(*arguments: str) -> str:
        command, *values = arguments
        if command == "list":
            lines = ["User packages:"]
            for package in sorted(installed_packages):
                lines.extend((f"  {package}", f"    {package}"))
            lines.extend(("Project packages:", f"  {project_package}", f"    {project_package}"))
            return "\n".join(lines)
        if command == "remove":
            assert values[0] != str(project_package)
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
    monkeypatch.setattr(git_source, "clone_github_source", clone)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "pi", "--source", "another/powercontext", "--ref", "master"],
    )

    assert result.exit_code == 0, result.output
    refreshed_package = data_dir / "plugins/pi"
    assert installed_packages == {str(unrelated), str(refreshed_package)}
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "pi", "--source", "another/powercontext", "--ref", "master"]
    )
    assert result.exit_code == 0, result.output
    assert installed_packages == {str(unrelated), str(refreshed_package)}
    assert (refreshed_package / "extensions/powercontext.ts").is_file()
    assert (legacy_package / "package.json").is_file()
    assert (project_package / "package.json").is_file()


@pytest.mark.parametrize("installed", [False, True])
def test_setup_pi_rejects_filtered_legacy_migration_without_changing_host(
    tmp_path: Path, monkeypatch, installed_agent_checks, installed: bool
) -> None:
    import powercontext.cli.pi as pi_cli

    package = _write_pi_package(tmp_path / "checkout")
    legacy = _write_pi_package(tmp_path / "legacy")
    home = tmp_path / "home"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(home / "data"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(home / "agent"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(home / "clients.json"))
    monkeypatch.setattr(pi_cli, "which", lambda _name: "pi")
    destination = home / "data/plugins/pi"
    if installed:
        destination.mkdir(parents=True)
        (destination / ".powercontext-owned").write_text("powercontext-pi\n", encoding="utf-8")
        (destination / "release.txt").write_text("old release", encoding="utf-8")
    registry = {"packages": [{"source": str(legacy), "extensions": [], "skills": []}]}
    settings = home / "agent/settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(registry), encoding="utf-8")

    def native_command(*arguments):
        if arguments[0] == "list":
            return f"User packages:\n  {legacy} (filtered)\n    {legacy}\n"
        if arguments[0] == "install":
            registry["packages"].append(arguments[1])
            settings.write_text(json.dumps(registry), encoding="utf-8")
        return ""

    monkeypatch.setattr(pi_cli, "_run_pi", native_command)
    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "pi", "--source", str(package), "--json"])

    assert result.exit_code == 1, result.output
    assert "resource filters" in json.loads(result.output)["hosts"]["pi"]["error"]
    assert {path: path.read_bytes() for path in home.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("owned", [False, True], ids=["checkout", "owned-package"])
def test_setup_pi_keeps_the_existing_package_when_refresh_installation_fails(
    tmp_path: Path, monkeypatch, installed_agent_checks, owned: bool
) -> None:
    import powercontext.cli.pi as pi_cli

    data_dir = tmp_path / "data"
    checkout = tmp_path / "replacement"
    _write_pi_package(checkout)
    existing = _write_pi_package(tmp_path / "existing")
    installed_packages = {str(existing)}
    (existing / "extensions/powercontext.ts").write_text("// working extension\n", encoding="utf-8")
    fail_install = False

    monkeypatch.setenv("POWERCONTEXT_HOME", str(data_dir))
    monkeypatch.setattr(pi_cli, "which", lambda _name: "/usr/bin/pi")

    def fake_pi(*arguments: str) -> str:
        command, *values = arguments
        if command == "list":
            return "User packages:\n" + "".join(f"  {path}\n    {path}\n" for path in installed_packages)
        if command == "install":
            if fail_install:
                message = "`pi install` failed: simulated Pi installation failure"
                raise SetupError(message)
            installed_packages.add(values[0])
            return ""
        if command == "remove":
            installed_packages.discard(values[0])
            return ""
        raise AssertionError

    monkeypatch.setattr(pi_cli, "_run_pi", fake_pi)
    cli = create_cli([setup_app])
    if owned:
        result = CliRunner().invoke(cli, ["setup", "pi", "--source", str(existing)])
        assert result.exit_code == 0, result.output
    previous = {path: (Path(path) / "extensions/powercontext.ts").read_bytes() for path in installed_packages}
    fail_install = True

    result = CliRunner().invoke(cli, ["setup", "pi", "--source", str(checkout)])

    assert result.exit_code == 1
    assert "simulated Pi installation failure" in result.output
    assert {path: (Path(path) / "extensions/powercontext.ts").read_bytes() for path in installed_packages} == previous


def test_setup_pi_does_not_echo_source_credentials(tmp_path: Path, monkeypatch, installed_agent_checks) -> None:

    marker = "redacted-value"
    source = f"https://{marker}@github.com/oceanbase/powercontext"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    with pytest.raises(SetupError) as raised:
        resolve_plugin_source(source=source, ref="master", host="pi")

    assert marker not in str(raised.value)


def test_setup_pi_does_not_echo_git_clone_output(tmp_path: Path, monkeypatch, installed_agent_checks) -> None:

    marker = "redacted-value"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(
        "subprocess.run",
        lambda command, **_kwargs: CompletedProcess(command, 1, "", f"fatal: https://{marker}@github.com/failed"),
    )

    with pytest.raises(SetupError) as raised:
        resolve_plugin_source(source="oceanbase/powercontext", ref="master", host="pi")

    assert marker not in str(raised.value)


def test_doctor_pi_reports_a_missing_cli(monkeypatch, server_checks) -> None:
    import powercontext.cli.pi as pi_cli

    monkeypatch.setattr(pi_cli, "which", lambda _name: None)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "pi"])

    assert result.exit_code == 1
    assert "pi: failed - Pi CLI is not installed or is not on PATH" in result.output
    assert "package: skipped - not checked because Pi CLI is unavailable" in result.output


@pytest.mark.parametrize("protocol, expected_exit", [(None, 1), ("collection-v1", 1), ("atomic-artifact-v1", 0)])
def test_doctor_pi_reports_an_installed_package_as_json(
    tmp_path: Path, monkeypatch, server_checks, protocol, expected_exit
) -> None:
    import powercontext.cli.pi as pi_cli

    package = _write_pi_package(tmp_path / "powercontext")
    metadata = json.loads((package / "package.json").read_text())
    metadata["powercontext"] = {"memoryProtocol": protocol}
    (package / "package.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(pi_cli, "which", lambda _name: "/usr/bin/pi")

    monkeypatch.setattr(pi_cli, "_run_pi", lambda *_arguments: f"User packages:\n  {package}\n    {package}\n")

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "pi", "--json"])

    assert result.exit_code == expected_exit
    payload = json.loads(result.output)["hosts"]["pi"]
    assert payload["ok"] is (expected_exit == 0)
    assert payload["checks"]["package"] == {
        "ok": True,
        "status": "ok",
        "detail": "powercontext-pi is installed",
    }
