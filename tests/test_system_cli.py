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
import os
import sys
import tomllib
from email.message import Message
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest
from typer.testing import CliRunner

import powercontext.cli.authorization as authorization_cli
import powercontext.cli.system as system_cli
from powercontext.cli.app import create_cli
from powercontext.cli.installation import PluginSource
from powercontext.cli.installation import setup as install_plugin
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError, doctor_app, setup_app
from powercontext.server.settings import ServerSettings
from powercontext.service.model import (
    DefinitionState,
    LivenessState,
    ManagerOwnershipState,
    ManagerState,
    RegistrationState,
    ServiceStatus,
    SupportState,
)

_probe_codex_mcp_status = system_cli._probe_codex_mcp_status
_run_codex_mcp_list = system_cli._run_codex_mcp_list


@pytest.fixture(autouse=True)
def isolated_codex_plugin_cache(tmp_path, monkeypatch):
    # Setup reads .env from cwd; tests must not consume a developer's credentials.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.delenv("POWERCONTEXT_CODEX_AUTHORIZATION", raising=False)
    monkeypatch.delenv("POWERCONTEXT_CODEX_SCOPE_ID", raising=False)
    monkeypatch.delenv("POWERCONTEXT_CLIENT_API_TOKEN", raising=False)
    monkeypatch.setattr(authorization_cli, "configure_codex_desktop_authorization", lambda _value: False)
    monkeypatch.setattr(authorization_cli, "read_codex_desktop_authorization", lambda: None)
    for marketplace in ("powercontext", "powercontext-local"):
        cache = tmp_path / "codex" / "plugins" / "cache" / marketplace / "powercontext" / "0.1.0"
        cache.mkdir(parents=True)
        (cache / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"powercontext": {"type": "http", "url": "http://127.0.0.1:8000/mcp"}}})
        )
    monkeypatch.setattr(
        system_cli,
        "_run_codex_mcp_list",
        lambda: [
            {
                "name": "powercontext",
                "enabled": True,
                "auth_status": "unsupported",
                "transport": {
                    "type": "streamable_http",
                    "url": "http://127.0.0.1:8000/mcp",
                    "env_http_headers": {"Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"},
                },
            }
        ],
    )
    monkeypatch.setattr(
        system_cli,
        "_probe_codex_mcp_status",
        lambda **_kwargs: {"name": "powercontext", "tools": {"remember_memory": {}, "search_memory": {}}},
    )


def test_server_defaults_to_persistent_user_storage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "powercontext-data"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(data_dir))

    settings = ServerSettings()

    assert settings.database.kind == "sqlite"
    database_path = (data_dir / "powercontext.db").as_posix()
    assert settings.database.url == f"sqlite+aiosqlite:///{database_path}"


@pytest.mark.parametrize("already_disabled", [False, True])
def test_setup_codex_installs_or_preserves_native_disablement(
    tmp_path: Path, monkeypatch, installed_agent_checks, already_disabled
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(data_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    record = {
        "name": "powercontext",
        "installed": True,
        "enabled": not already_disabled,
        "marketplaceName": "powercontext",
        "version": "0.1.0",
    }
    installed = [record] if already_disabled else []
    commands = []

    def native(*arguments):
        commands.append(arguments)
        if arguments[:2] == ("plugin", "marketplace"):
            return {"marketplaceName": "powercontext"}
        if arguments[:2] == ("plugin", "add"):
            record["enabled"] = True
            installed[:] = [record]
            return record
        return {"installed": installed}

    monkeypatch.setattr(system_cli, "_run_codex_json", native)
    result = CliRunner().invoke(
        create_cli([setup_app]),
        [
            "setup",
            "codex",
            "--source",
            "oceanbase/powercontext",
            "--ref",
            "tested-ref",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["hosts"]["codex"]["result"] == {
        "marketplace": "powercontext",
        "plugin": "powercontext",
        "plugin_version": "0.1.0",
        "data_dir": str(data_dir),
        "authorization_state": "not_configured",
    }
    assert record["installed"] is True
    assert record["enabled"] is not already_disabled
    assert ("plugin", "marketplace", "add", "oceanbase/powercontext", "--ref", "tested-ref") in commands


def test_codex_diagnostics_verify_native_mcp_tools_without_process_authorization(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "pluginId": "powercontext@powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    monkeypatch.delenv("POWERCONTEXT_CODEX_AUTHORIZATION", raising=False)

    diagnostics = system_cli.CodexInstaller().verify()

    assert diagnostics["mcp_configuration"].status is DiagnosticStatus.OK
    assert diagnostics["authorization"].status is DiagnosticStatus.OK
    assert diagnostics["mcp_tools"].status is DiagnosticStatus.OK
    assert "2 tools" in diagnostics["mcp_tools"].detail
    assert diagnostics["mcp_full_profile"].status is DiagnosticStatus.DEGRADED
    assert "generate_experience" in diagnostics["mcp_full_profile"].detail
    assert "import_external_skill" in diagnostics["mcp_full_profile"].detail


def test_codex_diagnostics_recognize_full_tools_from_the_server(monkeypatch) -> None:
    import asyncio

    from fastmcp import Client

    from powercontext.server.app import create_app
    from powercontext.server.mcp import create_mcp_server

    async def catalog():
        async with Client(create_mcp_server(create_app(handoff_report_enabled=True))) as client:
            return {tool.name: {} for tool in await client.list_tools()}

    tools = asyncio.run(catalog())
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", lambda **_kwargs: {"tools": tools})
    diagnostics = system_cli.CodexInstaller().verify()
    assert diagnostics["mcp_full_profile"].status is DiagnosticStatus.OK
    tools.pop("resolve_external_skill")
    diagnostics = system_cli.CodexInstaller().verify()
    assert diagnostics["mcp_tools"].status is DiagnosticStatus.OK
    assert diagnostics["mcp_full_profile"].status is DiagnosticStatus.DEGRADED
    assert "resolve_external_skill" in diagnostics["mcp_full_profile"].detail


def _declared_codex_mcp_tools() -> set[str]:
    path = Path(__file__).resolve().parents[1] / "integrations/capabilities.toml"
    manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    codex = next(host for host in manifest["integrations"] if host["id"] == "codex")
    return {
        tool["id"]
        for toolset in manifest["toolsets"]
        if toolset["id"] in codex["toolsets"] and toolset["probe"] == "server_mcp"
        for tool in toolset["tools"]
        if tool.get("capabilities")
    }


@pytest.mark.parametrize("missing", [None, *sorted(_declared_codex_mcp_tools())])
def test_doctor_codex_requires_every_declared_capability_tool(monkeypatch, missing, server_checks) -> None:
    tools = {name: {} for name in _declared_codex_mcp_tools()}
    if missing is not None:
        tools.pop(missing)
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", lambda **_kwargs: {"tools": tools})

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "codex", "--json"])
    full = json.loads(result.output)["hosts"]["codex"]["checks"]["mcp_full_profile"]
    assert result.exit_code == (0 if missing is None else 1)
    assert full["status"] == ("ok" if missing is None else "degraded")
    if missing is not None:
        assert missing in full["detail"]


def test_codex_diagnostics_fail_when_required_native_tools_are_missing(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "pluginId": "powercontext@powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", lambda **_kwargs: {"name": "powercontext", "tools": {}})

    diagnostics = system_cli.CodexInstaller().verify()

    assert diagnostics["mcp_tools"].status is DiagnosticStatus.FAILED
    assert "remember_memory, search_memory" in diagnostics["mcp_tools"].detail
    assert "POWERCONTEXT_CODEX_AUTHORIZATION" in diagnostics["mcp_tools"].detail


def test_codex_diagnostics_reject_native_mcp_without_authorization_environment(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "pluginId": "powercontext@powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    monkeypatch.setattr(
        system_cli,
        "_run_codex_mcp_list",
        lambda: [
            {
                "name": "powercontext",
                "enabled": True,
                "transport": {"type": "streamable_http", "url": "http://127.0.0.1:8000/mcp"},
            }
        ],
    )
    probe = Mock()
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", probe)

    diagnostics = system_cli.CodexInstaller().verify()

    assert diagnostics["mcp_configuration"].status is DiagnosticStatus.FAILED
    assert "reinstall the current plugin" in diagnostics["mcp_configuration"].detail
    assert diagnostics["authorization"].status is DiagnosticStatus.SKIPPED
    probe.assert_not_called()


@pytest.mark.parametrize("credential_helper", [False, True])
@pytest.mark.parametrize("server_accepts_unauthenticated", [False, True])
def test_codex_diagnostics_probe_without_url_mismatched_stored_authorization(
    tmp_path: Path, monkeypatch, credential_helper: bool, server_accepts_unauthenticated: bool
) -> None:
    """A previous endpoint's token stays private while the new Server verifies access."""
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "pluginId": "powercontext@powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    credential = tmp_path / "codex" / "powercontext" / "credentials.json"
    authorization_cli.write_stored_authorization(
        credential,
        server_url="http://127.0.0.1:9000",
        value="Bearer saved-token",
    )
    original_credential = credential.read_bytes()
    if credential_helper:
        servers = system_cli._run_codex_mcp_list()
        servers[0]["transport"]["http_headers_helper"] = "<redacted>"
        monkeypatch.setattr(system_cli, "_run_codex_mcp_list", lambda: servers)
    probe = Mock(
        return_value={
            "name": "powercontext",
            "tools": {"remember_memory": {}, "search_memory": {}} if server_accepts_unauthenticated else {},
        }
    )
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", probe)

    diagnostics = system_cli.CodexInstaller().verify()

    assert diagnostics["authorization"].status is DiagnosticStatus.OK
    assert "url_mismatch" in diagnostics["authorization"].detail
    assert "POWERCONTEXT_CODEX_AUTHORIZATION" in diagnostics["authorization"].detail
    assert "POWERCONTEXT_CLIENT_API_TOKEN" in diagnostics["authorization"].detail
    assert diagnostics["mcp_tools"].ok is server_accepts_unauthenticated
    if not server_accepts_unauthenticated:
        assert "POWERCONTEXT_CODEX_AUTHORIZATION" in diagnostics["mcp_tools"].detail
    assert credential.read_bytes() == original_credential
    assert "saved-token" not in json.dumps({key: value.as_json() for key, value in diagnostics.items()})
    probe.assert_called_once_with(authorization=None)


def test_codex_app_server_probe_clears_process_authorization(monkeypatch) -> None:
    responses = "\n".join((
        json.dumps({"id": 1, "result": {"userAgent": "test"}}),
        json.dumps({
            "id": 2,
            "result": {
                "data": [
                    {
                        "name": "powercontext",
                        "authStatus": "unsupported",
                        "tools": {"remember_memory": {}, "search_memory": {}},
                    }
                ]
            },
        }),
    ))

    class Process:
        def __init__(self) -> None:
            self.stdin = StringIO()
            self.stdout = StringIO(responses)

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

        def wait(self, timeout: float) -> int:
            return 0

    popen = Mock(return_value=Process())
    monkeypatch.setenv("POWERCONTEXT_CODEX_AUTHORIZATION", "Bearer process-token")
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(system_cli.subprocess, "Popen", popen)

    status = _probe_codex_mcp_status()

    assert set(status["tools"]) == {"remember_memory", "search_memory"}
    assert "POWERCONTEXT_CODEX_AUTHORIZATION" not in popen.call_args.kwargs["env"]


def test_codex_app_server_probe_uses_resolved_authorization(monkeypatch) -> None:
    responses = "\n".join((
        json.dumps({"id": 1, "result": {"userAgent": "test"}}),
        json.dumps({
            "id": 2,
            "result": {
                "data": [
                    {
                        "name": "powercontext",
                        "tools": {"remember_memory": {}, "search_memory": {}},
                    }
                ]
            },
        }),
    ))

    class Process:
        def __init__(self) -> None:
            self.stdin = StringIO()
            self.stdout = StringIO(responses)

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

        def wait(self, timeout: float) -> int:
            return 0

    popen = Mock(return_value=Process())
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(system_cli.subprocess, "Popen", popen)

    _probe_codex_mcp_status(authorization="Bearer resolved-token")

    assert popen.call_args.kwargs["env"]["POWERCONTEXT_CODEX_AUTHORIZATION"] == "Bearer resolved-token"


def test_setup_codex_persists_setup_only_token_in_codex_owned_storage(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "setup-token")
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    configure_desktop = Mock(return_value=True)
    monkeypatch.setattr(authorization_cli, "configure_codex_desktop_authorization", configure_desktop)
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        Mock(
            side_effect=lambda *args: (
                {"marketplaceName": "powercontext"}
                if args[:2] == ("plugin", "marketplace")
                else {"name": "powercontext", "version": "0.1.0"}
                if args[:2] == ("plugin", "add")
                else {
                    "installed": [
                        {
                            "name": "powercontext",
                            "installed": True,
                            "enabled": True,
                            "marketplaceName": "powercontext",
                            "version": "0.1.0",
                        }
                    ]
                }
            )
        ),
    )

    result = install_plugin("codex", source="oceanbase/powercontext", ref="master")

    assert result["authorization_state"] == "configured"
    assert (
        json.loads((tmp_path / "codex" / "powercontext" / "credentials.json").read_text())["authorization"]
        == "Bearer setup-token"
    )
    configure_desktop.assert_called_once_with("Bearer setup-token")
    mcp = json.loads((tmp_path / "codex/plugins/cache/powercontext/powercontext/0.1.0/.mcp.json").read_text())
    helper = mcp["mcpServers"]["powercontext"]["http_headers_helper"]
    assert "mcp_headers.py" in helper
    assert str(tmp_path / "codex/powercontext/credentials.json") in helper
    assert "setup-token" not in json.dumps(mcp)


def test_codex_diagnostics_use_matching_windows_user_authorization(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "pluginId": "powercontext@powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    credential = tmp_path / "codex" / "powercontext" / "credentials.json"
    authorization_cli.write_stored_authorization(
        credential,
        server_url="http://127.0.0.1:8000",
        value="Bearer saved-token",
    )
    monkeypatch.setattr(authorization_cli, "read_codex_desktop_authorization", lambda: "Bearer saved-token")
    probe = Mock(return_value={"name": "powercontext", "tools": {"remember_memory": {}, "search_memory": {}}})
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", probe)

    diagnostics = system_cli.CodexInstaller().verify()

    assert diagnostics["authorization"].status is DiagnosticStatus.OK
    assert "Windows user authorization" in diagnostics["authorization"].detail
    assert diagnostics["authorization"].checks == {
        "current_process": "not_configured",
        "setup_managed": "matches_desktop_restart",
        "desktop_restart": "configured",
    }
    probe.assert_called_once_with(authorization="Bearer saved-token")


def test_codex_diagnostics_prefer_process_authorization_over_stale_stored_credential(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "pluginId": "powercontext@powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    authorization_cli.write_stored_authorization(
        tmp_path / "codex" / "powercontext" / "credentials.json",
        server_url="http://127.0.0.1:8000",
        value="Bearer old-token",
    )
    monkeypatch.setenv("POWERCONTEXT_CODEX_AUTHORIZATION", "Bearer replacement-token")
    probe = Mock(return_value={"name": "powercontext", "tools": {"remember_memory": {}, "search_memory": {}}})
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", probe)

    diagnostics = system_cli.CodexInstaller().verify()

    assert diagnostics["authorization"].status is DiagnosticStatus.OK
    assert diagnostics["authorization"].checks == {
        "current_process": "configured",
        "setup_managed": "stale",
        "desktop_restart": "not_configured" if sys.platform == "win32" else "not_applicable",
    }
    assert "setup-managed credential is stale" in diagnostics["authorization"].detail
    assert diagnostics["mcp_tools"].status is DiagnosticStatus.OK
    probe.assert_called_once_with(authorization="Bearer replacement-token")
    report = json.dumps(diagnostics["authorization"].as_json())
    assert "old-token" not in report
    assert "replacement-token" not in report


def test_codex_diagnostics_reject_bare_process_token_without_repairing_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "pluginId": "powercontext@powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    authorization_cli.write_stored_authorization(
        tmp_path / "codex" / "powercontext" / "credentials.json",
        server_url="http://127.0.0.1:8000",
        value="Bearer replacement-token",
    )
    monkeypatch.setenv("POWERCONTEXT_CODEX_AUTHORIZATION", "replacement-token")
    monkeypatch.setattr(
        authorization_cli,
        "read_codex_desktop_authorization",
        lambda: "Bearer replacement-token",
    )
    probe = Mock()
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", probe)

    diagnostics = system_cli.CodexInstaller().verify()

    assert diagnostics["authorization"].status is DiagnosticStatus.FAILED
    assert diagnostics["authorization"].checks == {
        "current_process": "invalid",
        "setup_managed": "matches_desktop_restart",
        "desktop_restart": "configured",
    }
    assert "complete Bearer credential" in diagnostics["authorization"].detail
    assert diagnostics["mcp_tools"].status is DiagnosticStatus.SKIPPED
    probe.assert_not_called()
    assert "replacement-token" not in json.dumps(diagnostics["authorization"].as_json())


def test_codex_diagnostics_probe_lowercase_bearer_header_without_rewriting_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "pluginId": "powercontext@powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    authorization_cli.write_stored_authorization(
        tmp_path / "codex" / "powercontext" / "credentials.json",
        server_url="http://127.0.0.1:8000",
        value="Bearer replacement-token",
    )
    monkeypatch.setenv("POWERCONTEXT_CODEX_AUTHORIZATION", "bearer replacement-token")
    monkeypatch.setattr(
        authorization_cli,
        "read_codex_desktop_authorization",
        lambda: "Bearer replacement-token",
    )
    probe = Mock(return_value={"name": "powercontext", "tools": {"remember_memory": {}, "search_memory": {}}})
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", probe)

    diagnostics = system_cli.CodexInstaller().verify()

    assert diagnostics["authorization"].status is DiagnosticStatus.OK
    assert diagnostics["authorization"].checks == {
        "current_process": "configured",
        "setup_managed": "matches_current_process",
        "desktop_restart": "matches_current_process",
    }
    assert diagnostics["mcp_tools"].status is DiagnosticStatus.OK
    probe.assert_called_once_with(authorization="bearer replacement-token")


def test_codex_diagnostics_do_not_replace_process_authorization_with_windows_user_value(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "pluginId": "powercontext@powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    authorization_cli.write_stored_authorization(
        tmp_path / "codex" / "powercontext" / "credentials.json",
        server_url="http://127.0.0.1:8000",
        value="Bearer old-token",
    )
    monkeypatch.setenv("POWERCONTEXT_CODEX_AUTHORIZATION", "Bearer replacement-token")
    monkeypatch.setattr(authorization_cli, "read_codex_desktop_authorization", lambda: "Bearer old-token")
    probe = Mock(return_value={"name": "powercontext", "tools": {"remember_memory": {}, "search_memory": {}}})
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", probe)

    diagnostics = system_cli.CodexInstaller().verify()

    assert diagnostics["authorization"].status is DiagnosticStatus.OK
    assert diagnostics["authorization"].checks == {
        "current_process": "configured",
        "setup_managed": "stale",
        "desktop_restart": "differs_from_current_process",
    }
    assert "Windows user authorization differs" in diagnostics["authorization"].detail
    probe.assert_called_once_with(authorization="Bearer replacement-token")


def test_codex_diagnostics_report_matching_desktop_override_separately_from_stale_storage(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "pluginId": "powercontext@powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    authorization_cli.write_stored_authorization(
        tmp_path / "codex" / "powercontext" / "credentials.json",
        server_url="http://127.0.0.1:8000",
        value="Bearer old-token",
    )
    monkeypatch.setenv("POWERCONTEXT_CODEX_AUTHORIZATION", "Bearer replacement-token")
    monkeypatch.setattr(authorization_cli, "read_codex_desktop_authorization", lambda: "Bearer replacement-token")
    probe = Mock(return_value={"name": "powercontext", "tools": {"remember_memory": {}, "search_memory": {}}})
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", probe)

    diagnostics = system_cli.CodexInstaller().verify()

    assert diagnostics["authorization"].status is DiagnosticStatus.OK
    assert diagnostics["authorization"].checks == {
        "current_process": "configured",
        "setup_managed": "stale",
        "desktop_restart": "matches_current_process",
    }
    probe.assert_called_once_with(authorization="Bearer replacement-token")


@pytest.mark.parametrize("process_override", [False, True])
def test_codex_diagnostics_probe_the_native_credential_helper_without_injecting_storage(
    tmp_path: Path, monkeypatch, process_override: bool
) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "pluginId": "powercontext@powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    servers = system_cli._run_codex_mcp_list()
    servers[0]["transport"]["http_headers_helper"] = "<redacted>"
    monkeypatch.setattr(system_cli, "_run_codex_mcp_list", lambda: servers)
    authorization_cli.write_stored_authorization(
        tmp_path / "codex/powercontext/credentials.json",
        server_url="http://127.0.0.1:8000",
        value="Bearer saved-test-token",
    )
    if process_override:
        monkeypatch.setenv("POWERCONTEXT_CODEX_AUTHORIZATION", "Bearer override-test-token")
    probe = Mock(return_value={"name": "powercontext", "tools": {"remember_memory": {}, "search_memory": {}}})
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", probe)

    diagnostics = system_cli.CodexInstaller().verify()

    assert diagnostics["authorization"].ok
    assert diagnostics["mcp_tools"].ok
    probe.assert_called_once_with(authorization="Bearer override-test-token" if process_override else None)
    report = json.dumps({key: value.as_json() for key, value in diagnostics.items()})
    assert "saved-test-token" not in report
    assert "override-test-token" not in report
    if not process_override:
        assert diagnostics["authorization"].checks is not None
        assert diagnostics["authorization"].checks["setup_managed"] == "available_to_host"
    if sys.platform != "win32":
        assert "Windows" not in report


def test_codex_diagnostics_fail_when_setup_credential_is_unavailable_to_host(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "pluginId": "powercontext@powercontext",
                    "installed": True,
                    "enabled": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    credential = tmp_path / "codex" / "powercontext" / "credentials.json"
    authorization_cli.write_stored_authorization(
        credential,
        server_url="http://127.0.0.1:8000",
        value="Bearer saved-token",
    )
    probe = Mock()
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", probe)

    diagnostics = system_cli.CodexInstaller().verify()

    assert diagnostics["authorization"].status is DiagnosticStatus.FAILED
    assert "not available to the Codex host" in diagnostics["authorization"].detail
    assert diagnostics["mcp_tools"].status is DiagnosticStatus.SKIPPED
    probe.assert_not_called()


# npm installs the Codex CLI as codex.cmd without a codex.exe; the shim below answers both
# runner surfaces with the minimal JSON their callers expect.
CODEX_CMD_SHIM = (
    "@echo off\r\n"
    'if "%1" == "mcp" (\r\n'
    '  echo [{"name": "powercontext", "enabled": true, "auth_status": "unsupported",'
    ' "transport": {"type": "streamable_http", "url": "http://127.0.0.1:8000/mcp",'
    ' "env_http_headers": {"Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"}}}]\r\n'
    "  exit /b 0\r\n"
    ")\r\n"
    'if "%1" == "plugin" (\r\n'
    '  echo {"installed": [{"name": "powercontext", "pluginId": "powercontext@powercontext",'
    ' "installed": true, "enabled": true, "marketplaceName": "powercontext", "version": "0.1.0"}]}\r\n'
    "  exit /b 0\r\n"
    ")\r\n"
    'echo {"ok": true}\r\n'
    "exit /b 0\r\n"
)


@pytest.fixture
def codex_cmd_shim(tmp_path: Path, monkeypatch) -> Path:
    bin_dir = tmp_path / "bin with spaces 中文"
    bin_dir.mkdir()
    shim = bin_dir / "codex.cmd"
    shim.write_text(CODEX_CMD_SHIM, encoding="ascii")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setenv("PC_TEST_PYTHON", sys.executable)
    monkeypatch.setenv("PC_TEST_BIN", str(bin_dir))
    return shim


@pytest.mark.skipif(sys.platform != "win32", reason="npm-style codex.cmd shims without codex.exe are Windows-specific")
def test_codex_json_runner_launches_the_resolved_cmd_shim(codex_cmd_shim: Path) -> None:
    result = system_cli._run_codex_json("plugin", "list")

    assert result["installed"][0]["name"] == "powercontext"


@pytest.mark.skipif(sys.platform != "win32", reason="npm-style codex.cmd shims without codex.exe are Windows-specific")
def test_codex_mcp_list_runner_launches_the_resolved_cmd_shim(codex_cmd_shim: Path) -> None:
    servers = _run_codex_mcp_list()

    assert servers[0]["name"] == "powercontext"


@pytest.mark.skipif(sys.platform != "win32", reason="npm-style codex.cmd shims without codex.exe are Windows-specific")
def test_codex_diagnostics_run_through_the_resolved_cmd_shim(codex_cmd_shim: Path, monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "_run_codex_mcp_list", _run_codex_mcp_list)

    diagnostics = system_cli.CodexInstaller().verify()

    assert diagnostics["codex"].ok
    assert diagnostics["codex"].detail.casefold() == str(codex_cmd_shim).casefold()
    assert diagnostics["plugin"].ok
    assert diagnostics["mcp_configuration"].ok


@pytest.mark.skipif(sys.platform != "win32", reason="cmd shim output decoding is Windows-specific")
@pytest.mark.parametrize("runner", ["_run_codex_json", "_run_codex_mcp_list"])
def test_codex_runners_report_non_utf8_cmd_errors(codex_cmd_shim: Path, runner: str) -> None:
    emitter = codex_cmd_shim.parent / "failure.py"
    emitter.write_text(
        "import sys\nsys.stderr.buffer.write(b'cmd startup diagnostic\\ncodex failure: "
        "\\xd6\\xd0\\xce\\xc4\\n')\nsys.exit(7)\n",
        encoding="ascii",
    )
    codex_cmd_shim.write_text(
        '@echo off\n"%PC_TEST_PYTHON%" "%PC_TEST_BIN%\\failure.py"\nexit /b 7\n', encoding="ascii"
    )
    probe = (
        "import sys\n"
        "from powercontext.cli import system\n"
        "assert sys.flags.utf8_mode == 1\n"
        "try:\n"
        f"    system.{runner}(*(('plugin', 'list') if {runner!r} == '_run_codex_json' else ()))\n"
        "except system.SetupError as error:\n"
        "    print(error)\n"
        "else:\n"
        "    raise AssertionError('expected SetupError for a failed Codex command')\n"
    )

    completed = system_cli.subprocess.run(
        [sys.executable, "-X", "utf8", "-c", probe],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "failed:" in completed.stdout
    assert "cmd startup diagnostic" in completed.stdout
    assert "codex failure:" in completed.stdout
    assert completed.stderr == ""


@pytest.mark.skipif(sys.platform != "win32", reason="cmd shim output decoding is Windows-specific")
@pytest.mark.parametrize("runner", ["json", "mcp"])
@pytest.mark.parametrize("valid_utf8", [False, True])
def test_codex_runners_preserve_json_output(codex_cmd_shim: Path, runner: str, valid_utf8: bool) -> None:
    # A literal replacement character is valid data; an invalid byte must not become one.
    name = "中文�".encode() if valid_utf8 else b"\xff"
    output = b'{"name":"' + name + b'"}'
    if runner == "mcp":
        output = b"[" + output + b"]"
    emitter = codex_cmd_shim.parent / "output.py"
    emitter.write_text(
        f"import sys\nsys.stdout.buffer.write({output!r})\nsys.stderr.buffer.write(b'\\xff')\n",
        encoding="ascii",
    )
    codex_cmd_shim.write_text('@echo off\n"%PC_TEST_PYTHON%" "%PC_TEST_BIN%\\output.py"\n', encoding="ascii")

    def read():
        return system_cli._run_codex_json("plugin", "list") if runner == "json" else _run_codex_mcp_list()

    if valid_utf8:
        result = read()
        assert (result if runner == "json" else result[0])["name"] == "中文�"
    else:
        with pytest.raises(SetupError, match="invalid JSON"):
            read()


def test_codex_runners_report_a_missing_executable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    with pytest.raises(SetupError, match="Codex CLI is not installed or is not on PATH"):
        system_cli._run_codex_json("plugin", "list")
    with pytest.raises(SetupError, match="Codex CLI is not installed or is not on PATH"):
        _run_codex_mcp_list()


@pytest.fixture
def recording_codex_cmd(codex_cmd_shim: Path, monkeypatch) -> tuple[Path, Path]:
    received = codex_cmd_shim.parent / "received.jsonl"
    emitter = codex_cmd_shim.parent / "record.py"
    emitter.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"with Path({str(received)!r}).open('a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print(json.dumps({'marketplaceName': os.environ.get('CODEX_TEST_MARKETPLACE', 'powercontext'), "
        "'installed': [], 'name': 'powercontext', 'version': '0.1.0'}))\n",
        encoding="utf-8",
    )
    codex_cmd_shim.write_text('@echo off\n"%PC_TEST_PYTHON%" "%PC_TEST_BIN%\\record.py" %*\n', encoding="ascii")
    monkeypatch.setenv("CODEX_TEST_VALUE", "expanded")
    monkeypatch.delenv("CODEX_TEST_MARKETPLACE", raising=False)
    return codex_cmd_shim, received


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch argument handling")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "source^directory"),
        ("source", "source&directory"),
        ("source", "source %CODEX_TEST_VALUE% directory"),
        *[
            ("ref", value)
            for value in (
                "a&b",
                "a^b",
                "a%b",
                "a!b",
                "a|b",
                "a<b",
                "a>b",
                "a(b)",
                'a"b',
                "a\nb",
                "a\rb",
                "a\tb",
                "a\0b",
                "a\x7fb",
            )
        ],
    ],
)
def test_codex_batch_install_rejects_unsafe_input_before_launch(
    recording_codex_cmd: tuple[Path, Path], tmp_path: Path, field: str, value: str
) -> None:
    _, received = recording_codex_cmd
    source = "oceanbase/powercontext"
    ref = "master"
    if field == "source":
        directory = tmp_path / value
        directory.mkdir()
        source = str(directory)
    else:
        ref = value

    with pytest.raises(SetupError, match="Windows batch"):
        system_cli.CodexInstaller().install(PluginSource(source, ref))

    assert not received.exists(), "unsupported input must be rejected before marketplace registration"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PATHEXT batch aliases")
@pytest.mark.parametrize("extension", [".CMD", ".BAT"])
def test_codex_batch_alias_with_trailing_space_still_rejects_unsafe_arguments(
    recording_codex_cmd: tuple[Path, Path], monkeypatch, extension: str
) -> None:
    shim, received = recording_codex_cmd
    if extension == ".BAT":
        shim.with_suffix(".bat").write_bytes(shim.read_bytes())
    monkeypatch.setenv("PATHEXT", f".COM;.EXE;{extension} ")
    resolved = system_cli.which("codex")
    assert resolved is not None and resolved.endswith(" ")

    with pytest.raises(SetupError, match="Windows batch"):
        system_cli.CodexInstaller().install(PluginSource("oceanbase/powercontext", "x^y"))

    assert not received.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="the implicit current-directory preference is Windows-specific")
@pytest.mark.parametrize("runner", ["json", "mcp"])
def test_codex_runners_prefer_path_over_the_current_directory(tmp_path: Path, monkeypatch, runner: str) -> None:
    current = tmp_path / "cwd-shim"
    current.mkdir()
    payload = '{"marker": "CURRENT_DIRECTORY_SHIM"}'
    path_payload = '{"marker": "PATH_EXECUTABLE_SHIM"}'
    if runner == "mcp":
        payload = "[" + payload + "]"
        path_payload = "[" + path_payload + "]"
    (current / "codex.cmd").write_text(f"@echo off\r\necho {payload}\r\nexit /b 0\r\n", encoding="ascii")
    bin_dir = tmp_path / "path-bin"
    bin_dir.mkdir()
    (bin_dir / "codex.cmd").write_text(f"@echo off\r\necho {path_payload}\r\nexit /b 0\r\n", encoding="ascii")
    monkeypatch.chdir(current)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")

    result = system_cli._run_codex_json("plugin", "list") if runner == "json" else _run_codex_mcp_list()[0]

    assert result["marker"] == "PATH_EXECUTABLE_SHIM"


@pytest.mark.skipif(sys.platform != "win32", reason="explicit PATH entries keep Windows-specific semantics")
def test_codex_runners_honor_an_explicit_dot_path_entry(tmp_path: Path, monkeypatch) -> None:
    current = tmp_path / "cwd-shim"
    current.mkdir()
    (current / "codex.cmd").write_text(
        '@echo off\r\necho {"marker": "CURRENT_DIRECTORY_SHIM"}\r\nexit /b 0\r\n', encoding="ascii"
    )
    bin_dir = tmp_path / "path-bin"
    bin_dir.mkdir()
    (bin_dir / "codex.cmd").write_text(
        '@echo off\r\necho {"marker": "PATH_EXECUTABLE_SHIM"}\r\nexit /b 0\r\n', encoding="ascii"
    )
    monkeypatch.chdir(current)
    monkeypatch.setenv("PATH", f".;{bin_dir}")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")

    result = system_cli._run_codex_json("plugin", "list")

    # An explicit "." entry is a user decision and keeps its position in PATH order;
    # only the implicit current-directory preference is removed.
    assert result["marker"] == "CURRENT_DIRECTORY_SHIM"


@pytest.mark.skipif(sys.platform != "win32", reason="PATHEXT candidate order is Windows-specific")
def test_codex_path_executable_prefers_earlier_pathext_entries(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "codex.bat").write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
    (tmp_path / "codex.cmd").write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")

    resolved = system_cli._codex_path_executable()

    assert resolved is not None and resolved.casefold().endswith("codex.bat")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable discovery has no PATHEXT")
def test_codex_path_executable_finds_a_posix_executable(tmp_path: Path, monkeypatch) -> None:
    # The autouse CODEX_HOME fixture reserves tmp_path/"codex" as a directory.
    directory = tmp_path / "discovery"
    directory.mkdir()
    executable = directory / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(directory))

    assert system_cli._codex_path_executable() == str(executable)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX discovery requires the executable bit")
def test_codex_path_executable_skips_files_without_the_executable_bit(tmp_path: Path, monkeypatch) -> None:
    directory = tmp_path / "discovery"
    directory.mkdir()
    plain = directory / "codex"
    plain.write_text("not executable\n", encoding="ascii")
    plain.chmod(0o644)
    monkeypatch.setenv("PATH", str(directory))

    assert system_cli._codex_path_executable() is None


def _write_platform_codex_launcher(directory: Path) -> Path:
    """Create the launcher name Codex discovery looks for on this platform."""
    if sys.platform == "win32":
        launcher = directory / "codex.CMD"
        launcher.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
        return launcher
    launcher = directory / "codex"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    launcher.chmod(0o755)
    return launcher


def test_codex_path_executable_treats_an_empty_path_value_as_no_search(tmp_path: Path, monkeypatch) -> None:
    # Parity with which() (bpo-35755): an empty PATH value must not fall back to the working directory.
    # The autouse CODEX_HOME fixture reserves tmp_path/"codex" as a directory, so use a subdirectory.
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    _write_platform_codex_launcher(cwd)
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("PATH", "")

    assert system_cli._codex_path_executable() is None


def test_codex_path_executable_resolves_empty_path_entries_in_the_working_directory(
    tmp_path: Path, monkeypatch
) -> None:
    # Empty entries inside a non-empty PATH keep their usual cwd meaning, like which().
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    launcher = _write_platform_codex_launcher(cwd)
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("PATH", os.pathsep.join([str(cwd / "absent-a"), "", str(cwd / "absent-b")]))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")

    resolved = system_cli._codex_path_executable()

    assert resolved is not None and not Path(resolved).is_absolute()
    assert cwd / Path(resolved).name == launcher


def test_codex_path_executable_falls_back_to_confstr_when_path_is_unset(tmp_path: Path, monkeypatch) -> None:
    directory = tmp_path / "discovery"
    directory.mkdir()
    launcher = _write_platform_codex_launcher(directory)
    monkeypatch.delenv("PATH", raising=False)
    monkeypatch.setattr(os, "confstr", lambda name: str(directory), raising=False)
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")

    assert system_cli._codex_path_executable() == str(launcher)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch argument handling")
@pytest.mark.parametrize("local", [True, False])
def test_codex_batch_install_preserves_supported_input(
    recording_codex_cmd: tuple[Path, Path], tmp_path: Path, local: bool
) -> None:
    _, received = recording_codex_cmd
    directory = tmp_path / "local marketplace 中文"
    directory.mkdir()

    source = (
        PluginSource(str(directory), "ignored^local%ref")
        if local
        else PluginSource("oceanbase/powercontext", "feature/中文")
    )
    report = system_cli.CodexInstaller().install(source)
    calls = [json.loads(line) for line in received.read_text(encoding="utf-8").splitlines()]

    assert report["plugin"] == "powercontext"
    expected = [str(directory.resolve())] if local else [source.location, "--ref", source.ref]
    assert calls[0] == ["plugin", "marketplace", "add", *expected, "--json"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch argument handling")
def test_codex_batch_install_rejects_unsafe_returned_marketplace(
    recording_codex_cmd: tuple[Path, Path], monkeypatch
) -> None:
    _, received = recording_codex_cmd
    monkeypatch.setenv("CODEX_TEST_MARKETPLACE", "marketplace^name")

    with pytest.raises(SetupError, match="Windows batch"):
        system_cli.CodexInstaller().install(PluginSource("oceanbase/powercontext"))

    calls = [json.loads(line) for line in received.read_text(encoding="utf-8").splitlines()]
    assert all(call[:2] != ["plugin", "add"] for call in calls)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch executable paths")
@pytest.mark.parametrize("runner", ["json", "mcp", "app_server"])
@pytest.mark.parametrize("suffix", [".cmd", ".bat"])
def test_codex_batch_runners_reject_unsafe_executable_path(
    tmp_path: Path, monkeypatch, runner: str, suffix: str
) -> None:
    directory = tmp_path / "bin&directory"
    directory.mkdir()
    marker = tmp_path / "launched.txt"
    shim = directory / ("codex" + suffix)
    shim.write_text(f'@echo off\necho launched>"{marker}"\n', encoding="utf-8")
    monkeypatch.setenv("PATH", str(directory))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")

    with pytest.raises(SetupError, match="Windows batch"):
        if runner == "json":
            system_cli._run_codex_json("plugin", "list")
        elif runner == "mcp":
            _run_codex_mcp_list()
        else:
            _probe_codex_mcp_status()

    assert not marker.exists()


def test_codex_native_executable_preserves_arguments(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: sys.executable)
    value = 'source^&%!()<>|"\nvalue'

    result = system_cli._run_codex_json("-c", "import json,sys; print(json.dumps({'received': sys.argv[1:]}))", value)

    assert result["received"] == [value, "--json"]


def test_setup_codex_uses_an_absolute_local_marketplace_without_a_ref(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    marketplace = tmp_path / "marketplace"
    marketplace.mkdir()
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    run_codex = Mock(
        side_effect=lambda *args: (
            {"marketplaceName": "powercontext-local"}
            if args[:2] == ("plugin", "marketplace")
            else {"name": "powercontext", "version": "0.1.0"}
            if args[:2] == ("plugin", "add")
            else {
                "installed": [
                    {
                        "name": "powercontext",
                        "installed": True,
                        "enabled": True,
                        "marketplaceName": "powercontext-local",
                        "version": "0.1.0",
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(system_cli, "_run_codex_json", run_codex)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "codex", "--source", str(marketplace)],
    )

    assert result.exit_code == 0
    assert "authorization_state: not_configured" in result.output
    assert (
        "plugin",
        "marketplace",
        "add",
        str(marketplace),
    ) in [call.args for call in run_codex.call_args_list]


def test_setup_claude_code_installs_and_reports_connection_settings(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    config_dir = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    installed = []
    run_claude_json = Mock(side_effect=lambda *args: [] if args[:3] == ("plugin", "marketplace", "list") else installed)

    def native_command(*arguments):
        if arguments[:2] == ("plugin", "install"):
            installed.append({"id": "powercontext@powercontext", "scope": "user", "version": "0.1.0", "enabled": True})

    run_claude = Mock(side_effect=native_command)
    monkeypatch.setattr(system_cli, "_run_claude_json", run_claude_json)
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        [
            "setup",
            "claude-code",
            "--source",
            "oceanbase/powercontext",
            "--ref",
            "tested-ref",
            "--server-url",
            "http://127.0.0.1:9000",
            "--no-capture-prompts",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["hosts"]["claude-code"]["result"] == {
        "marketplace": "powercontext",
        "plugin": "powercontext",
        "plugin_version": "0.1.0",
        "settings_file": str(config_dir / "settings.json"),
        "cache_dir": str(config_dir / "plugins" / "cache" / "powercontext" / "powercontext" / "0.1.0"),
        "data_dir": str(config_dir / "plugins" / "data" / "powercontext-powercontext"),
        "authorization_state": "not_configured",
    }

    run_claude.assert_any_call(
        "plugin",
        "marketplace",
        "add",
        "oceanbase/powercontext@tested-ref",
        "--scope",
        "user",
    )
    run_claude.assert_any_call(
        "plugin",
        "install",
        "powercontext@powercontext",
        "--scope",
        "user",
    )
    assert json.loads((config_dir / "settings.json").read_text(encoding="utf-8"))["pluginConfigs"] == {
        "powercontext@powercontext": {
            "options": {
                "server_url": "http://127.0.0.1:9000",
                "capture_prompts": False,
                "allow_insecure_http": False,
            }
        }
    }
    statusline = json.loads((config_dir / "settings.json").read_text(encoding="utf-8"))["statusLine"]
    assert statusline["type"] == "command"
    assert statusline["refreshInterval"] == 30
    assert "scripts/statusline.py" in statusline["command"].replace("\\", "/")
    assert "--server-url http://127.0.0.1:9000" in statusline["command"]


def test_setup_claude_code_upgrades_and_preserves_disablement_and_capture_preference(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    config_dir = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    config_dir.mkdir()
    settings: dict[str, Any] = {
        "enabledPlugins": {"powercontext@powercontext": False},
        "pluginConfigs": {"powercontext@powercontext": {"options": {"capture_prompts": False}}},
    }
    (config_dir / "settings.json").write_text(json.dumps(settings))
    installed = [{"id": "powercontext@powercontext", "scope": "user", "version": "0.1.1", "enabled": False}]
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(
            side_effect=lambda *args: (
                [{"name": "powercontext", "source": "github", "repo": "oceanbase/powercontext", "ref": "master"}]
                if args[:3] == ("plugin", "marketplace", "list")
                else installed
            )
        ),
    )
    refreshed = False

    def native_command(*arguments):
        nonlocal refreshed
        if arguments[:3] == ("plugin", "marketplace", "update"):
            refreshed = True
        if arguments[:2] == ("plugin", "update") and refreshed:
            installed[0]["version"] = "0.2.0"
        if arguments[:2] == ("plugin", "install"):
            settings["enabledPlugins"]["powercontext@powercontext"] = True
            (config_dir / "settings.json").write_text(json.dumps(settings))

    run_claude = Mock(side_effect=native_command)
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)

    result = install_plugin(
        "claude-code",
        source="oceanbase/powercontext",
        ref="master",
        server_url="http://127.0.0.1:9000",
    )

    assert result["plugin_version"] == "0.2.0"
    assert Path(result["cache_dir"]).name == "0.2.0"

    persisted = json.loads((config_dir / "settings.json").read_text())
    assert "0.2.0" in persisted["statusLine"]["command"]
    assert persisted["enabledPlugins"]["powercontext@powercontext"] is False
    assert persisted["pluginConfigs"]["powercontext@powercontext"]["options"]["capture_prompts"] is False


def test_setup_claude_code_ignores_a_project_scoped_plugin_when_updating_user_scope(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    config_dir = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    project_plugin = {"id": "powercontext@powercontext", "scope": "project", "version": "0.1.1", "enabled": True}
    user_plugin = {"id": "powercontext@powercontext", "scope": "user", "version": "0.1.2", "enabled": True}
    installed = [project_plugin]
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(
            side_effect=lambda *args: (
                [{"name": "powercontext", "source": "github", "repo": "oceanbase/powercontext", "ref": "master"}]
                if args[:3] == ("plugin", "marketplace", "list")
                else installed
            )
        ),
    )

    def native_command(*arguments):
        if arguments == ("plugin", "install", "powercontext@powercontext", "--scope", "user"):
            installed.append(user_plugin)

    run_claude = Mock(side_effect=native_command)
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)

    result = install_plugin(
        "claude-code",
        source="oceanbase/powercontext",
        ref="master",
        server_url="http://127.0.0.1:9000",
        capture_prompts=True,
    )

    assert result["plugin_version"] == "0.1.2"
    assert project_plugin["version"] == "0.1.1"


def test_setup_claude_code_preserves_preexisting_objects_on_failure(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    config_dir = tmp_path / "claude"
    config_dir.mkdir()
    settings_file = config_dir / "settings.json"
    previous_settings = {
        "enabledPlugins": {"powercontext@powercontext": True},
        "pluginConfigs": {
            "powercontext@powercontext": {"options": {"server_url": "http://127.0.0.1:7000", "capture_prompts": False}}
        },
    }
    settings_file.write_text(json.dumps(previous_settings), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(
            side_effect=lambda *args: (
                [{"name": "powercontext", "source": "github", "repo": "oceanbase/powercontext", "ref": "master"}]
                if args[:3] == ("plugin", "marketplace", "list")
                else []
            )
        ),
    )
    run_claude = Mock()
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)

    with pytest.raises(system_cli.SetupError):
        install_plugin(
            "claude-code",
            source="oceanbase/powercontext",
            ref="master",
            server_url="http://127.0.0.1:8000",
            capture_prompts=True,
        )

    assert json.loads(settings_file.read_text(encoding="utf-8")) == previous_settings


@pytest.mark.parametrize(
    "existing_marketplace",
    [
        {"name": "powercontext", "source": "github", "repo": "other/powercontext", "ref": "tested-ref"},
        {"name": "powercontext", "source": "github", "repo": "oceanbase/powercontext", "ref": "other-ref"},
    ],
    ids=["different-repository", "different-ref"],
)
def test_setup_claude_code_rejects_a_conflicting_existing_marketplace_before_mutation(
    existing_marketplace: dict[str, object], monkeypatch, installed_agent_checks
) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    run_claude_json = Mock(return_value=[existing_marketplace])
    run_claude = Mock()
    monkeypatch.setattr(system_cli, "_run_claude_json", run_claude_json)
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)

    with pytest.raises(system_cli.SetupError, match="marketplace remove powercontext"):
        install_plugin(
            "claude-code",
            source="oceanbase/powercontext",
            ref="tested-ref",
            server_url="http://127.0.0.1:8000",
            capture_prompts=True,
        )

    run_claude.assert_not_called()


def test_setup_claude_code_normalizes_an_mcp_url_before_installing(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    config_dir = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(
            side_effect=lambda *args: (
                [{"name": "powercontext", "source": "github", "repo": "oceanbase/powercontext", "ref": "master"}]
                if args[:3] == ("plugin", "marketplace", "list")
                else [{"id": "powercontext@powercontext", "scope": "user", "version": "0.1.0", "enabled": True}]
            )
        ),
    )
    run_claude = Mock()
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)

    install_plugin(
        "claude-code",
        source="oceanbase/powercontext",
        ref="master",
        server_url="https://memory.example/api/mcp/",
        capture_prompts=True,
    )

    options = json.loads((config_dir / "settings.json").read_text(encoding="utf-8"))["pluginConfigs"][
        "powercontext@powercontext"
    ]["options"]
    assert options["server_url"] == "https://memory.example/api"


def test_setup_claude_code_preserves_an_unrelated_statusline(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    config_dir = tmp_path / "claude"
    config_dir.mkdir()
    settings_file = config_dir / "settings.json"
    custom_statusline = {"type": "command", "command": "custom-status", "refreshInterval": 5}
    settings_file.write_text(json.dumps({"statusLine": custom_statusline}), encoding="utf-8")
    installed = [{"id": "powercontext@powercontext", "scope": "user", "version": "0.1.0", "enabled": True}]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(side_effect=lambda *args: [] if args[:3] == ("plugin", "marketplace", "list") else installed),
    )
    monkeypatch.setattr(system_cli, "_run_claude", Mock())

    install_plugin(
        "claude-code",
        source="oceanbase/powercontext",
        ref="master",
        server_url="http://127.0.0.1:8000",
        capture_prompts=True,
    )

    assert json.loads(settings_file.read_text(encoding="utf-8"))["statusLine"] == custom_statusline


def test_setup_claude_code_preserves_a_command_that_merely_mentions_powercontext(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    config_dir = tmp_path / "claude"
    config_dir.mkdir()
    settings_file = config_dir / "settings.json"
    custom_statusline = {
        "type": "command",
        "command": "echo powercontext statusline.py",
        "refreshInterval": 5,
    }
    settings_file.write_text(json.dumps({"statusLine": custom_statusline}), encoding="utf-8")
    installed = [{"id": "powercontext@powercontext", "scope": "user", "version": "0.1.0", "enabled": True}]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(side_effect=lambda *args: [] if args[:3] == ("plugin", "marketplace", "list") else installed),
    )
    monkeypatch.setattr(system_cli, "_run_claude", Mock())

    install_plugin(
        "claude-code",
        source="oceanbase/powercontext",
        ref="master",
        server_url="http://127.0.0.1:8000",
        capture_prompts=True,
    )

    assert json.loads(settings_file.read_text(encoding="utf-8"))["statusLine"] == custom_statusline


def test_setup_claude_code_refreshes_an_existing_powercontext_statusline(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    config_dir = tmp_path / "claude"
    config_dir.mkdir()
    settings_file = config_dir / "settings.json"
    previous_command = (
        "python3 /home/me/.claude/plugins/cache/powercontext/powercontext/0.0.9/scripts/statusline.py"
        " --server-url http://127.0.0.1:7000"
    )
    settings_file.write_text(
        json.dumps({"statusLine": {"type": "command", "command": previous_command, "refreshInterval": 30}}),
        encoding="utf-8",
    )
    installed = [{"id": "powercontext@powercontext", "scope": "user", "version": "0.1.0", "enabled": True}]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(side_effect=lambda *args: [] if args[:3] == ("plugin", "marketplace", "list") else installed),
    )
    monkeypatch.setattr(system_cli, "_run_claude", Mock())

    install_plugin(
        "claude-code",
        source="oceanbase/powercontext",
        ref="master",
        server_url="http://127.0.0.1:9000",
        capture_prompts=True,
    )

    statusline = json.loads(settings_file.read_text(encoding="utf-8"))["statusLine"]
    assert statusline["command"] != previous_command
    assert "0.0.9" not in statusline["command"]
    assert "scripts/statusline.py" in statusline["command"]
    assert "--server-url http://127.0.0.1:9000" in statusline["command"]


def test_setup_claude_code_preserves_unrelated_settings_when_updating_options(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    config_dir = tmp_path / "claude"
    config_dir.mkdir()
    settings_file = config_dir / "settings.json"
    settings_file.write_text(
        json.dumps({
            "unrelated": {"preserved": True},
            "pluginConfigs": {
                "powercontext@powercontext": {
                    "custom": "preserved",
                    "options": {"server_url": "http://127.0.0.1:7000", "other": 1},
                }
            },
        }),
        encoding="utf-8",
    )
    installed = [{"id": "powercontext@powercontext", "scope": "user", "version": "0.1.0", "enabled": True}]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(side_effect=lambda *args: [] if args[:3] == ("plugin", "marketplace", "list") else installed),
    )
    monkeypatch.setattr(system_cli, "_run_claude", Mock())

    install_plugin(
        "claude-code",
        source="oceanbase/powercontext",
        ref="master",
        server_url="http://127.0.0.1:8000",
        capture_prompts=False,
    )

    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert settings["unrelated"] == {"preserved": True}
    assert settings["pluginConfigs"]["powercontext@powercontext"] == {
        "custom": "preserved",
        "options": {
            "server_url": "http://127.0.0.1:8000",
            "capture_prompts": False,
            "allow_insecure_http": False,
            "other": 1,
        },
    }


@pytest.mark.parametrize(
    "server_url",
    [
        "http://memory.example.com",
        "https://user:password@memory.example.com",
        "https://memory.example.com?token=secret",
        "https://memory.example.com#fragment",
        "file:///tmp/powercontext",
    ],
)
def test_setup_claude_code_rejects_unsafe_server_urls_before_cli_writes(
    monkeypatch, server_url: str, installed_agent_checks
) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    run_claude = Mock()
    run_claude_json = Mock()
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)
    monkeypatch.setattr(system_cli, "_run_claude_json", run_claude_json)

    with pytest.raises(system_cli.SetupError):
        install_plugin(
            "claude-code",
            source="oceanbase/powercontext",
            ref="master",
            server_url=server_url,
            capture_prompts=True,
        )

    run_claude.assert_not_called()
    run_claude_json.assert_not_called()


def test_doctor_reports_each_check_and_exits_nonzero_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        system_cli,
        "run_diagnostics",
        lambda **_kwargs: {
            "package": Diagnostic(status=DiagnosticStatus.OK, detail="powercontext 0.0.1"),
            "server_liveness": Diagnostic(status=DiagnosticStatus.FAILED, detail="cannot connect"),
            "server_readiness": Diagnostic(
                status=DiagnosticStatus.SKIPPED,
                detail="not checked because Server liveness failed",
            ),
        },
    )

    result = CliRunner().invoke(
        create_cli([doctor_app]),
        ["doctor", "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "ok": False,
        "status": "failed",
        "checks": {
            "package": {"ok": True, "status": "ok", "detail": "powercontext 0.0.1"},
            "server_liveness": {"ok": False, "status": "failed", "detail": "cannot connect"},
            "server_readiness": {
                "ok": False,
                "status": "skipped",
                "detail": "not checked because Server liveness failed",
            },
        },
    }


def _service_status(
    *,
    endpoint: str = "http://127.0.0.1:8000",
    manager: ManagerState = ManagerState.UNKNOWN,
    ownership: ManagerOwnershipState = ManagerOwnershipState.UNKNOWN,
) -> ServiceStatus:
    return ServiceStatus(
        support=SupportState.SUPPORTED,
        registration=RegistrationState.INSTALLED,
        definition=DefinitionState.CURRENT,
        manager=manager,
        server_liveness=LivenessState.UNKNOWN,
        endpoint=endpoint,
        log_location="fake logs",
        manager_ownership=ownership,
    )


def test_doctor_mismatched_local_endpoint_skips_service_query_and_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    class Controller:
        def registration_status(self) -> ServiceStatus:
            return _service_status()

        def status(self) -> ServiceStatus:
            raise AssertionError(  # noqa: TRY003
                "mismatched endpoint must not query the manager or registered endpoint"
            )

    monkeypatch.setattr("powercontext.service.controller.ServiceController", Controller)

    assert system_cli._local_service_diagnostics("http://127.0.0.1:9000") == {}


@pytest.mark.parametrize(
    "target",
    ["http://localhost:8000", "http://127.0.0.2:8000/", "http://[::1]:8000"],
)
def test_doctor_matching_loopback_endpoint_includes_service_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    class Controller:
        def registration_status(self) -> ServiceStatus:
            return _service_status()

        def status(self) -> ServiceStatus:
            return _service_status(
                manager=ManagerState.ACTIVE,
                ownership=ManagerOwnershipState.OWNED,
            )

    monkeypatch.setattr("powercontext.service.controller.ServiceController", Controller)

    diagnostics = system_cli._local_service_diagnostics(target)

    assert diagnostics["service_registration"].status is DiagnosticStatus.OK
    assert diagnostics["service_manager"].status is DiagnosticStatus.OK


@pytest.mark.parametrize("target", ["https://memory.example", "http://192.0.2.10:8000"])
def test_doctor_remote_endpoint_skips_local_registration(monkeypatch: pytest.MonkeyPatch, target: str) -> None:
    monkeypatch.setattr(
        "powercontext.service.controller.ServiceController",
        Mock(side_effect=AssertionError("remote endpoint must not inspect the local service")),
    )

    assert system_cli._local_service_diagnostics(target) == {}


class _Response(BytesIO):
    def __init__(self, status: int, payload: object) -> None:
        super().__init__(json.dumps(payload).encode())
        self._status = status

    def getcode(self) -> int:
        return self._status


def test_default_doctor_checks_server_without_inspecting_codex(monkeypatch) -> None:
    _mock_optional_personal_service(monkeypatch)
    monkeypatch.setattr(
        system_cli.CodexInstaller,
        "verify",
        staticmethod(Mock(side_effect=AssertionError("default doctor must not inspect Codex"))),
    )
    monkeypatch.setattr(
        system_cli,
        "urlopen",
        Mock(
            side_effect=[
                _Response(200, {"status": "ok"}),
                _Response(200, {"status": "ready", "checks": {"runtime": "ready", "database": "ready"}}),
            ]
        ),
    )

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert list(payload["checks"]) == [
        "package",
        "service_support",
        "service_registration",
        "server_liveness",
        "server_readiness",
    ]


def test_default_doctor_uses_client_server_url_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "http://127.0.0.1:8888/")
    urlopen = Mock(
        side_effect=[
            _Response(200, {"status": "ok"}),
            _Response(200, {"status": "ready", "checks": {"runtime": "ready", "database": "ready"}}),
        ]
    )
    monkeypatch.setattr(system_cli, "urlopen", urlopen)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor"])

    assert result.exit_code == 0
    assert "server liveness: ok - http://127.0.0.1:8888 status=ok" in result.output
    assert "server readiness: ok - http://127.0.0.1:8888 status=ready" in result.output
    assert [call.args[0].full_url for call in urlopen.call_args_list] == [
        "http://127.0.0.1:8888/health/live",
        "http://127.0.0.1:8888/health/ready",
    ]

    # Explicit CLI argument should override the environment variable.
    urlopen.reset_mock()
    urlopen.side_effect = [
        _Response(200, {"status": "ok"}),
        _Response(200, {"status": "ready", "checks": {"runtime": "ready", "database": "ready"}}),
    ]
    override = CliRunner().invoke(
        create_cli([doctor_app]),
        ["doctor", "--server-url", "http://127.0.0.1:9999"],
    )

    assert override.exit_code == 0
    assert "server liveness: ok - http://127.0.0.1:9999 status=ok" in override.output
    assert "server readiness: ok - http://127.0.0.1:9999 status=ready" in override.output
    assert [call.args[0].full_url for call in urlopen.call_args_list] == [
        "http://127.0.0.1:9999/health/live",
        "http://127.0.0.1:9999/health/ready",
    ]


def test_default_doctor_rejects_non_loopback_plaintext_environment_url_without_request(monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "http://memory.example")
    urlopen = Mock()
    monkeypatch.setattr(system_cli, "urlopen", urlopen)
    monkeypatch.setattr(
        system_cli,
        "_local_service_diagnostics",
        Mock(side_effect=AssertionError("invalid URL must not inspect the local service")),
    )

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor"])

    assert result.exit_code == 1
    assert "Unencrypted PowerContext Server URLs must be loopback addresses" in result.output
    urlopen.assert_not_called()


def test_default_doctor_skips_readiness_when_liveness_is_unreachable(monkeypatch) -> None:
    _mock_optional_personal_service(monkeypatch)
    urlopen = Mock(side_effect=OSError("connection refused"))
    monkeypatch.setattr(system_cli, "urlopen", urlopen)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor"])

    assert result.exit_code == 1
    assert "server liveness: failed - cannot reach http://127.0.0.1:8000" in result.output
    assert "server readiness: skipped - not checked because Server liveness failed" in result.output
    assert urlopen.call_count == 1


def test_default_doctor_preserves_not_ready_checks_in_human_and_json_output(monkeypatch) -> None:
    _mock_optional_personal_service(monkeypatch)

    def responses() -> list[object]:
        readiness = HTTPError(
            "http://127.0.0.1:8000/health/ready",
            503,
            "Service Unavailable",
            hdrs=Message(),
            fp=_Response(
                503,
                {
                    "status": "not_ready",
                    "checks": {
                        "runtime": "ready",
                        "database": "unavailable",
                    },
                },
            ),
        )
        return [_Response(200, {"status": "ok"}), readiness]

    monkeypatch.setattr(system_cli, "urlopen", Mock(side_effect=responses()))
    human = CliRunner().invoke(create_cli([doctor_app]), ["doctor"])
    monkeypatch.setattr(system_cli, "urlopen", Mock(side_effect=responses()))
    machine = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "--json"])

    assert human.exit_code == 1
    assert "server readiness: failed - http://127.0.0.1:8000 status=not_ready" in human.output
    assert "  database: unavailable" in human.output
    assert machine.exit_code == 1
    assert json.loads(machine.output)["status"] == "failed"
    assert json.loads(machine.output)["checks"]["server_readiness"] == {
        "ok": False,
        "status": "failed",
        "detail": "http://127.0.0.1:8000 status=not_ready",
        "checks": {
            "runtime": "ready",
            "database": "unavailable",
        },
    }


def test_default_doctor_preserves_degraded_checks_in_human_and_json_output(monkeypatch) -> None:
    _mock_optional_personal_service(monkeypatch)
    monkeypatch.setattr(system_cli, "version", lambda _package: "0.0.2")

    def responses() -> list[_Response]:
        return [
            _Response(200, {"status": "ok"}),
            _Response(
                200,
                {
                    "status": "degraded",
                    "checks": {
                        "runtime": "ready",
                        "database": "ready",
                        "inference.embedding": "misconfigured",
                    },
                },
            ),
            _Response(200, {"status": "ok"}),
            _Response(200, [{"scope_id": "default", "display_name": "Default", "summary": ""}]),
        ]

    monkeypatch.setattr(system_cli, "urlopen", Mock(side_effect=responses()))
    human = CliRunner().invoke(create_cli([doctor_app]), ["doctor"])
    monkeypatch.setattr(system_cli, "urlopen", Mock(side_effect=responses()))
    machine = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "--json"])

    assert human.exit_code == 1
    assert "server readiness: degraded - http://127.0.0.1:8000 status=degraded" in human.output
    assert "  inference.embedding: misconfigured" in human.output
    assert machine.exit_code == 1
    assert json.loads(machine.output) == {
        "ok": False,
        "status": "degraded",
        "checks": {
            "package": {
                "ok": True,
                "status": "ok",
                "detail": "powercontext 0.0.2",
            },
            "service_support": {
                "ok": True,
                "status": "ok",
                "detail": "native personal service adapter is supported",
            },
            "service_registration": {
                "ok": True,
                "status": "ok",
                "detail": "not_installed (optional)",
            },
            "server_liveness": {
                "ok": True,
                "status": "ok",
                "detail": "http://127.0.0.1:8000 status=ok",
            },
            "server_readiness": {
                "ok": False,
                "status": "degraded",
                "detail": "http://127.0.0.1:8000 status=degraded",
                "checks": {
                    "runtime": "ready",
                    "database": "ready",
                    "inference.embedding": "misconfigured",
                },
            },
        },
    }


def _mock_optional_personal_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        system_cli,
        "_local_service_diagnostics",
        lambda _server_url: {
            "service_support": Diagnostic(
                status=DiagnosticStatus.OK,
                detail="native personal service adapter is supported",
            ),
            "service_registration": Diagnostic(
                status=DiagnosticStatus.OK,
                detail="not_installed (optional)",
            ),
        },
    )


def test_doctor_codex_reports_missing_cli_and_skipped_plugin(tmp_path, monkeypatch, server_checks) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: None)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "codex", "--json"])

    assert result.exit_code == 1
    checks = json.loads(result.output)["hosts"]["codex"]["checks"]
    assert checks["codex"]["status"] == "failed"
    assert checks["plugin"]["status"] == "skipped"


def test_doctor_codex_requires_an_enabled_powercontext_plugin(monkeypatch, server_checks) -> None:
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_run_codex_json", lambda *_args: {"installed": []})

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "codex"])

    assert result.exit_code == 1
    assert "plugin: failed - PowerContext plugin is not installed" in result.output


def test_doctor_claude_code_reports_missing_cli_and_skipped_plugin(monkeypatch, server_checks) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: None)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "claude-code", "--json"])

    assert result.exit_code == 1
    checks = json.loads(result.output)["hosts"]["claude-code"]["checks"]
    assert checks["claude_code"]["status"] == "failed"
    assert checks["plugin"]["status"] == "skipped"


def test_doctor_claude_code_requires_an_enabled_powercontext_plugin(monkeypatch, server_checks) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(system_cli, "_run_claude_json", lambda *_args: [])

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "claude-code"])

    assert result.exit_code == 1
    assert "plugin: failed - PowerContext plugin is not installed" in result.output


def test_doctor_claude_reports_enabled_plugin_load_errors(monkeypatch, server_checks):
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        lambda *_args: [
            {
                "id": "powercontext@powercontext",
                "enabled": True,
                "errors": ["Failed to load hooks: invalid hooks.json"],
            }
        ],
    )
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "claude-code", "--json"])
    assert result.exit_code == 1
    plugin = json.loads(result.output)["hosts"]["claude-code"]["checks"]["plugin"]
    assert plugin["status"] == "failed"
    assert "Failed to load hooks" in plugin["detail"]


def test_setup_dsh_adds_plugin_from_a_local_checkout(tmp_path: Path, monkeypatch, installed_agent_checks) -> None:
    import powercontext.cli.dsh as dsh_cli
    import powercontext.cli.dsh_transport as dsh_transport

    checkout = tmp_path / "powercontext"
    plugin = checkout / "integrations" / "dsh" / "plugins" / "powercontext"
    plugin.mkdir(parents=True)
    (plugin / "package.json").write_text('{"name": "powercontext-dsh"}', encoding="utf-8")
    (plugin / "lib").mkdir()
    (plugin / "lib" / "index.js").write_text("export const name = 'powercontext-dsh'\n", encoding="utf-8")
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(dsh_cli, "which", lambda _name: "/usr/bin/dsh")
    run_dsh = Mock(return_value="id: powercontext-dsh\n")
    monkeypatch.setattr(dsh_cli, "_run_dsh", run_dsh)
    monkeypatch.setattr(dsh_transport, "read_dsh_settings", lambda **_kwargs: {})

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "dsh", "--source", str(checkout), "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["hosts"]["dsh"]["result"] == {
        "plugin": "powercontext-dsh",
        "plugin_path": str(plugin),
        "data_dir": str(tmp_path / "data"),
        "authorization_state": "not_configured",
        "profile": "web",
        "profile_dir": str(tmp_path / "dsh-home/profiles/web"),
    }
    assert run_dsh.call_args_list[0].args == (
        "plugin",
        "--profile",
        "web",
        "add",
        str(plugin),
    )


def test_setup_dsh_fails_when_dsh_cli_is_missing(tmp_path: Path, monkeypatch, installed_agent_checks) -> None:
    import powercontext.cli.dsh as dsh_cli

    monkeypatch.setattr(dsh_cli, "which", lambda _name: None)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "dsh", "--source", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "DeepSeek Harness CLI is not installed" in result.output


def test_doctor_dsh_reports_missing_cli_and_skipped_plugin(monkeypatch, server_checks) -> None:
    import powercontext.cli.dsh as dsh_cli

    monkeypatch.setattr(dsh_cli, "which", lambda _name: None)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "dsh", "--json"])

    assert result.exit_code == 1
    checks = json.loads(result.output)["hosts"]["dsh"]["checks"]
    assert checks["dsh"]["status"] == "failed"
    assert checks["plugin"]["status"] == "skipped"


def test_doctor_dsh_requires_the_installed_plugin(monkeypatch, server_checks) -> None:
    import powercontext.cli.dsh as dsh_cli

    monkeypatch.setattr(dsh_cli, "which", lambda _name: "/usr/bin/dsh")
    monkeypatch.setattr(dsh_cli, "_run_dsh", lambda *_args, **_kwargs: "id: other-plugin\n")

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "dsh"])

    assert result.exit_code == 1
    assert "plugin: failed - PowerContext DSH plugin is not registered in the Web profile" in result.output


def test_codex_configuration_and_doctor_follow_the_native_installation_not_old_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "installed": True,
                    "enabled": True,
                    "pluginId": "powercontext@powercontext",
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    monkeypatch.setattr(
        system_cli,
        "_probe_codex_mcp_status",
        lambda **_kwargs: {
            "tools": {name: {} for name in system_cli._CODEX_FULL_MCP_TOOLS},
        },
    )
    monkeypatch.setattr(
        system_cli,
        "server_diagnostics",
        lambda **options: {
            "server_readiness": Diagnostic(DiagnosticStatus.OK, options["server_url"]),
        },
    )
    obsolete = tmp_path / "codex/plugins/cache/powercontext/powercontext/0.0.0/.mcp.json"
    obsolete.parent.mkdir(parents=True)
    original = '{"mcpServers":{"powercontext":{"type":"http","url":"https://old.example/mcp/"}}}'
    obsolete.write_text(original)
    runner = CliRunner()
    configured = runner.invoke(
        create_cli(), ["config", "agent", "codex", "--server-url", "https://new.example", "--json"]
    )
    assert configured.exit_code == 0, configured.output
    diagnosed = runner.invoke(create_cli(), ["doctor", "codex", "--json"])
    assert diagnosed.exit_code == 0, diagnosed.output
    report = json.loads(diagnosed.output)["hosts"]["codex"]
    assert report["checks"]["server_readiness"]["detail"] == "https://new.example"
    assert obsolete.read_text() == original
