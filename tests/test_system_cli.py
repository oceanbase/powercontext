from __future__ import annotations

import json
from email.message import Message
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError

from typer.testing import CliRunner

import powercontext.cli.system as system_cli
from powercontext.cli.app import create_cli
from powercontext.cli.system import Diagnostic, DiagnosticStatus, doctor_app, setup_app
from powercontext.paths import default_scheduler_path
from powercontext.server.settings import ServerSettings


def test_server_defaults_to_persistent_user_storage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "powercontext-data"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(data_dir))

    settings = ServerSettings()

    assert settings.database.kind == "sqlite"
    assert settings.database.url == f"sqlite+aiosqlite:///{data_dir / 'powercontext.db'}"
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
    assert run_codex.call_args_list[0].args == (
        "plugin",
        "marketplace",
        "add",
        str(marketplace),
    )


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


class _Response(BytesIO):
    def __init__(self, status: int, payload: object) -> None:
        super().__init__(json.dumps(payload).encode())
        self._status = status

    def getcode(self) -> int:
        return self._status


def test_default_doctor_checks_server_without_inspecting_codex(monkeypatch) -> None:
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
    assert list(payload["checks"]) == ["package", "server_liveness", "server_readiness"]


def test_default_doctor_skips_readiness_when_liveness_is_unreachable(monkeypatch) -> None:
    urlopen = Mock(side_effect=OSError("connection refused"))
    monkeypatch.setattr(system_cli, "urlopen", urlopen)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor"])

    assert result.exit_code == 1
    assert "server liveness: failed - cannot reach http://127.0.0.1:8000" in result.output
    assert "server readiness: skipped - not checked because Server liveness failed" in result.output
    assert urlopen.call_count == 1


def test_default_doctor_preserves_not_ready_checks_in_human_and_json_output(monkeypatch) -> None:
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
                "detail": "powercontext 0.0.1",
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
