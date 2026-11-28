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

"""`powercontext setup <agent> --remove` undoes setup for one agent and keeps everything else."""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest
from typer.testing import CliRunner

from powercontext.cli import git_source
from powercontext.cli.app import create_cli
from powercontext.cli.installation import setup as install_plugin
from powercontext.cli.system import SetupError, doctor_app, setup_app
from tests.test_cli_workbuddy import _write_plugin as write_workbuddy_plugin
from tests.test_cli_zcode import _checkout as write_zcode_checkout
from tests.test_opencode_cli import _patch_opencode_runtime
from tests.test_opencode_cli import _write_plugin as write_opencode_plugin
from tests.test_pi_cli import _write_pi_package

REPOSITORY = Path(__file__).parents[1]


def _remove(*agents: str) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(create_cli([setup_app]), ["setup", *agents, "--remove", "--json"])
    return result.exit_code, json.loads(result.output)


def _doctor(agent: str) -> dict[str, Any]:
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", agent, "--json"])
    return json.loads(result.output)["hosts"][agent]


def _saved_hosts(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))["hosts"]


@pytest.fixture
def client_config(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "clients.json"
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(path))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    return path


def test_workbuddy_remove_undoes_setup_and_keeps_user_configuration(
    tmp_path: Path, monkeypatch, client_config: Path, server_checks
) -> None:
    from powercontext.cli.authorization import credential_path

    home = tmp_path / "workbuddy"
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_WORKBUDDY_AUTHORIZATION", "test-token")
    checkout = tmp_path / "checkout"
    write_workbuddy_plugin(checkout)
    home.mkdir()
    user_hook = {"hooks": [{"type": "command", "command": "echo mine"}]}
    (home / "settings.json").write_text(
        json.dumps({"theme": "dark", "hooks": {"UserPromptSubmit": [user_hook]}}), encoding="utf-8"
    )
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {"other": {"url": "http://other"}}}), encoding="utf-8")
    (home / "hooks").mkdir()
    (home / "hooks" / "mine.py").write_text("# mine\n", encoding="utf-8")
    setup = CliRunner().invoke(create_cli([setup_app]), ["setup", "workbuddy", "--source", str(checkout)])
    assert setup.exit_code == 0, setup.output
    assert credential_path("workbuddy").is_file()
    assert "workbuddy" in _saved_hosts(client_config)

    code, payload = _remove("workbuddy")

    assert code == 0
    row = payload["hosts"]["workbuddy"]
    assert payload["ok"] is True
    assert (row["status"], row["removal"], row["ok"]) == ("ok", "removed", True)
    assert str(credential_path("workbuddy")) in row["result"]["removed"]
    settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert settings == {"theme": "dark", "hooks": {"UserPromptSubmit": [user_hook]}}
    assert json.loads((home / "mcp.json").read_text(encoding="utf-8")) == {
        "mcpServers": {"other": {"url": "http://other"}}
    }
    assert sorted(path.name for path in (home / "hooks").iterdir()) == ["mine.py"]
    assert not (home / "skills" / "powercontext-project-context").exists()
    assert not credential_path("workbuddy").exists()
    assert "workbuddy" not in _saved_hosts(client_config)
    assert {name: check["status"] for name, check in _doctor("workbuddy")["checks"].items()} == {
        "hooks": "failed",
        "settings": "failed",
        "mcp": "failed",
        "skill": "failed",
    }

    code, payload = _remove("workbuddy")

    assert code == 0
    assert payload["hosts"]["workbuddy"]["removal"] == "not_set_up"
    assert payload["hosts"]["workbuddy"]["host_registration"] == "checked"
    assert payload["hosts"]["workbuddy"]["result"] == {"removed": [], "notes": []}


def test_workbuddy_remove_keeps_same_named_hook_files_without_powercontext_setup(
    tmp_path: Path, monkeypatch, client_config: Path
) -> None:
    home = tmp_path / "workbuddy"
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    (home / "hooks").mkdir(parents=True)
    (home / "hooks" / "prepared_context.py").write_text("# someone else's hook\n", encoding="utf-8")

    code, payload = _remove("workbuddy")

    assert code == 0
    assert payload["hosts"]["workbuddy"]["removal"] == "not_set_up"
    assert (home / "hooks" / "prepared_context.py").read_text(encoding="utf-8") == "# someone else's hook\n"


def test_pi_remove_unregisters_the_owned_package(
    tmp_path: Path, monkeypatch, client_config: Path, server_checks
) -> None:
    import powercontext.cli.pi as pi_cli
    from powercontext.cli.authorization import credential_path

    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    monkeypatch.setenv("POWERCONTEXT_PI_AUTHORIZATION", "test-token")
    monkeypatch.setattr(pi_cli, "which", lambda _name: "/usr/bin/pi")
    unrelated = tmp_path / "other-package"
    registered = {str(unrelated): unrelated}

    def fake_pi(command: str, *values: str) -> str:
        if command == "list":
            return "User packages:\n" + "".join(f"  {source}\n    {path}\n" for source, path in registered.items())
        if command == "install":
            registered[values[0]] = Path(values[0])
        elif command == "remove":
            registered.pop(values[0])  # Pi rejects removing an unknown source.
        return ""

    monkeypatch.setattr(pi_cli, "_run_pi", fake_pi)
    package = _write_pi_package(tmp_path / "checkout")
    setup = CliRunner().invoke(create_cli([setup_app]), ["setup", "pi", "--source", str(package)])
    assert setup.exit_code == 0, setup.output
    destination = tmp_path / "data" / "plugins" / "pi"
    assert str(destination) in registered

    for removal in ("removed", "not_set_up"):
        code, payload = _remove("pi")
        assert code == 0
        assert payload["hosts"]["pi"]["removal"] == removal

    assert registered == {str(unrelated): unrelated}
    assert not destination.exists()
    assert not credential_path("pi").exists()
    assert _doctor("pi")["checks"]["package"]["status"] == "failed"


def test_pi_remove_finds_a_registration_whose_package_directory_is_gone(
    tmp_path: Path, monkeypatch, client_config: Path, server_checks
) -> None:
    import os

    import powercontext.cli.pi as pi_cli

    agent = tmp_path / "pi" / "agent"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("POWERCONTEXT_PI_AUTHORIZATION", "test-token")
    on_path = {"pi": True}
    monkeypatch.setattr(pi_cli, "which", lambda _name: "/usr/bin/pi" if on_path["pi"] else None)
    sources: list[str] = []

    def fake_pi(command: str, *values: str) -> str:
        # Pi stores a local package relative to its agent directory and resolves it only while the directory exists.
        if command == "list":
            lines = [
                f"  {source}\n" + (f"    {(agent / source).resolve()}\n" if (agent / source).is_dir() else "")
                for source in sources
            ]
            return "User packages:\n" + "".join(lines) if sources else "No packages installed.\n"
        target = Path(values[0]).resolve()
        if command == "install":
            sources.append(os.path.relpath(target, agent))
        elif command == "remove":
            matches = [source for source in sources if (agent / source).resolve() == target]
            if not matches:
                raise SetupError("not_installed")
            sources.remove(matches[0])
        return ""

    monkeypatch.setattr(pi_cli, "_run_pi", fake_pi)
    package = _write_pi_package(tmp_path / "checkout")
    setup = CliRunner().invoke(create_cli([setup_app]), ["setup", "pi", "--source", str(package)])
    assert setup.exit_code == 0, setup.output
    on_path["pi"] = False
    code, payload = _remove("pi")
    assert (code, payload["hosts"]["pi"]["host_registration"]) == (0, "not_checked")
    assert len(sources) == 1  # Pi still lists PowerContext's package, now without a directory.
    on_path["pi"] = True

    code, payload = _remove("pi")

    assert code == 0
    row = payload["hosts"]["pi"]
    assert (row["removal"], row["host_registration"]) == ("removed", "checked")
    assert sources == []
    code, payload = _remove("pi")
    assert (payload["hosts"]["pi"]["removal"], payload["hosts"]["pi"]["host_registration"]) == ("not_set_up", "checked")


def test_kimi_remove_keeps_other_registrations(tmp_path: Path, monkeypatch, client_config: Path, server_checks) -> None:
    from powercontext.cli import kimi_code

    home = tmp_path / "kimi"
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.delenv("POWERCONTEXT_KIMI_TOKEN", raising=False)
    monkeypatch.setattr(kimi_code, "kimi_executable", lambda: "kimi")
    registry = home / "plugins" / "installed.json"
    registry.parent.mkdir(parents=True)
    other = {"id": "other", "root": str(tmp_path / "other"), "enabled": True}
    registry.write_text(json.dumps({"version": 1, "plugins": [other]}), encoding="utf-8")
    install_plugin("kimi-code", source=str(REPOSITORY), scope_id="scope-1")

    for removal in ("removed", "not_set_up"):
        code, payload = _remove("kimi-code")
        assert code == 0
        assert payload["hosts"]["kimi-code"]["removal"] == removal

    assert json.loads(registry.read_text(encoding="utf-8"))["plugins"] == [other]
    assert not (home / "plugins" / "managed" / "powercontext").exists()
    assert "kimi-code" not in _saved_hosts(client_config)
    assert _doctor("kimi-code")["checks"]["plugin"]["detail"] == "not installed"


def test_zcode_remove_keeps_other_plugin_directories(
    tmp_path: Path, monkeypatch, client_config: Path, server_checks
) -> None:
    from powercontext.cli import zcode

    config_path = tmp_path / "home" / ".zcode" / "cli" / "config.json"
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: config_path)
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"model": "glm", "plugins": {"dirs": ["/other/plugin"]}}), encoding="utf-8")
    checkout = tmp_path / "checkout"
    write_zcode_checkout(checkout)
    install_plugin("zcode", source=str(checkout), ref="unused")

    for removal in ("removed", "not_set_up"):
        code, payload = _remove("zcode")
        assert code == 0
        assert payload["hosts"]["zcode"]["removal"] == removal

    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "model": "glm",
        "plugins": {"dirs": ["/other/plugin"], "enabled": True},
    }
    assert not zcode.zcode_plugin_dir().exists()
    assert _doctor("zcode")["checks"]["plugin"]["detail"] == "not registered"


def test_opencode_remove_keeps_user_tui_entries_and_deletes_its_checkout(
    tmp_path: Path, monkeypatch, client_config: Path
) -> None:
    import powercontext.cli.opencode as opencode_cli

    config = tmp_path / "config"
    (config / "plugins").mkdir(parents=True)
    (config / "plugins" / "mine.js").write_text("export default {}\n", encoding="utf-8")
    (config / "tui.jsonc").write_text(
        '// keep this comment\n{\n  "plugin": ["@mem9/opencode"], // trailing comment\n}\n', encoding="utf-8"
    )
    monkeypatch.setattr(git_source, "clone_github_source", lambda _source, _ref, target: write_opencode_plugin(target))
    _patch_opencode_runtime(tmp_path, monkeypatch, tmp_path / "unused", config)
    result = install_plugin("opencode", source="oceanbase/powercontext", ref="master")
    checkout = git_source.linked_checkout(Path(result["plugin_path"]))
    assert checkout is not None
    assert checkout.is_dir()

    for removal in ("removed", "not_set_up"):
        code, payload = _remove("opencode")
        assert code == 0
        assert payload["hosts"]["opencode"]["removal"] == removal

    text = (config / "tui.jsonc").read_text(encoding="utf-8")
    assert "// keep this comment" in text
    assert "// trailing comment" in text
    assert json.loads(opencode_cli._strip_jsonc(text))["plugin"] == ["@mem9/opencode"]
    assert sorted(path.name for path in (config / "plugins").iterdir()) == ["mine.js"]
    assert not (config / "skills" / "powercontext-project-context").exists()
    assert not checkout.exists()
    assert "opencode" not in _saved_hosts(client_config)


class _FakeClaude:
    """Model the plugin state that the Claude Code CLI keeps, failing like it does on missing entries."""

    def __init__(self, cache: Path) -> None:
        self.cache = cache
        self.marketplaces: list[dict[str, Any]] = []
        self.plugins: list[dict[str, Any]] = []

    def __call__(self, *arguments: str) -> CompletedProcess[str]:  # noqa: C901 - one branch per modeled command.
        command = list(arguments)
        output = command[-1] == "--json"
        if output:
            command.pop()
        result: object = None
        match command:
            case ["plugin", "marketplace", "list"]:
                result = self.marketplaces
            case ["plugin", "list"]:
                result = self.plugins
            case ["plugin", "marketplace", "add", location, "--scope", "user"]:
                self.marketplaces.append({"name": "powercontext", "source": "directory", "path": location})
            case ["plugin", "marketplace", "update", _]:
                pass
            case ["plugin", "install" | "update", plugin_id, "--scope", "user"]:
                self.plugins = [item for item in self.plugins if item["id"] != plugin_id] + [
                    {
                        "id": plugin_id,
                        "version": "1.0.0",
                        "scope": "user",
                        "enabled": True,
                        "installPath": str(self.cache),
                    }
                ]
            case ["plugin", "uninstall", plugin_id, "--scope", "user"]:
                if not any(item["id"] == plugin_id for item in self.plugins):
                    raise SetupError("not_installed")
                self.plugins = [item for item in self.plugins if item["id"] != plugin_id]
            case ["plugin", "marketplace", "remove", name]:
                if not any(item["name"] == name for item in self.marketplaces):
                    raise SetupError("marketplace_not_found")
                self.marketplaces = [item for item in self.marketplaces if item["name"] != name]
            case _:
                raise AssertionError(command)
        return CompletedProcess(command, 0, json.dumps(result) if output else "", "")


@pytest.mark.parametrize("user_statusline", [False, True])
def test_claude_code_remove_keeps_user_settings(
    tmp_path: Path, monkeypatch, client_config: Path, server_checks, user_statusline: bool
) -> None:
    from powercontext.cli import system
    from powercontext.cli.authorization import credential_path

    claude_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setenv("POWERCONTEXT_CLAUDE_AUTHORIZATION", "test-token")
    fake = _FakeClaude(tmp_path / "powercontext" / "plugins" / "claude-code")
    monkeypatch.setattr(system, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(system, "_run_claude", fake)
    claude_home.mkdir()
    statusline = {"type": "command", "command": "my-status"}
    initial = {"permissions": {"allow": ["Bash(ls)"]}} | ({"statusLine": statusline} if user_statusline else {})
    (claude_home / "settings.json").write_text(json.dumps(initial), encoding="utf-8")
    setup = CliRunner().invoke(create_cli([setup_app]), ["setup", "claude-code", "--source", str(REPOSITORY)])
    assert setup.exit_code == 0, setup.output

    for removal in ("removed", "not_set_up"):
        code, payload = _remove("claude-code")
        assert code == 0
        assert payload["hosts"]["claude-code"]["removal"] == removal

    assert (fake.plugins, fake.marketplaces) == ([], [])
    settings = json.loads((claude_home / "settings.json").read_text(encoding="utf-8"))
    assert settings == initial | {"pluginConfigs": {}}
    assert not credential_path("claude-code").exists()
    assert _doctor("claude-code")["checks"]["plugin"]["detail"] == "PowerContext plugin is not installed"


def test_codex_remove_keeps_a_marketplace_that_offers_other_plugins(
    tmp_path: Path, monkeypatch, client_config: Path, server_checks
) -> None:
    from powercontext.cli import system
    from powercontext.cli.authorization import credential_path, write_stored_authorization

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    shared = tmp_path / "shared-marketplace"
    (shared / ".agents" / "plugins").mkdir(parents=True)
    (shared / ".agents" / "plugins" / "marketplace.json").write_text(
        json.dumps({"name": "team", "plugins": [{"name": "powercontext"}, {"name": "linter"}]}), encoding="utf-8"
    )
    marketplaces = [{"name": "powercontext", "root": str(REPOSITORY)}, {"name": "team", "root": str(shared)}]
    installed = [
        {"name": "powercontext", "marketplaceName": "powercontext", "installed": True, "enabled": True},
        {"name": "powercontext", "marketplaceName": "team", "installed": True, "enabled": True},
    ]

    def fake_codex(*arguments: str) -> dict[str, Any]:
        match arguments:
            case ("plugin", "list"):
                return {"installed": list(installed), "available": []}
            case ("plugin", "remove", selector):
                installed[:] = [item for item in installed if f"{item['name']}@{item['marketplaceName']}" != selector]
            case ("plugin", "marketplace", "list"):
                return {"marketplaces": list(marketplaces)}
            case ("plugin", "marketplace", "remove", name):
                if not any(item["name"] == name for item in marketplaces):
                    raise SetupError("marketplace_not_configured")
                marketplaces[:] = [item for item in marketplaces if item["name"] != name]
            case _:
                raise AssertionError(arguments)
        return {}

    monkeypatch.setattr(system, "_codex_path_executable", lambda: "/usr/bin/codex")
    monkeypatch.setattr(system, "_run_codex_json", fake_codex)
    client_config.write_text(
        json.dumps({"version": 1, "hosts": {"codex": {"server_url": "http://127.0.0.1:8000"}, "pi": {}}}),
        encoding="utf-8",
    )
    write_stored_authorization(credential_path("codex"), server_url="http://127.0.0.1:8000", value="test-token")

    for removal in ("removed", "not_set_up"):
        code, payload = _remove("codex")
        assert code == 0
        assert payload["hosts"]["codex"]["removal"] == removal

    assert installed == []
    assert marketplaces == [{"name": "team", "root": str(shared)}]
    assert not credential_path("codex").exists()
    assert _saved_hosts(client_config) == {"pi": {}}
    assert _doctor("codex")["checks"]["plugin"]["detail"] == "PowerContext plugin is not installed"


def _register_dsh(profile: Path, *packages: str) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    manifest = {"name": f"dsh-profile-{profile.name}", "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base"]}}}
    manifest["dependencies"] = {name: f"link:/packages/{name}" for name in packages}
    manifest["dsh"]["profile"]["bundles"] += list(packages)
    (profile / "package.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_dsh_remove_keeps_shared_state_while_another_profile_uses_it(
    tmp_path: Path, monkeypatch, client_config: Path
) -> None:
    from powercontext.cli import dsh, dsh_runtime
    from powercontext.cli.authorization import credential_path, write_stored_authorization

    home = tmp_path / "dsh"
    monkeypatch.setenv("DSH_HOME", str(home))
    web, desktop = home / "profiles" / "web", home / "profiles" / "desktop"
    _register_dsh(web, "powercontext-dsh", "powercontext-dsh-pet")
    _register_dsh(desktop, "powercontext-dsh")
    (web / "cordis.patch.yml").write_text("user: patch\n", encoding="utf-8")
    pet_state = web / ".powercontext-dsh-pet" / "bindings.json"
    pet_state.parent.mkdir()
    pet_state.write_text("{}", encoding="utf-8")
    skill = home / "skills" / "powercontext-project-context"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "<!-- POWERCONTEXT-GUIDANCE:START -->\nguidance\n<!-- POWERCONTEXT-GUIDANCE:END -->\n", encoding="utf-8"
    )
    client_config.write_text(json.dumps({"version": 1, "hosts": {"dsh": {"profile": "web"}}}), encoding="utf-8")
    write_stored_authorization(credential_path("dsh"), server_url="http://127.0.0.1:8000", value="test-token")
    removed_packages = []

    def fake_dsh(*arguments: str, executable: str | None = None) -> str:
        assert executable == "/usr/bin/dsh"
        match arguments:
            case ("plugin", "--profile", profile, "remove", name):
                manifest = json.loads((home / "profiles" / profile / "package.json").read_text(encoding="utf-8"))
                del manifest["dependencies"][name]  # pnpm fails for a package that is not a dependency.
                (home / "profiles" / profile / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
                removed_packages.append((profile, name))
            case _:
                raise AssertionError(arguments)
        return ""

    monkeypatch.setattr(dsh, "_run_dsh", fake_dsh)
    monkeypatch.setattr(dsh, "dsh_executable", lambda: "/usr/bin/dsh")
    # Desktop removal needs the Desktop-installed command, which this machine does not have.
    monkeypatch.setattr(
        dsh_runtime,
        "resolve_dsh_target",
        lambda profile, _command: dsh_runtime.DshTarget(profile, "/usr/bin/dsh" if profile == "desktop" else None),
    )

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "dsh", "--remove"])

    assert result.exit_code == 0, result.output
    assert "dsh: ok (removed)" in result.output
    assert "note: PowerContext is still registered in DSH profile desktop" in result.output
    assert "`powercontext setup dsh --remove --profile desktop`" in result.output
    assert removed_packages == [("web", "powercontext-dsh-pet"), ("web", "powercontext-dsh")]
    assert (web / "cordis.patch.yml").read_text(encoding="utf-8") == "user: patch\n"
    assert pet_state.is_file()
    assert skill.is_dir()
    assert credential_path("dsh").is_file()
    assert "dsh" in _saved_hosts(client_config)

    code, payload = _remove("dsh")

    assert code == 0
    assert payload["hosts"]["dsh"]["removal"] == "not_set_up"
    assert payload["hosts"]["dsh"]["result"]["remaining_profiles"] == ["desktop"]

    code, payload = _remove("dsh", "--profile", "desktop")

    assert code == 0
    assert payload["hosts"]["dsh"]["removal"] == "removed"
    assert "remaining_profiles" not in payload["hosts"]["dsh"]["result"]
    assert removed_packages[2:] == [("desktop", "powercontext-dsh")]
    assert not skill.exists()
    assert not credential_path("dsh").exists()
    assert "dsh" not in _saved_hosts(client_config)


def _save_connection(client_config: Path, host: str) -> None:
    from powercontext.cli.authorization import credential_path, write_stored_authorization

    client_config.write_text(
        json.dumps({"version": 1, "hosts": {host: {"server_url": "http://127.0.0.1:8000"}, "zcode": {}}}),
        encoding="utf-8",
    )
    write_stored_authorization(credential_path(host), server_url="http://127.0.0.1:8000", value="test-token")


def _unavailable(*arguments: object, **_options: object) -> Any:
    raise AssertionError(arguments)  # The host CLI is not on PATH, so nothing may run it.


@pytest.mark.parametrize(("agent", "cli"), [("codex", "Codex"), ("claude-code", "Claude Code"), ("pi", "Pi")])
def test_remove_forgets_the_connection_when_the_host_cli_is_gone(
    tmp_path: Path, monkeypatch, client_config: Path, agent: str, cli: str
) -> None:
    import powercontext.cli.pi as pi_cli
    from powercontext.cli import system
    from powercontext.cli.authorization import credential_path

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    monkeypatch.setattr(system, "_codex_path_executable", lambda: None)
    monkeypatch.setattr(system, "which", lambda _name: None)
    monkeypatch.setattr(pi_cli, "which", lambda _name: None)
    for module, name in ((system, "_run_codex_json"), (system, "_run_claude"), (pi_cli, "_run_pi")):
        monkeypatch.setattr(module, name, _unavailable)
    package = tmp_path / "data" / "plugins" / "pi"
    package.mkdir(parents=True)
    (package / ".powercontext-owned").write_text("", encoding="utf-8")
    _save_connection(client_config, agent)

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", agent, "--remove"])

    assert result.exit_code == 0, result.output
    assert f"{agent}: ok (removed; host registration not checked)" in result.output
    assert f"note: {cli} CLI is not on PATH" in result.output
    assert not credential_path(agent).exists()
    assert _saved_hosts(client_config) == {"zcode": {}}
    assert package.exists() is (agent != "pi")

    code, payload = _remove(agent)

    assert code == 0
    row = payload["hosts"][agent]
    assert (row["removal"], row["host_registration"]) == ("not_set_up", "not_checked")
    assert row["result"]["notes"][0].startswith(f"{cli} CLI is not on PATH")


def test_claude_code_remove_without_its_cli_keeps_plugin_options(
    tmp_path: Path, monkeypatch, client_config: Path, server_checks
) -> None:
    from powercontext.cli import system

    claude_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setenv("POWERCONTEXT_CLAUDE_AUTHORIZATION", "test-token")
    monkeypatch.setattr(system, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(system, "_run_claude", _FakeClaude(tmp_path / "powercontext" / "plugins" / "claude-code"))
    setup = CliRunner().invoke(create_cli([setup_app]), ["setup", "claude-code", "--source", str(REPOSITORY)])
    assert setup.exit_code == 0, setup.output
    options = json.loads((claude_home / "settings.json").read_text(encoding="utf-8"))["pluginConfigs"]
    monkeypatch.setattr(system, "which", lambda _name: None)
    monkeypatch.setattr(system, "_run_claude", _unavailable)

    code, payload = _remove("claude-code")

    assert code == 0
    assert payload["hosts"]["claude-code"]["host_registration"] == "not_checked"
    settings = json.loads((claude_home / "settings.json").read_text(encoding="utf-8"))
    assert "statusLine" not in settings
    assert settings["pluginConfigs"] == options


def test_dsh_remove_without_its_command_forgets_the_connection(
    tmp_path: Path, monkeypatch, client_config: Path
) -> None:
    from powercontext.cli import dsh
    from powercontext.cli.authorization import credential_path, write_stored_authorization

    home = tmp_path / "dsh"
    monkeypatch.setenv("DSH_HOME", str(home))
    web = home / "profiles" / "web"
    _register_dsh(web, "powercontext-dsh", "powercontext-dsh-pet")
    skill = home / "skills" / "powercontext-project-context"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "<!-- POWERCONTEXT-GUIDANCE:START -->\nguidance\n<!-- POWERCONTEXT-GUIDANCE:END -->\n", encoding="utf-8"
    )
    client_config.write_text(json.dumps({"version": 1, "hosts": {"dsh": {"profile": "web"}}}), encoding="utf-8")
    write_stored_authorization(credential_path("dsh"), server_url="http://127.0.0.1:8000", value="test-token")

    def missing() -> str:
        raise SetupError("DeepSeek Harness CLI is not installed or is not on PATH.")  # noqa: TRY003

    removed_packages = []

    def fake_dsh(*arguments: str, executable: str | None = None) -> str:
        match arguments:
            case ("plugin", "--profile", profile, "remove", name):
                manifest = json.loads((home / "profiles" / profile / "package.json").read_text(encoding="utf-8"))
                del manifest["dependencies"][name]
                (home / "profiles" / profile / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
                removed_packages.append((profile, name))
            case _:
                raise AssertionError(arguments)
        return ""

    monkeypatch.setattr(dsh, "_run_dsh", fake_dsh)
    monkeypatch.setattr(dsh, "dsh_executable", missing)

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "dsh", "--remove"])

    assert result.exit_code == 0, result.output
    assert "dsh: ok (removed; host registration not checked)" in result.output
    assert (
        "`dsh plugin --profile web remove powercontext-dsh-pet` and `dsh plugin --profile web remove powercontext-dsh`"
        in result.output
    )
    assert removed_packages == []
    assert not skill.exists()
    assert not credential_path("dsh").exists()
    assert "dsh" not in _saved_hosts(client_config)

    monkeypatch.setattr(dsh, "dsh_executable", lambda: "/usr/bin/dsh")
    code, payload = _remove("dsh", "--profile", "web")

    assert code == 0
    row = payload["hosts"]["dsh"]
    assert (row["removal"], row["host_registration"]) == ("removed", "checked")
    assert removed_packages == [("web", "powercontext-dsh-pet"), ("web", "powercontext-dsh")]


def test_unsupported_agent_fails_without_blocking_other_removals(
    tmp_path: Path, monkeypatch, client_config: Path
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))
    client_config.write_text(json.dumps({"version": 1, "hosts": {"hermes": {}, "workbuddy": {}}}), encoding="utf-8")

    code, payload = _remove("hermes", "workbuddy")

    assert code == 1
    assert payload["ok"] is False
    assert payload["hosts"]["hermes"]["status"] == "failed"
    assert "Automatic removal is not available for hermes" in payload["hosts"]["hermes"]["error"]
    assert payload["hosts"]["workbuddy"]["removal"] == "removed"
    assert _saved_hosts(client_config) == {"hermes": {}}


@pytest.mark.parametrize(
    "arguments",
    [
        ["setup", "--remove", "--json"],
        ["setup", "codex", "--remove", "--server-url", "https://memory.example"],
        ["setup", "codex", "--remove", "--source", "oceanbase/powercontext"],
        ["setup", "codex", "--remove", "--no-capture-prompts"],
        ["setup", "kimi-code", "--remove", "--workspace-scope"],
        ["setup", "codex", "--remove", "--env-file", ".env"],
        ["setup", "codex", "--remove", "--profile", "web"],
        ["setup", "dsh", "--remove", "--profile", "mobile"],
    ],
)
def test_remove_rejects_missing_agents_and_setup_options(arguments: list[str], client_config: Path) -> None:
    result = CliRunner().invoke(create_cli([setup_app]), arguments)

    assert result.exit_code == 2
    if "--no-capture-prompts" in arguments:
        assert "--no-capture-prompts cannot be combined with --remove" in result.output
    assert not client_config.exists()
