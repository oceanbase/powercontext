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
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.cli.git_source import resolve_plugin_source
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


def test_setup_dsh_rejects_plugin_without_bundle(tmp_path: Path, monkeypatch, installed_agent_checks) -> None:
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


def test_setup_dsh_does_not_echo_source_credentials(tmp_path: Path, monkeypatch, installed_agent_checks) -> None:

    marker = "redacted-value"
    source = f"https://{marker}@github.com/oceanbase/powercontext"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    with pytest.raises(SetupError) as raised:
        resolve_plugin_source(source=source, ref="master", host="dsh")

    assert marker not in str(raised.value)


def test_setup_dsh_clones_a_github_url_and_replaces_a_broken_checkout(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
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

    plugin = resolve_plugin_source(source="https://github.com/oceanbase/powercontext", ref="master", host="dsh")

    assert (plugin / "package.json").is_file()
    assert captured[0][:4] == ["git", "clone", "--depth", "1"]
    assert captured[0][4:6] == ["--branch", "master"]
    assert captured[0][6] == "https://github.com/oceanbase/powercontext.git"
    assert (stale / "README").read_text() == "incomplete"


def test_doctor_dsh_requires_the_plugin_id_field(monkeypatch, server_checks) -> None:
    import powercontext.cli.dsh as dsh_cli

    monkeypatch.setattr(dsh_cli, "which", lambda _name: "/usr/bin/dsh")
    monkeypatch.setattr(dsh_cli, "_run_dsh", lambda *_args, **_kwargs: "name: powercontext-dsh\n")

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "dsh"])

    assert result.exit_code == 1
    assert "plugin: failed - PowerContext DSH plugin is not registered" in result.output


@pytest.mark.parametrize("protocol, expected_exit", [(None, 1), ("collection-v1", 1), ("atomic-artifact-v1", 0)])
def test_doctor_dsh_reports_an_installed_plugin(monkeypatch, server_checks, protocol, expected_exit) -> None:
    import powercontext.cli.dsh as dsh_cli

    monkeypatch.setattr(dsh_cli, "which", lambda _name: "/usr/bin/dsh")
    monkeypatch.setattr(
        dsh_cli,
        "_run_dsh",
        lambda *_args, **_kwargs: "- id: powercontext-dsh\n  name: powercontext-dsh\n",
    )

    monkeypatch.setattr(
        "powercontext.cli.dsh_transport.read_dsh_settings", lambda **kwargs: {"memoryProtocol": protocol}
    )
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "dsh", "--json"])

    assert result.exit_code == expected_exit
    payload = json.loads(result.output)["hosts"]["dsh"]
    assert payload["ok"] is (expected_exit == 0)
    if expected_exit:
        assert "powercontext setup dsh" in payload["checks"]["plugin_memory_protocol"]["detail"]
    plugin = payload["checks"]["plugin"]
    assert plugin["ok"] is True
    assert plugin["checks"]["registration"] == "present"
    assert plugin["checks"]["running_host_configuration"] == "not_observed"
    assert plugin["checks"]["server_readiness"] == "not_checked"
    assert "/pc doctor" in plugin["detail"]

    human = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "dsh"])
    assert "running_host_configuration: not_observed" in human.output
    assert "/pc doctor" in human.output


def test_doctor_dsh_does_not_expose_host_config_or_claim_to_check_its_server(monkeypatch, server_checks) -> None:
    import powercontext.cli.dsh as dsh_cli

    marker = "private-dsh-config-marker"
    monkeypatch.setattr(dsh_cli, "which", lambda _name: "/usr/bin/dsh")
    monkeypatch.setattr(
        dsh_cli,
        "_run_dsh",
        lambda *_args, **_kwargs: f"- id: powercontext-dsh\n  config:\n    baseUrl: http://unreachable/{marker}\n",
    )
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "dsh", "--json"])
    assert result.exit_code == 1
    assert marker not in result.output
    checks = json.loads(result.output)["hosts"]["dsh"]["checks"]["plugin"]["checks"]
    assert checks["server_liveness"] == "not_checked"
    assert checks["route_compatibility"] == "not_checked"


def test_doctor_dsh_redacts_config_dump_failures(monkeypatch, server_checks) -> None:
    import powercontext.cli.dsh as dsh_cli

    monkeypatch.setattr(dsh_cli, "which", lambda _name: "/usr/bin/dsh")
    monkeypatch.setattr(
        dsh_cli,
        "_run_dsh",
        Mock(side_effect=SetupError("`dsh --dump-config` failed: private-dsh-config-marker")),
    )
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "dsh"])
    assert result.exit_code == 1
    assert "private-dsh-config-marker" not in result.output
    assert "dump-config failed" in result.output


@pytest.mark.parametrize("timeout", [False, True])
def test_doctor_dsh_identifies_config_dump_exit_and_timeout(monkeypatch, timeout: bool, server_checks) -> None:
    import powercontext.cli.dsh as dsh_cli

    monkeypatch.setattr(dsh_cli, "which", lambda _name: "/usr/bin/dsh")
    if timeout:
        subprocess_run = Mock(side_effect=TimeoutExpired("dsh", 120, output="private-config"))
    else:
        subprocess_run = Mock(return_value=CompletedProcess(["dsh"], 7, "", "private-config"))
    monkeypatch.setattr(dsh_cli.subprocess, "run", subprocess_run)
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "dsh", "--json"])
    assert result.exit_code == 1
    assert "private-config" not in result.output
    check = json.loads(result.output)["hosts"]["dsh"]["checks"]["dsh"]
    if timeout:
        assert check["checks"]["dump_config"] == "timeout"
        assert "120-second deadline" in check["detail"]
    else:
        assert check["checks"]["exit_code"] == "7"
        assert "exited with code 7" in check["detail"]
