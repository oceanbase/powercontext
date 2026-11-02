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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from click import unstyle
from typer.testing import CliRunner

from powercontext.cli import installation, kimi_code
from powercontext.cli.app import create_cli
from powercontext.cli.installation import setup as install_plugin
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
    return install_plugin("kimi-code", source=str(REPOSITORY), scope_id="scope-中文", **options)


def test_runtime_history_survives_setup_and_doctor_reports_missing_history(kimi_home, server_checks):
    install()
    history = kimi_home / "powercontext/runtime"
    history.mkdir(parents=True)
    preserved = history / "unrelated.txt"
    preserved.write_text("Preserve local state.", encoding="utf-8")
    install()
    assert preserved.read_text(encoding="utf-8") == "Preserve local state."
    report = kimi_code.KimiInstaller().verify(runtime=True, cwd=REPOSITORY, session_id="new-session")
    assert report["runtime"].status.value == "skipped"
    assert report["runtime"].detail == "not_observed"
    assert report["runtime"].checks == {"selection": "session"}


def test_doctor_runtime_option_requires_explicit_selection(kimi_home, server_checks):
    install()
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "kimi-code", "--session-id", "session"])
    assert result.exit_code != 0
    assert "--session-id requires --runtime" in unstyle(result.output)


def test_cli_installs_repeatably_and_preserves_configuration(kimi_home, installed_agent_checks):
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
    installed = Path(json.loads(result.output)["hosts"]["kimi-code"]["result"]["plugin_path"])
    value = json.loads(registry.read_text(encoding="utf-8"))
    value["plugins"][1].update(enabled=False, capabilities={"mcpServers": {"powercontext": {"enabled": False}}})
    registry.write_text(json.dumps(value), encoding="utf-8")
    skill_file = installed / "skills/powercontext-project-context/SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8") + "\nPersonal instruction to retain.\n", encoding="utf-8"
    )
    install_plugin("kimi-code", source=str(REPOSITORY))
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
        "profile": "minimal",
    }
    manifest = json.loads((installed / "kimi.plugin.json").read_text(encoding="utf-8"))
    assert manifest["mcpServers"]["powercontext"]["url"] == settings["server_url"] + "/mcp"
    assert "bearerTokenEnvVar" not in manifest["mcpServers"]["powercontext"]
    assert '"scope-中文"' in (installed / "skills/powercontext-project-context/SKILL.md").read_text(encoding="utf-8")
    report = kimi_code.KimiInstaller().verify()
    assert report["plugin"].detail == "disabled"
    assert not report["mcp"].ok


@pytest.mark.parametrize("url", ["http://memory.example.test", "https://memory.example.test/path", "https://u:p@test"])
def test_install_rejects_unsupported_transport_before_writing(kimi_home, url):
    with pytest.raises((SetupError, ValueError)):
        install(server_url=url)
    assert not (kimi_home / "plugins/installed.json").exists()


def test_install_requires_explicit_scope_and_preserves_unowned_directory(kimi_home):
    with pytest.raises(SetupError, match="scope-id"):
        install_plugin("kimi-code", source=str(REPOSITORY))
    destination = kimi_home / "plugins/managed/powercontext"
    destination.mkdir(parents=True)
    (destination / "user.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(SetupError, match="not owned"):
        install()
    assert (destination / "user.txt").read_text(encoding="utf-8") == "keep"


def test_failed_registry_write_restores_plugin_and_registration(kimi_home, monkeypatch):
    result = install(capture_prompts=False)
    before = {p.relative_to(kimi_home): p.read_bytes() for p in kimi_home.rglob("*") if p.is_file()}
    original = installation.write_bytes

    def fail_registration(path, value):
        if path == Path(result["config_file"]):
            raise OSError("synthetic write failure")  # noqa: TRY003
        original(path, value)

    monkeypatch.setattr(installation, "write_bytes", fail_registration)
    with pytest.raises(SetupError):
        install(capture_prompts=True)
    assert {p.relative_to(kimi_home): p.read_bytes() for p in kimi_home.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:1", "http://127.0.0.2:1", "http://127.255.255.254:1", "http://[::1]:1"]
)
def test_doctor_reports_unavailable_server_without_writes(kimi_home, url, server_checks):
    install(server_url=url)
    before = {p.relative_to(kimi_home): p.read_bytes() for p in kimi_home.rglob("*") if p.is_file()}
    report = kimi_code.KimiInstaller().verify()
    assert report["plugin"].ok and report["mcp"].ok and report["hook"].ok
    assert report["server"].detail == "server_unavailable"
    assert report["scope"].detail == "server_unavailable"
    assert {p.relative_to(kimi_home): p.read_bytes() for p in kimi_home.rglob("*") if p.is_file()} == before


def test_authentication_mode_is_preserved_without_storing_token(kimi_home, monkeypatch):
    monkeypatch.setenv("POWERCONTEXT_KIMI_TOKEN", "synthetic-only")
    result = install()
    plugin = Path(result["plugin_path"])
    assert all(b"synthetic-only" not in path.read_bytes() for path in plugin.rglob("*") if path.is_file())
    monkeypatch.delenv("POWERCONTEXT_KIMI_TOKEN")
    install_plugin("kimi-code", source=str(REPOSITORY))
    manifest = json.loads((plugin / "kimi.plugin.json").read_text(encoding="utf-8"))
    assert manifest["mcpServers"]["powercontext"]["bearerTokenEnvVar"] == "POWERCONTEXT_KIMI_TOKEN"
    assert kimi_code.KimiInstaller().verify()["credentials"].detail == "authentication_failed"


def test_reconfiguration_rejects_installed_source_with_stale_scope(kimi_home):
    installed = Path(install()["plugin_path"])
    before = (installed / "powercontext.json").read_bytes()
    with pytest.raises(SetupError, match="original PowerContext checkout"):
        install_plugin("kimi-code", source=str(installed), scope_id="another-scope")
    assert (installed / "powercontext.json").read_bytes() == before


@pytest.mark.parametrize("command", ["setup", "config"])
def test_scope_changes_preserve_personal_guidance(kimi_home, installed_agent_checks, command):
    installed = install()
    skill = Path(installed["plugin_path"]) / "skills/powercontext-project-context/SKILL.md"
    notes = (
        '\r\nPersonal example: The configured explicit Scope is "scope-中文".\r\n'
        "Literal templates: __POWERCONTEXT_SCOPE_JSON__ __POWERCONTEXT_SCOPE_SCRIPT_JSON__\r\n"
    ).encode()
    skill.write_bytes(skill.read_bytes() + notes)
    arguments = (
        ["setup", "kimi-code", "--source", str(REPOSITORY)] if command == "setup" else ["config", "agent", "kimi-code"]
    )
    result = CliRunner().invoke(create_cli(), [*arguments, "--scope-id", "new-scope", "--json"])
    assert result.exit_code == 0, result.output
    content = skill.read_bytes()
    assert content.endswith(notes)
    assert b'The configured explicit Scope is "new-scope".' in content


def test_configuration_updates_scope_without_reinstalling(kimi_home):
    from powercontext.cli.transport import SetupTransport

    installed = install()
    root = Path(installed["plugin_path"])
    hook = root / "hooks/user_prompt_submit.mjs"
    original = hook.read_bytes()
    manifest_path = root / "kimi.plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["mcpServers"]["other"] = {"url": "https://other.example"}
    manifest["mcpServers"]["powercontext"].update(enabled=False, enabledTools=["search_memory"], toolTimeoutMs=12345)
    manifest_path.write_text(json.dumps(manifest))
    registry_path = Path(installed["config_file"])
    registry = json.loads(registry_path.read_text())
    record = registry["plugins"][0]
    record.update(
        enabled=False, capabilities={"mcpServers": {"powercontext": {"enabled": False}, "other": {"enabled": False}}}
    )
    registry_path.write_text(json.dumps(registry))
    installer = kimi_code.KimiInstaller(scope_id="new-scope", capture_prompts=False)
    updated = installer.configure(SetupTransport("kimi-code", "https://new.example", False))
    assert updated["scope_id"] == "new-scope"
    assert hook.read_bytes() == original
    assert json.loads((root / "powercontext.json").read_text())["capture_prompts"] is False
    skill = (root / "skills/powercontext-project-context/SKILL.md").read_text()
    assert 'The configured explicit Scope is "new-scope".' in skill
    assert 'The configured explicit Scope is "scope-中文".' not in skill
    manifest = json.loads(manifest_path.read_text())
    assert manifest["mcpServers"]["other"] == {"url": "https://other.example"}
    assert manifest["mcpServers"]["powercontext"]["enabledTools"] == ["search_memory"]
    assert manifest["mcpServers"]["powercontext"]["toolTimeoutMs"] == 12345
    assert manifest["mcpServers"]["powercontext"]["enabled"] is False
    assert json.loads(registry_path.read_text()) == registry


@pytest.mark.parametrize(
    "readiness, old_probe, service_failed, http_status, with_checks",
    [
        ("ready", False, False, 200, True),
        ("degraded", False, False, 200, True),
        ("ready", True, False, 200, True),
        ("ready", False, True, 200, True),
        ("ready", False, False, 201, True),
        ("ready", False, False, 200, False),
    ],
)
def test_doctor_preserves_server_readiness_without_changing_installed_settings(
    kimi_home, monkeypatch, readiness, old_probe, service_failed, http_status, with_checks
):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(http_status)
            self.end_headers()
            response = {"status": readiness}
            if with_checks:
                response["checks"] = {"inference.generation": "unavailable" if readiness == "degraded" else "ready"}
            self.wfile.write(json.dumps(response).encode())

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"scope_id": "scope-中文"}).encode())

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib override.
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        install(server_url=f"http://127.0.0.1:{server.server_port}")
        if old_probe:
            script = kimi_home / "plugins/managed/powercontext/scripts/doctor.mjs"
            script.write_text(script.read_text().replace("diagnostics.v2", "diagnostics.v1"))
        if service_failed:
            from powercontext.cli.system import Diagnostic, DiagnosticStatus

            monkeypatch.setattr(
                "powercontext.cli.system._local_service_diagnostics",
                lambda _endpoint: {
                    "service_manager": Diagnostic(DiagnosticStatus.FAILED, "registered service failed"),
                },
            )
        original = {path: path.read_bytes() for path in kimi_home.rglob("*") if path.is_file()}
        result = CliRunner().invoke(create_cli(), ["doctor", "kimi-code", "--json"])
        valid = http_status == 200 and with_checks
        assert result.exit_code == (
            0 if valid and readiness == "ready" and not old_probe and not service_failed else 1
        ), result.output
        checks = json.loads(result.output)["hosts"]["kimi-code"]["checks"]
        if old_probe:
            assert checks["probe"]["status"] == "failed"
            assert "powercontext setup kimi-code" in checks["probe"]["detail"]
        else:
            assert checks["server"]["status"] == (
                "failed" if not valid else "ok" if readiness == "ready" else "degraded"
            )
            if valid:
                assert checks["server"]["checks"] == {
                    "inference.generation": "unavailable" if readiness == "degraded" else "ready"
                }
        if service_failed:
            assert checks["service_manager"]["status"] == "failed"
        assert {path: path.read_bytes() for path in kimi_home.rglob("*") if path.is_file()} == original
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_workspace_scope_setup_preserves_mode_and_can_restore_explicit_scope(kimi_home, installed_agent_checks):
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "kimi-code", "--source", str(REPOSITORY), "--workspace-scope", "--json"]
    )
    assert result.exit_code == 0, result.output
    metadata = json.loads(result.output)["hosts"]["kimi-code"]["result"]
    assert metadata["scope_id"] is None
    installed = Path(metadata["plugin_path"])
    install_plugin("kimi-code", source=str(REPOSITORY))
    settings = json.loads((installed / "powercontext.json").read_text(encoding="utf-8"))
    assert settings["scope_id"] is None
    skill = (installed / "skills/powercontext-project-context/SKILL.md").read_text(encoding="utf-8")
    assert "explicit Scope is null" in skill
    assert "__POWERCONTEXT_" not in skill
    assert (installed / "scripts/scope.mjs").is_file()
    install_plugin("kimi-code", source=str(REPOSITORY), scope_id="scope-explicit")
    assert json.loads((installed / "powercontext.json").read_text(encoding="utf-8"))["scope_id"] == "scope-explicit"
    install_plugin("kimi-code", source=str(REPOSITORY), workspace_scope=True)
    assert json.loads((installed / "powercontext.json").read_text(encoding="utf-8"))["scope_id"] is None


def test_workspace_mode_rejects_an_ambiguous_explicit_scope_before_writing(kimi_home):
    with pytest.raises(SetupError, match="mutually exclusive"):
        install_plugin("kimi-code", source=str(REPOSITORY), workspace_scope=True, scope_id="scope-conflict")
    assert not (kimi_home / "plugins/installed.json").exists()


def test_profile_updates_preserve_settings_and_minimal_legacy_installations(
    kimi_home, monkeypatch, installed_agent_checks
):
    monkeypatch.setenv("POWERCONTEXT_KIMI_TOKEN", "synthetic-only")
    installed = Path(install(capture_prompts=False)["plugin_path"])
    settings_path = installed / "powercontext.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    del settings["profile"]
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    install_plugin("kimi-code", source=str(REPOSITORY))
    manifest_path = installed / "kimi.plugin.json"
    minimal = json.loads(manifest_path.read_text(encoding="utf-8"))["mcpServers"]["powercontext"]["enabledTools"]
    assert set(minimal) == {"search_memory", "remember_memory", "get_artifact_revision"}
    registry_path = kimi_home / "plugins/installed.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["plugins"][0].update(enabled=False, capabilities={"mcpServers": {"powercontext": {"enabled": False}}})
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    for profile, required, absent in (
        ("recommended", "handoff_current_work", "approve_candidate"),
        ("full", "approve_candidate", "query_code"),
        ("minimal", "search_memory", "handoff_current_work"),
    ):
        result = CliRunner().invoke(
            create_cli([setup_app]),
            ["setup", "kimi-code", "--source", str(REPOSITORY), "--profile", profile, "--json"],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["hosts"]["kimi-code"]["result"]["profile"] == profile
        install_plugin("kimi-code", source=str(REPOSITORY))
        current = json.loads(settings_path.read_text(encoding="utf-8"))
        assert current == {**settings, "profile": profile}
        record = json.loads(registry_path.read_text(encoding="utf-8"))["plugins"][0]
        assert record["enabled"] is False
        assert record["capabilities"]["mcpServers"]["powercontext"]["enabled"] is False
        tools = json.loads(manifest_path.read_text(encoding="utf-8"))["mcpServers"]["powercontext"]["enabledTools"]
        assert required in tools and absent not in tools
        skill = (installed / "skills/powercontext-project-context/SKILL.md").read_text(encoding="utf-8")
        assert "__POWERCONTEXT_" not in skill
        assert f'profile is "{profile}"' in skill


def test_invalid_profile_does_not_replace_installation(kimi_home):
    installed = Path(install()["plugin_path"])
    before = (installed / "powercontext.json").read_bytes()
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "kimi-code", "--source", str(REPOSITORY), "--profile", "everything"]
    )
    assert result.exit_code == 2
    assert (installed / "powercontext.json").read_bytes() == before


def test_configure_switches_profiles_without_reinstalling_or_replacing_personal_guidance(kimi_home):
    installed = Path(install()["plugin_path"])
    hook = installed / "hooks/user_prompt_submit.mjs"
    original_hook = hook.read_bytes()
    skill = installed / "skills/powercontext-project-context/SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\nPersonal project instruction.\n", encoding="utf-8")
    for profile, tool, absent in (
        ("recommended", "handoff_current_work", "approve_candidate"),
        ("full", "approve_candidate", "query_code"),
        ("minimal", "search_memory", "handoff_current_work"),
    ):
        result = CliRunner().invoke(create_cli(), ["config", "agent", "kimi-code", "--profile", profile, "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["hosts"]["kimi-code"]["result"]["profile"] == profile
        tools = json.loads((installed / "kimi.plugin.json").read_text())["mcpServers"]["powercontext"]["enabledTools"]
        assert tool in tools and absent not in tools
        content = skill.read_text(encoding="utf-8")
        assert f'profile is "{profile}".' in content
        assert content.endswith("Personal project instruction.\n")
        assert ("[Work Handoff]" in content) is (profile != "minimal")
        assert ("[Experience and Skill]" in content) is (profile == "full")
        assert hook.read_bytes() == original_hook


@pytest.mark.parametrize("command", [["setup"], ["config", "agent"]], ids=["setup", "configure"])
def test_incompatible_batch_profile_is_rejected_before_any_agent_writes(kimi_home, command):
    result = CliRunner().invoke(
        create_cli(), [*command, "kimi-code", "dsh", "--scope-id", "scope-test", "--profile", "full"]
    )
    assert result.exit_code == 2, result.output
    assert "dsh profile must be one of" in unstyle(result.output)
    assert not (kimi_home / "plugins/installed.json").exists()


@pytest.mark.parametrize("profile", ["web", "desktop"])
def test_mixed_doctor_scopes_profile_to_dsh_and_preserves_saved_kimi_profile(
    kimi_home, monkeypatch, server_checks, profile
):
    from subprocess import CompletedProcess

    from powercontext.cli import dsh, dsh_runtime, native_transport
    from powercontext.cli.system import Diagnostic, DiagnosticStatus

    install(profile="full")
    original = {path: path.read_bytes() for path in kimi_home.rglob("*") if path.is_file()}
    monkeypatch.setattr(dsh_runtime, "resolve_dsh_target", lambda selected, command: dsh_runtime.DshTarget(selected))
    monkeypatch.setattr(
        dsh.DshInstaller,
        "verify",
        lambda self: {"profile": Diagnostic(DiagnosticStatus.OK, self.dsh_target.profile.value)},
    )
    monkeypatch.setattr(
        native_transport, "resolve_host_transport", lambda host, **options: ("https://memory.example.test", False)
    )
    # Native probes are controlled; Kimi's installed declarations and saved profile are checked by the real adapter.
    monkeypatch.setattr(
        kimi_code.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args[0], 0, stdout=json.dumps({"schema": "powercontext.kimi-code.diagnostics.v2", "checks": {}})
        ),
    )
    result = CliRunner().invoke(create_cli(), ["doctor", "dsh", "kimi-code", "--profile", profile, "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)["hosts"]
    assert rows["dsh"]["checks"]["profile"]["detail"] == profile
    assert rows["kimi-code"]["checks"]["profile"]["detail"] == "full"
    assert {path: path.read_bytes() for path in kimi_home.rglob("*") if path.is_file()} == original
