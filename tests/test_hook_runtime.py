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

"""Installed-client prerequisites must fail before host setup changes anything."""

import json

import powercontext_integrations.claude_code as claude_cli
import powercontext_integrations.hook_runtime as hook_runtime
import pytest
from powercontext_integrations.host import HOST_ADAPTERS
from powercontext_integrations.hosts import build_integration_row, diagnose_host
from powercontext_integrations.system import doctor_app, setup_app
from typer.testing import CliRunner

from powercontext.cli.app import create_cli


@pytest.mark.parametrize("host", [adapter.name for adapter in HOST_ADAPTERS])
@pytest.mark.parametrize("select", [False, True])
def test_setup_requires_installed_client_before_creating_files(tmp_path, monkeypatch, host, select) -> None:
    data = tmp_path / "data"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(data))
    monkeypatch.setattr(hook_runtime, "which", lambda _name: None)
    arguments = ["select", "--host", host] if select else [host]
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", *arguments, "--source", str(tmp_path / "missing-source"), "--json"]
    )
    assert result.exit_code == 1
    assert "powercontext-hook" in result.output
    assert not data.exists()
    assert not (tmp_path / "client-settings.json").exists()


def test_doctor_fails_when_the_plugin_is_present_but_the_client_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(hook_runtime, "which", lambda _name: None)
    monkeypatch.setattr(claude_cli, "which", lambda _name: "/bin/claude")
    monkeypatch.setattr(
        claude_cli,
        "_run_claude_json",
        lambda *_args: [{"id": "powercontext@powercontext", "enabled": True}],
    )
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "claude-code", "--json"])
    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["checks"]["plugin"]["status"] == "ok"
    assert payload["checks"]["client"]["status"] == "failed"
    row = build_integration_row("claude-code", diagnose_host("claude-code"))
    assert row.presence == "present"
    assert row.failed
