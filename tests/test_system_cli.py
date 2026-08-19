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
from email.message import Message
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest
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
    }
    assert "no changes made yet" in result.stderr
    assert str(config_dir / "settings.json") in result.stderr
    assert "read/write access" in result.stderr
    assert "claude plugin uninstall powercontext@powercontext --scope user" in result.stderr
    assert "claude plugin marketplace remove powercontext --scope user" in result.stderr
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
        "--config",
        "server_url=http://127.0.0.1:9000",
        "--config",
        "capture_prompts=false",
    )
    assert run_claude_json.call_args_list[2].args == ("plugin", "list")


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
        "--scope",
        "user",
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
    installed = [{"id": "powercontext@powercontext", "version": "0.1.0", "enabled": True}]
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

    assert [call.args[:2] for call in run_claude.call_args_list] == [("plugin", "install")]
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
    disabled = [{"id": "powercontext@powercontext", "version": "0.1.0", "enabled": False}]
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

    assert [call.args[:2] for call in run_claude_mock.call_args_list] == [("plugin", "install")]
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

    with pytest.raises(system_cli.SetupError, match="marketplace remove powercontext --scope user"):
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


def test_setup_claude_code_normalizes_an_mcp_url_before_installing(monkeypatch) -> None:
    monkeypatch.setattr(system_cli, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        system_cli,
        "_run_claude_json",
        Mock(
            side_effect=[
                [{"name": "powercontext", "source": "github", "repo": "oceanbase/powercontext", "ref": "master"}],
                [{"id": "powercontext@powercontext", "version": "0.1.0", "enabled": True}],
                [{"id": "powercontext@powercontext", "version": "0.1.0", "enabled": True}],
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

    assert "server_url=https://memory.example/api" in run_claude.call_args.args


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
    assert "plugin: failed - PowerContext DSH plugin is not installed" in result.output
