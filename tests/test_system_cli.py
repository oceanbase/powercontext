from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

from typer.testing import CliRunner

import powercontext.cli.system as system_cli
from powercontext.cli.app import create_cli
from powercontext.cli.system import Diagnostic, doctor_app, setup_app
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
    assert settings.runtime.scheduler_path == data_dir / "scheduler.db"


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
            "package": Diagnostic(ok=True, detail="powercontext 0.0.1"),
            "server": Diagnostic(ok=False, detail="cannot connect"),
        },
    )

    result = CliRunner().invoke(
        create_cli([doctor_app]),
        ["doctor", "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "ok": False,
        "checks": {
            "package": {"ok": True, "detail": "powercontext 0.0.1"},
            "server": {"ok": False, "detail": "cannot connect"},
        },
    }
