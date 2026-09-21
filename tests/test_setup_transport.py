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
import os
import subprocess
import sys
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.cli.hosts import HOST_NAMES
from powercontext.cli.system import setup_app


@pytest.fixture(autouse=True)
def isolate_client_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.chdir(tmp_path)
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


@pytest.mark.parametrize("host", ["codex", "claude-code", "dsh", "openclaw", "opencode", "pi", "hermes", "workbuddy"])
def test_discovered_local_port_persists_for_every_agent(host, tmp_path):
    """A new Agent session keeps the selected local listener, not the default port."""
    from powercontext.cli.transport import prepare_setup_transport, save_setup_transport
    from powercontext.client.transport_policy import resolve_client_transport

    (tmp_path / ".env").write_text("POWERCONTEXT_SERVER_HTTP_PORT=18000\n")
    selected = prepare_setup_transport(host)
    assert selected.server_url == "http://127.0.0.1:18000"
    save_setup_transport(selected)
    (tmp_path / ".env").unlink()
    assert resolve_client_transport(host)[0] == selected.server_url


def test_explicit_remote_endpoint_overrides_file_and_local_port(tmp_path):
    """An explicit choice supersedes stale file settings without modifying that file."""
    from powercontext.cli.transport import prepare_setup_transport

    path = tmp_path / ".env"
    content = "POWERCONTEXT_CLIENT_SERVER_URL=https://old.example/proxy\nPOWERCONTEXT_SERVER_HTTP_PORT=18000\n"
    path.write_text(content)
    assert (
        prepare_setup_transport("pi", server_url="https://remote.example/proxy").server_url
        == "https://remote.example/proxy"
    )
    assert path.read_text() == content


def test_conflicting_file_and_process_endpoints_require_choice(tmp_path, monkeypatch):
    """Ambiguous endpoints fail with remediation and without disclosing tokens."""
    from powercontext.cli.transport import prepare_setup_transport

    (tmp_path / ".env").write_text("POWERCONTEXT_CLIENT_SERVER_URL=https://one.example\nSECRET=private-value\n")
    monkeypatch.setenv("POWERCONTEXT_PI_BASE_URL", "https://two.example")
    with pytest.raises(RuntimeError, match=r"Conflicting.*--server-url") as error:
        prepare_setup_transport("pi", json_output=True)
    assert "private-value" not in str(error.value)
    with pytest.raises(RuntimeError, match="Unset POWERCONTEXT_PI_BASE_URL"):
        prepare_setup_transport("pi", server_url="https://one.example")
    assert prepare_setup_transport("pi", server_url="https://two.example").server_url == "https://two.example"


def test_explicit_claude_endpoint_rejects_conflicting_runtime_plugin_option(monkeypatch):
    """Do not install credentials for a URL that Claude's runtime option overrides."""
    from powercontext.cli.transport import prepare_setup_transport

    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SERVER_URL", "https://old.example")
    with pytest.raises(RuntimeError, match="Unset CLAUDE_PLUGIN_OPTION_SERVER_URL"):
        prepare_setup_transport("claude-code", server_url="https://selected.example", json_output=True)

    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SERVER_URL", "https://selected.example:443/mcp/")
    selected = prepare_setup_transport("claude-code", server_url="https://selected.example", json_output=True)
    assert selected.server_url == "https://selected.example"


def test_saved_native_remote_endpoint_is_not_replaced_by_local_port(tmp_path, monkeypatch):
    """Local deployment settings never replace an existing proxy endpoint."""
    from powercontext.cli.transport import prepare_setup_transport

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "powercontext/config.json"
    path.parent.mkdir()
    path.write_text('{"base_url":"https://proxy.example/prefix","capture":false}')
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", "18000")
    assert prepare_setup_transport("hermes").server_url == "https://proxy.example/prefix"


def test_explicit_environment_file_selects_installer_endpoint(tmp_path, monkeypatch):
    """The setup group loads --env-file safely before handing one URL to installers."""
    import powercontext.cli.pi as pi
    from powercontext.cli.system import Diagnostic, DiagnosticStatus

    path = tmp_path / "chosen.env"
    path.write_text("POWERCONTEXT_CLIENT_SERVER_URL=https://proxy.example/path\n")
    installer = Mock(return_value=pi.PiSetupResult("test", "test", "test"))
    monkeypatch.setattr(pi, "install_pi_plugin", installer)
    monkeypatch.setattr(pi, "run_pi_diagnostics", lambda: {"pi": Diagnostic(DiagnosticStatus.OK, "ok")})
    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "--env-file", str(path), "pi", "--json"])
    assert result.exit_code == 0, result.output
    assert installer.call_args.kwargs["server_url"] == "https://proxy.example/path"


def test_setup_supports_cli_installation_without_server_dependencies(tmp_path):
    """Resolve client configuration when the optional database dependency is absent."""
    (tmp_path / ".env").write_text("POWERCONTEXT_SERVER_HTTP_PORT=18000\n")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.modules['sqlalchemy'] = None; "
            "from powercontext.cli.transport import prepare_setup_transport; "
            "assert prepare_setup_transport('pi').server_url == 'http://127.0.0.1:18000'",
        ],
        cwd=tmp_path,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("content", [None, b"SECRET='private-value", b"\xff"])
def test_invalid_explicit_environment_file_fails_before_installation(tmp_path, monkeypatch, content):
    """Missing or malformed explicit files fail safely without installing an Agent."""
    from powercontext.cli import hosts

    path = tmp_path / "invalid.env"
    if content is not None:
        path.write_bytes(content)
    installer = Mock(side_effect=AssertionError("Invalid configuration must not install an Agent"))
    monkeypatch.setattr(hosts, "install_host", installer)
    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "--env-file", str(path), "pi", "--json"])

    assert result.exit_code == 1, result.output
    assert "Cannot read setup environment file" in result.output
    assert "private-value" not in result.output
    installer.assert_not_called()
    assert not (tmp_path / "clients.json").exists()


@pytest.mark.parametrize("host", HOST_NAMES)
@pytest.mark.parametrize("bulk", [False, True], ids=["individual", "selected"])
@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:18000", "https://proxy.example/prefix"])
def test_all_setup_routes_persist_adapter_endpoint(host, bulk, endpoint, tmp_path, monkeypatch):
    """Both CLI routes apply identical policy and preserve unrelated user preferences."""
    import importlib

    from powercontext.cli import hosts, system
    from powercontext.cli.transport import client_config_file

    path = client_config_file()
    path.write_text(json.dumps({"version": 1, "hosts": {host: {"custom": "keep"}, "other": {"custom": True}}}))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    (tmp_path / ".env").write_text("POWERCONTEXT_SERVER_HTTP_PORT=18000\n")

    def install(name, **options):
        """Stand in for an external Agent installer and check its effective endpoint."""
        assert name == host
        assert options["server_url"] == endpoint
        return system.CodexSetupResult("marketplace", "plugin", "1.0", "data")

    monkeypatch.setattr(hosts, "install_host", install)
    monkeypatch.setattr(hosts, "verify_host", lambda _host: None)
    if host not in {"claude-code", "openclaw"}:
        module = system if host == "codex" else importlib.import_module(f"powercontext.cli.{host}")
        monkeypatch.setattr(module, f"run_{host}_diagnostics", lambda: {})
    arguments = ["setup", "select", "--host", host, "--json"] if bulk else ["setup", host, "--json"]
    if endpoint.startswith("https:"):
        arguments.extend(["--server-url", endpoint])
    result = CliRunner().invoke(create_cli([setup_app]), arguments)
    assert result.exit_code == 0, result.output
    saved = json.loads(path.read_text())["hosts"]
    assert saved[host] == {"custom": "keep", "server_url": endpoint, "allow_insecure_http": False}
    assert saved["other"] == {"custom": True}
    if host == "hermes":
        native = json.loads((tmp_path / "hermes/powercontext/config.json").read_text())
        assert native["base_url"] == saved[host]["server_url"]


@pytest.mark.parametrize("explicit", [False, True], ids=["discovered", "explicit"])
def test_equivalent_endpoint_spellings_do_not_conflict(tmp_path, monkeypatch, explicit):
    from powercontext.cli.transport import prepare_setup_transport

    (tmp_path / ".env").write_text("POWERCONTEXT_CLIENT_SERVER_URL=https://proxy.example/prefix/mcp/\n")
    monkeypatch.setenv("POWERCONTEXT_PI_BASE_URL", "https://proxy.example:443/prefix/")
    (tmp_path / "clients.json").write_text(
        json.dumps({"version": 1, "hosts": {"pi": {"server_url": "https://proxy.example/prefix"}}})
    )

    selected = prepare_setup_transport(
        "pi", server_url="https://proxy.example/prefix/mcp" if explicit else None, json_output=True
    )
    assert selected.server_url == "https://proxy.example/prefix"
    assert selected.allow_insecure_http is False


@pytest.mark.parametrize(
    ("file_consent", "process_consent", "explicit_consent", "allowed"),
    [
        ("true", None, None, True),
        ("false", None, None, False),
        ("true", "false", None, False),
        ("false", "true", None, True),
        ("true", "true", False, False),
        ("false", "false", True, True),
    ],
    ids=["file-yes", "file-no", "process-no", "process-yes", "explicit-no", "explicit-yes"],
)
def test_setup_consent_precedence(tmp_path, monkeypatch, file_consent, process_consent, explicit_consent, allowed):
    from powercontext.cli.transport import prepare_setup_transport, save_setup_transport
    from powercontext.client.transport_policy import resolve_client_transport

    monkeypatch.delenv("POWERCONTEXT_PI_ALLOW_INSECURE_HTTP", raising=False)
    path = tmp_path / ".env"
    path.write_text(
        f"POWERCONTEXT_CLIENT_SERVER_URL=http://192.0.2.10:18000\nPOWERCONTEXT_PI_ALLOW_INSECURE_HTTP={file_consent}\n"
    )
    if process_consent is not None:
        monkeypatch.setenv("POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP", process_consent)
    if not allowed:
        with pytest.raises(RuntimeError, match="--allow-insecure-http"):
            prepare_setup_transport("pi", allow_insecure_http=explicit_consent, json_output=True)
        assert not (tmp_path / "clients.json").exists()
        return

    selected = prepare_setup_transport("pi", allow_insecure_http=explicit_consent, json_output=True)
    save_setup_transport(selected)
    path.unlink()
    monkeypatch.delenv("POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP", raising=False)
    assert resolve_client_transport("pi") == ("http://192.0.2.10:18000", True)


@pytest.mark.parametrize("bulk", [False, True], ids=["individual", "selected"])
def test_failed_setup_env_file_does_not_install_or_leak_into_next_invocation(tmp_path, monkeypatch, bulk):
    from powercontext.cli import hosts
    from powercontext.cli.transport import resolve_setup_endpoint

    chosen = tmp_path / "chosen.env"
    chosen.write_text("POWERCONTEXT_CLIENT_SERVER_URL=http://192.0.2.10\n")
    (tmp_path / ".env").write_text("POWERCONTEXT_SERVER_HTTP_PORT=18000\n")
    installer = Mock(side_effect=AssertionError("Rejected configuration must not install an Agent"))
    monkeypatch.setattr(hosts, "install_host", installer)
    command = ["select", "--host", "pi"] if bulk else ["pi"]
    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "--env-file", str(chosen), *command, "--json"])

    assert result.exit_code == 1, result.output
    assert "--allow-insecure-http" in result.output
    installer.assert_not_called()
    assert not (tmp_path / "clients.json").exists()
    assert resolve_setup_endpoint("pi") == "http://127.0.0.1:18000"


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
                "url": "http://127.0.0.1:8000/mcp/",
                "env_http_headers": {"Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"},
            }
        }
    }
    path.write_text(json.dumps(original))
    _configure_codex_endpoint("powercontext", "0.1.0", "http://192.0.2.10:8000")
    changed = json.loads(path.read_text())["mcpServers"]["powercontext"]
    assert changed["url"] == "http://192.0.2.10:8000/mcp/"
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
