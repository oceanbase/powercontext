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

"""Application installation and directory upgrades preserve ownership boundaries."""

import json
import os
from pathlib import Path

import pytest
from powercontext_integrations.host import HOST_ADAPTERS, host_adapter
from powercontext_integrations.packages import application_python, install_directory
from powercontext_integrations.system import SetupError, doctor_app, setup_app
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.client.transport_policy import client_config_file

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("command", ["setup", "doctor"])
def test_all_distribution_targets_have_management_commands(command):
    app = create_cli([setup_app, doctor_app])
    for adapter in HOST_ADAPTERS:
        result = CliRunner().invoke(app, [command, adapter.name, "--help"])
        assert result.exit_code == 0
        assert adapter.label in result.output


def test_python_selection_ignores_cli_virtual_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIRTUAL_ENV", str(ROOT / ".venv"))
    target = host_adapter("bub").target
    with pytest.raises(SetupError, match="Application Python"):
        application_python(target, None)
    assert application_python(target, str(ROOT / ".venv/bin/python")) == str(ROOT / ".venv/bin/python")


def test_portable_setup_verifies_generated_files_and_preserves_private_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "client.json"))
    target = tmp_path / "plugin"
    adapter = host_adapter("agent-plugin")
    result = adapter.install(source=str(ROOT), ref="master", destination=target, server_url="https://context.example")
    assert result.destination == str(target)
    mcp = target / "mcp.json"
    config = json.loads(mcp.read_text())
    config["mcpServers"]["powercontext"]["headers"] = {"Authorization": "Bearer private"}
    config["mcpServers"]["other"] = {"url": "https://other.example/mcp"}
    mcp.write_text(json.dumps(config))
    (target / "user.txt").write_text("private content")
    adapter.install(source=str(ROOT), ref="master", destination=target)
    assert json.loads(mcp.read_text()) == config
    assert (target / "user.txt").read_text() == "private content"
    checks = adapter.diagnose()
    assert checks["plugin"].ok
    assert json.loads(client_config_file().read_text())["hosts"]["agent-plugin"]["installation"]["destination"] == str(
        target
    )


@pytest.mark.parametrize("kind", ["traversal", "symlink", "foreign"])
def test_directory_setup_rejects_unowned_or_escaping_files(tmp_path, kind):
    destination = tmp_path / "plugin"
    destination.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("private content")
    if kind == "traversal":
        (destination / ".powercontext-install.json").write_text('["../private.txt"]')
    elif kind == "symlink":
        (destination / "skills").symlink_to(tmp_path, target_is_directory=True)
    else:
        (destination / "plugin.json").write_text("private manifest")
    target = host_adapter("agent-plugin").target
    with pytest.raises(SetupError):
        install_directory(target, ROOT / target.source, destination, None)
    assert outside.read_text() == "private content"


def test_failed_package_verification_does_not_save_connection(tmp_path, monkeypatch):
    from powercontext_integrations.packages import PackageSetupResult
    from powercontext_integrations.system import Diagnostic, DiagnosticStatus

    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "client.json"))
    monkeypatch.setattr(
        "powercontext_integrations.packages.install_package_plugin",
        lambda *args, **kwargs: PackageSetupResult("bub", str(tmp_path), "/application/python"),
    )
    monkeypatch.setattr(
        "powercontext_integrations.packages.run_package_diagnostics",
        lambda *args, **kwargs: {"package": Diagnostic(DiagnosticStatus.FAILED, "Import failed")},
    )
    with pytest.raises(SetupError, match="post-install verification"):
        host_adapter("bub").install(source=str(ROOT), ref="master", server_url="https://context.example")
    assert not client_config_file().exists()


def test_missing_application_package_is_not_a_present_broken_host(tmp_path):
    import venv

    from powercontext_integrations.hosts import build_integration_row
    from powercontext_integrations.packages import run_package_diagnostics

    venv.EnvBuilder(with_pip=False).create(tmp_path)
    executable = tmp_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    target = host_adapter("bub").target
    missing = run_package_diagnostics(target, python=str(executable))
    assert build_integration_row("bub", missing).presence == "missing"
