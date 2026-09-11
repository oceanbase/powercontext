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

"""Setup consent must be explicit, endpoint-bound, and usable in a new session."""

import json
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.cli.system import setup_app


@pytest.fixture(autouse=True)
def isolate_client_config(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    monkeypatch.delenv("POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP", raising=False)
    monkeypatch.delenv("POWERCONTEXT_CLIENT_SERVER_URL", raising=False)


@pytest.mark.parametrize("host", ["codex", "claude-code", "dsh", "openclaw", "opencode", "pi", "hermes", "workbuddy"])
def test_every_setup_rejects_remote_http_without_consent_before_install(host):
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", host, "--server-url", "http://192.0.2.10:8000", "--json"]
    )
    assert result.exit_code == 1
    assert "--allow-insecure-http" in result.output


def test_json_setup_never_prompts_even_with_tty(monkeypatch):
    from powercontext.cli import transport

    monkeypatch.setattr(transport.sys.stdin, "isatty", lambda: True)
    confirm = Mock(side_effect=AssertionError("JSON setup must not prompt"))
    monkeypatch.setattr(transport.typer, "confirm", confirm)
    with pytest.raises(RuntimeError, match="--allow-insecure-http"):
        transport.prepare_setup_transport("pi", server_url="http://192.0.2.10", json_output=True)


def test_interactive_consent_is_default_no_and_not_saved_until_completed(monkeypatch):
    from powercontext.cli import transport

    monkeypatch.setattr(transport.sys.stdin, "isatty", lambda: True)
    confirm = Mock(return_value=False)
    monkeypatch.setattr(transport.typer, "confirm", confirm)
    with pytest.raises(RuntimeError):
        transport.prepare_setup_transport("pi", server_url="http://192.0.2.10")
    assert confirm.call_args.kwargs["default"] is False
    assert not transport.client_config_file().exists()
    confirm.return_value = True
    settings = transport.prepare_setup_transport("pi", server_url="http://192.0.2.10")
    assert not transport.client_config_file().exists()
    transport.save_setup_transport(settings)
    assert json.loads(transport.client_config_file().read_text())["hosts"]["pi"] == {
        "server_url": "http://192.0.2.10",
        "allow_insecure_http": True,
    }


def test_saved_consent_does_not_follow_changed_url(monkeypatch):
    from powercontext.cli import transport

    settings = transport.prepare_setup_transport("pi", server_url="http://192.0.2.10", allow_insecure_http=True)
    transport.save_setup_transport(settings)
    assert transport.prepare_setup_transport("pi", json_output=True).allow_insecure_http
    with pytest.raises(RuntimeError, match="--allow-insecure-http"):
        transport.prepare_setup_transport("pi", server_url="http://192.0.2.11", json_output=True)


def test_setup_saves_only_nonsecret_fields_and_preserves_other_hosts(monkeypatch):
    from powercontext.cli import transport

    monkeypatch.setenv("POWERCONTEXT_CLIENT_AUTHORIZATION", "Bearer test-secret")
    for host in ("pi", "dsh"):
        transport.save_setup_transport(transport.prepare_setup_transport(host, server_url="https://example.test"))
    content = transport.client_config_file().read_text()
    assert "secret" not in content
    assert set(json.loads(content)["hosts"]) == {"pi", "dsh"}


def test_explicit_no_does_not_prompt_to_override_refusal(monkeypatch):
    from powercontext.cli import transport

    monkeypatch.setattr(transport.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(transport.typer, "confirm", Mock(side_effect=AssertionError("explicit no")))
    with pytest.raises(RuntimeError):
        transport.prepare_setup_transport("pi", server_url="http://192.0.2.10", allow_insecure_http=False)


def test_environment_false_does_not_prompt_to_override_refusal(monkeypatch):
    from powercontext.cli import transport

    monkeypatch.setenv("POWERCONTEXT_PI_ALLOW_INSECURE_HTTP", "false")
    monkeypatch.setattr(transport.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(transport.typer, "confirm", Mock(side_effect=AssertionError("explicit environment no")))
    with pytest.raises(RuntimeError):
        transport.prepare_setup_transport("pi", server_url="http://192.0.2.10")


def test_hermes_setup_synchronizes_native_endpoint_and_preserves_preferences(tmp_path, monkeypatch):
    from powercontext.cli.transport import SetupTransport, save_setup_transport

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "powercontext/config.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"base_url": "https://old.example", "capture": False}))
    save_setup_transport(SetupTransport("hermes", "http://192.0.2.10", True))
    assert json.loads(path.read_text()) == {
        "base_url": "http://192.0.2.10",
        "capture": False,
        "allow_insecure_http": True,
    }


@pytest.mark.parametrize("failed_file", ["shared", "native"])
def test_failed_persistence_restores_native_settings(tmp_path, monkeypatch, failed_file):
    import powercontext.cli.system as system
    from powercontext.cli.transport import SetupTransport, client_config_file, save_setup_transport

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    native = tmp_path / "powercontext/config.json"
    native.parent.mkdir()
    original = '{"base_url":"https://old.example","capture":false}'
    native.write_text(original)
    write = system._write_bytes_atomically

    def fail_shared(path, content):
        if path == (client_config_file() if failed_file == "shared" else native):
            raise OSError("simulated disk failure")  # noqa: TRY003
        write(path, content)

    monkeypatch.setattr(system, "_write_bytes_atomically", fail_shared)
    with pytest.raises(RuntimeError):
        save_setup_transport(SetupTransport("hermes", "http://192.0.2.10", True))
    assert native.read_text() == original
    assert not client_config_file().exists()


def test_codex_setup_updates_native_mcp_endpoint_without_touching_headers(tmp_path, monkeypatch):
    from powercontext.cli.system import _configure_codex_endpoint

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    path = tmp_path / "plugins/cache/powercontext/powercontext/0.1.0/.mcp.json"
    path.parent.mkdir(parents=True)
    original = {
        "mcpServers": {
            "powercontext": {
                "type": "http",
                "url": "http://127.0.0.1:8000/mcp",
                "env_http_headers": {"Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"},
            }
        }
    }
    path.write_text(json.dumps(original))
    _configure_codex_endpoint("powercontext", "0.1.0", "http://192.0.2.10:8000")
    changed = json.loads(path.read_text())["mcpServers"]["powercontext"]
    assert changed["url"] == "http://192.0.2.10:8000/mcp"
    assert changed["env_http_headers"] == original["mcpServers"]["powercontext"]["env_http_headers"]


def test_workbuddy_setup_aligns_mcp_with_selected_endpoint(tmp_path):
    from powercontext.cli.workbuddy import _merge_workbuddy_mcp

    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"other": {"url": "https://other.test"}}}))
    _merge_workbuddy_mcp(path, server_url="http://192.0.2.10:8000")
    servers = json.loads(path.read_text())["mcpServers"]
    assert "http://192.0.2.10:8000" in servers["powercontext"]["url"]
    assert servers["other"] == {"url": "https://other.test"}


def test_doctor_reports_insecure_opt_in_as_degraded():
    from powercontext.cli.transport import prepare_setup_transport, save_setup_transport, transport_diagnostic

    save_setup_transport(prepare_setup_transport("pi", server_url="http://192.0.2.10", allow_insecure_http=True))
    diagnostic = transport_diagnostic("pi")
    assert diagnostic.status == "degraded"
    assert "unencrypted" in diagnostic.detail


def test_doctor_connects_only_after_explicit_opt_in(monkeypatch):
    import powercontext.cli.system as system

    probe = Mock(return_value=system.Diagnostic(status=system.DiagnosticStatus.OK, detail="reachable"))
    monkeypatch.setattr(system, "_server_liveness_diagnostic", probe)
    monkeypatch.setattr(system, "_server_readiness_diagnostic", probe)
    diagnostics = system.run_diagnostics(server_url="http://192.0.2.10")
    assert diagnostics["server_liveness"].status == "failed"
    probe.assert_not_called()
    diagnostics = system.run_diagnostics(server_url="http://192.0.2.10", allow_insecure_http=True)
    assert diagnostics["server_liveness"].ok
    assert diagnostics["transport"].status == "degraded"


def test_doctor_reads_openclaw_native_endpoint_and_binds_consent(tmp_path, monkeypatch):
    from powercontext.cli.transport import transport_diagnostic

    path = tmp_path / "openclaw.json"
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(path))
    path.write_text(
        json.dumps({
            "plugins": {
                "entries": {
                    "memory-powercontext": {"config": {"endpoint": "http://192.0.2.10", "allowInsecureHttp": True}}
                }
            }
        })
    )
    assert transport_diagnostic("openclaw").status == "degraded"
    monkeypatch.setenv("POWERCONTEXT_OPENCLAW_BASE_URL", "http://192.0.2.11")
    assert transport_diagnostic("openclaw").status == "failed"


def test_doctor_does_not_claim_safety_for_unreadable_native_configuration(tmp_path, monkeypatch):
    from powercontext.cli.transport import transport_diagnostic

    path = tmp_path / "openclaw.json"
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(path))
    path.write_text("{ this is not plain JSON }")
    assert transport_diagnostic("openclaw").status != "ok"


def test_dsh_setup_checks_the_web_profile_even_with_another_runtime_profile(tmp_path, monkeypatch):
    from powercontext.cli.transport import prepare_setup_transport

    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    monkeypatch.setenv("DSH_PROFILE", "custom")
    patch = tmp_path / "profiles/web/cordis.patch.yml"
    patch.parent.mkdir(parents=True)
    patch.write_text("- id: powercontext-dsh\n  config:\n    baseUrl: https://old.example\n")
    with pytest.raises(RuntimeError, match="DSH"):
        prepare_setup_transport("dsh", server_url="https://new.example", json_output=True)
