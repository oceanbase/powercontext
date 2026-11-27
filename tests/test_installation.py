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


"""Failures across the full public installation lifecycle retain recoverable state."""

import json
import shutil
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from typer.testing import CliRunner

from powercontext.cli import openclaw
from powercontext.cli.app import create_cli
from powercontext.cli.installation import SetupError, setup

ROOT = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    monkeypatch.chdir(tmp_path)


def test_failed_openclaw_rebuild_keeps_the_native_managed_bundle(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "package.json").write_text('{"name":"@oceanbase/openclaw-memory-powercontext"}', encoding="utf-8")
    monkeypatch.setattr(openclaw, "openclaw_executable", lambda: "openclaw")
    monkeypatch.setattr(openclaw, "require_supported_openclaw", lambda _executable: None)
    monkeypatch.setattr(openclaw, "openclaw_install_flags", lambda *_args: ("--force", "--no-enable"))
    monkeypatch.setattr(openclaw, "configure_openclaw", lambda **_kwargs: None)
    monkeypatch.setattr(
        openclaw,
        "read_config_value",
        lambda _executable, path: (
            {"entries": {"memory-powercontext": {"enabled": False}}} if path == "plugins" else "memory-powercontext"
        ),
    )
    loaded = json.dumps({
        "plugins": [{"id": "memory-powercontext", "enabled": True, "status": "loaded", "memorySlotSelected": True}]
    })
    managed = tmp_path / "native-managed"

    def native_command(*args):
        if "install" in args:
            shutil.copytree(Path(args[-1]).parent / "plugin", managed)
        output = json.dumps({"install": {"installPath": str(managed)}}) if "inspect" in args else loaded
        return CompletedProcess([], 0, output, "")

    monkeypatch.setattr(openclaw, "run_openclaw", native_command)
    monkeypatch.setattr(openclaw, "pnpm_executable", lambda: "pnpm")
    monkeypatch.setattr(
        openclaw, "run_process", lambda command, **kwargs: (Path(command[-1]) / "plugin.tgz").write_bytes(b"archive")
    )

    def build(candidate):
        (candidate / "dist").mkdir()
        (candidate / "dist/index.js").write_bytes(b"working bundle")

    monkeypatch.setattr(openclaw, "build_openclaw_plugin", build)
    first = setup("openclaw", source=str(source))
    linked = Path(first["plugin_path"])

    def fail_build(candidate):
        shutil.rmtree(candidate / "dist", ignore_errors=True)
        raise SetupError("build failed")  # noqa: TRY003

    monkeypatch.setattr(openclaw, "build_openclaw_plugin", fail_build)
    with pytest.raises(SetupError, match="build failed"):
        setup("openclaw", source=str(source))
    assert (linked / "dist/index.js").read_bytes() == b"working bundle"


def test_connection_can_be_configured_without_reinstalling(tmp_path, monkeypatch):
    from powercontext.cli import authorization, workbuddy
    from powercontext.cli.installation import PluginSource
    from powercontext.cli.transport import SetupTransport
    from powercontext.client.transport_policy import load_client_settings

    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))
    monkeypatch.setattr(authorization, "setup_authorization_value", lambda _host: None)
    installer = workbuddy.WorkBuddyInstaller()
    installed = installer.install(PluginSource(str(ROOT)))
    hook = Path(installed["hooks_dir"]) / "workbuddy_powercontext_hook.py"
    hook.write_bytes(hook.read_bytes() + b"\n# local hook edit\n")
    original = hook.read_bytes()
    installer.configure(SetupTransport("workbuddy", "https://memory.example", False))
    assert hook.read_bytes() == original
    assert load_client_settings("workbuddy")["server_url"] == "https://memory.example"
    mcp = json.loads((Path(installed["workbuddy_home"]) / "mcp.json").read_text())
    assert "https://memory.example" in mcp["mcpServers"]["powercontext"]["url"]


@pytest.mark.parametrize("fail", [False, True], ids=["success", "rollback"])
def test_connection_readers_keep_a_configured_endpoint_during_update(
    tmp_path, monkeypatch, installed_agent_checks, fail
):
    from powercontext.cli import installation
    from powercontext.client.transport_policy import load_client_settings

    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))
    cli = create_cli()
    runner = CliRunner()
    result = runner.invoke(cli, ["setup", "workbuddy", "--source", str(ROOT), "--server-url", "https://old.example"])
    assert result.exit_code == 0, result.output
    client = tmp_path / "clients.json"
    observed = []
    write, replace = installation.write_bytes, installation.os.replace

    def read_endpoint():
        observed.append(load_client_settings("workbuddy").get("server_url"))

    def write_connection(path, value):
        if path == client:
            read_endpoint()
        write(path, value)
        if path == client and fail:
            raise OSError("connection write failed")  # noqa: TRY003

    def replace_file(source, target):
        if target == client:
            read_endpoint()
        replace(source, target)
        if target == client:
            read_endpoint()

    monkeypatch.setattr(installation, "write_bytes", write_connection)
    monkeypatch.setattr(installation.os, "replace", replace_file)
    result = runner.invoke(cli, ["config", "agent", "workbuddy", "--server-url", "https://new.example"])
    assert result.exit_code == int(fail), result.output
    assert set(observed) <= {"https://old.example", "https://new.example"}
    assert load_client_settings("workbuddy")["server_url"] == ("https://old.example" if fail else "https://new.example")


@pytest.mark.parametrize("host", ["kimi-code", "zcode", "workbuddy"])
def test_failed_connection_publication_keeps_previous_configuration(host, tmp_path, monkeypatch):
    from powercontext.cli import installation, kimi_code, workbuddy, zcode
    from powercontext.cli.installation import PluginSource
    from powercontext.cli.transport import SetupTransport

    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi"))
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))
    monkeypatch.setattr(kimi_code, "kimi_executable", lambda: "kimi")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: tmp_path / "zcode/config.json")
    installer = installation.installers()[host](**({"scope_id": "scope"} if host == "kimi-code" else {}))
    installer.install(PluginSource(str(ROOT)))
    installer.configure(SetupTransport(host, "https://old.example", False))
    roots = {
        "kimi-code": kimi_code.kimi_home(),
        "zcode": zcode.zcode_config_file().parent,
        "workbuddy": workbuddy.workbuddy_home(),
    }
    root = roots[host]
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    client = tmp_path / "clients.json"
    original = client.read_bytes()
    write = installation.write_bytes

    def fail_connection(path, value):
        if path == client:
            raise OSError("client configuration write failed")  # noqa: TRY003
        write(path, value)

    monkeypatch.setattr(installation, "write_bytes", fail_connection)
    with pytest.raises(OSError, match="client configuration write failed"):
        installer.configure(SetupTransport(host, "https://new.example", False))
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert client.read_bytes() == original
