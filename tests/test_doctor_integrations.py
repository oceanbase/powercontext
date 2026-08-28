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

from typer.testing import CliRunner

import powercontext.cli.dsh as dsh_cli
import powercontext.cli.hermes as hermes_cli
import powercontext.cli.openclaw as openclaw_cli
import powercontext.cli.opencode as opencode_cli
import powercontext.cli.pi as pi_cli
import powercontext.cli.system as system_cli
from powercontext.cli.app import create_cli
from powercontext.cli.system import Diagnostic, DiagnosticStatus, doctor_app

FIRST_CLASS_HOSTS = ("codex", "claude-code", "dsh", "openclaw", "opencode", "pi", "hermes")
CLI_KEYS = {
    "codex": "codex",
    "claude-code": "claude_code",
    "dsh": "dsh",
    "openclaw": "openclaw",
    "opencode": "opencode",
    "pi": "pi",
    "hermes": "hermes",
}
INTEGRATION_KEYS = {
    "codex": ("plugin",),
    "claude-code": ("plugin",),
    "dsh": ("plugin",),
    "openclaw": ("plugin",),
    "opencode": ("plugin", "skill"),
    "pi": ("package",),
    "hermes": ("plugin",),
}
PATH_MISSING = {
    "codex": "Codex CLI is not installed or is not on PATH",
    "claude-code": "Claude Code CLI is not installed or is not on PATH",
    "dsh": "DeepSeek Harness CLI is not installed or is not on PATH",
    "openclaw": "OpenClaw CLI is not installed or is not on PATH",
    "opencode": "OpenCode CLI is not installed or is not on PATH",
    "pi": "Pi CLI is not installed or is not on PATH",
    "hermes": "Hermes CLI is not installed or is not on PATH",
}


def _cli():
    return create_cli([doctor_app])


def _invoke(arguments: list[str]):
    return CliRunner().invoke(_cli(), arguments)


def _missing(host: str) -> dict[str, Diagnostic]:
    diagnostics = {
        CLI_KEYS[host]: Diagnostic(status=DiagnosticStatus.FAILED, detail=PATH_MISSING[host]),
    }
    diagnostics.update({
        key: Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because the host CLI is unavailable",
        )
        for key in INTEGRATION_KEYS[host]
    })
    return diagnostics


def _ok_codex() -> dict[str, Diagnostic]:
    return {
        "codex": Diagnostic(status=DiagnosticStatus.OK, detail="/usr/bin/codex"),
        "plugin": Diagnostic(status=DiagnosticStatus.OK, detail="powercontext@powercontext enabled=True"),
    }


def _failed_plugin_codex() -> dict[str, Diagnostic]:
    return {
        "codex": Diagnostic(status=DiagnosticStatus.OK, detail="/usr/bin/codex"),
        "plugin": Diagnostic(status=DiagnosticStatus.FAILED, detail="PowerContext plugin is not installed"),
    }


def _list_failed_codex() -> dict[str, Diagnostic]:
    return {
        "codex": Diagnostic(status=DiagnosticStatus.FAILED, detail="codex plugin list failed: timeout"),
        "plugin": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="plugin list is unavailable"),
    }


def _failed_skill_opencode() -> dict[str, Diagnostic]:
    return {
        "opencode": Diagnostic(status=DiagnosticStatus.OK, detail="/usr/bin/opencode (1.18.21)"),
        "plugin": Diagnostic(status=DiagnosticStatus.OK, detail="powercontext is configured and active"),
        "skill": Diagnostic(status=DiagnosticStatus.FAILED, detail="PowerContext OpenCode Skill is not installed"),
    }


def _patch_diagnostics(monkeypatch, **replacements: Mock) -> dict[str, Mock]:
    probes = {host: Mock(name=f"run_{host}_diagnostics", return_value=_missing(host)) for host in FIRST_CLASS_HOSTS}
    probes.update(replacements)
    monkeypatch.setattr(system_cli, "run_codex_diagnostics", probes["codex"])
    monkeypatch.setattr(system_cli, "run_claude_code_diagnostics", probes["claude-code"])
    monkeypatch.setattr(dsh_cli, "run_dsh_diagnostics", probes["dsh"])
    monkeypatch.setattr(openclaw_cli, "run_openclaw_diagnostics", probes["openclaw"])
    monkeypatch.setattr(opencode_cli, "run_opencode_diagnostics", probes["opencode"])
    monkeypatch.setattr(pi_cli, "run_pi_diagnostics", probes["pi"])
    monkeypatch.setattr(hermes_cli, "run_hermes_diagnostics", probes["hermes"])
    return probes


def test_doctor_integrations_json_includes_every_first_class_host(monkeypatch) -> None:
    _patch_diagnostics(monkeypatch)

    result = _invoke(["doctor", "integrations", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert list(payload["hosts"]) == list(FIRST_CLASS_HOSTS)
    for host in FIRST_CLASS_HOSTS:
        assert payload["hosts"][host]["presence"] == "missing"
        assert CLI_KEYS[host] in payload["hosts"][host]
        for integration_key in INTEGRATION_KEYS[host]:
            assert integration_key in payload["hosts"][host]


def test_doctor_integrations_treats_missing_clis_as_success(monkeypatch) -> None:
    _patch_diagnostics(monkeypatch, codex=Mock(return_value=_ok_codex()))

    result = _invoke(["doctor", "integrations", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert payload["hosts"]["codex"]["presence"] == "present"
    assert payload["hosts"]["codex"]["plugin"]["ok"] is True
    assert payload["hosts"]["pi"]["presence"] == "missing"
    assert payload["hosts"]["pi"]["package"]["status"] == "skipped"


def test_doctor_integrations_prints_a_human_matrix(monkeypatch) -> None:
    _patch_diagnostics(monkeypatch, codex=Mock(return_value=_ok_codex()))

    result = _invoke(["doctor", "integrations"])

    assert result.exit_code == 0
    assert "codex: present - cli=ok plugin=ok" in result.output
    assert "claude-code: missing - cli=failed plugin=skipped" in result.output
    assert "opencode: missing - cli=failed plugin=skipped skill=skipped" in result.output
    assert "pi: missing - cli=failed package=skipped" in result.output


def test_doctor_integrations_fails_when_a_present_plugin_is_broken(monkeypatch) -> None:
    _patch_diagnostics(monkeypatch, codex=Mock(return_value=_failed_plugin_codex()))

    result = _invoke(["doctor", "integrations", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert payload["hosts"]["codex"]["presence"] == "present"
    assert payload["hosts"]["codex"]["plugin"]["status"] == "failed"
    assert payload["hosts"]["dsh"]["presence"] == "missing"


def test_doctor_integrations_fails_when_a_present_cli_cannot_list_plugins(monkeypatch) -> None:
    _patch_diagnostics(monkeypatch, codex=Mock(return_value=_list_failed_codex()))

    result = _invoke(["doctor", "integrations", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["hosts"]["codex"]["presence"] == "present"
    assert payload["hosts"]["codex"]["codex"]["status"] == "failed"
    assert payload["hosts"]["codex"]["plugin"]["status"] == "skipped"
    assert "is not installed or is not on PATH" not in payload["hosts"]["codex"]["codex"]["detail"]


def test_doctor_integrations_fails_when_a_present_opencode_skill_is_broken(monkeypatch) -> None:
    _patch_diagnostics(monkeypatch, opencode=Mock(return_value=_failed_skill_opencode()))

    result = _invoke(["doctor", "integrations", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["hosts"]["opencode"]["presence"] == "present"
    assert payload["hosts"]["opencode"]["plugin"]["status"] == "ok"
    assert payload["hosts"]["opencode"]["skill"]["status"] == "failed"


def test_doctor_integrations_succeeds_when_every_host_is_missing(monkeypatch) -> None:
    _patch_diagnostics(monkeypatch)

    result = _invoke(["doctor", "integrations"])

    assert result.exit_code == 0
    assert "codex: missing - cli=failed plugin=skipped" in result.output
    assert "hermes: missing - cli=failed plugin=skipped" in result.output


def test_default_doctor_does_not_scan_first_class_hosts(monkeypatch) -> None:
    probes = _patch_diagnostics(
        monkeypatch,
        **{
            host: Mock(side_effect=AssertionError(f"default doctor must not inspect {host}"))
            for host in FIRST_CLASS_HOSTS
        },
    )
    monkeypatch.setattr(
        system_cli,
        "run_diagnostics",
        lambda **_kwargs: {
            "package": Diagnostic(status=DiagnosticStatus.OK, detail="powercontext 0.0.1"),
            "server_liveness": Diagnostic(status=DiagnosticStatus.OK, detail="http://127.0.0.1:8000 status=ok"),
            "server_readiness": Diagnostic(status=DiagnosticStatus.OK, detail="http://127.0.0.1:8000 status=ready"),
        },
    )

    result = _invoke(["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert list(payload["checks"]) == ["package", "server_liveness", "server_readiness"]
    for probe in probes.values():
        probe.assert_not_called()


def test_doctor_codex_still_fails_when_the_cli_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: None)

    result = _invoke(["doctor", "codex"])

    assert result.exit_code == 1
    assert "Codex CLI is not installed or is not on PATH" in result.output
