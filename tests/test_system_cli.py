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
import sys
from email.message import Message
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest
from typer.testing import CliRunner

import powercontext.cli.authorization as authorization_cli
import powercontext.cli.system as system_cli
from powercontext.cli.app import create_cli
from powercontext.cli.system import Diagnostic, DiagnosticStatus, doctor_app, setup_app
from powercontext.paths import default_scheduler_path
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


@pytest.fixture(autouse=True)
def isolated_codex_plugin_cache(tmp_path, monkeypatch):
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
    assert default_scheduler_path() == data_dir / "scheduler.db"


def test_setup_codex_installs_from_a_remote_ref_and_prepares_storage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(data_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    run_codex = Mock(
        side_effect=[
            {"marketplaceName": "powercontext", "alreadyAdded": False},
            {"name": "powercontext", "version": "0.1.0"},
            {
                "installed": [
                    {
                        "name": "powercontext",
                        "pluginId": "powercontext@powercontext",
                        "installed": True,
                        "enabled": True,
                    }
                ]
            },
        ]
    )
    monkeypatch.setattr(system_cli, "_run_codex_json", run_codex)

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

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "marketplace": "powercontext",
        "plugin": "powercontext",
        "plugin_version": "0.1.0",
        "data_dir": str(data_dir),
        "authorization_state": "not_configured",
    }
    assert data_dir.is_dir()
    assert run_codex.call_args_list[0].args == (
        "plugin",
        "marketplace",
        "add",
        "oceanbase/powercontext",
        "--ref",
        "tested-ref",
    )
    assert run_codex.call_args_list[1].args == (
        "plugin",
        "add",
        "powercontext@powercontext",
    )
    assert run_codex.call_args_list[2].args == ("plugin", "list")


def test_codex_diagnostics_verify_native_mcp_tools_without_process_authorization(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
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
                }
            ]
        },
    )
    monkeypatch.delenv("POWERCONTEXT_CODEX_AUTHORIZATION", raising=False)

    diagnostics = system_cli.run_codex_diagnostics()

    assert diagnostics["mcp_configuration"].status is DiagnosticStatus.OK
    assert diagnostics["authorization"].status is DiagnosticStatus.OK
    assert diagnostics["mcp_tools"].status is DiagnosticStatus.OK
    assert "2 tools" in diagnostics["mcp_tools"].detail


def test_codex_diagnostics_fail_when_required_native_tools_are_missing(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
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
                }
            ]
        },
    )
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", lambda **_kwargs: {"name": "powercontext", "tools": {}})

    diagnostics = system_cli.run_codex_diagnostics()

    assert diagnostics["mcp_tools"].status is DiagnosticStatus.FAILED
    assert "remember_memory, search_memory" in diagnostics["mcp_tools"].detail
    assert "POWERCONTEXT_CODEX_AUTHORIZATION" in diagnostics["mcp_tools"].detail


def test_codex_diagnostics_reject_native_mcp_without_authorization_environment(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
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

    diagnostics = system_cli.run_codex_diagnostics()

    assert diagnostics["mcp_configuration"].status is DiagnosticStatus.FAILED
    assert "reinstall the current plugin" in diagnostics["mcp_configuration"].detail
    assert diagnostics["authorization"].status is DiagnosticStatus.SKIPPED
    probe.assert_not_called()


def test_codex_diagnostics_reject_url_mismatched_stored_authorization(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
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
    probe = Mock()
    monkeypatch.setattr(system_cli, "_probe_codex_mcp_status", probe)

    diagnostics = system_cli.run_codex_diagnostics()

    assert diagnostics["authorization"].status is DiagnosticStatus.FAILED
    assert "url_mismatch" in diagnostics["authorization"].detail
    assert diagnostics["mcp_tools"].status is DiagnosticStatus.SKIPPED
    probe.assert_not_called()


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
    monkeypatch.setattr(system_cli.subprocess, "Popen", popen)

    _probe_codex_mcp_status(authorization="Bearer resolved-token")

    assert popen.call_args.kwargs["env"]["POWERCONTEXT_CODEX_AUTHORIZATION"] == "Bearer resolved-token"


def test_setup_codex_persists_setup_only_token_in_codex_owned_storage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "setup-token")
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    configure_desktop = Mock(return_value=True)
    monkeypatch.setattr(authorization_cli, "configure_codex_desktop_authorization", configure_desktop)
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        Mock(
            side_effect=[
                {"marketplaceName": "powercontext"},
                {"name": "powercontext", "version": "0.1.0"},
            ]
        ),
    )

    result = system_cli.install_codex_plugin(source="oceanbase/powercontext", ref="master")

    assert result.authorization_state == "configured"
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

    diagnostics = system_cli.run_codex_diagnostics()

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

    diagnostics = system_cli.run_codex_diagnostics()

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

    diagnostics = system_cli.run_codex_diagnostics()

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

    diagnostics = system_cli.run_codex_diagnostics()

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

    diagnostics = system_cli.run_codex_diagnostics()

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

    diagnostics = system_cli.run_codex_diagnostics()

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
    monkeypatch.setattr(
        system_cli,
        "_run_codex_json",
        lambda *_args: {
            "installed": [
                {"name": "powercontext", "pluginId": "powercontext@powercontext", "installed": True, "enabled": True}
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

    diagnostics = system_cli.run_codex_diagnostics()

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

    diagnostics = system_cli.run_codex_diagnostics()

    assert diagnostics["authorization"].status is DiagnosticStatus.FAILED
    assert "not available to the Codex host" in diagnostics["authorization"].detail
    assert diagnostics["mcp_tools"].status is DiagnosticStatus.SKIPPED
    probe.assert_not_called()


def test_setup_codex_uses_an_absolute_local_marketplace_without_a_ref(
    tmp_path: Path,
    monkeypatch,
) -> None:
    marketplace = tmp_path / "marketplace"
    marketplace.mkdir()
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    run_codex = Mock(
        side_effect=[
            {"marketplaceName": "powercontext-local"},
            {"name": "powercontext", "version": "0.1.0"},
            {
                "installed": [
                    {
                        "name": "powercontext",
                        "pluginId": "powercontext@powercontext-local",
                        "installed": True,
                        "enabled": True,
                    }
                ]
            },
        ]
    )
    monkeypatch.setattr(system_cli, "_run_codex_json", run_codex)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "codex", "--source", str(marketplace)],
    )

    assert result.exit_code == 0
    assert "Authorization: not_configured" in result.output
    assert run_codex.call_args_list[0].args == (
        "plugin",
        "marketplace",
        "add",
        str(marketplace),
    )


def test_setup_claude_code_reports_mutations_then_installs_and_verifies(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    run_claude_json = Mock(
        side_effect=[
            [],
            [],
            [
                {
                    "id": "powercontext@powercontext",
                    "scope": "user",
                    "version": "0.1.0",
                    "enabled": True,
                }
            ],
        ]
    )
    run_claude = Mock()
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
    assert json.loads(result.stdout) == {
        "marketplace": "powercontext",
        "plugin": "powercontext",
        "plugin_version": "0.1.0",
        "settings_file": str(config_dir / "settings.json"),
        "cache_dir": str(config_dir / "plugins" / "cache" / "powercontext" / "powercontext" / "<version>"),
        "data_dir": str(config_dir / "plugins" / "data" / "powercontext-powercontext"),
        "authorization_state": "not_configured",
    }
    assert "no changes made yet" in result.stderr
    assert str(config_dir / "settings.json") in result.stderr
    assert "read/write access" in result.stderr
    assert "claude plugin uninstall powercontext@powercontext --scope user" in result.stderr
    assert "claude plugin marketplace remove powercontext" in result.stderr
    assert run_claude_json.call_args_list[0].args == ("plugin", "marketplace", "list")
    assert run_claude_json.call_args_list[1].args == ("plugin", "list")
    assert run_claude.call_args_list[0].args == (
        "plugin",
        "marketplace",
        "add",
        "oceanbase/powercontext@tested-ref",
        "--scope",
        "user",
    )
    assert run_claude.call_args_list[1].args == (
        "plugin",
        "install",
        "powercontext@powercontext",
        "--scope",
        "user",
    )
    assert run_claude_json.call_args_list[2].args == ("plugin", "list")
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


def test_setup_claude_code_refreshes_the_marketplace_and_plugin_before_installing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    installed = [{"id": "powercontext@powercontext", "scope": "user", "version": "0.1.1", "enabled": True}]
    refreshed = [{"id": "powercontext@powercontext", "scope": "user", "version": "0.1.2", "enabled": True}]
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(
            side_effect=[
                [{"name": "powercontext", "source": "github", "repo": "oceanbase/powercontext", "ref": "master"}],
                installed,
                refreshed,
            ]
        ),
    )
    run_claude = Mock()
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)

    result = system_cli.install_claude_code_plugin(
        source="oceanbase/powercontext",
        ref="master",
        server_url="http://127.0.0.1:9000",
        capture_prompts=True,
    )

    assert result.plugin_version == "0.1.2"
    assert [call.args for call in run_claude.call_args_list] == [
        ("plugin", "marketplace", "update", "powercontext"),
        ("plugin", "update", "powercontext@powercontext", "--scope", "user"),
        ("plugin", "install", "powercontext@powercontext", "--scope", "user"),
    ]


def test_setup_claude_code_ignores_a_project_scoped_plugin_when_updating_user_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    project_plugin = {"id": "powercontext@powercontext", "scope": "project", "version": "0.1.1", "enabled": True}
    user_plugin = {"id": "powercontext@powercontext", "scope": "user", "version": "0.1.2", "enabled": True}
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(
            side_effect=[
                [{"name": "powercontext", "source": "github", "repo": "oceanbase/powercontext", "ref": "master"}],
                [project_plugin],
                [project_plugin, user_plugin],
            ]
        ),
    )
    run_claude = Mock()
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)

    result = system_cli.install_claude_code_plugin(
        source="oceanbase/powercontext",
        ref="master",
        server_url="http://127.0.0.1:9000",
        capture_prompts=True,
    )

    assert result.plugin_version == "0.1.2"
    assert [call.args for call in run_claude.call_args_list] == [
        ("plugin", "marketplace", "update", "powercontext"),
        ("plugin", "install", "powercontext@powercontext", "--scope", "user"),
    ]


def test_setup_claude_code_rolls_back_only_new_objects_after_verification_failure(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(system_cli, "_run_claude_json", Mock(side_effect=[[], [], []]))
    run_claude = Mock()
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)

    with pytest.raises(system_cli.SetupError):
        system_cli.install_claude_code_plugin(
            source="https://github.com/oceanbase/powercontext.git",
            ref="tested-ref",
            server_url="http://127.0.0.1:8000",
            capture_prompts=True,
        )

    assert run_claude.call_args_list[-2].args == (
        "plugin",
        "uninstall",
        "powercontext@powercontext",
        "--scope",
        "user",
    )
    assert run_claude.call_args_list[-1].args == (
        "plugin",
        "marketplace",
        "remove",
        "powercontext",
    )


def test_setup_claude_code_preserves_preexisting_objects_on_failure(tmp_path: Path, monkeypatch) -> None:
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
    installed = [{"id": "powercontext@powercontext", "scope": "user", "version": "0.1.0", "enabled": True}]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(
            side_effect=[
                [{"name": "powercontext", "source": "github", "repo": "oceanbase/powercontext", "ref": "master"}],
                installed,
                [],
            ]
        ),
    )
    run_claude = Mock()
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)

    with pytest.raises(system_cli.SetupError):
        system_cli.install_claude_code_plugin(
            source="oceanbase/powercontext",
            ref="master",
            server_url="http://127.0.0.1:8000",
            capture_prompts=True,
        )

    assert [call.args[:2] for call in run_claude.call_args_list] == [
        ("plugin", "marketplace"),
        ("plugin", "update"),
        ("plugin", "install"),
    ]
    assert json.loads(settings_file.read_text(encoding="utf-8")) == previous_settings


def test_setup_claude_code_restores_a_preexisting_disabled_plugin_after_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "claude"
    config_dir.mkdir()
    settings_file = config_dir / "settings.json"
    previous_settings = {
        "enabledPlugins": {"powercontext@powercontext": False},
        "pluginConfigs": {
            "powercontext@powercontext": {"options": {"server_url": "http://127.0.0.1:7000", "capture_prompts": False}}
        },
        "unrelated": {"preserved": True},
    }
    settings_file.write_text(json.dumps(previous_settings), encoding="utf-8")
    disabled = [{"id": "powercontext@powercontext", "scope": "user", "version": "0.1.0", "enabled": False}]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(
            side_effect=[
                [{"name": "powercontext", "source": "github", "repo": "oceanbase/powercontext", "ref": "master"}],
                disabled,
                [],
            ]
        ),
    )

    def run_claude(*arguments: str) -> None:
        if arguments[:2] == ("plugin", "install"):
            changed = {
                **previous_settings,
                "enabledPlugins": {"powercontext@powercontext": True},
                "pluginConfigs": {
                    "powercontext@powercontext": {
                        "options": {"server_url": "http://127.0.0.1:8000", "capture_prompts": True}
                    }
                },
            }
            settings_file.write_text(json.dumps(changed), encoding="utf-8")

    run_claude_mock = Mock(side_effect=run_claude)
    monkeypatch.setattr(system_cli, "_run_claude", run_claude_mock)

    with pytest.raises(system_cli.SetupError):
        system_cli.install_claude_code_plugin(
            source="oceanbase/powercontext",
            ref="master",
            server_url="http://127.0.0.1:8000",
            capture_prompts=True,
        )

    assert [call.args[:2] for call in run_claude_mock.call_args_list] == [
        ("plugin", "marketplace"),
        ("plugin", "update"),
        ("plugin", "install"),
    ]
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
    existing_marketplace: dict[str, object],
    monkeypatch,
) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    run_claude_json = Mock(return_value=[existing_marketplace])
    run_claude = Mock()
    monkeypatch.setattr(system_cli, "_run_claude_json", run_claude_json)
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)

    with pytest.raises(system_cli.SetupError, match="marketplace remove powercontext"):
        system_cli.install_claude_code_plugin(
            source="oceanbase/powercontext",
            ref="tested-ref",
            server_url="http://127.0.0.1:8000",
            capture_prompts=True,
        )

    run_claude_json.assert_called_once_with("plugin", "marketplace", "list")
    run_claude.assert_not_called()


@pytest.mark.parametrize(
    ("source", "ref", "expected"),
    [
        ("oceanbase/powercontext", "feature", "oceanbase/powercontext@feature"),
        (
            "https://github.com/oceanbase/powercontext.git",
            "feature",
            "https://github.com/oceanbase/powercontext.git#feature",
        ),
    ],
)
def test_claude_marketplace_remote_ref_syntax(source: str, ref: str, expected: str) -> None:
    assert system_cli._normalize_claude_marketplace_source(source, ref=ref) == expected


def test_claude_marketplace_accepts_json_that_omits_the_configured_ref() -> None:
    assert system_cli._claude_marketplace_matches(
        {"source": "github", "repo": "oceanbase/powercontext"},
        "oceanbase/powercontext@master",
    )


def test_setup_claude_code_normalizes_an_mcp_url_before_installing(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(
            side_effect=[
                [{"name": "powercontext", "source": "github", "repo": "oceanbase/powercontext", "ref": "master"}],
                [{"id": "powercontext@powercontext", "scope": "user", "version": "0.1.0", "enabled": True}],
                [{"id": "powercontext@powercontext", "scope": "user", "version": "0.1.0", "enabled": True}],
            ]
        ),
    )
    run_claude = Mock()
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)

    system_cli.install_claude_code_plugin(
        source="oceanbase/powercontext",
        ref="master",
        server_url="https://memory.example/api/mcp/",
        capture_prompts=True,
    )

    options = json.loads((config_dir / "settings.json").read_text(encoding="utf-8"))["pluginConfigs"][
        "powercontext@powercontext"
    ]["options"]
    assert options["server_url"] == "https://memory.example/api"
    assert "--config" not in run_claude.call_args.args


def test_setup_claude_code_preserves_an_unrelated_statusline(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "claude"
    config_dir.mkdir()
    settings_file = config_dir / "settings.json"
    custom_statusline = {"type": "command", "command": "custom-status", "refreshInterval": 5}
    settings_file.write_text(json.dumps({"statusLine": custom_statusline}), encoding="utf-8")
    installed = [{"id": "powercontext@powercontext", "scope": "user", "version": "0.1.0", "enabled": True}]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(system_cli, "_run_claude_json", Mock(side_effect=[[], installed, installed]))
    monkeypatch.setattr(system_cli, "_run_claude", Mock())

    system_cli.install_claude_code_plugin(
        source="oceanbase/powercontext",
        ref="master",
        server_url="http://127.0.0.1:8000",
        capture_prompts=True,
    )

    assert json.loads(settings_file.read_text(encoding="utf-8"))["statusLine"] == custom_statusline


def test_setup_claude_code_preserves_a_command_that_merely_mentions_powercontext(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setattr(system_cli, "_run_claude_json", Mock(side_effect=[[], installed, installed]))
    monkeypatch.setattr(system_cli, "_run_claude", Mock())

    system_cli.install_claude_code_plugin(
        source="oceanbase/powercontext",
        ref="master",
        server_url="http://127.0.0.1:8000",
        capture_prompts=True,
    )

    assert json.loads(settings_file.read_text(encoding="utf-8"))["statusLine"] == custom_statusline


def test_setup_claude_code_refreshes_an_existing_powercontext_statusline(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setattr(system_cli, "_run_claude_json", Mock(side_effect=[[], installed, installed]))
    monkeypatch.setattr(system_cli, "_run_claude", Mock())

    system_cli.install_claude_code_plugin(
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


def test_setup_claude_code_preserves_unrelated_settings_when_updating_options(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setattr(system_cli, "_run_claude_json", Mock(side_effect=[[], installed, installed]))
    monkeypatch.setattr(system_cli, "_run_claude", Mock())

    system_cli.install_claude_code_plugin(
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
    monkeypatch,
    server_url: str,
) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    run_claude = Mock()
    run_claude_json = Mock()
    monkeypatch.setattr(system_cli, "_run_claude", run_claude)
    monkeypatch.setattr(system_cli, "_run_claude_json", run_claude_json)

    with pytest.raises(system_cli.SetupError):
        system_cli.install_claude_code_plugin(
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
    calls: list[str] = []

    class Controller:
        def registration_status(self) -> ServiceStatus:
            calls.append("registration")
            return _service_status()

        def status(self) -> ServiceStatus:
            calls.append("manager")
            return _service_status(
                manager=ManagerState.ACTIVE,
                ownership=ManagerOwnershipState.OWNED,
            )

    monkeypatch.setattr("powercontext.service.controller.ServiceController", Controller)

    diagnostics = system_cli._local_service_diagnostics(target)

    assert calls == ["registration", "manager"]
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
        system_cli,
        "run_codex_diagnostics",
        Mock(side_effect=AssertionError("default doctor must not inspect Codex")),
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


def test_doctor_codex_reports_missing_cli_and_skipped_plugin(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: None)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "codex", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "ok": False,
        "status": "failed",
        "checks": {
            "codex": {
                "ok": False,
                "status": "failed",
                "detail": "Codex CLI is not installed or is not on PATH",
            },
            "plugin": {
                "ok": False,
                "status": "skipped",
                "detail": "not checked because Codex CLI is unavailable",
            },
        },
    }


def test_doctor_codex_requires_an_enabled_powercontext_plugin(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(system_cli, "_run_codex_json", lambda *_args: {"installed": []})

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "codex"])

    assert result.exit_code == 1
    assert "codex: ok - /usr/bin/codex" in result.output
    assert "plugin: failed - PowerContext plugin is not installed" in result.output


def test_doctor_claude_code_reports_missing_cli_and_skipped_plugin(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: None)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "claude-code", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "ok": False,
        "status": "failed",
        "checks": {
            "claude_code": {
                "ok": False,
                "status": "failed",
                "detail": "Claude Code CLI is not installed or is not on PATH",
            },
            "plugin": {
                "ok": False,
                "status": "skipped",
                "detail": "not checked because Claude Code CLI is unavailable",
            },
        },
    }


def test_doctor_claude_code_requires_an_enabled_powercontext_plugin(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(system_cli, "_run_claude_json", lambda *_args: [])

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "claude-code"])

    assert result.exit_code == 1
    assert "claude code: ok - /usr/bin/claude" in result.output
    assert "plugin: failed - PowerContext plugin is not installed" in result.output


def test_claude_runner_uses_the_resolved_executable(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/resolved/bin/claude")
    run = Mock(return_value=system_cli.subprocess.CompletedProcess([], 0, stdout="[]", stderr=""))
    monkeypatch.setattr(system_cli.subprocess, "run", run)

    assert system_cli._run_claude_json("plugin", "list") == []
    assert run.call_args.args[0] == ["/resolved/bin/claude", "plugin", "list", "--json"]
    assert run.call_args.kwargs["encoding"] == "utf-8"
    assert run.call_args.kwargs["errors"] == "replace"


def test_setup_dsh_adds_plugin_from_a_local_checkout(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.dsh as dsh_cli

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

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "dsh", "--source", str(checkout), "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "plugin": "powercontext-dsh",
        "plugin_path": str(plugin),
        "data_dir": str(tmp_path / "data"),
        "authorization_state": "not_configured",
    }
    assert run_dsh.call_args_list[0].args == (
        "plugin",
        "--profile",
        "web",
        "add",
        str(plugin),
    )


def test_setup_dsh_fails_when_dsh_cli_is_missing(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.dsh as dsh_cli

    monkeypatch.setattr(dsh_cli, "which", lambda _name: None)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "dsh", "--source", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "DeepSeek Harness CLI is not installed" in result.output


def test_doctor_dsh_reports_missing_cli_and_skipped_plugin(monkeypatch) -> None:
    import powercontext.cli.dsh as dsh_cli

    monkeypatch.setattr(dsh_cli, "which", lambda _name: None)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "dsh", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "ok": False,
        "status": "failed",
        "checks": {
            "dsh": {
                "ok": False,
                "status": "failed",
                "detail": "DeepSeek Harness CLI is not installed or is not on PATH",
            },
            "plugin": {
                "ok": False,
                "status": "skipped",
                "detail": "not checked because DeepSeek Harness CLI is unavailable",
            },
        },
    }


def test_doctor_dsh_requires_the_installed_plugin(monkeypatch) -> None:
    import powercontext.cli.dsh as dsh_cli

    monkeypatch.setattr(dsh_cli, "which", lambda _name: "/usr/bin/dsh")
    monkeypatch.setattr(dsh_cli, "_run_dsh", lambda *_args: "id: other-plugin\n")

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "dsh"])

    assert result.exit_code == 1
    assert "dsh: ok - /usr/bin/dsh" in result.output
    assert "plugin: failed - PowerContext DSH plugin is not registered in the Web profile" in result.output
