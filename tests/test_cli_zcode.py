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
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from powercontext.cli import git_source, installation, zcode
from powercontext.cli.app import create_cli
from powercontext.cli.installation import setup as install_plugin
from powercontext.cli.system import SetupError, doctor_app, setup_app


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop installation path")
def test_zcode_executable_finds_official_windows_desktop(tmp_path: Path, monkeypatch) -> None:
    desktop = tmp_path / "Programs" / "ZCode" / "ZCode.exe"
    desktop.parent.mkdir(parents=True)
    desktop.write_bytes(b"desktop executable")
    monkeypatch.delenv("ZCODE_CLI_BIN", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(zcode, "which", lambda _: None)

    assert zcode.zcode_executable() == str(desktop)


def _checkout(root: Path) -> Path:
    plugin = root / "integrations" / "zcode" / "plugins" / "powercontext"
    (plugin / ".zcode-plugin").mkdir(parents=True)
    (plugin / ".zcode-plugin" / "plugin.json").write_text('{"name":"powercontext"}', encoding="utf-8")
    (plugin / "hooks").mkdir()
    (plugin / "hooks" / "user_prompt_submit.mjs").write_text("// hook", encoding="utf-8")
    for name in ("session_start.mjs", "stop.mjs"):
        (plugin / "hooks" / name).write_text("// hook", encoding="utf-8")
    for folder, names in {
        "shared": (
            "settings.mjs",
            "transport.mjs",
            "git-worktree.mjs",
            "scope.mjs",
            "observations.mjs",
            "context.mjs",
            "pending.mjs",
        ),
        "scripts": ("scope.mjs", "status.mjs", "doctor.mjs", "pending.mjs"),
    }.items():
        (plugin / folder).mkdir()
        for name in names:
            source = Path(__file__).parents[1] / "integrations/zcode/plugins/powercontext" / folder / name
            shutil.copyfile(source, plugin / folder / name)
    (plugin / "hooks" / "hooks.json").write_text(
        json.dumps({
            "hooks": {
                event: [{"hooks": [{"type": "process"}]}] for event in ("UserPromptSubmit", "SessionStart", "Stop")
            }
        }),
        encoding="utf-8",
    )
    (plugin / ".mcp.json").write_text(
        '{"mcpServers":{"powercontext":{"type":"http","url":"http://127.0.0.1:8000/mcp"}}}',
        encoding="utf-8",
    )
    return plugin


@pytest.mark.parametrize("endpoint", ["https://memory.example.test", "https://memory.example.test/mcp/"])
def test_zcode_install_preserves_other_config_and_is_repeatable(tmp_path: Path, monkeypatch, endpoint: str) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode" / "cli" / "config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    config_path = zcode.zcode_config_file()
    config_path.parent.mkdir(parents=True)
    original = {"model": {"main": "glm"}, "plugins": {"dirs": ["C:/other/plugin"], "enabled": False}}
    config_path.write_text(json.dumps(original), encoding="utf-8")

    for _ in range(2):
        result = install_plugin(
            "zcode",
            source=str(str(checkout)),
            ref="unused",
            server_url=endpoint,
            capture_prompts=False,
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["model"] == original["model"]
        assert config["plugins"]["dirs"] == ["C:/other/plugin", result["plugin_path"]]
        plugin = Path(result["plugin_path"])
        assert json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["powercontext"]["url"] == (
            "https://memory.example.test/mcp"
        )
        assert json.loads((plugin / "powercontext.json").read_text(encoding="utf-8"))["capture_prompts"] is False
    assert config["plugins"]["enabled"] is False


def test_zcode_install_rejects_unowned_plugin_without_changing_config(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode" / "cli" / "config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    plugin = zcode.zcode_plugin_dir()
    plugin.mkdir(parents=True)
    (plugin / "other.txt").write_text("keep", encoding="utf-8")
    checkout = tmp_path / "checkout"
    _checkout(checkout)

    with pytest.raises(SetupError, match="not owned"):
        install_plugin("zcode", source=str(str(checkout)), ref="unused")
    assert (plugin / "other.txt").read_text(encoding="utf-8") == "keep"
    assert not zcode.zcode_config_file().exists()


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize(
    "endpoint",
    [
        "https://memory.example.test/prefix",
        "https://memory.example.test/prefix/mcp",
    ],
)
def test_zcode_setup_rejects_invalid_origin_without_changing_installation(
    tmp_path: Path, monkeypatch, installed: bool, endpoint: str
) -> None:
    home = tmp_path / "home"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode/cli/config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(home / "clients.json"))
    checkout = tmp_path / "checkout"
    plugin = _checkout(checkout)
    if installed:
        install_plugin("zcode", source=str(checkout), server_url="https://memory.example.test")
    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    (plugin / "updated.txt").write_text("new package", encoding="utf-8")

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "zcode", "--source", str(checkout), "--server-url", endpoint, "--json"],
    )

    assert result.exit_code == 1, result.output
    assert "origin" in json.loads(result.output)["hosts"]["zcode"]["error"]
    assert {path: path.read_bytes() for path in home.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("authorization", ["raw-token", "Bearer invalid token"])
@pytest.mark.parametrize("command", ["setup", "config"])
def test_zcode_rejects_invalid_runtime_authorization_without_changing_installation(
    tmp_path: Path, monkeypatch, authorization: str, command: str
) -> None:
    home = tmp_path / "home"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode/cli/config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    monkeypatch.delenv("POWERCONTEXT_ZCODE_AUTHORIZATION", raising=False)
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(home / "clients.json"))
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    if command == "config":
        install_plugin("zcode", source=str(checkout), server_url="https://old.example")
    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    monkeypatch.setenv("POWERCONTEXT_ZCODE_AUTHORIZATION", authorization)
    arguments = ["setup", "zcode", "--source", str(checkout)] if command == "setup" else ["config", "agent", "zcode"]

    result = CliRunner().invoke(create_cli(), [*arguments, "--server-url", "https://new.example", "--json"])

    assert result.exit_code == 1, result.output
    assert "authorization" in json.loads(result.output)["hosts"]["zcode"]["error"].lower()
    assert authorization not in result.output
    assert {path: path.read_bytes() for path in home.rglob("*") if path.is_file()} == before


def test_zcode_upgrade_rejects_missing_packaged_reference_and_preserves_installation(
    tmp_path: Path, monkeypatch
) -> None:
    from powercontext.cli.guidance import HOST_GUIDANCE, SKILL_DIRECTORY

    home = tmp_path / "home"
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode/cli/config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    checkout = tmp_path / "checkout"
    source = _checkout(checkout)
    shutil.copytree(Path(__file__).parents[1] / HOST_GUIDANCE["agent-plugin"].skill, source / SKILL_DIRECTORY)
    result = install_plugin("zcode", source=str(str(checkout)), ref="unused", server_url="https://memory.example.test")
    installed = Path(result["plugin_path"])
    (installed / SKILL_DIRECTORY / "personal.md").write_bytes(b"My instructions\r\n")
    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    (source / SKILL_DIRECTORY / "references/work-handoff.md").unlink()

    with pytest.raises(SetupError, match="missing guidance resource"):
        install_plugin("zcode", source=str(str(checkout)), ref="unused", server_url="https://memory.example.test")
    assert {path: path.read_bytes() for path in home.rglob("*") if path.is_file()} == before
    assert not list(installed.parent.glob(".powercontext-stage-*"))


def test_zcode_diagnostics_distinguish_registration_hook_mcp_and_server(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode" / "cli" / "config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    install_plugin("zcode", source=str(str(checkout)), ref="unused", server_url="http://127.0.0.1:1")
    diagnostics = zcode.ZCodeInstaller().verify()
    assert diagnostics["plugin"].ok
    assert diagnostics["hooks"].ok
    assert diagnostics["mcp"].ok
    assert not diagnostics["server"].ok

    (zcode.zcode_plugin_dir() / ".mcp.json").unlink()
    diagnostics = zcode.ZCodeInstaller().verify()
    assert diagnostics["hooks"].ok
    assert not diagnostics["mcp"].ok
    (zcode.zcode_plugin_dir() / ".mcp.json").write_text(
        '{"mcpServers":{"powercontext":{"url":123,"headers":[]}}}', encoding="utf-8"
    )
    assert not zcode.ZCodeInstaller().verify()["mcp"].ok


@pytest.mark.parametrize("readiness", ["ready", "degraded"])
def test_zcode_doctor_uses_installed_url_with_stale_environment_override(
    tmp_path: Path, monkeypatch, server_checks, readiness
) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode" / "cli" / "config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    checkout = tmp_path / "checkout"
    _checkout(checkout)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"status": readiness, "checks": {}}).encode())

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"scope_id":"scope-probe"}')

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib override signature.
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_address[1]}"
        install_plugin("zcode", source=str(str(checkout)), ref="unused", server_url=endpoint)
        monkeypatch.setenv("POWERCONTEXT_ZCODE_SERVER_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "http://127.0.0.1:2")
        result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "zcode", "--json"])
        assert result.exit_code == (0 if readiness == "ready" else 1), result.output
        report = json.loads(result.output)["hosts"]["zcode"]
        assert report["ok"] is (readiness == "ready")
        assert report["checks"]["server"]["status"] == ("ok" if readiness == "ready" else "degraded")
        assert "transport" not in report["checks"]
        assert report["checks"]["protected_api"]["status"] == "ok"
        assert report["checks"]["mcp_session"]["status"] == "skipped"
        monkeypatch.delenv("POWERCONTEXT_ZCODE_SERVER_URL")
        monkeypatch.delenv("POWERCONTEXT_CLIENT_SERVER_URL")
        setup = CliRunner().invoke(
            create_cli([setup_app]), ["setup", "zcode", "--source", str(checkout), "--server-url", endpoint]
        )
        assert setup.exit_code == (0 if readiness == "ready" else 1), setup.output
        assert "setup complete" in setup.output
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_zcode_install_restores_config_and_plugin_when_config_write_fails(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode" / "cli" / "config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    install_plugin("zcode", source=str(str(checkout)), ref="unused")
    plugin = zcode.zcode_plugin_dir()
    (plugin / "keep.txt").write_text("prior installation", encoding="utf-8")
    config_before = zcode.zcode_config_file().read_bytes()
    original_write = installation.write_bytes

    def fail_config(path: Path, value: bytes) -> None:
        if path == zcode.zcode_config_file():
            raise OSError("simulated write failure")  # noqa: TRY003
        original_write(path, value)

    monkeypatch.setattr(installation, "write_bytes", fail_config)
    with pytest.raises(SetupError, match="simulated write failure"):
        install_plugin("zcode", source=str(str(checkout)), ref="unused", server_url="https://new.example.test")
    assert zcode.zcode_config_file().read_bytes() == config_before
    assert (plugin / "keep.txt").read_text(encoding="utf-8") == "prior installation"


def test_zcode_remote_http_requires_consent_and_auth_uses_runtime_env(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode" / "cli" / "config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    monkeypatch.setattr(zcode, "setup_environment", lambda: {})
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    with pytest.raises(SetupError, match="explicit"):
        install_plugin("zcode", source=str(str(checkout)), ref="unused", server_url="http://memory.example.test")
    assert not zcode.zcode_config_file().exists()

    monkeypatch.setenv("POWERCONTEXT_ZCODE_AUTHORIZATION", "Bearer test-value")
    result = install_plugin(
        "zcode",
        source=str(str(checkout)),
        ref="unused",
        server_url="http://memory.example.test",
        allow_insecure_http=True,
    )
    entry = json.loads((Path(result["plugin_path"]) / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"][
        "powercontext"
    ]
    assert entry["headers"] == {"Authorization": "${POWERCONTEXT_ZCODE_AUTHORIZATION}"}
    assert "test-value" not in (Path(result["plugin_path"]) / ".mcp.json").read_text(encoding="utf-8")

    assert zcode.ZCodeInstaller().verify()["mcp"].ok
    monkeypatch.delenv("POWERCONTEXT_ZCODE_AUTHORIZATION")
    assert not zcode.ZCodeInstaller().verify()["mcp"].ok


def test_zcode_configuration_failure_keeps_completed_installation(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode" / "cli" / "config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    requested: list[tuple[str, str]] = []

    def clone(source: str, ref: str, target: Path) -> None:
        requested.append((source, ref))
        shutil.copytree(checkout, target)

    monkeypatch.setattr(git_source, "clone_github_source", clone)
    first = install_plugin("zcode", source="owner/powercontext", ref="v1.2.3")
    config_before = zcode.zcode_config_file().read_bytes()
    (checkout / "integrations/zcode/plugins/powercontext/updated.txt").write_text("new package", encoding="utf-8")

    def fail_persistence(path, value):
        if path.name == "clients.json":
            raise OSError("later setup failure")  # noqa: TRY003
        original_write(path, value)

    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    original_write = installation.write_bytes
    monkeypatch.setattr(installation, "write_bytes", fail_persistence)
    with pytest.raises(SetupError, match="later setup failure") as failure:
        install_plugin("zcode", source="owner/powercontext", ref="v1.2.3", server_url="https://new.test")
    assert requested == [("owner/powercontext", "v1.2.3")] * 2
    assert zcode.zcode_config_file().read_bytes() == config_before
    assert (Path(first["plugin_path"]) / "updated.txt").read_text(encoding="utf-8") == "new package"
    assert "Plugin installation completed" in " ".join(failure.value.__notes__)


def test_zcode_diagnostics_detect_missing_installed_scope_dependency(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: tmp_path / "home/.zcode/cli/config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    result = install_plugin("zcode", source=str(str(checkout)), ref="unused")
    (Path(result["plugin_path"]) / "shared/scope.mjs").unlink()
    assert not zcode.ZCodeInstaller().verify()["hooks"].ok


def test_zcode_upgrade_keeps_saved_capture_and_boundary_options(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: tmp_path / "home/.zcode/cli/config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    monkeypatch.delenv("POWERCONTEXT_ZCODE_BOUNDARY_FLUSH", raising=False)
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    first = install_plugin("zcode", source=str(str(checkout)), ref="unused", capture_prompts=False, boundary_flush=True)
    install_plugin("zcode", source=str(str(checkout)), ref="unused")
    saved = json.loads((Path(first["plugin_path"]) / "powercontext.json").read_text(encoding="utf-8"))
    assert saved["capture_prompts"] is False
    assert saved["boundary_flush"] is True
    monkeypatch.setenv("POWERCONTEXT_ZCODE_BOUNDARY_FLUSH", "false")
    install_plugin("zcode", source=str(str(checkout)), ref="unused")
    assert (
        json.loads((Path(first["plugin_path"]) / "powercontext.json").read_text(encoding="utf-8"))["boundary_flush"]
        is False
    )


@pytest.mark.parametrize("command", ["setup", "config"])
@pytest.mark.parametrize(
    ("mcp_endpoint", "selected_endpoint"),
    [
        ("https://old.example", "https://old.example"),
        ("https://mcp-previous.example", "https://old.example"),
        ("https://mcp-previous.example", "https://mcp-previous.example"),
    ],
)
@pytest.mark.parametrize(
    ("header", "authorization"),
    [
        ("Authorization", "Bearer previous-server-token"),
        ("authorization", "Bearer previous-server-token"),
        ("Authorization", "${POWERCONTEXT_ZCODE_AUTHORIZATION}"),
    ],
)
def test_zcode_rebinding_preserves_other_headers_and_drops_old_authorization(
    tmp_path, monkeypatch, installed_agent_checks, command, header, authorization, mcp_endpoint, selected_endpoint
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: tmp_path / "zcode/config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    monkeypatch.delenv("POWERCONTEXT_ZCODE_AUTHORIZATION", raising=False)
    source = str(Path(__file__).parents[1])
    installed = install_plugin("zcode", source=source, server_url="https://old.example")
    mcp_path = Path(installed["plugin_path"]) / ".mcp.json"
    mcp = json.loads(mcp_path.read_text())
    mcp["mcpServers"]["powercontext"]["url"] = mcp_endpoint + "/mcp"
    mcp["mcpServers"]["powercontext"]["headers"] = {header: authorization, "X-Custom": "keep"}
    mcp_path.write_text(json.dumps(mcp))
    arguments = ["setup", "zcode", "--source", source] if command == "setup" else ["config", "agent", "zcode"]
    cli, runner = create_cli(), CliRunner()
    result = runner.invoke(cli, [*arguments, "--server-url", selected_endpoint, "--json"])
    assert result.exit_code == 0, result.output
    expected = {"X-Custom": "keep"}
    if mcp_endpoint == selected_endpoint:
        expected[header] = authorization
    assert json.loads(mcp_path.read_text())["mcpServers"]["powercontext"]["headers"] == expected
    result = runner.invoke(cli, [*arguments, "--server-url", "https://new.example", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(mcp_path.read_text())["mcpServers"]["powercontext"]["headers"] == {"X-Custom": "keep"}
    monkeypatch.setenv("POWERCONTEXT_ZCODE_AUTHORIZATION", "Bearer replacement-token")
    result = runner.invoke(cli, [*arguments, "--server-url", "https://third.example", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(mcp_path.read_text())["mcpServers"]["powercontext"]["headers"] == {
        "Authorization": "${POWERCONTEXT_ZCODE_AUTHORIZATION}",
        "X-Custom": "keep",
    }
    assert "replacement-token" not in mcp_path.read_text()
