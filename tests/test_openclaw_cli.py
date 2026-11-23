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
import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

import powercontext.cli.openclaw as openclaw_cli
from powercontext.cli.app import create_cli
from powercontext.cli.installation import setup as install_plugin
from powercontext.cli.system import DiagnosticStatus, SetupError, doctor_app


def _write_openclaw_plugin(root: Path) -> Path:
    plugin = root / "integrations" / "openclaw" / "plugins" / "memory-powercontext"
    plugin.mkdir(parents=True)
    (plugin / "package.json").write_text(
        json.dumps({"name": openclaw_cli.OPENCLAW_PACKAGE_NAME}),
        encoding="utf-8",
    )
    return plugin


def test_configure_openclaw_changes_only_connection_settings(monkeypatch):
    commands = []
    monkeypatch.setattr(openclaw_cli, "run_openclaw", lambda *args: commands.append(args))
    openclaw_cli.configure_openclaw(
        executable="openclaw", server_url="https://memory.example", allow_insecure_http=False
    )
    settings = [item for command in commands for item in json.loads(command[-1])]
    assert settings == [
        {"path": "plugins.entries.memory-powercontext.config.endpoint", "value": "https://memory.example"},
        {"path": "plugins.entries.memory-powercontext.config.allowInsecureHttp", "value": False},
    ]


@pytest.mark.parametrize("failure", [None, "local_write", "native_rejection"])
def test_config_agent_openclaw_keeps_connection_settings_consistent(
    tmp_path: Path, monkeypatch, failure: str | None
) -> None:
    from powercontext.cli import installation
    from powercontext.cli.config import app as config_app

    clients = tmp_path / "clients.json"
    previous = {"version": 1, "hosts": {"openclaw": {"server_url": "https://old.example"}}}
    clients.write_text(json.dumps(previous), encoding="utf-8")
    native = tmp_path / "openclaw.json"
    native.write_text(json.dumps({"endpoint": "https://old.example"}), encoding="utf-8")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(clients))
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "openclaw")

    def native_command(_executable, *arguments):
        if arguments[:2] == ("config", "set"):
            if failure == "native_rejection":
                message = "Native configuration rejected"
                raise SetupError(message)
            settings = json.loads(native.read_text(encoding="utf-8"))
            for update in json.loads(arguments[-1]):
                settings[update["path"].rsplit(".", 1)[1]] = update["value"]
            native.write_text(json.dumps(settings), encoding="utf-8")
        return CompletedProcess([], 0, "{}", "")

    monkeypatch.setattr(openclaw_cli, "run_openclaw", native_command)
    write_bytes = installation.write_bytes

    def write(path, value):
        if failure == "local_write" and path == clients:
            message = "Connection file is read-only"
            raise PermissionError(message)
        write_bytes(path, value)

    monkeypatch.setattr(installation, "write_bytes", write)
    result = CliRunner().invoke(
        create_cli([config_app]),
        ["config", "agent", "openclaw", "--server-url", "https://new.example", "--json"],
    )

    assert result.exit_code == (1 if failure else 0), result.output
    expected = "https://old.example" if failure else "https://new.example"
    assert json.loads(native.read_text(encoding="utf-8"))["endpoint"] == expected
    assert json.loads(clients.read_text(encoding="utf-8"))["hosts"]["openclaw"]["server_url"] == expected


# A fake host with the native semantics of OpenClaw 2026.8.1 (ea80657) and 2026.8.1-beta.2: unless installation is
# given --no-enable, it removes the plugin from plugins.deny, appends it to a non-empty plugins.allow and, unless the
# entry or all plugins are disabled, enables the entry and selects it for the memory slot.
_FAKE_OPENCLAW = r"""
import json
import os
import pathlib
import sys

state = pathlib.Path(os.environ["OPENCLAW_STATE_DIR"])
config_file = state / "openclaw.json"
config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else {}
arguments = sys.argv[1:]
with (state / "commands.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps(arguments) + "\n")
plugin_id = "memory-powercontext"
options = ["--force", "--link", "--pin"]
options += ["--no-enable"] if os.environ.get("FAKE_OPENCLAW_NO_ENABLE") else ["--accept-capabilities"]


def save():
    config_file.write_text(json.dumps(config), encoding="utf-8")


def node(path, create=False):
    current = config
    for key in path:
        if not isinstance(current, dict) or (key not in current and not create):
            return None
        current = current.setdefault(key, {}) if create else current[key]
    return current


if arguments == ["--version"]:
    print(os.environ["FAKE_OPENCLAW_VERSION"])
elif arguments == ["plugins", "install", "--help"]:
    print("Usage: openclaw plugins install [options] <path-or-spec-or-plugin>\n\nOptions:")
    print("\n".join("  " + option for option in sorted(options + ["-h, --help"])))
elif arguments[:2] == ["config", "get"]:
    value = node(arguments[2].split("."))
    if value is None:
        print(json.dumps({"ok": False, "error": {"message": f"Config path is valid but unset: {arguments[2]}."}}))
        sys.exit(1)
    print(json.dumps(value))
elif arguments[:3] == ["config", "set", "--batch-json"]:
    for update in json.loads(arguments[3]):
        *parents, leaf = update["path"].split(".")
        node(parents, create=True)[leaf] = update["value"]
    save()
elif arguments[:2] == ["plugins", "install"]:
    *flags, archive = arguments[2:]
    if not set(flags) <= set(options) or not pathlib.Path(archive).is_file():
        sys.exit(f"unsupported install: {arguments}")
    plugins = config.setdefault("plugins", {})
    if "--no-enable" not in flags:
        if "--accept-capabilities" not in flags:
            sys.exit(f'Plugin "{plugin_id}" requires capability consent.')
        if plugins.get("allow") and plugin_id not in plugins["allow"]:
            plugins["allow"].append(plugin_id)
        if plugin_id in plugins.get("deny", []):
            plugins["deny"].remove(plugin_id)
        entry = plugins.setdefault("entries", {}).setdefault(plugin_id, {})
        if entry.get("enabled") is not False and plugins.get("enabled") is not False:
            entry["enabled"] = True
            plugins.setdefault("slots", {})["memory"] = plugin_id
    (state / "extensions" / plugin_id).mkdir(parents=True, exist_ok=True)
    # Lets a test change the policy as a user would between installation and activation.
    for update in json.loads(os.environ.get("FAKE_OPENCLAW_USER_UPDATES_AFTER_INSTALL", "[]")):
        *parents, leaf = update["path"].split(".")
        node(parents, create=True)[leaf] = update["value"]
    save()
elif arguments[:3] == ["plugins", "inspect", plugin_id]:
    print(json.dumps({"install": {"installPath": str(state / "extensions" / plugin_id)}}))
elif arguments == ["plugins", "enable", plugin_id]:
    node(["plugins", "entries", plugin_id], create=True)["enabled"] = True
    save()
elif arguments != ["gateway", "restart"]:
    sys.exit(f"unexpected command: {arguments}")
"""

_FAKE_PNPM = r"""
import json
import os
import pathlib
import sys

with (pathlib.Path(os.environ["OPENCLAW_STATE_DIR"]) / "commands.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps(["pnpm", *sys.argv[1:]]) + "\n")
plugin = pathlib.Path(sys.argv[sys.argv.index("--dir") + 1])
if "build" in sys.argv:
    (plugin / "dist").mkdir(exist_ok=True)
    (plugin / "dist" / "index.js").write_text("export {};\n", encoding="utf-8")
    # Lets a test change the OpenClaw policy as a user would while the plugin builds.
    updates = json.loads(os.environ.get("FAKE_PNPM_USER_UPDATES_DURING_BUILD", "[]"))
    if updates:
        config_file = pathlib.Path(os.environ["OPENCLAW_STATE_DIR"]) / "openclaw.json"
        config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else {}
        for update in updates:
            current = config
            *parents, leaf = update["path"].split(".")
            for key in parents:
                current = current.setdefault(key, {})
            current[leaf] = update["value"]
        config_file.write_text(json.dumps(config), encoding="utf-8")
if "pack" in sys.argv:
    (pathlib.Path(sys.argv[-1]) / "plugin.tgz").write_bytes(b"archive")
"""

_OPENCLAW_2026_8_1 = "OpenClaw 2026.8.1 (ea80657)"


def _install_executable(directory: Path, name: str, source: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / name
    script.write_text("#!/usr/bin/env python3\n" + source, encoding="utf-8", newline="\n")
    script.chmod(0o755)
    if os.name == "nt":
        (directory / f"{name}.cmd").write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")


@pytest.fixture
def openclaw_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Put a fake OpenClaw host and pnpm on PATH and return the host's command log reader and state paths."""

    state = tmp_path / "openclaw-state"
    state.mkdir()
    bin_dir = tmp_path / "bin"
    _install_executable(bin_dir, "openclaw", _FAKE_OPENCLAW)
    _install_executable(bin_dir, "pnpm", _FAKE_PNPM)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state))
    monkeypatch.setenv("FAKE_OPENCLAW_VERSION", _OPENCLAW_2026_8_1)
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("FAKE_OPENCLAW_NO_ENABLE", raising=False)
    monkeypatch.delenv("FAKE_OPENCLAW_USER_UPDATES_AFTER_INSTALL", raising=False)
    monkeypatch.delenv("FAKE_PNPM_USER_UPDATES_DURING_BUILD", raising=False)
    config = state / "openclaw.json"

    def commands() -> list[list[str]]:
        log = state / "commands.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
        return [json.loads(line) for line in lines]

    return config, commands


@pytest.mark.parametrize(
    ("version", "no_enable", "install_flags"),
    [
        (_OPENCLAW_2026_8_1, False, ["--force", "--accept-capabilities"]),
        ("OpenClaw 2026.12.0", True, ["--force", "--no-enable"]),
    ],
    ids=["without-no-enable", "with-no-enable"],
)
def test_setup_openclaw_installs_the_archive_and_activates_it_on_supported_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, openclaw_host, version, no_enable, install_flags
) -> None:
    config, commands = openclaw_host
    monkeypatch.setenv("FAKE_OPENCLAW_VERSION", version)
    if no_enable:
        monkeypatch.setenv("FAKE_OPENCLAW_NO_ENABLE", "1")
    plugin = _write_openclaw_plugin(tmp_path / "checkout")

    result = install_plugin("openclaw", source=str(plugin), server_url="http://127.0.0.1:8765/")

    assert result == {
        "plugin": "memory-powercontext",
        "plugin_path": str(config.parent / "extensions" / "memory-powercontext"),
        "server_url": "http://127.0.0.1:8765",
        "data_dir": str(tmp_path / "data"),
    }
    installs = [command[2:] for command in commands() if command[:2] == ["plugins", "install"]]
    installs.remove(["--help"])
    assert [command[:-1] for command in installs] == [install_flags]
    assert installs[0][-1].endswith(".tgz")
    assert ["gateway", "restart"] in commands()
    native = json.loads(config.read_text(encoding="utf-8"))
    assert native["plugins"]["slots"] == {"memory": "memory-powercontext"}
    assert native["plugins"]["entries"]["memory-powercontext"] == {
        "enabled": True,
        "config": {
            "endpoint": "http://127.0.0.1:8765",
            "allowInsecureHttp": False,
            "autoRecall": True,
            "autoCapture": True,
        },
        "hooks": {"allowConversationAccess": True},
    }
    assert native["tools"]["alsoAllow"] == list(openclaw_cli.POWERCONTEXT_TOOLS)


@pytest.mark.parametrize(
    ("plugins", "overridden"),
    [
        ({"deny": ["memory-powercontext"]}, "plugins.deny"),
        ({"allow": ["memory-core"]}, "plugins.allow"),
        ({"slots": {"memory": "memory-core"}}, "plugins.slots.memory"),
    ],
)
def test_setup_openclaw_without_no_enable_refuses_to_override_native_policy(
    tmp_path: Path, openclaw_host, plugins, overridden
) -> None:
    config, commands = openclaw_host
    config.write_text(json.dumps({"plugins": plugins}), encoding="utf-8")
    before = config.read_text(encoding="utf-8")

    with pytest.raises(SetupError, match=rf"would override {overridden}; setup changed nothing"):
        install_plugin(
            "openclaw", source=str(_write_openclaw_plugin(tmp_path / "checkout")), server_url="http://127.0.0.1:8765"
        )

    assert config.read_text(encoding="utf-8") == before
    assert [command for command in commands() if command[0] == "pnpm" or command[:2] == ["plugins", "install"]] == [
        ["plugins", "install", "--help"]
    ]


@pytest.mark.parametrize(
    "plugins",
    [
        {"entries": {"memory-powercontext": {"enabled": False}}, "slots": {"memory": "memory-core"}},
        {"enabled": False, "slots": {"memory": "memory-core"}},
    ],
)
def test_setup_openclaw_without_no_enable_installs_a_disabled_plugin_without_activating_it(
    tmp_path: Path, openclaw_host, plugins
) -> None:
    config, commands = openclaw_host
    config.write_text(json.dumps({"plugins": plugins}), encoding="utf-8")

    install_plugin(
        "openclaw", source=str(_write_openclaw_plugin(tmp_path / "checkout")), server_url="http://127.0.0.1:8765"
    )

    native = json.loads(config.read_text(encoding="utf-8"))["plugins"]
    assert native.get("enabled") == plugins.get("enabled")
    assert native["slots"] == {"memory": "memory-core"}
    assert native["entries"]["memory-powercontext"].get("enabled") is plugins.get("entries", {}).get(
        "memory-powercontext", {}
    ).get("enabled")
    assert ["plugins", "enable", "memory-powercontext"] not in commands()
    assert ["gateway", "restart"] not in commands()


@pytest.mark.parametrize(
    "plugins",
    [
        {"entries": {"memory-powercontext": {"enabled": False}}},
        {"deny": ["memory-powercontext"]},
        {"allow": ["memory-core"]},
        {"slots": {"memory": "memory-core"}},
    ],
)
def test_setup_openclaw_with_no_enable_installs_without_overriding_native_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, openclaw_host, plugins
) -> None:
    config, commands = openclaw_host
    monkeypatch.setenv("FAKE_OPENCLAW_VERSION", "OpenClaw 2026.12.0")
    monkeypatch.setenv("FAKE_OPENCLAW_NO_ENABLE", "1")
    config.write_text(json.dumps({"plugins": plugins}), encoding="utf-8")

    install_plugin(
        "openclaw", source=str(_write_openclaw_plugin(tmp_path / "checkout")), server_url="http://127.0.0.1:8765"
    )

    native = json.loads(config.read_text(encoding="utf-8"))["plugins"]
    entry = native.pop("entries")["memory-powercontext"]
    assert entry.pop("config") == {"endpoint": "http://127.0.0.1:8765", "allowInsecureHttp": False}
    assert entry == plugins.get("entries", {}).get("memory-powercontext", {})
    assert native == {key: value for key, value in plugins.items() if key != "entries"}
    assert ["plugins", "enable", "memory-powercontext"] not in commands()
    assert ["gateway", "restart"] not in commands()


@pytest.mark.parametrize("no_enable", [False, True], ids=["without-no-enable", "with-no-enable"])
@pytest.mark.parametrize(
    "variable", ["FAKE_PNPM_USER_UPDATES_DURING_BUILD", "FAKE_OPENCLAW_USER_UPDATES_AFTER_INSTALL"]
)
def test_setup_openclaw_keeps_a_plugin_the_user_disabled_during_setup_inactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, openclaw_host, no_enable, variable
) -> None:
    config, commands = openclaw_host
    if no_enable:
        monkeypatch.setenv("FAKE_OPENCLAW_VERSION", "OpenClaw 2026.12.0")
        monkeypatch.setenv("FAKE_OPENCLAW_NO_ENABLE", "1")
    monkeypatch.setenv(variable, json.dumps([{"path": "plugins.entries.memory-powercontext.enabled", "value": False}]))

    install_plugin(
        "openclaw", source=str(_write_openclaw_plugin(tmp_path / "checkout")), server_url="http://127.0.0.1:8765"
    )

    native = json.loads(config.read_text(encoding="utf-8"))
    assert native["plugins"]["entries"]["memory-powercontext"]["enabled"] is False
    # Only an activating install that ran before the user's change selects the slot; setup itself never does.
    installed_first = not no_enable and variable == "FAKE_OPENCLAW_USER_UPDATES_AFTER_INSTALL"
    assert native["plugins"].get("slots") == ({"memory": "memory-powercontext"} if installed_first else None)
    assert "tools" not in native
    assert ["plugins", "enable", "memory-powercontext"] not in commands()
    assert ["gateway", "restart"] not in commands()


def test_setup_openclaw_without_no_enable_rechecks_native_policy_after_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, openclaw_host
) -> None:
    config, commands = openclaw_host
    monkeypatch.setenv(
        "FAKE_PNPM_USER_UPDATES_DURING_BUILD", json.dumps([{"path": "plugins.slots.memory", "value": "memory-core"}])
    )

    with pytest.raises(SetupError, match=r"would override plugins.slots.memory; setup changed nothing"):
        install_plugin(
            "openclaw", source=str(_write_openclaw_plugin(tmp_path / "checkout")), server_url="http://127.0.0.1:8765"
        )

    assert json.loads(config.read_text(encoding="utf-8")) == {"plugins": {"slots": {"memory": "memory-core"}}}
    assert [command for command in commands() if command[:2] == ["plugins", "install"]] == [
        ["plugins", "install", "--help"]
    ]


@pytest.mark.parametrize(
    ("plugins", "path"),
    [
        ({"entries": None}, "plugins.entries"),
        ({"slots": ["memory-core"]}, "plugins.slots"),
        ({"allow": "memory-powercontext-extra"}, "plugins.allow"),
        ({"deny": None}, "plugins.deny"),
        ({"entries": {"memory-powercontext": None}}, "plugins.entries.memory-powercontext"),
        ({"entries": {"memory-powercontext": {"config": None}}}, "plugins.entries.memory-powercontext.config"),
    ],
)
def test_setup_openclaw_rejects_malformed_native_plugin_policy_before_installing(
    tmp_path: Path, openclaw_host, plugins, path
) -> None:
    config, commands = openclaw_host
    config.write_text(json.dumps({"plugins": plugins}), encoding="utf-8")

    with pytest.raises(SetupError, match=rf"OpenClaw {re.escape(path)} must be an"):
        install_plugin(
            "openclaw", source=str(_write_openclaw_plugin(tmp_path / "checkout")), server_url="http://127.0.0.1:8765"
        )

    assert not [command for command in commands() if command[0] == "pnpm" or command[:2] == ["plugins", "install"]]


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
        newline="\n",
    )
    fake_pnpm.chmod(0o755)
    if os.name == "nt":
        launcher = tmp_path / "pnpm.cmd"
        launcher.write_text(f'@"{sys.executable}" "{fake_pnpm}" %*\n', encoding="utf-8")
        fake_pnpm = launcher
    monkeypatch.setattr(openclaw_cli, "pnpm_executable", lambda: str(fake_pnpm))
    monkeypatch.setenv("CI", "false")

    openclaw_cli.build_openclaw_plugin(plugin)

    assert (plugin / "dist" / "index.js").is_file()


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

    diagnostics = openclaw_cli.OpenClawInstaller().verify()

    assert diagnostics["openclaw"].status is DiagnosticStatus.OK
    assert diagnostics["plugin"].status is DiagnosticStatus.OK
    assert "installed, enabled and selected" in diagnostics["plugin"].detail
    assert diagnostics["plugin"].checks is not None
    assert diagnostics["plugin"].checks["running_host_configuration"] == "not_observed"


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
        if command[1:3] == ["plugins", "inspect"]:
            return CompletedProcess(command, 0, "{}", "")
        raise AssertionError(command)

    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    diagnostics = openclaw_cli.OpenClawInstaller().verify()

    assert diagnostics["plugin"].status is DiagnosticStatus.OK


def test_run_openclaw_diagnostics_reports_missing_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    run_process = Mock(
        return_value=CompletedProcess(
            _OPENCLAW_PLUGIN_LIST_COMMAND, 0, _openclaw_plugin_list_output(plugin_id=None), ""
        )
    )
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    diagnostics = openclaw_cli.OpenClawInstaller().verify()

    assert diagnostics["openclaw"].status is DiagnosticStatus.OK
    assert diagnostics["plugin"].status is DiagnosticStatus.FAILED
    assert (
        diagnostics["plugin"].detail == "PowerContext OpenClaw plugin is not enabled and selected as the memory plugin"
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

    diagnostics = openclaw_cli.OpenClawInstaller().verify()

    assert diagnostics["openclaw"].status is DiagnosticStatus.OK
    assert diagnostics["plugin"].status is DiagnosticStatus.FAILED


def test_run_openclaw_diagnostics_reports_missing_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_executable() -> str:
        message = "OpenClaw CLI is not installed or is not on PATH."
        raise SetupError(message)

    monkeypatch.setattr(openclaw_cli, "openclaw_executable", missing_executable)

    diagnostics = openclaw_cli.OpenClawInstaller().verify()

    assert diagnostics["openclaw"].status is DiagnosticStatus.FAILED
    assert diagnostics["plugin"].status is DiagnosticStatus.SKIPPED


def test_doctor_openclaw_reports_an_installed_plugin_as_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server_checks
) -> None:
    monkeypatch.setenv("POWERCONTEXT_OPENCLAW_BASE_URL", "http://127.0.0.1:8000")
    run_process = Mock(
        return_value=CompletedProcess(_OPENCLAW_PLUGIN_LIST_COMMAND, 0, _openclaw_plugin_list_output(), "")
    )
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "openclaw", "--json"])

    assert result.exit_code == 1
    report = json.loads(result.output)["hosts"]["openclaw"]
    assert report["checks"]["openclaw"]["ok"] is True
    assert report["checks"]["plugin"]["ok"] is True
    assert report["checks"]["plugin_memory_protocol"]["status"] == "degraded"
    assert "powercontext setup openclaw" in report["checks"]["plugin_memory_protocol"]["detail"]


def test_doctor_openclaw_exits_nonzero_when_plugin_is_missing(monkeypatch: pytest.MonkeyPatch, server_checks) -> None:
    run_process = Mock(
        return_value=CompletedProcess(
            _OPENCLAW_PLUGIN_LIST_COMMAND, 0, _openclaw_plugin_list_output(plugin_id=None), ""
        )
    )
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "/usr/bin/openclaw")
    monkeypatch.setattr(openclaw_cli, "run_process", run_process)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "openclaw"])

    assert result.exit_code == 1


@pytest.mark.parametrize(
    "plugins",
    [
        {"enabled": False},
        {"entries": {"memory-powercontext": {"enabled": False}}},
        {"deny": ["memory-powercontext"]},
        {"allow": ["other-plugin"]},
        {"slots": {"memory": "other-memory-provider"}},
    ],
)
def test_openclaw_activation_preserves_existing_native_choices(monkeypatch, plugins):
    from copy import deepcopy

    before = deepcopy(plugins)
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "openclaw")
    monkeypatch.setattr(
        openclaw_cli, "read_config_value", lambda _command, path: plugins if path == "plugins" else None
    )

    def native(executable, *arguments):
        if arguments[:2] == ("plugins", "enable"):
            plugins.setdefault("entries", {}).setdefault("memory-powercontext", {})["enabled"] = True
        return CompletedProcess([], 0, "", "")

    monkeypatch.setattr(openclaw_cli, "run_openclaw", native)
    openclaw_cli.OpenClawInstaller().enable()
    assert plugins == before


def test_openclaw_config_read_failure_is_not_an_absent_setting(monkeypatch):
    monkeypatch.setattr(
        openclaw_cli, "run_process", lambda *args, **kwargs: CompletedProcess([], 1, "", "configuration is invalid")
    )
    with pytest.raises(SetupError, match="Cannot read OpenClaw configuration"):
        openclaw_cli.read_config_value("openclaw", "plugins")


def test_openclaw_repeat_activation_preserves_removed_native_tool_grants(monkeypatch):
    plugins = {
        "enabled": True,
        "slots": {"memory": "memory-powercontext"},
        "entries": {
            "memory-powercontext": {
                "enabled": True,
                "config": {"autoRecall": False, "autoCapture": False},
                "hooks": {"allowConversationAccess": False},
            }
        },
    }
    allowed = ["powercontext_memory_get"]
    monkeypatch.setattr(openclaw_cli, "openclaw_executable", lambda: "openclaw")
    monkeypatch.setattr(
        openclaw_cli,
        "read_config_value",
        lambda _executable, path: {"plugins": plugins, "tools.alsoAllow": allowed, "gateway.mode": "local"}[path],
    )

    def native(*_args):
        pytest.fail("Repeated setup must not rewrite the existing native activation policy")

    monkeypatch.setattr(openclaw_cli, "run_openclaw", native)
    openclaw_cli.OpenClawInstaller().enable()


def test_openclaw_recognizes_native_json_unset_without_treating_other_errors_as_absence(monkeypatch):
    output = {
        "ok": False,
        "error": {
            "type": "cli_error",
            "message": "Config path is valid but unset: gateway.mode. The runtime default applies.",
        },
    }
    monkeypatch.setattr(
        openclaw_cli, "run_process", lambda *args, **kwargs: CompletedProcess([], 1, json.dumps(output), "")
    )
    assert openclaw_cli.read_config_value("openclaw", "gateway.mode") is None
    output["error"]["message"] = "Unknown config path: gateway.mode."
    with pytest.raises(SetupError, match="Cannot read OpenClaw configuration"):
        openclaw_cli.read_config_value("openclaw", "gateway.mode")
