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

"""Observable installation and readonly diagnostics for the Kimi Code integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click import unstyle
from typer.testing import CliRunner

from powercontext.cli import kimi_code
from powercontext.cli.app import create_cli
from powercontext.cli.system import SetupError, doctor_app, setup_app

REPOSITORY = Path(__file__).parents[1]


@pytest.fixture
def kimi_home(tmp_path, monkeypatch):
    home = tmp_path / "kimi home"
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.delenv("POWERCONTEXT_KIMI_TOKEN", raising=False)
    monkeypatch.setattr(kimi_code, "kimi_executable", lambda: "kimi")
    return home


def install(**options):
    return kimi_code.install_kimi_plugin(source=REPOSITORY, scope_id="scope-中文", **options)


def test_runtime_history_survives_setup_and_doctor_reports_missing_history(kimi_home):
    install()
    history = kimi_home / "powercontext/runtime"
    history.mkdir(parents=True)
    preserved = history / "unrelated.txt"
    preserved.write_text("Preserve local state.", encoding="utf-8")
    install()
    assert preserved.read_text(encoding="utf-8") == "Preserve local state."
    report = kimi_code.run_kimi_diagnostics(runtime=True, cwd=REPOSITORY, session_id="new-session")
    assert report["runtime"].status.value == "skipped"
    assert report["runtime"].detail == "not_observed"
    assert report["runtime"].checks == {"selection": "session"}


def test_doctor_runtime_option_requires_explicit_selection(kimi_home):
    install()
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "kimi-code", "--session-id", "session"])
    assert result.exit_code != 0
    assert "--session-id requires --runtime" in unstyle(result.output)


def test_cli_installs_repeatably_and_preserves_configuration(kimi_home):
    registry = kimi_home / "plugins/installed.json"
    registry.parent.mkdir(parents=True)
    other = {"id": "other", "root": "/other", "enabled": True, "custom": {"keep": "原样"}}
    registry.write_text(json.dumps({"version": 1, "plugins": [other], "extension": 42}), encoding="utf-8")
    config = kimi_home / "config.toml"
    config.write_text('default_model = "keep"\n', encoding="utf-8")
    result = CliRunner().invoke(
        create_cli([setup_app]),
        [
            "setup",
            "kimi-code",
            "--source",
            str(REPOSITORY),
            "--scope-id",
            "scope-中文",
            "--server-url",
            "https://memory.example.test",
            "--no-capture-prompts",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    installed = Path(json.loads(result.output)["plugin_path"])
    value = json.loads(registry.read_text(encoding="utf-8"))
    value["plugins"][1].update(enabled=False, capabilities={"mcpServers": {"powercontext": {"enabled": False}}})
    registry.write_text(json.dumps(value), encoding="utf-8")
    skill_file = installed / "skills/powercontext-project-context/SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8") + "\nPersonal instruction to retain.\n", encoding="utf-8"
    )
    kimi_code.install_kimi_plugin(source=REPOSITORY)
    assert "Personal instruction to retain." in skill_file.read_text(encoding="utf-8")
    value = json.loads(registry.read_text(encoding="utf-8"))
    assert value["plugins"][0] == other
    assert value["extension"] == 42
    assert len(value["plugins"]) == 2
    assert value["plugins"][1]["enabled"] is False
    assert value["plugins"][1]["capabilities"]["mcpServers"]["powercontext"]["enabled"] is False
    assert config.read_text(encoding="utf-8") == 'default_model = "keep"\n'
    settings = json.loads((installed / "powercontext.json").read_text(encoding="utf-8"))
    assert settings == {
        "server_url": "https://memory.example.test",
        "scope_id": "scope-中文",
        "capture_prompts": False,
        "use_token": False,
    }
    manifest = json.loads((installed / "kimi.plugin.json").read_text(encoding="utf-8"))
    assert manifest["mcpServers"]["powercontext"]["url"] == settings["server_url"] + "/mcp"
    assert "bearerTokenEnvVar" not in manifest["mcpServers"]["powercontext"]
    assert "scope-中文" in (installed / "skills/powercontext-project-context/SKILL.md").read_text(encoding="utf-8")
    report = kimi_code.run_kimi_diagnostics()
    assert report["plugin"].detail == "disabled"
    assert not report["mcp"].ok


@pytest.mark.parametrize("url", ["http://memory.example.test", "https://memory.example.test/path", "https://u:p@test"])
def test_install_rejects_unsupported_transport_before_writing(kimi_home, url):
    with pytest.raises((SetupError, ValueError)):
        install(server_url=url)
    assert not (kimi_home / "plugins/installed.json").exists()


def test_install_requires_explicit_scope_and_preserves_unowned_directory(kimi_home):
    with pytest.raises(SetupError, match="scope-id"):
        kimi_code.install_kimi_plugin(source=REPOSITORY)
    destination = kimi_home / "plugins/managed/powercontext"
    destination.mkdir(parents=True)
    (destination / "user.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(SetupError, match="not owned"):
        install()
    assert (destination / "user.txt").read_text(encoding="utf-8") == "keep"


def test_failed_registry_write_restores_plugin_and_registration(kimi_home, monkeypatch):
    result = install(capture_prompts=False)
    before = {p.relative_to(kimi_home): p.read_bytes() for p in kimi_home.rglob("*") if p.is_file()}
    original = kimi_code._write_json

    def fail_registration(path, value):
        if path == Path(result.config_file):
            raise OSError("synthetic write failure")  # noqa: TRY003
        original(path, value)

    monkeypatch.setattr(kimi_code, "_write_json", fail_registration)
    with pytest.raises(OSError):
        install(capture_prompts=True)
    assert {p.relative_to(kimi_home): p.read_bytes() for p in kimi_home.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:1", "http://127.0.0.2:1", "http://127.255.255.254:1", "http://[::1]:1"]
)
def test_doctor_reports_unavailable_server_without_writes(kimi_home, url):
    install(server_url=url)
    before = {p.relative_to(kimi_home): p.read_bytes() for p in kimi_home.rglob("*") if p.is_file()}
    report = kimi_code.run_kimi_diagnostics()
    assert report["plugin"].ok and report["mcp"].ok and report["hook"].ok
    assert report["server"].detail == "server_unavailable"
    assert report["scope"].detail == "server_unavailable"
    assert {p.relative_to(kimi_home): p.read_bytes() for p in kimi_home.rglob("*") if p.is_file()} == before


def test_authentication_mode_is_preserved_without_storing_token(kimi_home, monkeypatch):
    monkeypatch.setenv("POWERCONTEXT_KIMI_TOKEN", "synthetic-only")
    result = install()
    plugin = Path(result.plugin_path)
    assert all(b"synthetic-only" not in path.read_bytes() for path in plugin.rglob("*") if path.is_file())
    monkeypatch.delenv("POWERCONTEXT_KIMI_TOKEN")
    kimi_code.install_kimi_plugin(source=REPOSITORY)
    manifest = json.loads((plugin / "kimi.plugin.json").read_text(encoding="utf-8"))
    assert manifest["mcpServers"]["powercontext"]["bearerTokenEnvVar"] == "POWERCONTEXT_KIMI_TOKEN"
    assert kimi_code.run_kimi_diagnostics()["credentials"].detail == "authentication_failed"


def test_failed_rollback_keeps_previous_plugin_for_recovery(kimi_home, monkeypatch):
    result = install()
    destination = Path(result.plugin_path)
    original_files = {p.relative_to(destination): p.read_bytes() for p in destination.rglob("*") if p.is_file()}
    write = kimi_code._write_json
    rename = Path.rename

    def fail_write(path, value):
        if path == Path(result.config_file):
            raise OSError("publication failed")  # noqa: TRY003
        write(path, value)

    def fail_restore(path, target):
        if path.name == ".powercontext-kimi-backup":
            raise OSError("restore denied")  # noqa: TRY003
        return rename(path, target)

    monkeypatch.setattr(kimi_code, "_write_json", fail_write)
    monkeypatch.setattr(Path, "rename", fail_restore)
    with pytest.raises(SetupError, match="backup is retained"):
        install()
    backup = destination.with_name(".powercontext-kimi-backup")
    assert {p.relative_to(backup): p.read_bytes() for p in backup.rglob("*") if p.is_file()} == original_files


def test_reconfiguration_rejects_installed_source_with_stale_scope(kimi_home):
    installed = Path(install().plugin_path)
    before = (installed / "powercontext.json").read_bytes()
    with pytest.raises(SetupError, match="original PowerContext checkout"):
        kimi_code.install_kimi_plugin(source=installed, scope_id="another-scope")
    assert (installed / "powercontext.json").read_bytes() == before


def test_workspace_scope_setup_preserves_mode_and_can_restore_explicit_scope(kimi_home):
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "kimi-code", "--source", str(REPOSITORY), "--workspace-scope", "--json"]
    )
    assert result.exit_code == 0, result.output
    metadata = json.loads(result.output)
    assert metadata["scope_id"] is None
    installed = Path(metadata["plugin_path"])
    kimi_code.install_kimi_plugin(source=REPOSITORY)
    settings = json.loads((installed / "powercontext.json").read_text(encoding="utf-8"))
    assert settings["scope_id"] is None
    skill = (installed / "skills/powercontext-project-context/SKILL.md").read_text(encoding="utf-8")
    assert "explicit Scope is null" in skill
    assert "__POWERCONTEXT_" not in skill
    assert (installed / "scripts/scope.mjs").is_file()
    kimi_code.install_kimi_plugin(source=REPOSITORY, scope_id="scope-explicit")
    assert json.loads((installed / "powercontext.json").read_text(encoding="utf-8"))["scope_id"] == "scope-explicit"
    kimi_code.install_kimi_plugin(source=REPOSITORY, workspace_scope=True)
    assert json.loads((installed / "powercontext.json").read_text(encoding="utf-8"))["scope_id"] is None


def test_workspace_mode_rejects_an_ambiguous_explicit_scope_before_writing(kimi_home):
    with pytest.raises(SetupError, match="mutually exclusive"):
        kimi_code.install_kimi_plugin(source=REPOSITORY, workspace_scope=True, scope_id="scope-conflict")
    assert not (kimi_home / "plugins/installed.json").exists()
