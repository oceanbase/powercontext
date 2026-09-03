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

import pytest
from typer.testing import CliRunner

import powercontext.cli.openclaw as openclaw_cli
import powercontext.cli.system as system_cli
from powercontext.cli.app import create_cli
from powercontext.cli.system import DiagnosticStatus, OpenClawSetupResult, SetupError, doctor_app, setup_app


def _write_openclaw_plugin(root: Path) -> Path:
    plugin = root / "integrations" / "openclaw" / "plugins" / "memory-powercontext"
    plugin.mkdir(parents=True)
    (plugin / "package.json").write_text(
        json.dumps({"name": openclaw_cli.OPENCLAW_PACKAGE_NAME}),
        encoding="utf-8",
    )
    return plugin


def test_resolve_openclaw_plugin_dir_accepts_checkout_root(tmp_path: Path) -> None:
    checkout = tmp_path / "powercontext"
    plugin = _write_openclaw_plugin(checkout)

    assert openclaw_cli.resolve_openclaw_plugin_dir(source=str(checkout), ref="master") == plugin


def test_checkout_target_rejects_path_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    with pytest.raises(system_cli.SetupError, match="invalid OpenClaw ref"):
        openclaw_cli.checkout_target("../../outside")


def test_configure_openclaw_preserves_existing_tools_and_adds_missing_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    run_process = Mock(
        return_value=CompletedProcess(
            ["openclaw", "config", "get"],
            0,
            '["custom_tool", "powercontext_memory_get"]',
            "",
        )
    )
    run_openclaw = Mock()
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)
    monkeypatch.setattr(openclaw_cli, "run_openclaw", run_openclaw)

    openclaw_cli.configure_openclaw(
        executable="openclaw",
        server_url="http://127.0.0.1:8765",
        scope_mode="agent",
    )

    allowlist_call = run_openclaw.call_args_list[1]
    assert allowlist_call.args[:3] == ("openclaw", "config", "set")
    assert json.loads(allowlist_call.args[4]) == [
        "custom_tool",
        "powercontext_memory_get",
        "powercontext_memory_search",
        "powercontext_memory_store",
        "powercontext_memory_revise",
        "powercontext_memory_retire",
    ]
    assert run_openclaw.call_args_list[-1].args == ("openclaw", "gateway", "restart")


def test_configure_openclaw_initializes_local_gateway_when_mode_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def read_config(command: list[str], *, timeout: int, check: bool = True) -> CompletedProcess[str]:
        del timeout, check
        if command[1:4] == ["config", "get", "gateway.mode"]:
            return CompletedProcess(command, 1, "", "Config path not found")
        if command[1:4] == ["config", "get", "tools.alsoAllow"]:
            return CompletedProcess(command, 0, "[]", "")
        raise AssertionError(command)

    run_openclaw = Mock()
    monkeypatch.setattr(openclaw_cli, "run_process", read_config)
    monkeypatch.setattr(openclaw_cli, "run_openclaw", run_openclaw)

    openclaw_cli.configure_openclaw(
        executable="openclaw",
        server_url="http://127.0.0.1:8765",
        scope_mode="agent",
    )

    settings = json.loads(run_openclaw.call_args_list[0].args[4])
    assert settings[0] == {"path": "gateway.mode", "value": "local"}


def test_install_openclaw_plugin_builds_installs_and_configures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _write_openclaw_plugin(tmp_path / "checkout")
    run_openclaw = Mock()
    build = Mock()
    configure = Mock()
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "require_supported_openclaw", Mock())
    monkeypatch.setattr(openclaw_cli, "resolve_openclaw_plugin_dir", lambda **_kwargs: plugin)
    monkeypatch.setattr(openclaw_cli, "build_openclaw_plugin", build)
    monkeypatch.setattr(openclaw_cli, "run_openclaw", run_openclaw)
    monkeypatch.setattr(openclaw_cli, "configure_openclaw", configure)
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    result = openclaw_cli.install_openclaw_plugin(
        source="oceanbase/powercontext",
        ref="tested-ref",
        server_url="http://127.0.0.1:8765/",
        scope_mode="project",
    )

    assert result == OpenClawSetupResult(
        plugin="memory-powercontext",
        plugin_path=str(plugin),
        server_url="http://127.0.0.1:8765",
        scope_mode="project",
        data_dir=str(tmp_path / "data"),
    )
    build.assert_called_once_with(plugin)
    run_openclaw.assert_called_once_with("/usr/bin/openclaw", "plugins", "install", "--link", "--force", str(plugin))
    configure.assert_called_once_with(
        executable="/usr/bin/openclaw",
        server_url="http://127.0.0.1:8765",
        scope_mode="project",
    )


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
    )
    fake_pnpm.chmod(0o755)
    monkeypatch.setattr(openclaw_cli, "pnpm_executable", lambda: str(fake_pnpm))
    monkeypatch.setenv("CI", "false")

    openclaw_cli.build_openclaw_plugin(plugin)

    assert (plugin / "dist" / "index.js").is_file()


def test_setup_openclaw_exposes_source_ref_and_runtime_options(monkeypatch: pytest.MonkeyPatch) -> None:
    import powercontext.cli.openclaw as openclaw_module

    install = Mock(
        return_value=OpenClawSetupResult(
            plugin="memory-powercontext",
            plugin_path="plugin-path",
            server_url="http://127.0.0.1:8765",
            scope_mode="agent",
            data_dir="data-dir",
        )
    )
    monkeypatch.setattr(openclaw_module, "install_openclaw_plugin", install)

    result = CliRunner().invoke(
        create_cli([setup_app]),
        [
            "setup",
            "openclaw",
            "--source",
            "oceanbase/powercontext",
            "--ref",
            "tested-ref",
            "--server-url",
            "http://127.0.0.1:8765",
            "--scope-mode",
            "agent",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["plugin"] == "memory-powercontext"
    install.assert_called_once_with(
        source="oceanbase/powercontext",
        ref="tested-ref",
        server_url="http://127.0.0.1:8765",
        scope_mode="agent",
    )


def test_setup_openclaw_defaults_to_the_server_default_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    import powercontext.cli.openclaw as openclaw_module

    def install(**kwargs: str) -> OpenClawSetupResult:
        return OpenClawSetupResult(
            plugin="memory-powercontext",
            plugin_path="plugin-path",
            server_url=kwargs["server_url"],
            scope_mode=kwargs["scope_mode"],
            data_dir="data-dir",
        )

    monkeypatch.setattr(openclaw_module, "install_openclaw_plugin", install)

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "openclaw", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["server_url"] == "http://127.0.0.1:8000"


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

    diagnostics = openclaw_cli.run_openclaw_diagnostics()

    assert diagnostics["openclaw"].status is DiagnosticStatus.OK
    assert diagnostics["plugin"].status is DiagnosticStatus.OK
    assert diagnostics["plugin"].detail == "memory-powercontext is installed and active"


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
        raise AssertionError(command)

    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    diagnostics = openclaw_cli.run_openclaw_diagnostics()

    assert diagnostics["plugin"].status is DiagnosticStatus.OK


def test_run_openclaw_diagnostics_reports_missing_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    run_process = Mock(
        return_value=CompletedProcess(
            _OPENCLAW_PLUGIN_LIST_COMMAND, 0, _openclaw_plugin_list_output(plugin_id=None), ""
        )
    )
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    diagnostics = openclaw_cli.run_openclaw_diagnostics()

    assert diagnostics["openclaw"].status is DiagnosticStatus.OK
    assert diagnostics["plugin"].status is DiagnosticStatus.FAILED
    assert (
        diagnostics["plugin"].detail
        == "PowerContext OpenClaw plugin is not enabled, loaded, and selected as the memory plugin"
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

    diagnostics = openclaw_cli.run_openclaw_diagnostics()

    assert diagnostics["openclaw"].status is DiagnosticStatus.OK
    assert diagnostics["plugin"].status is DiagnosticStatus.FAILED


def test_run_openclaw_diagnostics_reports_missing_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_executable() -> str:
        raise SetupError.openclaw_unavailable()

    monkeypatch.setattr(openclaw_cli, "openclaw_executable", missing_executable)

    diagnostics = openclaw_cli.run_openclaw_diagnostics()

    assert diagnostics["openclaw"].status is DiagnosticStatus.FAILED
    assert diagnostics["plugin"].status is DiagnosticStatus.SKIPPED


def test_doctor_openclaw_reports_an_installed_plugin_as_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_process = Mock(
        return_value=CompletedProcess(_OPENCLAW_PLUGIN_LIST_COMMAND, 0, _openclaw_plugin_list_output(), "")
    )
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "openclaw", "--json"])

    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["checks"]["openclaw"]["ok"] is True
    assert report["checks"]["plugin"]["ok"] is True


def test_doctor_openclaw_exits_nonzero_when_plugin_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    run_process = Mock(
        return_value=CompletedProcess(
            _OPENCLAW_PLUGIN_LIST_COMMAND, 0, _openclaw_plugin_list_output(plugin_id=None), ""
        )
    )
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "openclaw"])

    assert result.exit_code == 1
