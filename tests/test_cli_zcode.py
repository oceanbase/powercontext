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

from powercontext.cli import zcode
from powercontext.cli.app import create_cli
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
        "shared": ("settings.mjs", "transport.mjs", "scope.mjs", "observations.mjs", "context.mjs", "pending.mjs"),
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


def test_zcode_install_preserves_other_config_and_is_repeatable(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode" / "cli" / "config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    config_path = zcode.zcode_config_file()
    config_path.parent.mkdir(parents=True)
    original = {"model": {"main": "glm"}, "plugins": {"dirs": ["C:/other/plugin"], "enabled": True}}
    config_path.write_text(json.dumps(original), encoding="utf-8")

    for _ in range(2):
        result = zcode.install_zcode_plugin(
            source=str(checkout), ref="unused", server_url="https://memory.example.test", capture_prompts=False
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["model"] == original["model"]
        assert config["plugins"]["dirs"] == ["C:/other/plugin", result.plugin_path]
        plugin = Path(result.plugin_path)
        assert json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["powercontext"]["url"] == (
            "https://memory.example.test/mcp"
        )
        assert json.loads((plugin / "powercontext.json").read_text(encoding="utf-8"))["capture_prompts"] is False
    assert not list(config_path.parent.glob(".powercontext-backup"))


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
        zcode.install_zcode_plugin(source=str(checkout), ref="unused")
    assert (plugin / "other.txt").read_text(encoding="utf-8") == "keep"
    assert not zcode.zcode_config_file().exists()


def test_zcode_diagnostics_distinguish_registration_hook_mcp_and_server(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode" / "cli" / "config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    zcode.install_zcode_plugin(source=str(checkout), ref="unused", server_url="http://127.0.0.1:1")
    diagnostics = zcode.run_zcode_diagnostics()
    assert diagnostics["plugin"].ok
    assert diagnostics["hooks"].ok
    assert diagnostics["mcp"].ok
    assert not diagnostics["server"].ok

    (zcode.zcode_plugin_dir() / ".mcp.json").unlink()
    diagnostics = zcode.run_zcode_diagnostics()
    assert diagnostics["hooks"].ok
    assert not diagnostics["mcp"].ok
    (zcode.zcode_plugin_dir() / ".mcp.json").write_text(
        '{"mcpServers":{"powercontext":{"url":123,"headers":[]}}}', encoding="utf-8"
    )
    assert not zcode.run_zcode_diagnostics()["mcp"].ok


def test_zcode_doctor_uses_installed_url_with_stale_environment_override(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode" / "cli" / "config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    checkout = tmp_path / "checkout"
    _checkout(checkout)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

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
        zcode.install_zcode_plugin(source=str(checkout), ref="unused", server_url=endpoint)
        monkeypatch.setenv("POWERCONTEXT_ZCODE_SERVER_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "http://127.0.0.1:2")
        result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "zcode", "--json"])
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert report["ok"] is True
        assert "transport" not in report["checks"]
        assert report["checks"]["protected_api"]["status"] == "ok"
        assert report["checks"]["mcp_session"]["status"] == "skipped"
        monkeypatch.delenv("POWERCONTEXT_ZCODE_SERVER_URL")
        monkeypatch.delenv("POWERCONTEXT_CLIENT_SERVER_URL")
        setup = CliRunner().invoke(
            create_cli([setup_app]), ["setup", "zcode", "--source", str(checkout), "--server-url", endpoint]
        )
        assert setup.exit_code == 0, setup.output
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
    zcode.install_zcode_plugin(source=str(checkout), ref="unused")
    plugin = zcode.zcode_plugin_dir()
    (plugin / "keep.txt").write_text("prior installation", encoding="utf-8")
    config_before = zcode.zcode_config_file().read_bytes()
    original_write = zcode._write_json

    def fail_config(path: Path, value: dict[str, Any]) -> None:
        if path == zcode.zcode_config_file():
            raise OSError("simulated write failure")  # noqa: TRY003
        original_write(path, value)

    monkeypatch.setattr(zcode, "_write_json", fail_config)
    with pytest.raises(SetupError, match="restored"):
        zcode.install_zcode_plugin(source=str(checkout), ref="unused", server_url="https://new.example.test")
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
        zcode.install_zcode_plugin(source=str(checkout), ref="unused", server_url="http://memory.example.test")
    assert not zcode.zcode_config_file().exists()

    monkeypatch.setenv("POWERCONTEXT_ZCODE_AUTHORIZATION", "Bearer test-value")
    result = zcode.install_zcode_plugin(
        source=str(checkout), ref="unused", server_url="http://memory.example.test", allow_insecure_http=True
    )
    entry = json.loads((Path(result.plugin_path) / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"][
        "powercontext"
    ]
    assert entry["headers"] == {"Authorization": "${POWERCONTEXT_ZCODE_AUTHORIZATION}"}
    assert "test-value" not in (Path(result.plugin_path) / ".mcp.json").read_text(encoding="utf-8")

    assert zcode.run_zcode_diagnostics()["mcp"].ok
    monkeypatch.delenv("POWERCONTEXT_ZCODE_AUTHORIZATION")
    assert not zcode.run_zcode_diagnostics()["mcp"].ok


def test_zcode_git_ref_route_and_later_setup_failure_restore_prior_state(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: home / ".zcode" / "cli" / "config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    requested: list[tuple[str, str]] = []

    def clone(source: str, ref: str, target: Path) -> None:
        requested.append((source, ref))
        shutil.copytree(checkout, target)

    monkeypatch.setattr(zcode, "clone_github_source", clone)
    first = zcode.install_zcode_plugin(source="owner/powercontext", ref="v1.2.3")
    config_before = zcode.zcode_config_file().read_bytes()
    (Path(first.plugin_path) / "keep.txt").write_text("previous", encoding="utf-8")
    with pytest.raises(SetupError, match="later setup failure"), zcode.preserve_zcode_installation():
        zcode.install_zcode_plugin(source="owner/powercontext", ref="v1.2.3", server_url="https://new.test")
        raise SetupError("later setup failure")  # noqa: TRY003
    assert requested == [("owner/powercontext", "v1.2.3")] * 2
    assert zcode.zcode_config_file().read_bytes() == config_before
    assert (Path(first.plugin_path) / "keep.txt").read_text(encoding="utf-8") == "previous"


def test_zcode_diagnostics_detect_missing_installed_scope_dependency(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: tmp_path / "home/.zcode/cli/config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    result = zcode.install_zcode_plugin(source=str(checkout), ref="unused")
    (Path(result.plugin_path) / "shared/scope.mjs").unlink()
    assert not zcode.run_zcode_diagnostics()["hooks"].ok


def test_zcode_upgrade_keeps_saved_capture_and_boundary_options(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(zcode, "zcode_config_file", lambda: tmp_path / "home/.zcode/cli/config.json")
    monkeypatch.setattr(zcode, "zcode_executable", lambda: "zcode")
    monkeypatch.delenv("POWERCONTEXT_ZCODE_BOUNDARY_FLUSH", raising=False)
    checkout = tmp_path / "checkout"
    _checkout(checkout)
    first = zcode.install_zcode_plugin(source=str(checkout), ref="unused", capture_prompts=False, boundary_flush=True)
    zcode.install_zcode_plugin(source=str(checkout), ref="unused")
    saved = json.loads((Path(first.plugin_path) / "powercontext.json").read_text(encoding="utf-8"))
    assert saved["capture_prompts"] is False
    assert saved["boundary_flush"] is True
    monkeypatch.setenv("POWERCONTEXT_ZCODE_BOUNDARY_FLUSH", "false")
    zcode.install_zcode_plugin(source=str(checkout), ref="unused")
    assert (
        json.loads((Path(first.plugin_path) / "powercontext.json").read_text(encoding="utf-8"))["boundary_flush"]
        is False
    )
