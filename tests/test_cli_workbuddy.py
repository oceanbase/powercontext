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
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import powercontext.cli.workbuddy as workbuddy_cli
from powercontext.cli.app import create_cli
from powercontext.cli.system import doctor_app, setup_app

_HOOK_MODULES = (
    "workbuddy_powercontext_hook.py",
    "workbuddy_settings.py",
    "prepared_context.py",
)


def _write_plugin(root: Path) -> Path:
    plugin = root / "integrations" / "workbuddy" / "plugins" / "powercontext"
    hooks = plugin / "hooks"
    hooks.mkdir(parents=True)
    for name in _HOOK_MODULES:
        (hooks / name).write_text(f"# {name}\n", encoding="utf-8")
    scripts = plugin / "scripts"
    scripts.mkdir()
    (scripts / "__init__.py").write_text('"""PowerContext helper scripts."""\n', encoding="utf-8")
    (scripts / "project_scope.py").write_text("def resolve_scope_id() -> str:\n    return 'scope'\n", encoding="utf-8")
    cache = scripts / "__pycache__"
    cache.mkdir()
    (cache / "project_scope.cpython-312.pyc").write_bytes(b"\x00")
    skill = plugin / "skills" / "project-context"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "${POWERCONTEXT_PYTHON} ${WORKBUDDY_HOOKS_DIR}/scripts/project_scope.py\n",
        encoding="utf-8",
    )
    return plugin


def _powercontext_hook(settings: dict[str, Any]) -> dict[str, Any]:
    matchers = settings["hooks"]["UserPromptSubmit"]
    for matcher in matchers:
        for entry in matcher["hooks"]:
            if "workbuddy_powercontext_hook.py" in str(entry.get("command", "")):
                return entry
    raise AssertionError


def _expected_hook_command(hooks_dir: Path) -> str:
    executable = Path(sys.executable).as_posix()
    script = (hooks_dir / "workbuddy_powercontext_hook.py").as_posix()
    if os.name == "nt":
        return subprocess.list2cmdline([executable, script])
    return f"{shlex.quote(executable)} {shlex.quote(script)}"


def test_setup_workbuddy_installs_from_a_local_checkout(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy home"
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "workbuddy", "--source", str(checkout), "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "plugin": "powercontext",
        "plugin_path": str(checkout / "integrations" / "workbuddy" / "plugins" / "powercontext"),
        "workbuddy_home": str(home),
        "hooks_dir": str(home / "hooks"),
        "data_dir": str(tmp_path / "data"),
    }

    hooks_dir = home / "hooks"
    for name in _HOOK_MODULES:
        assert (hooks_dir / name).is_file()
    assert (hooks_dir / "scripts" / "__init__.py").is_file()
    assert (hooks_dir / "scripts" / "project_scope.py").is_file()
    assert not (hooks_dir / "scripts" / "__pycache__").exists()

    skill_markdown = home / "skills" / "project-context" / "SKILL.md"
    assert skill_markdown.is_file()
    assert "${WORKBUDDY_HOOKS_DIR}" not in skill_markdown.read_text(encoding="utf-8")
    assert "${POWERCONTEXT_PYTHON}" not in skill_markdown.read_text(encoding="utf-8")
    assert hooks_dir.as_posix() in skill_markdown.read_text(encoding="utf-8")
    assert Path(sys.executable).as_posix() in skill_markdown.read_text(encoding="utf-8")

    settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    hook = _powercontext_hook(settings)
    assert hook["command"] == _expected_hook_command(hooks_dir)
    assert hook["timeout"] == 10
    assert hook["statusMessage"] == "Syncing PowerContext"

    mcp = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["powercontext"]["type"] == "http"
    assert mcp["mcpServers"]["powercontext"]["url"] == (
        "${POWERCONTEXT_WORKBUDDY_SERVER_URL:-http://127.0.0.1:8000}/mcp"
    )
    assert mcp["mcpServers"]["powercontext"]["headers"] == {
        "Authorization": "${POWERCONTEXT_WORKBUDDY_AUTHORIZATION:-}"
    }
    assert mcp["mcpServers"]["powercontext"]["disabled"] is False


def test_setup_workbuddy_preserves_existing_settings_and_mcp(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy"
    home.mkdir()
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    previous_settings = {
        "enabledPlugins": {"other-plugin": True},
        "sandbox": {"enabled": True},
        "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo hello", "timeout": 5}]}]},
    }
    (home / "settings.json").write_text(json.dumps(previous_settings), encoding="utf-8")
    previous_mcp = {
        "mcpServers": {
            "other-server": {"type": "stdio", "command": "other", "args": []},
        }
    }
    (home / "mcp.json").write_text(json.dumps(previous_mcp), encoding="utf-8")

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "workbuddy", "--source", str(checkout)],
    )

    assert result.exit_code == 0

    settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert settings["enabledPlugins"] == {"other-plugin": True}
    assert settings["sandbox"] == {"enabled": True}
    matchers = settings["hooks"]["UserPromptSubmit"]
    assert {"type": "command", "command": "echo hello", "timeout": 5} in matchers[0]["hooks"]
    assert _powercontext_hook(settings)["command"] == _expected_hook_command(home / "hooks")

    mcp = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["other-server"] == {"type": "stdio", "command": "other", "args": []}
    assert mcp["mcpServers"]["powercontext"]["url"] == (
        "${POWERCONTEXT_WORKBUDDY_SERVER_URL:-http://127.0.0.1:8000}/mcp"
    )


def test_setup_workbuddy_preserves_remote_authenticated_mcp_configuration(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy"
    home.mkdir()
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    (home / "mcp.json").write_text(
        json.dumps({
            "mcpServers": {
                "powercontext": {
                    "type": "http",
                    "url": "https://memory.example.test/mcp",
                    "headers": {"Authorization": "Bearer existing-token"},
                    "disabled": True,
                }
            }
        }),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "workbuddy", "--source", str(checkout)],
    )

    assert result.exit_code == 0
    mcp = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["powercontext"]["url"] == "https://memory.example.test/mcp"
    assert mcp["mcpServers"]["powercontext"]["headers"] == {"Authorization": "Bearer existing-token"}
    assert mcp["mcpServers"]["powercontext"]["disabled"] is False


def test_setup_workbuddy_updates_an_existing_powercontext_hook(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy"
    home.mkdir()
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    (home / "settings.json").write_text(
        json.dumps({
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 /old/path/workbuddy_powercontext_hook.py",
                                "timeout": 3,
                                "custom": "preserved",
                            }
                        ]
                    }
                ]
            }
        }),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "workbuddy", "--source", str(checkout)],
    )

    assert result.exit_code == 0
    settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    matchers = settings["hooks"]["UserPromptSubmit"]
    assert len(matchers) == 1
    hook = matchers[0]["hooks"][0]
    assert hook["command"] == _expected_hook_command(home / "hooks")
    assert hook["timeout"] == 10
    assert hook["statusMessage"] == "Syncing PowerContext"
    assert hook["custom"] == "preserved"


def test_setup_workbuddy_rolls_back_json_changes_on_failure(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy"
    home.mkdir()
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    previous_settings = {"enabledPlugins": {"other-plugin": True}}
    (home / "settings.json").write_text(json.dumps(previous_settings), encoding="utf-8")
    previous_mcp = {"mcpServers": {"other-server": {"type": "stdio", "command": "other", "args": []}}}
    (home / "mcp.json").write_text(json.dumps(previous_mcp), encoding="utf-8")

    def fail_skill_install(*_args, **_kwargs) -> None:
        raise OSError

    monkeypatch.setattr(workbuddy_cli, "_install_workbuddy_skill", fail_skill_install)

    with pytest.raises(OSError):
        workbuddy_cli.install_workbuddy_plugin(source=str(checkout), ref="master")

    assert json.loads((home / "settings.json").read_text(encoding="utf-8")) == previous_settings
    assert json.loads((home / "mcp.json").read_text(encoding="utf-8")) == previous_mcp


def test_doctor_workbuddy_reports_failures_before_install(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "workbuddy", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert {name: check["status"] for name, check in payload["checks"].items()} == {
        "hooks": "failed",
        "settings": "failed",
        "mcp": "failed",
        "skill": "failed",
    }


def test_doctor_workbuddy_reports_ok_after_install(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy"
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    install = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "workbuddy", "--source", str(checkout)],
    )
    assert install.exit_code == 0

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "workbuddy", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert {name: check["status"] for name, check in payload["checks"].items()} == {
        "hooks": "ok",
        "settings": "ok",
        "mcp": "ok",
        "skill": "ok",
    }


def test_setup_workbuddy_rejects_a_missing_plugin(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "workbuddy", "--source", str(tmp_path / "not-a-checkout")],
    )

    assert result.exit_code == 1
    assert "WorkBuddy plugin was not found" in result.output


def test_setup_workbuddy_remote_checkout_uses_the_requested_ref(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    captured: list[tuple[str, str, Path]] = []

    def fake_clone(source: str, ref: str, target: Path) -> None:
        captured.append((source, ref, target))
        _write_plugin(target)

    monkeypatch.setattr(workbuddy_cli, "clone_github_source", fake_clone)

    plugin = workbuddy_cli.resolve_workbuddy_plugin_dir(source="oceanbase/powercontext", ref="v0.0.2")

    checkout_root = workbuddy_cli.checkout_target("oceanbase/powercontext", "v0.0.2")
    assert plugin == checkout_root / "integrations" / "workbuddy" / "plugins" / "powercontext"
    assert captured[0][0] == "oceanbase/powercontext"
    assert captured[0][1] == "v0.0.2"


def test_workbuddy_home_honors_the_environment_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))

    assert workbuddy_cli.workbuddy_home() == (tmp_path / "workbuddy").resolve()
