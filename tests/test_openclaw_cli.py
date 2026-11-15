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
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

import powercontext.cli.openclaw as openclaw_cli
from powercontext.cli.app import create_cli
from powercontext.cli.installation import setup as install_plugin
from powercontext.cli.system import DiagnosticStatus, SetupError, doctor_app


def _write_openclaw_plugin(root: Path) -> Path:
    plugin = root / "integrations" / "openclaw" / "plugins" / "memory-powercontext"
    plugin.mkdir(parents=True)
    (plugin / "package.json").write_text(
        json.dumps({"name": openclaw_cli.OPENCLAW_PACKAGE_NAME}),
        encoding="utf-8",
    )
    return plugin


def test_configure_openclaw_changes_only_connection_settings(monkeypatch):
    commands = []
    monkeypatch.setattr(openclaw_cli, "run_openclaw", lambda *args: commands.append(args))
    openclaw_cli.configure_openclaw(
        executable="openclaw", server_url="https://memory.example", allow_insecure_http=False
    )
    settings = [item for command in commands for item in json.loads(command[-1])]
    assert settings == [
        {"path": "plugins.entries.memory-powercontext.config.endpoint", "value": "https://memory.example"},
        {"path": "plugins.entries.memory-powercontext.config.allowInsecureHttp", "value": False},
    ]


@pytest.mark.parametrize("failure", [None, "local_write", "native_rejection"])
def test_config_agent_openclaw_keeps_connection_settings_consistent(
    tmp_path: Path, monkeypatch, failure: str | None
) -> None:
    from powercontext.cli import installation
    from powercontext.cli.config import app as config_app

    clients = tmp_path / "clients.json"
    previous = {"version": 1, "hosts": {"openclaw": {"server_url": "https://old.example"}}}
    clients.write_text(json.dumps(previous), encoding="utf-8")
    native = tmp_path / "openclaw.json"
    native.write_text(json.dumps({"endpoint": "https://old.example"}), encoding="utf-8")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(clients))
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "openclaw")

    def native_command(_executable, *arguments):
        if arguments[:2] == ("config", "set"):
            if failure == "native_rejection":
                message = "Native configuration rejected"
                raise SetupError(message)
            settings = json.loads(native.read_text(encoding="utf-8"))
            for update in json.loads(arguments[-1]):
                settings[update["path"].rsplit(".", 1)[1]] = update["value"]
            native.write_text(json.dumps(settings), encoding="utf-8")
        return CompletedProcess([], 0, "{}", "")

    monkeypatch.setattr(openclaw_cli, "run_openclaw", native_command)
    write_bytes = installation.write_bytes

    def write(path, value):
        if failure == "local_write" and path == clients:
            message = "Connection file is read-only"
            raise PermissionError(message)
        write_bytes(path, value)

    monkeypatch.setattr(installation, "write_bytes", write)
    result = CliRunner().invoke(
        create_cli([config_app]),
        ["config", "agent", "openclaw", "--server-url", "https://new.example", "--json"],
    )

    assert result.exit_code == (1 if failure else 0), result.output
    expected = "https://old.example" if failure else "https://new.example"
    assert json.loads(native.read_text(encoding="utf-8"))["endpoint"] == expected
    assert json.loads(clients.read_text(encoding="utf-8"))["hosts"]["openclaw"]["server_url"] == expected


def test_install_openclaw_plugin_builds_installs_and_configures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _write_openclaw_plugin(tmp_path / "checkout")
    managed = tmp_path / "native-managed"
    managed.mkdir()

    def native_command(*args):
        output = (
            json.dumps({"install": {"installPath": str(managed)}})
            if "inspect" in args
            else _openclaw_plugin_list_output()
        )
        return CompletedProcess([], 0, output, "")

    run_openclaw = Mock(side_effect=native_command)
    monkeypatch.setattr(
        openclaw_cli,
        "read_config_value",
        lambda _executable, path: (
            {"entries": {"memory-powercontext": {"enabled": False}}} if path == "plugins" else "memory-powercontext"
        ),
    )
    build = Mock()

    def pack(command, **kwargs):
        (Path(command[-1]) / "plugin.tgz").write_bytes(b"archive")

    monkeypatch.setattr(openclaw_cli, "run_process", pack)
    monkeypatch.setattr(openclaw_cli, "pnpm_executable", lambda: "pnpm")
    configure = Mock()
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "require_supported_openclaw", Mock())
    monkeypatch.setattr("powercontext.cli.git_source.resolve_plugin_source", lambda *_args, **_kwargs: plugin)
    monkeypatch.setattr(openclaw_cli, "build_openclaw_plugin", build)
    monkeypatch.setattr(openclaw_cli, "run_openclaw", run_openclaw)
    monkeypatch.setattr(openclaw_cli, "configure_openclaw", configure)
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    result = install_plugin("openclaw", source=str(plugin), ref="tested-ref", server_url="http://127.0.0.1:8765/")

    assert result == {
        "plugin": "memory-powercontext",
        "plugin_path": result["plugin_path"],
        "server_url": "http://127.0.0.1:8765",
        "data_dir": str(tmp_path / "data"),
    }
    assert Path(result["plugin_path"]) == managed


def test_build_openclaw_plugin_runs_pnpm_install_non_interactively(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    fake_pnpm = tmp_path / "pnpm"
    fake_pnpm.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import sys

if os.environ.get("CI") != "true":
    raise SystemExit(42)
plugin = pathlib.Path(sys.argv[sys.argv.index("--dir") + 1])
if "build" in sys.argv:
    output = plugin / "dist" / "index.js"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("export {};\\n", encoding="utf-8")
""",
        encoding="utf-8",
        newline="\n",
    )
    fake_pnpm.chmod(0o755)
    if os.name == "nt":
        launcher = tmp_path / "pnpm.cmd"
        launcher.write_text(f'@"{sys.executable}" "{fake_pnpm}" %*\n', encoding="utf-8")
        fake_pnpm = launcher
    monkeypatch.setattr(openclaw_cli, "pnpm_executable", lambda: str(fake_pnpm))
    monkeypatch.setenv("CI", "false")

    openclaw_cli.build_openclaw_plugin(plugin)

    assert (plugin / "dist" / "index.js").is_file()


_OPENCLAW_PLUGIN_LIST_COMMAND = ["openclaw", "plugins", "list", "--enabled", "--json"]


def _openclaw_plugin_list_output(
    *,
    plugin_id: str | None = "memory-powercontext",
    enabled: bool = True,
    status: str = "loaded",
    memory_slot_selected: bool | None = True,
) -> str:
    plugins = (
        []
        if plugin_id is None
        else [
            {
                "id": plugin_id,
                "enabled": enabled,
                "status": status,
                **({"memorySlotSelected": memory_slot_selected} if memory_slot_selected is not None else {}),
            }
        ]
    )
    return json.dumps({"plugins": plugins})


def test_run_openclaw_diagnostics_reports_installed_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    run_process = Mock(
        return_value=CompletedProcess(_OPENCLAW_PLUGIN_LIST_COMMAND, 0, _openclaw_plugin_list_output(), "")
    )
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    diagnostics = openclaw_cli.OpenClawInstaller().verify()

    assert diagnostics["openclaw"].status is DiagnosticStatus.OK
    assert diagnostics["plugin"].status is DiagnosticStatus.OK
    assert "installed, enabled and selected" in diagnostics["plugin"].detail
    assert diagnostics["plugin"].checks is not None
    assert diagnostics["plugin"].checks["running_host_configuration"] == "not_observed"


def test_run_openclaw_diagnostics_reads_memory_slot_when_plugin_list_omits_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _openclaw_plugin_list_output(memory_slot_selected=None)

    def run_process(command: list[str], *, timeout: int, check: bool = True) -> CompletedProcess[str]:
        del timeout, check
        if command[1:4] == ["plugins", "list", "--enabled"]:
            return CompletedProcess(command, 0, output, "")
        if command[1:4] == ["config", "get", "plugins.slots.memory"]:
            return CompletedProcess(command, 0, json.dumps("memory-powercontext"), "")
        if command[1:3] == ["plugins", "inspect"]:
            return CompletedProcess(command, 0, "{}", "")
        raise AssertionError(command)

    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    diagnostics = openclaw_cli.OpenClawInstaller().verify()

    assert diagnostics["plugin"].status is DiagnosticStatus.OK


def test_run_openclaw_diagnostics_reports_missing_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    run_process = Mock(
        return_value=CompletedProcess(
            _OPENCLAW_PLUGIN_LIST_COMMAND, 0, _openclaw_plugin_list_output(plugin_id=None), ""
        )
    )
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    diagnostics = openclaw_cli.OpenClawInstaller().verify()

    assert diagnostics["openclaw"].status is DiagnosticStatus.OK
    assert diagnostics["plugin"].status is DiagnosticStatus.FAILED
    assert (
        diagnostics["plugin"].detail == "PowerContext OpenClaw plugin is not enabled and selected as the memory plugin"
    )


@pytest.mark.parametrize(
    ("enabled", "status", "memory_slot_selected"),
    [
        (False, "disabled", False),
        (True, "error", False),
        (True, "loaded", False),
    ],
    ids=["disabled", "load-error", "wrong-memory-slot"],
)
def test_run_openclaw_diagnostics_rejects_inactive_plugin(
    enabled: bool,
    status: str,
    memory_slot_selected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _openclaw_plugin_list_output(
        enabled=enabled,
        status=status,
        memory_slot_selected=memory_slot_selected,
    )
    run_process = Mock(return_value=CompletedProcess(_OPENCLAW_PLUGIN_LIST_COMMAND, 0, output, ""))
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    diagnostics = openclaw_cli.OpenClawInstaller().verify()

    assert diagnostics["openclaw"].status is DiagnosticStatus.OK
    assert diagnostics["plugin"].status is DiagnosticStatus.FAILED


def test_run_openclaw_diagnostics_reports_missing_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_executable() -> str:
        message = "OpenClaw CLI is not installed or is not on PATH."
        raise SetupError(message)

    monkeypatch.setattr(openclaw_cli, "openclaw_executable", missing_executable)

    diagnostics = openclaw_cli.OpenClawInstaller().verify()

    assert diagnostics["openclaw"].status is DiagnosticStatus.FAILED
    assert diagnostics["plugin"].status is DiagnosticStatus.SKIPPED


def test_doctor_openclaw_reports_an_installed_plugin_as_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server_checks
) -> None:
    monkeypatch.setenv("POWERCONTEXT_OPENCLAW_BASE_URL", "http://127.0.0.1:8000")
    run_process = Mock(
        return_value=CompletedProcess(_OPENCLAW_PLUGIN_LIST_COMMAND, 0, _openclaw_plugin_list_output(), "")
    )
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "openclaw", "--json"])

    assert result.exit_code == 1
    report = json.loads(result.output)["hosts"]["openclaw"]
    assert report["checks"]["openclaw"]["ok"] is True
    assert report["checks"]["plugin"]["ok"] is True
    assert report["checks"]["plugin_memory_protocol"]["status"] == "degraded"
    assert "powercontext setup openclaw" in report["checks"]["plugin_memory_protocol"]["detail"]


def test_doctor_openclaw_exits_nonzero_when_plugin_is_missing(monkeypatch: pytest.MonkeyPatch, server_checks) -> None:
    run_process = Mock(
        return_value=CompletedProcess(
            _OPENCLAW_PLUGIN_LIST_COMMAND, 0, _openclaw_plugin_list_output(plugin_id=None), ""
        )
    )
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "openclaw"])

    assert result.exit_code == 1


@pytest.mark.parametrize(
    "plugins",
    [
        {"enabled": False},
        {"entries": {"memory-powercontext": {"enabled": False}}},
        {"deny": ["memory-powercontext"]},
        {"slots": {"memory": "other-memory-provider"}},
    ],
)
def test_openclaw_activation_preserves_existing_native_choices(monkeypatch, plugins):
    from copy import deepcopy

    before = deepcopy(plugins)
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "openclaw")
    monkeypatch.setattr(
        openclaw_cli, "read_config_value", lambda _command, path: plugins if path == "plugins" else None
    )

    def native(executable, *arguments):
        if arguments[:2] == ("plugins", "enable"):
            plugins.setdefault("entries", {}).setdefault("memory-powercontext", {})["enabled"] = True
        return CompletedProcess([], 0, "", "")

    monkeypatch.setattr(openclaw_cli, "run_openclaw", native)
    openclaw_cli.OpenClawInstaller().enable()
    assert plugins == before


def test_openclaw_rejects_installers_without_policy_preserving_installation(monkeypatch):
    def native(command, **options):
        return CompletedProcess(
            command, 0, "OpenClaw 2026.8.33" if command[-1] == "--version" else "plugins install", ""
        )

    monkeypatch.setattr(openclaw_cli, "run_process", native)
    with pytest.raises(SetupError, match="--no-enable"):
        openclaw_cli.require_supported_openclaw("openclaw")


def test_openclaw_config_read_failure_is_not_an_absent_setting(monkeypatch):
    monkeypatch.setattr(
        openclaw_cli, "run_process", lambda *args, **kwargs: CompletedProcess([], 1, "", "configuration is invalid")
    )
    with pytest.raises(SetupError, match="Cannot read OpenClaw configuration"):
        openclaw_cli.read_config_value("openclaw", "plugins")


def test_openclaw_repeat_activation_preserves_removed_native_tool_grants(monkeypatch):
    plugins = {
        "enabled": True,
        "slots": {"memory": "memory-powercontext"},
        "entries": {
            "memory-powercontext": {
                "enabled": True,
                "config": {"autoRecall": False, "autoCapture": False},
                "hooks": {"allowConversationAccess": False},
            }
        },
    }
    allowed = ["powercontext_memory_get"]
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "openclaw")
    monkeypatch.setattr(
        openclaw_cli,
        "read_config_value",
        lambda _executable, path: {"plugins": plugins, "tools.alsoAllow": allowed, "gateway.mode": "local"}[path],
    )

    def native(*_args):
        pytest.fail("Repeated setup must not rewrite the existing native activation policy")

    monkeypatch.setattr(openclaw_cli, "run_openclaw", native)
    openclaw_cli.OpenClawInstaller().enable()


def test_openclaw_recognizes_native_json_unset_without_treating_other_errors_as_absence(monkeypatch):
    output = {
        "ok": False,
        "error": {
            "type": "cli_error",
            "message": "Config path is valid but unset: gateway.mode. The runtime default applies.",
        },
    }
    monkeypatch.setattr(
        openclaw_cli, "run_process", lambda *args, **kwargs: CompletedProcess([], 1, json.dumps(output), "")
    )
    assert openclaw_cli.read_config_value("openclaw", "gateway.mode") is None
    output["error"]["message"] = "Unknown config path: gateway.mode."
    with pytest.raises(SetupError, match="Cannot read OpenClaw configuration"):
        openclaw_cli.read_config_value("openclaw", "gateway.mode")
