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
    (scripts / "workspace_scope.py").write_text(
        "import json\n\n"
        "def resolve_scope_id(*_args, **_kwargs) -> str:\n"
        "    return 'scope'\n\n"
        "if __name__ == '__main__':\n"
        "    print(json.dumps({'scope_id': 'scope'}))\n",
        encoding="utf-8",
    )
    cache = scripts / "__pycache__"
    cache.mkdir()
    (cache / "scope_binding.cpython-312.pyc").write_bytes(b"\x00")
    skill = plugin / "skills" / "project-context"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '${POWERCONTEXT_PYTHON} ${POWERCONTEXT_SCOPE_BINDING_SCRIPT} --cwd "$PWD"\n',
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
    return f"{_shell_argument(Path(sys.executable).as_posix())} {_shell_argument(hooks_dir / 'workbuddy_powercontext_hook.py')}"


def _shell_argument(value: str | Path) -> str:
    text = Path(value).as_posix() if isinstance(value, Path) else value
    if os.name == "nt":
        return subprocess.list2cmdline([text])
    return shlex.quote(text)


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
    assert (hooks_dir / "powercontext_scope_binding.py").is_file()

    skill_markdown = home / "skills" / "project-context" / "SKILL.md"
    assert skill_markdown.is_file()
    skill_content = skill_markdown.read_text(encoding="utf-8")
    assert "${POWERCONTEXT_SCOPE_BINDING_SCRIPT}" not in skill_content
    assert "${POWERCONTEXT_PYTHON}" not in skill_content
    assert (hooks_dir / "powercontext_scope_binding.py").as_posix() in skill_content
    assert Path(sys.executable).as_posix() in skill_content
    assert json.loads((skill_markdown.parent / ".powercontext.json").read_text(encoding="utf-8")) == {
        "schema": 1,
        "owner": "powercontext",
        "integration": "workbuddy",
    }

    project = tmp_path / "project root"
    project.mkdir()
    scope_command = next(line for line in skill_content.splitlines() if "--cwd" in line)
    scope_command = scope_command.replace('"$PWD"', _shell_argument(project))
    completed = subprocess.run(  # noqa: S602 - the installed shell command is the behavior under test.
        scope_command,
        shell=True,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"scope_id": "scope"}

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


def test_setup_workbuddy_migrates_the_legacy_generated_mcp_entry(tmp_path: Path, monkeypatch) -> None:
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
                    "url": "http://127.0.0.1:8000/mcp",
                    "headers": {},
                    "description": "PowerContext agent memory & handoff MCP server (local service on port 8000)",
                    "disabled": False,
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
    assert mcp["mcpServers"]["powercontext"]["url"] == (
        "${POWERCONTEXT_WORKBUDDY_SERVER_URL:-http://127.0.0.1:8000}/mcp"
    )
    assert mcp["mcpServers"]["powercontext"]["headers"] == {
        "Authorization": "${POWERCONTEXT_WORKBUDDY_AUTHORIZATION:-}"
    }


def test_setup_workbuddy_preserves_other_hooks_shared_scripts(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy"
    shared_script = home / "hooks" / "scripts" / "other_hook.py"
    shared_script.parent.mkdir(parents=True)
    shared_script.write_text("# owned by another hook\n", encoding="utf-8")
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "workbuddy", "--source", str(checkout)],
    )

    assert result.exit_code == 0
    assert shared_script.read_text(encoding="utf-8") == "# owned by another hook\n"


def test_setup_workbuddy_refuses_an_unowned_skill(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy"
    skill = home / "skills" / "project-context"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("user-owned\n", encoding="utf-8")
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "workbuddy", "--source", str(checkout)],
    )

    assert result.exit_code == 1
    assert "is not owned by PowerContext" in result.output
    assert (skill / "SKILL.md").read_text(encoding="utf-8") == "user-owned\n"
    assert not (home / "hooks").exists()
    assert not (home / "settings.json").exists()
    assert not (home / "mcp.json").exists()


def test_setup_workbuddy_refreshes_an_owned_skill(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "powercontext"
    plugin = _write_plugin(checkout)
    home = tmp_path / "workbuddy"
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    first = CliRunner().invoke(create_cli([setup_app]), ["setup", "workbuddy", "--source", str(checkout)])
    assert first.exit_code == 0

    (plugin / "skills" / "project-context" / "SKILL.md").write_text(
        'updated\n${POWERCONTEXT_PYTHON} ${POWERCONTEXT_SCOPE_BINDING_SCRIPT} --cwd "$PWD"\n',
        encoding="utf-8",
    )
    refreshed = CliRunner().invoke(create_cli([setup_app]), ["setup", "workbuddy", "--source", str(checkout)])

    assert refreshed.exit_code == 0
    assert (home / "skills" / "project-context" / "SKILL.md").read_text(encoding="utf-8").startswith("updated\n")


def test_setup_workbuddy_stops_before_writes_when_settings_snapshot_is_unreadable(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy"
    (home / "settings.json").mkdir(parents=True)
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "workbuddy", "--source", str(checkout)],
    )

    assert result.exit_code == 1
    assert "Cannot update WorkBuddy settings" in result.output
    assert not (home / "hooks").exists()
    assert not (home / "mcp.json").exists()


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


def test_setup_workbuddy_remote_checkout_refreshes_the_requested_ref(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    generation = 0

    def fake_clone(source: str, ref: str, target: Path) -> None:
        nonlocal generation
        generation += 1
        plugin = _write_plugin(target)
        (plugin / "revision.txt").write_text(f"{source}@{ref}:{generation}\n", encoding="utf-8")

    monkeypatch.setattr(workbuddy_cli, "clone_github_source", fake_clone)

    first = workbuddy_cli.resolve_workbuddy_plugin_dir(source="oceanbase/powercontext", ref="master")
    refreshed = workbuddy_cli.resolve_workbuddy_plugin_dir(source="oceanbase/powercontext", ref="master")

    checkout_root = workbuddy_cli.checkout_target("oceanbase/powercontext", "master")
    expected = checkout_root / "integrations" / "workbuddy" / "plugins" / "powercontext"
    assert first == expected
    assert refreshed == expected
    assert (refreshed / "revision.txt").read_text(encoding="utf-8") == "oceanbase/powercontext@master:2\n"


def test_workbuddy_home_honors_the_environment_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))

    assert workbuddy_cli.workbuddy_home() == (tmp_path / "workbuddy").resolve()
