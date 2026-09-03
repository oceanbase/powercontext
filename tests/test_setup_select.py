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
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

import powercontext.cli.dsh as dsh_cli
import powercontext.cli.hermes as hermes_cli
import powercontext.cli.hosts as hosts_cli
import powercontext.cli.openclaw as openclaw_cli
import powercontext.cli.opencode as opencode_cli
import powercontext.cli.pi as pi_cli
import powercontext.cli.system as system_cli
from powercontext.cli.app import create_cli
from powercontext.cli.hosts import parse_host_selection
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError, setup_app

FIRST_CLASS_HOSTS = ("codex", "claude-code", "dsh", "openclaw", "opencode", "pi", "hermes")


def _cli():
    return create_cli([setup_app])


def _invoke(arguments: list[str], *, stdin_text: str | None = None):
    return CliRunner().invoke(_cli(), arguments, input=stdin_text)


def _patch_installers(monkeypatch, **replacements: Mock) -> dict[str, Mock]:
    _patch_diagnostics(monkeypatch)
    installers = {
        "codex": Mock(name="install_codex_plugin", return_value=object()),
        "claude-code": Mock(name="install_claude_code_plugin", return_value=object()),
        "dsh": Mock(name="install_dsh_plugin", return_value=object()),
        "openclaw": Mock(name="install_openclaw_plugin", return_value=object()),
        "opencode": Mock(name="install_opencode_plugin", return_value=object()),
        "pi": Mock(name="install_pi_plugin", return_value=object()),
        "hermes": Mock(name="install_hermes_plugin", return_value=object()),
    }
    installers.update(replacements)
    monkeypatch.setattr(system_cli, "install_codex_plugin", installers["codex"])
    monkeypatch.setattr(system_cli, "install_claude_code_plugin", installers["claude-code"])
    monkeypatch.setattr(dsh_cli, "install_dsh_plugin", installers["dsh"])
    monkeypatch.setattr(openclaw_cli, "install_openclaw_plugin", installers["openclaw"])
    monkeypatch.setattr(opencode_cli, "install_opencode_plugin", installers["opencode"])
    monkeypatch.setattr(pi_cli, "install_pi_plugin", installers["pi"])
    monkeypatch.setattr(hermes_cli, "install_hermes_plugin", installers["hermes"])
    return installers


def _patch_diagnostics(monkeypatch, **replacements: Mock) -> dict[str, Mock]:
    diagnostics = {
        "codex": Mock(return_value={"plugin": Diagnostic(DiagnosticStatus.OK, "installed")}),
        "dsh": Mock(return_value={"plugin": Diagnostic(DiagnosticStatus.OK, "installed")}),
        "opencode": Mock(return_value={"plugin": Diagnostic(DiagnosticStatus.OK, "installed")}),
        "pi": Mock(return_value={"package": Diagnostic(DiagnosticStatus.OK, "installed")}),
        "hermes": Mock(return_value={"plugin": Diagnostic(DiagnosticStatus.OK, "installed")}),
    }
    diagnostics.update(replacements)
    monkeypatch.setattr(system_cli, "run_codex_diagnostics", diagnostics["codex"])
    monkeypatch.setattr(dsh_cli, "run_dsh_diagnostics", diagnostics["dsh"])
    monkeypatch.setattr(opencode_cli, "run_opencode_diagnostics", diagnostics["opencode"])
    monkeypatch.setattr(pi_cli, "run_pi_diagnostics", diagnostics["pi"])
    monkeypatch.setattr(hermes_cli, "run_hermes_diagnostics", diagnostics["hermes"])
    return diagnostics


def _assert_not_called(*installers: Mock) -> None:
    for installer in installers:
        installer.assert_not_called()


def test_setup_without_subcommand_prints_help_and_installs_nothing(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)

    result = _invoke(["setup"])

    assert result.exit_code == 2
    assert "Usage" in result.output
    assert "Install and configure PowerContext integrations." in result.output
    _assert_not_called(*installers.values())


def test_setup_select_json_without_host_does_not_install(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)

    result = _invoke(["setup", "select", "--json"])

    assert result.exit_code == 1
    assert "--host" in result.output
    _assert_not_called(*installers.values())


def test_setup_select_without_host_requires_host_on_a_non_tty(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)
    monkeypatch.setattr(hosts_cli, "stdin_is_tty", lambda: False)

    result = _invoke(["setup", "select"])

    assert result.exit_code == 1
    assert "--host" in result.output
    _assert_not_called(*installers.values())


def test_setup_select_rejects_an_unknown_host_before_installing(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)

    result = _invoke(["setup", "select", "--host", "unknown"])

    assert result.exit_code == 1
    assert "unknown host: unknown" in result.output
    assert "codex" in result.output
    _assert_not_called(*installers.values())


def test_setup_select_installs_only_the_requested_hosts(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)

    result = _invoke(["setup", "select", "--host", "codex", "--host", "dsh", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "hosts": [
            {"host": "codex", "status": "installed"},
            {"host": "claude-code", "status": "skipped"},
            {"host": "dsh", "status": "installed"},
            {"host": "openclaw", "status": "skipped"},
            {"host": "opencode", "status": "skipped"},
            {"host": "pi", "status": "skipped"},
            {"host": "hermes", "status": "skipped"},
        ]
    }
    installers["codex"].assert_called_once()
    installers["dsh"].assert_called_once()
    _assert_not_called(
        installers["claude-code"],
        installers["openclaw"],
        installers["opencode"],
        installers["pi"],
        installers["hermes"],
    )


def test_setup_select_continues_after_a_selected_host_fails(monkeypatch) -> None:
    installers = _patch_installers(
        monkeypatch,
        dsh=Mock(side_effect=SetupError.dsh_unavailable()),
    )

    result = _invoke(["setup", "select", "--host", "dsh", "--host", "hermes", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "hosts": [
            {"host": "codex", "status": "skipped"},
            {"host": "claude-code", "status": "skipped"},
            {
                "host": "dsh",
                "status": "failed",
                "error": "DeepSeek Harness CLI is not installed or is not on PATH.",
            },
            {"host": "openclaw", "status": "skipped"},
            {"host": "opencode", "status": "skipped"},
            {"host": "pi", "status": "skipped"},
            {"host": "hermes", "status": "installed"},
        ]
    }
    installers["dsh"].assert_called_once()
    installers["hermes"].assert_called_once()


def test_setup_select_reports_an_unavailable_selected_host_and_continues(monkeypatch) -> None:
    installers = _patch_installers(
        monkeypatch,
        codex=Mock(side_effect=SetupError.codex_unavailable()),
    )

    result = _invoke(["setup", "select", "--host", "codex", "--host", "pi"])

    assert result.exit_code == 1
    assert "codex: failed - Codex CLI is not installed or is not on PATH." in result.output
    assert "pi: installed" in result.output
    installers["codex"].assert_called_once()
    installers["pi"].assert_called_once()


def test_setup_select_reports_a_successful_rerun_as_installed(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)

    first = _invoke(["setup", "select", "--host", "codex", "--json"])
    second = _invoke(["setup", "select", "--host", "codex", "--json"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert json.loads(second.output)["hosts"][0] == {"host": "codex", "status": "installed"}
    assert installers["codex"].call_count == 2


def test_setup_select_reads_a_tty_selection_by_number(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)
    monkeypatch.setattr(hosts_cli, "stdin_is_tty", lambda: True)

    result = _invoke(["setup", "select"], stdin_text="1,3\n")

    assert result.exit_code == 0
    assert "codex: installed" in result.output
    assert "dsh: installed" in result.output
    assert "claude-code: skipped" in result.output
    installers["codex"].assert_called_once()
    installers["dsh"].assert_called_once()
    _assert_not_called(
        installers["claude-code"],
        installers["openclaw"],
        installers["opencode"],
        installers["pi"],
        installers["hermes"],
    )


def test_setup_select_cancels_an_empty_tty_selection(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)
    monkeypatch.setattr(hosts_cli, "stdin_is_tty", lambda: True)

    result = _invoke(["setup", "select"], stdin_text="\n")

    assert result.exit_code == 0
    _assert_not_called(*installers.values())


def test_setup_select_rejects_an_invalid_tty_token_without_installing(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)
    monkeypatch.setattr(hosts_cli, "stdin_is_tty", lambda: True)

    result = _invoke(["setup", "select"], stdin_text="nope\n")

    assert result.exit_code == 1
    assert "unknown host: nope" in result.output
    _assert_not_called(*installers.values())


def test_setup_select_deduplicates_repeated_host_flags(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)

    result = _invoke(["setup", "select", "--host", "codex", "--host", "codex", "--json"])

    assert result.exit_code == 0
    installers["codex"].assert_called_once()


def test_setup_select_json_writes_the_matrix_without_a_prompt(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)
    monkeypatch.setattr(hosts_cli, "stdin_is_tty", lambda: True)

    result = _invoke(["setup", "select", "--host", "codex", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["hosts"][0] == {"host": "codex", "status": "installed"}
    assert "Select hosts" not in result.output
    assert "Next:" not in result.output
    installers["codex"].assert_called_once()


def test_setup_select_passes_source_ref_and_host_specific_defaults(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)

    result = _invoke([
        "setup",
        "select",
        "--host",
        "codex",
        "--host",
        "claude-code",
        "--host",
        "openclaw",
        "--host",
        "opencode",
        "--source",
        "oceanbase/powercontext",
        "--ref",
        "tested-ref",
    ])

    assert result.exit_code == 0
    installers["codex"].assert_called_once_with(source="oceanbase/powercontext", ref="tested-ref")
    installers["claude-code"].assert_called_once_with(
        source="oceanbase/powercontext",
        ref="tested-ref",
        server_url="http://127.0.0.1:8000",
        capture_prompts=True,
    )
    installers["openclaw"].assert_called_once_with(
        source="oceanbase/powercontext",
        ref="tested-ref",
        server_url="http://127.0.0.1:8000",
    )
    installers["opencode"].assert_called_once_with(source="oceanbase/powercontext", ref="tested-ref")


def test_setup_select_passes_server_override_to_openclaw(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)

    result = _invoke([
        "setup",
        "select",
        "--host",
        "openclaw",
        "--server-url",
        "https://memory.example",
    ])

    assert result.exit_code == 0
    installers["openclaw"].assert_called_once_with(
        source="oceanbase/powercontext",
        ref="master",
        server_url="https://memory.example",
    )


@pytest.mark.parametrize(
    ("host", "module", "attribute"),
    [
        ("codex", system_cli, "run_codex_diagnostics"),
        ("dsh", dsh_cli, "run_dsh_diagnostics"),
        ("opencode", opencode_cli, "run_opencode_diagnostics"),
        ("pi", pi_cli, "run_pi_diagnostics"),
        ("hermes", hermes_cli, "run_hermes_diagnostics"),
    ],
)
def test_setup_select_fails_a_row_when_post_install_verification_fails(
    monkeypatch,
    host: str,
    module: object,
    attribute: str,
) -> None:
    installers = _patch_installers(monkeypatch)
    verification = Mock(
        return_value={"plugin": Diagnostic(DiagnosticStatus.FAILED, "PowerContext plugin is not loaded")}
    )
    monkeypatch.setattr(module, attribute, verification)

    result = _invoke(["setup", "select", "--host", host, "--json"])

    assert result.exit_code == 1
    row = next(row for row in json.loads(result.output)["hosts"] if row["host"] == host)
    assert row == {
        "host": host,
        "status": "failed",
        "error": "post-install verification failed: plugin: PowerContext plugin is not loaded",
    }
    installers[host].assert_called_once()
    verification.assert_called_once_with()


def test_setup_select_continues_after_post_install_verification_fails(monkeypatch) -> None:
    installers = _patch_installers(monkeypatch)
    monkeypatch.setattr(
        system_cli,
        "run_codex_diagnostics",
        Mock(return_value={"plugin": Diagnostic(DiagnosticStatus.FAILED, "plugin is not loaded")}),
    )

    result = _invoke(["setup", "select", "--host", "codex", "--host", "openclaw", "--json"])

    assert result.exit_code == 1
    rows = {row["host"]: row for row in json.loads(result.output)["hosts"]}
    assert rows["codex"]["status"] == "failed"
    assert rows["openclaw"] == {"host": "openclaw", "status": "installed"}
    installers["openclaw"].assert_called_once()


def test_setup_select_prints_hermes_specific_next_step_only_when_installed(monkeypatch) -> None:
    _patch_installers(monkeypatch)

    installed = _invoke(["setup", "select", "--host", "hermes"])
    skipped = _invoke(["setup", "select", "--host", "openclaw"])

    assert installed.exit_code == 0
    assert "`hermes memory setup`" in installed.output
    assert "`hermes memory setup`" not in skipped.output


def test_setup_dsh_still_fails_closed_when_the_cli_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(dsh_cli, "which", lambda _name: None)

    result = _invoke(["setup", "dsh"])

    assert result.exit_code == 1
    assert "DeepSeek Harness CLI is not installed" in result.output


def test_parse_host_selection_accepts_names_and_reorders_to_the_catalog() -> None:
    assert parse_host_selection("dsh,codex") == ("codex", "dsh")
    assert parse_host_selection("4") == ("openclaw",)
    assert parse_host_selection("5") == ("opencode",)
    assert parse_host_selection("7") == ("hermes",)
    assert parse_host_selection("") is None
    assert parse_host_selection("  ") is None
