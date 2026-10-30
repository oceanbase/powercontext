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
import shlex
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from powercontext.cli import installation
from powercontext.cli.app import create_cli
from powercontext.cli.installation import installers
from powercontext.cli.system import setup_app
from tests.install_support import stub_installer


@pytest.fixture(autouse=True)
def isolate_client_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("powercontext.cli.system._run_codex_json", lambda *_args: {"installed": []})
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    monkeypatch.delenv("POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP", raising=False)
    monkeypatch.delenv("POWERCONTEXT_CLIENT_SERVER_URL", raising=False)


@pytest.mark.parametrize("host", ["codex", "claude-code", "dsh", "openclaw", "opencode", "pi", "hermes", "workbuddy"])
def test_every_setup_rejects_remote_http_without_consent_before_install(host, monkeypatch):
    if host == "dsh":
        monkeypatch.setattr("powercontext.cli.dsh.which", lambda _name: None)
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", host, "--server-url", "http://192.0.2.10:8000", "--json"]
    )
    assert result.exit_code == 1
    assert "--allow-insecure-http" in result.output


@pytest.mark.parametrize("host", ["codex", "claude-code", "dsh", "openclaw", "opencode", "pi", "hermes", "workbuddy"])
def test_discovered_local_port_persists_for_every_agent(install_connection, host, tmp_path):
    """A new Agent session keeps the selected local listener, not the default port."""
    from powercontext.cli.transport import prepare_setup_transport
    from powercontext.client.transport_policy import resolve_client_transport

    (tmp_path / ".env").write_text("POWERCONTEXT_SERVER_HTTP_PORT=18000\n")
    selected = prepare_setup_transport(host)
    assert selected.server_url == "http://127.0.0.1:18000"
    install_connection(selected)
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


@pytest.mark.parametrize("source", ["file", "process"])
@pytest.mark.parametrize("port", [None, "18000"])
@pytest.mark.parametrize("setting", ["POWERCONTEXT_SERVER_HTTP_UDS", "POWERCONTEXT_SERVER_HTTP"])
def test_uds_server_has_no_implicit_agent_setup_endpoint(tmp_path, monkeypatch, source, port, setting):
    from powercontext.cli.transport import prepare_setup_transport

    value = '{"uds":"/run/powercontext/core.sock"}' if setting.endswith("HTTP") else "/run/powercontext/core.sock"
    values = {setting: value}
    if port is not None:
        values["POWERCONTEXT_SERVER_HTTP_PORT"] = port
    if source == "file":
        (tmp_path / ".env").write_text("".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()))
    else:
        for key, value in values.items():
            monkeypatch.setenv(key.lower(), value)

    with pytest.raises(RuntimeError, match="uses UDS and has no TCP endpoint") as error:
        prepare_setup_transport("pi", json_output=True)

    assert "--server-url" in str(error.value)
    assert "/run/powercontext/core.sock" not in str(error.value)
    assert not (tmp_path / "clients.json").exists()


@pytest.mark.parametrize("source", ["explicit", "saved", "public"])
def test_uds_server_preserves_declared_agent_access_urls(tmp_path, monkeypatch, source):
    from powercontext.cli.transport import prepare_setup_transport
    from powercontext.client.transport_policy import client_config_file

    url = "https://proxy.example/prefix"
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_UDS", "/run/powercontext/core.sock")
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", "18000")
    if source == "saved":
        client_config_file().write_text(json.dumps({"version": 1, "hosts": {"pi": {"server_url": url}}}))
    elif source == "public":
        (tmp_path / ".env").write_text(f"POWERCONTEXT_SERVER_PUBLIC_URL={url}\n")

    selected = prepare_setup_transport("pi", server_url=url if source == "explicit" else None)

    assert selected.server_url == url


@pytest.mark.parametrize("configuration", ["file", "process", "both", "explicit"])
def test_new_codex_endpoint_replaces_saved_and_native_settings(tmp_path, monkeypatch, configuration):
    """Refreshing setup persists a newly supplied URL despite stale installed settings."""
    from powercontext.client.transport_policy import client_config_file, load_client_settings

    old_url = "http://127.0.0.1:8100"
    new_url = "http://127.0.0.1:17429"
    settings = client_config_file()
    settings.write_text(json.dumps({"version": 1, "hosts": {"codex": {"server_url": old_url, "custom": "keep"}}}))
    # Obsolete cache versions must not prevent a new endpoint from being installed.
    for version, url in (("0.1.0", old_url), ("0.2.0", "http://127.0.0.1:8000")):
        cache = tmp_path / "codex/plugins/cache/powercontext/powercontext" / version
        cache.mkdir(parents=True)
        (cache / ".mcp.json").write_text(json.dumps({"mcpServers": {"powercontext": {"url": url + "/mcp/"}}}))
    if configuration in {"file", "both"}:
        (tmp_path / ".env").write_text(f"POWERCONTEXT_CLIENT_SERVER_URL={new_url}\n")
    if configuration in {"process", "both"}:
        monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", new_url)
    installer = Mock(return_value={})
    stub_installer(monkeypatch, "codex", installer)

    result = installation.setup(
        "codex", source="local-source", ref="master", server_url=new_url if configuration == "explicit" else None
    )

    assert result == {}
    assert load_client_settings("codex")["server_url"] == new_url
    assert json.loads(settings.read_text())["hosts"]["codex"] == {
        "server_url": new_url,
        "custom": "keep",
        "allow_insecure_http": False,
    }


def test_saved_endpoints_still_require_choice_without_new_configuration(tmp_path, monkeypatch):
    """Conflicting persisted settings remain ambiguous when no new URL is supplied."""
    from powercontext.cli.transport import client_config_file, prepare_setup_transport

    client_config_file().write_text(
        json.dumps({"version": 1, "hosts": {"codex": {"server_url": "http://127.0.0.1:8100"}}})
    )
    cache = tmp_path / "codex/plugins/cache/powercontext/powercontext/0.1.0"
    cache.mkdir(parents=True)
    (cache / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"powercontext": {"url": "http://127.0.0.1:8000/mcp/"}}})
    )

    monkeypatch.setattr(
        "powercontext.cli.system._run_codex_json",
        lambda *_args: {
            "installed": [
                {
                    "name": "powercontext",
                    "installed": True,
                    "marketplaceName": "powercontext",
                    "version": "0.1.0",
                }
            ]
        },
    )
    with pytest.raises(RuntimeError, match=r"Conflicting.*saved client settings, native host settings"):
        prepare_setup_transport("codex")


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


def test_explicit_environment_file_selects_installer_endpoint(tmp_path, monkeypatch, installed_agent_checks):
    """The setup group loads --env-file safely before handing one URL to installers."""
    import powercontext.cli.pi as pi
    from powercontext.cli.system import Diagnostic, DiagnosticStatus

    path = tmp_path / "chosen.env"
    path.write_text("POWERCONTEXT_CLIENT_SERVER_URL=https://proxy.example/path\n")
    installer = Mock(
        return_value={
            "package": "test",
            "package_path": "test",
            "data_dir": "test",
        }
    )
    stub_installer(monkeypatch, "pi", installer)
    monkeypatch.setattr(pi.PiInstaller, "verify", staticmethod(lambda: {"pi": Diagnostic(DiagnosticStatus.OK, "ok")}))
    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "--env-file", str(path), "pi", "--json"])
    assert result.exit_code == 0, result.output
    from powercontext.client.transport_policy import resolve_client_transport

    assert resolve_client_transport("pi")[0] == "https://proxy.example/path"


@pytest.mark.parametrize("content", [None, b"SECRET='private-value", b"\xff"])
def test_invalid_explicit_environment_file_fails_before_installation(tmp_path, monkeypatch, content):
    """Missing or malformed explicit files fail safely without installing an Agent."""

    path = tmp_path / "invalid.env"
    if content is not None:
        path.write_bytes(content)
    installer = Mock(side_effect=AssertionError("Invalid configuration must not install an Agent"))
    stub_installer(monkeypatch, "pi", installer)
    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "--env-file", str(path), "pi", "--json"])

    assert result.exit_code == 1, result.output
    assert "Cannot read setup environment file" in result.output
    assert "private-value" not in result.output
    assert not (tmp_path / "clients.json").exists()


@pytest.mark.parametrize("host", [name for name in installers() if name != "kimi-code"])
@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:18000", "https://proxy.example/prefix"])
def test_setup_persists_adapter_endpoint(host, endpoint, tmp_path, monkeypatch, installed_agent_checks):
    """The setup CLI applies endpoint policy and preserve unrelated user preferences."""

    from powercontext.cli.transport import client_config_file

    if host == "zcode":
        endpoint = endpoint.removesuffix("/prefix")
    if host == "dsh":
        monkeypatch.setattr("powercontext.cli.dsh.which", lambda _name: None)
    path = client_config_file()
    path.write_text(json.dumps({"version": 1, "hosts": {host: {"custom": "keep"}, "other": {"custom": True}}}))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    (tmp_path / ".env").write_text("POWERCONTEXT_SERVER_HTTP_PORT=18000\n")

    def install(request):
        """Stand in for an external Agent installer and check its effective endpoint."""
        assert request.host == host
        assert request.server_url == endpoint
        return {
            "marketplace": "marketplace",
            "plugin": "plugin",
            "plugin_version": "1.0",
            "data_dir": "data",
        }

    stub_installer(monkeypatch, host, install)
    arguments = ["setup", host, "--json"]
    if endpoint.startswith("https:"):
        arguments.extend(["--server-url", endpoint])
    result = CliRunner().invoke(create_cli([setup_app]), arguments)
    assert result.exit_code == 0, result.output
    saved = json.loads(path.read_text())["hosts"]
    assert saved[host] == {"custom": "keep", "server_url": endpoint, "allow_insecure_http": False}
    assert saved["other"] == {"custom": True}


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
def test_setup_consent_precedence(
    install_connection,
    tmp_path,
    monkeypatch,
    file_consent,
    process_consent,
    explicit_consent,
    allowed,
    installed_agent_checks,
):
    from powercontext.cli.transport import prepare_setup_transport
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
    install_connection(selected)
    path.unlink()
    monkeypatch.delenv("POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP", raising=False)
    assert resolve_client_transport("pi") == ("http://192.0.2.10:18000", True)


def test_failed_setup_env_file_does_not_install_or_leak_into_next_invocation(tmp_path, monkeypatch):
    from powercontext.cli.transport import resolve_setup_endpoint

    chosen = tmp_path / "chosen.env"
    chosen.write_text("POWERCONTEXT_CLIENT_SERVER_URL=http://192.0.2.10\n")
    (tmp_path / ".env").write_text("POWERCONTEXT_SERVER_HTTP_PORT=18000\n")
    installer = Mock(side_effect=AssertionError("Rejected configuration must not install an Agent"))
    stub_installer(monkeypatch, "pi", installer)
    command = ["pi"]
    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "--env-file", str(chosen), *command, "--json"])

    assert result.exit_code == 1, result.output
    assert "--allow-insecure-http" in result.output
    assert not (tmp_path / "clients.json").exists()
    assert resolve_setup_endpoint("pi") == "http://127.0.0.1:18000"


def test_json_setup_never_prompts_even_with_tty(monkeypatch):
    from powercontext.cli import transport

    monkeypatch.setattr(transport.sys.stdin, "isatty", lambda: True)
    confirm = Mock(side_effect=AssertionError("JSON setup must not prompt"))
    monkeypatch.setattr(transport.typer, "confirm", confirm)
    with pytest.raises(RuntimeError, match="--allow-insecure-http"):
        transport.prepare_setup_transport("pi", server_url="http://192.0.2.10", json_output=True)


def test_saved_consent_does_not_follow_changed_url(install_connection, monkeypatch):
    from powercontext.cli import transport

    settings = transport.prepare_setup_transport("pi", server_url="http://192.0.2.10", allow_insecure_http=True)
    install_connection(settings)
    assert transport.prepare_setup_transport("pi", json_output=True).allow_insecure_http
    with pytest.raises(RuntimeError, match="--allow-insecure-http"):
        transport.prepare_setup_transport("pi", server_url="http://192.0.2.11", json_output=True)


def test_setup_saves_only_nonsecret_fields_and_preserves_other_hosts(
    install_connection, monkeypatch, installed_agent_checks
):
    from powercontext.cli import transport

    monkeypatch.setenv("POWERCONTEXT_CLIENT_AUTHORIZATION", "Bearer test-secret")
    for host in ("pi", "dsh"):
        install_connection(transport.prepare_setup_transport(host, server_url="https://example.test"))
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


def test_hermes_setup_synchronizes_native_endpoint_and_preserves_preferences(install_connection, tmp_path, monkeypatch):
    from powercontext.cli.transport import SetupTransport

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "powercontext/config.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"base_url": "https://old.example", "capture": False}))
    install_connection(SetupTransport("hermes", "http://192.0.2.10", True))
    assert json.loads(path.read_text()) == {
        "base_url": "http://192.0.2.10",
        "capture": False,
        "allow_insecure_http": True,
    }


@pytest.mark.parametrize("failed_file", ["shared", "native"])
def test_failed_persistence_restores_native_settings(install_connection, tmp_path, monkeypatch, failed_file):
    from powercontext.cli.transport import SetupTransport, client_config_file

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    native = tmp_path / "powercontext/config.json"
    native.parent.mkdir()
    original = '{"base_url":"https://old.example","capture":false}'
    native.write_text(original)
    write = installation.write_bytes

    def fail_shared(path, content):
        if path == (client_config_file() if failed_file == "shared" else native):
            raise OSError("simulated disk failure")  # noqa: TRY003
        write(path, content)

    monkeypatch.setattr(installation, "write_bytes", fail_shared)
    with pytest.raises(OSError):
        install_connection(SetupTransport("hermes", "http://192.0.2.10", True))
    assert native.read_text() == original
    assert not client_config_file().exists()


def test_codex_setup_updates_native_mcp_endpoint_without_touching_headers(tmp_path, monkeypatch):

    from powercontext.cli.system import CodexInstaller
    from powercontext.cli.transport import SetupTransport

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
    monkeypatch.setattr(
        "powercontext.cli.system._run_codex_json",
        lambda *args: {
            "installed": [
                {"name": "powercontext", "installed": True, "marketplaceName": "powercontext", "version": "0.1.0"}
            ]
        },
    )
    CodexInstaller().configure(SetupTransport("codex", "http://192.0.2.10:8000", True))
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


@pytest.fixture
def install_connection(monkeypatch):
    """Exercise persistence through the real operation with native commands substituted."""

    def complete(settings):
        if settings.host == "hermes":
            from powercontext.cli.hermes import HermesInstaller, hermes_home

            marker = hermes_home() / "plugins/powercontext/__init__.py"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            return HermesInstaller().configure(settings)
        with monkeypatch.context() as native:
            stub_installer(native, settings.host, lambda request: {})
            return installation.setup(
                settings.host,
                source="unused",
                server_url=settings.server_url,
                allow_insecure_http=settings.allow_insecure_http,
            )

    return complete


@pytest.mark.parametrize("flat_override", [False, True])
def test_setup_json_listener_override_matches_server_precedence(tmp_path, monkeypatch, flat_override):
    from powercontext.cli.transport import resolve_setup_endpoint

    (tmp_path / ".env").write_text("POWERCONTEXT_SERVER_HTTP_UDS=/run/private/core.sock\n")
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP", '{"uds":null,"port":18123}')
    if flat_override:
        monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_UDS", "/run/private/other.sock")
        with pytest.raises(ValueError, match="uses UDS and has no TCP endpoint"):
            resolve_setup_endpoint("pi")
    else:
        assert resolve_setup_endpoint("pi") == "http://127.0.0.1:18123"
