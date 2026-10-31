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
import re
import shlex
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from typer.testing import CliRunner

import powercontext.cli.workbuddy as workbuddy_cli
from powercontext.cli.app import create_cli
from powercontext.cli.installation import setup as install_plugin
from powercontext.cli.system import SetupError, doctor_app, setup_app

_HOOK_MODULES = (
    "workbuddy_powercontext_hook.py",
    "workbuddy_settings.py",
    "powercontext_client_config.py",
    "prepared_context.py",
    "prompt_privacy.py",
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
    skill = plugin / "skills" / "powercontext-project-context"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '${POWERCONTEXT_PYTHON} ${POWERCONTEXT_SCOPE_BINDING_SCRIPT} --cwd "$PWD"\n',
        encoding="utf-8",
    )
    (skill / "references").mkdir()
    (skill / "references" / "scope-memory.md").write_text(
        (skill / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8"
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


def test_setup_and_doctor_workbuddy_reach_loopback_server_without_server_dependencies(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = {"status": "ok"} if self.path == "/health/live" else {"status": "ready", "checks": {}}
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature.
            del format, args

    script = """
import builtins
import json
import sys

real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "sqlalchemy" or name.startswith("sqlalchemy."):
        raise ModuleNotFoundError("Server dependency is not installed", name="sqlalchemy")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
from typer.testing import CliRunner
from powercontext.cli.app import create_cli
from powercontext.cli.system import setup_app, doctor_app

cli = create_cli([setup_app, doctor_app])
for arguments in (
    ["setup", "workbuddy", "--source", sys.argv[1], "--server-url", sys.argv[2], "--json"],
    ["doctor", "workbuddy", "--json"],
):
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["ok"] is True
    checks = report["hosts"]["workbuddy"]["checks"]
    assert checks["server_liveness"]["status"] == "ok"
    assert checks["server_readiness"]["status"] == "ok"
"""
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(Path(__file__).parents[1]),
                    f"http://127.0.0.1:{server.server_port}",
                ],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("from_environment_file", [False, True])
def test_setup_workbuddy_rejects_invalid_credentials_without_changing_host(
    tmp_path: Path, monkeypatch, installed_agent_checks, installed: bool, from_environment_file: bool
) -> None:
    checkout = tmp_path / "checkout"
    plugin = _write_plugin(checkout)
    home = tmp_path / "home"
    monkeypatch.setenv("WORKBUDDY_HOME", str(home / "workbuddy"))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(home / "data"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(home / "clients.json"))
    env_file = tmp_path / "setup.env"
    env_file.write_text("", encoding="utf-8")
    arguments = ["setup", "--env-file", str(env_file), "workbuddy", "--source", str(checkout), "--json"]
    cli = create_cli([setup_app])
    if installed:
        result = CliRunner().invoke(cli, arguments)
        assert result.exit_code == 0, result.output
    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    (plugin / "hooks/workbuddy_powercontext_hook.py").write_text("# replacement\n", encoding="utf-8")
    if from_environment_file:
        env_file.write_text('POWERCONTEXT_CLIENT_API_TOKEN="invalid token"\n', encoding="utf-8")
    else:
        monkeypatch.setenv("POWERCONTEXT_WORKBUDDY_AUTHORIZATION", "invalid token")

    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 1, result.output
    assert "single-token credential" in json.loads(result.output)["hosts"]["workbuddy"]["error"]
    assert "invalid token" not in result.output
    assert {path: path.read_bytes() for path in home.rglob("*") if path.is_file()} == before


def test_setup_workbuddy_installs_from_a_local_checkout(tmp_path: Path, monkeypatch, installed_agent_checks) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy home"
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    # --env-file: a developer's repo-root .env must not supply the authorization state under test.
    env_file = tmp_path / "setup.env"
    env_file.write_text("", encoding="utf-8")

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "--env-file", str(env_file), "workbuddy", "--source", str(checkout), "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["hosts"]["workbuddy"]["result"] == {
        "plugin": "powercontext",
        "plugin_path": str(home),
        "workbuddy_home": str(home),
        "hooks_dir": str(home / "hooks"),
        "data_dir": str(tmp_path / "data"),
        "authorization_state": "not_configured",
    }

    hooks_dir = home / "hooks"
    for name in _HOOK_MODULES:
        assert (hooks_dir / name).is_file()
    assert (hooks_dir / "powercontext_scope_binding.py").is_file()

    skill_markdown = home / "skills" / "powercontext-project-context" / "SKILL.md"
    assert skill_markdown.is_file()
    skill_content = skill_markdown.read_text(encoding="utf-8")
    assert "${POWERCONTEXT_SCOPE_BINDING_SCRIPT}" not in skill_content
    assert "${POWERCONTEXT_PYTHON}" not in skill_content
    assert (hooks_dir / "powercontext_scope_binding.py").as_posix() in skill_content
    assert Path(sys.executable).as_posix() in skill_content
    assert (skill_markdown.parent / "references" / "scope-memory.md").read_text(encoding="utf-8") == skill_content
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
    assert hook["timeout"] == 30
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


@pytest.mark.skipif(shutil.which("bash") is None, reason="the manual installation steps are bash commands")
@pytest.mark.parametrize(
    "document",
    [
        "docs/en/docs/integrations/workbuddy.md",
        "docs/zh/docs/integrations/workbuddy.md",
        "integrations/workbuddy/README.md",
    ],
)
def test_manual_workbuddy_copy_installs_the_same_hook_files_as_setup(
    document: str, tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    repository = Path(__file__).parents[1]
    home = tmp_path / "workbuddy"
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    env_file = tmp_path / "setup.env"
    env_file.write_text("", encoding="utf-8")
    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "--env-file", str(env_file), "workbuddy", "--source", str(repository), "--json"],
    )
    assert result.exit_code == 0, result.output

    text = (repository / document).read_text(encoding="utf-8")
    copy_steps = next(
        block
        for block in re.findall(r"```bash\n(.*?)```", text, re.DOTALL)
        if 'mkdir -p "$WORKBUDDY_HOOKS_DIR"' in block
    )
    manual_hooks = tmp_path / "manual hooks"
    completed = subprocess.run(
        ["bash", "-euc", copy_steps],  # noqa: S607
        cwd=repository,
        env={**os.environ, "WORKBUDDY_HOOKS_DIR": str(manual_hooks)},
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    assert sorted(path.name for path in manual_hooks.iterdir()) == sorted(
        path.name for path in (home / "hooks").iterdir()
    )


def test_setup_workbuddy_preserves_existing_settings_and_mcp(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
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


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks requires Windows privileges")
def test_install_preserves_unowned_hook_symlinks(tmp_path: Path, monkeypatch) -> None:
    checkout = tmp_path / "checkout"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy"
    hooks = home / "hooks"
    hooks.mkdir(parents=True)
    external = tmp_path / "other.py"
    external.write_text("# other plugin\n", encoding="utf-8")
    linked = hooks / "other.py"
    linked.symlink_to(external)
    dangling = hooks / "missing.py"
    dangling.symlink_to(tmp_path / "missing.py")
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    install_plugin("workbuddy", source=str(str(checkout)), ref="main")

    assert linked.is_symlink() and linked.readlink() == external
    assert dangling.is_symlink() and dangling.readlink() == tmp_path / "missing.py"
    assert external.read_text(encoding="utf-8") == "# other plugin\n"


def test_setup_workbuddy_preserves_remote_authenticated_mcp_configuration(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy"
    home.mkdir()
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    # --env-file: a developer's repo-root .env must not override the endpoint preserved here.
    env_file = tmp_path / "setup.env"
    env_file.write_text("", encoding="utf-8")

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
        ["setup", "--env-file", str(env_file), "workbuddy", "--source", str(checkout)],
    )

    assert result.exit_code == 0
    mcp = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["powercontext"]["url"] == "https://memory.example.test/mcp"
    assert mcp["mcpServers"]["powercontext"]["headers"] == {"Authorization": "Bearer existing-token"}
    assert mcp["mcpServers"]["powercontext"]["disabled"] is True


def test_setup_workbuddy_preserves_other_hooks_shared_scripts(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
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


def test_setup_workbuddy_refuses_an_unowned_skill(tmp_path: Path, monkeypatch, installed_agent_checks) -> None:
    checkout = tmp_path / "powercontext"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy"
    skill = home / "skills" / "powercontext-project-context"
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


def test_setup_workbuddy_refreshes_an_owned_skill(tmp_path: Path, monkeypatch, installed_agent_checks) -> None:
    from powercontext.cli.guidance import END, RESOURCE_MANIFEST, START

    checkout = tmp_path / "powercontext"
    plugin = _write_plugin(checkout)
    source = plugin / "skills/powercontext-project-context/SKILL.md"
    source.write_text(f"{START}\n{source.read_text(encoding='utf-8')}{END}\n", encoding="utf-8")
    (source.parent / RESOURCE_MANIFEST).write_text(
        json.dumps({
            "schema_version": 1,
            "resources": ["SKILL.md", "references/scope-memory.md"],
        }),
        encoding="utf-8",
    )
    home = tmp_path / "workbuddy"
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    first = CliRunner().invoke(create_cli([setup_app]), ["setup", "workbuddy", "--source", str(checkout)])
    assert first.exit_code == 0

    source.write_text(
        source.read_text(encoding="utf-8").replace(START + "\n", START + "\nupdated\n"),
        encoding="utf-8",
    )
    refreshed = CliRunner().invoke(create_cli([setup_app]), ["setup", "workbuddy", "--source", str(checkout)])

    assert refreshed.exit_code == 0
    assert (
        (home / "skills" / "powercontext-project-context" / "SKILL.md")
        .read_text(encoding="utf-8")
        .startswith(START + "\nupdated\n")
    )


def test_setup_workbuddy_preserves_installation_when_settings_are_unreadable(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
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
    assert "settings.json" in result.output
    assert not (home / "hooks").exists()
    assert not (home / "mcp.json").exists()


def test_setup_workbuddy_updates_an_existing_powercontext_hook(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
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
    assert hook["timeout"] == 30
    assert hook["statusMessage"] == "Syncing PowerContext"
    assert hook["custom"] == "preserved"


def test_setup_workbuddy_rolls_back_json_changes_on_failure(
    tmp_path: Path, monkeypatch, installed_agent_checks
) -> None:
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

    with pytest.raises(SetupError):
        install_plugin("workbuddy", source=str(str(checkout)), ref="master")

    assert json.loads((home / "settings.json").read_text(encoding="utf-8")) == previous_settings
    assert json.loads((home / "mcp.json").read_text(encoding="utf-8")) == previous_mcp


def test_doctor_workbuddy_reports_failures_before_install(tmp_path: Path, monkeypatch, server_checks) -> None:
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "workbuddy", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)["hosts"]["workbuddy"]
    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert {name: check["status"] for name, check in payload["checks"].items()} == {
        "hooks": "failed",
        "settings": "failed",
        "mcp": "failed",
        "skill": "failed",
    }


def test_doctor_workbuddy_reports_ok_after_install(tmp_path: Path, monkeypatch, server_checks) -> None:
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
    payload = json.loads(result.output)["hosts"]["workbuddy"]
    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert {name: check["status"] for name, check in payload["checks"].items()} == {
        "hooks": "ok",
        "settings": "ok",
        "mcp": "ok",
        "skill": "ok",
    }


def test_setup_workbuddy_rejects_a_missing_plugin(tmp_path: Path, monkeypatch, installed_agent_checks) -> None:
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "workbuddy", "--source", str(tmp_path / "not-a-checkout")],
    )

    assert result.exit_code == 1
    assert "workbuddy plugin was not found" in result.output.casefold()


def test_workbuddy_home_honors_the_environment_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))

    assert workbuddy_cli.workbuddy_home() == (tmp_path / "workbuddy").resolve()


@pytest.mark.parametrize("failure", ["invalid_credential", "credential_write"])
def test_config_agent_failure_preserves_the_working_connection(tmp_path, monkeypatch, failure):
    from powercontext.cli import installation
    from powercontext.cli.authorization import credential_path, read_stored_authorization
    from powercontext.client.transport_policy import load_client_settings

    checkout = tmp_path / "checkout"
    _write_plugin(checkout)
    home = tmp_path / "workbuddy"
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    monkeypatch.setenv("POWERCONTEXT_WORKBUDDY_AUTHORIZATION", "old-token")
    install_plugin("workbuddy", source=str(checkout), server_url="https://old.example", json_output=True)
    credential = credential_path("workbuddy")
    paths = (home / "mcp.json", tmp_path / "clients.json", credential)
    original = {path: path.read_bytes() for path in paths}
    monkeypatch.setenv(
        "POWERCONTEXT_WORKBUDDY_AUTHORIZATION", "bad token" if failure == "invalid_credential" else "new-token"
    )
    if failure == "credential_write":
        write = installation.write_bytes

        def fail_credential(path, content):
            if path == credential:
                raise OSError("credential write failed")  # noqa: TRY003
            write(path, content)

        monkeypatch.setattr(installation, "write_bytes", fail_credential)
    result = CliRunner().invoke(
        create_cli(), ["config", "agent", "workbuddy", "--server-url", "https://new.example", "--json"]
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["hosts"]["workbuddy"]["status"] == "failed"
    assert {path: path.read_bytes() for path in paths} == original
    assert load_client_settings("workbuddy")["server_url"] == "https://old.example"
    assert read_stored_authorization(credential, server_url="https://old.example").authorization == "Bearer old-token"
    assert "old-token" not in result.output
    assert "new-token" not in result.output
