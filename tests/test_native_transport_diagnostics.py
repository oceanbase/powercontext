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

"""Doctor resolves native endpoints without making network or configuration changes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_native_settings(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith(("POWERCONTEXT_", "CLAUDE_PLUGIN_OPTION_", "DSH_", "OPENCLAW_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    for name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME", "WORKBUDDY_HOME", "DSH_HOME", "OPENCLAW_STATE_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))


def _write(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def _resolve(host: str) -> tuple[str, bool]:
    from powercontext.cli.native_transport import resolve_host_transport

    return resolve_host_transport(host)


def _save_shared(tmp_path: Path, host: str, url: str, consent: bool) -> None:
    _write(
        tmp_path / "clients.json", {"version": 1, "hosts": {host: {"server_url": url, "allow_insecure_http": consent}}}
    )


@pytest.mark.parametrize("host", ["claude-code", "hermes", "openclaw"])
def test_native_http_configuration_is_not_mistaken_for_loopback(host, tmp_path, monkeypatch):
    url = "http://memory.example:8000"
    if host == "claude-code":
        _write(
            tmp_path / "CLAUDE_CONFIG_DIR/settings.json",
            {
                "pluginConfigs": {
                    "powercontext@powercontext": {"options": {"server_url": url, "allow_insecure_http": True}}
                },
            },
        )
        prefix, url_key = "CLAUDE", "SERVER_URL"
    elif host == "hermes":
        _write(tmp_path / "HERMES_HOME/powercontext/config.json", {"base_url": url, "allow_insecure_http": True})
        prefix, url_key = "HERMES", "BASE_URL"
    else:
        _write(
            tmp_path / "OPENCLAW_STATE_DIR/openclaw.json",
            {
                "plugins": {
                    "entries": {"memory-powercontext": {"config": {"endpoint": url, "allowInsecureHttp": True}}}
                },
            },
        )
        prefix, url_key = "OPENCLAW", "BASE_URL"
    assert _resolve(host) == (url, True)
    monkeypatch.setenv(f"POWERCONTEXT_{prefix}_{url_key}", "http://another.example")
    assert _resolve(host) == ("http://another.example", False)
    monkeypatch.setenv("POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP", "true")
    assert _resolve(host) == ("http://another.example", True)
    monkeypatch.setenv(f"POWERCONTEXT_{prefix}_ALLOW_INSECURE_HTTP", "false")
    assert _resolve(host) == ("http://another.example", False)


def test_claude_option_environment_overrides_native_options(tmp_path, monkeypatch):
    _write(
        tmp_path / "CLAUDE_CONFIG_DIR/settings.json",
        {
            "pluginConfigs": {"powercontext@powercontext": {"options": {"server_url": "https://old.example"}}},
        },
    )
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SERVER_URL", "http://memory.example/mcp/")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_ALLOW_INSECURE_HTTP", "true")
    assert _resolve("claude-code") == ("http://memory.example", True)


def test_hermes_native_url_precedes_common_url(tmp_path, monkeypatch):
    custom = _write(tmp_path / "custom-hermes.json", {"base_url": "http://memory.example", "allow_insecure_http": True})
    monkeypatch.setenv("POWERCONTEXT_HERMES_CONFIG", str(custom))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "https://common.example")
    assert _resolve("hermes") == ("http://memory.example", True)


def test_openclaw_config_path_and_common_url_precedence(tmp_path, monkeypatch):
    custom = _write(
        tmp_path / "custom-openclaw.json",
        {
            "plugins": {"entries": {"memory-powercontext": {"config": {"endpoint": "https://native.example"}}}},
        },
    )
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(custom))
    assert _resolve("openclaw") == ("https://native.example", False)
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "http://common.example")
    assert _resolve("openclaw") == ("http://common.example", False)


def test_codex_installed_mcp_endpoint_is_authoritative(tmp_path, monkeypatch):
    _write(
        tmp_path / "CODEX_HOME/plugins/cache/local/powercontext/0.1.0/.mcp.json",
        {
            "mcpServers": {"powercontext": {"url": "http://memory.example:8000/mcp"}},
        },
    )
    _save_shared(tmp_path, "codex", "http://memory.example:8000/", True)
    monkeypatch.setenv("POWERCONTEXT_CODEX_SERVER_URL", "https://irrelevant.example")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "https://irrelevant-common.example")
    assert _resolve("codex") == ("http://memory.example:8000", True)


@pytest.mark.parametrize("second_url", ["http://memory.example/mcp/", "http://another.example/mcp"])
def test_codex_multiple_cache_versions_require_an_unambiguous_endpoint(tmp_path, second_url):
    for version, url in (("1", "http://memory.example/mcp"), ("2", second_url)):
        _write(
            tmp_path / f"CODEX_HOME/plugins/cache/local/powercontext/{version}/.mcp.json",
            {
                "mcpServers": {"powercontext": {"url": url}},
            },
        )
    if "another" in second_url:
        with pytest.raises(ValueError, match="determine"):
            _resolve("codex")
    else:
        assert _resolve("codex") == ("http://memory.example", False)


def test_workbuddy_native_mcp_template_matches_hook_endpoint(tmp_path, monkeypatch):
    _write(
        tmp_path / "WORKBUDDY_HOME/mcp.json",
        {
            "mcpServers": {"powercontext": {"url": "${POWERCONTEXT_WORKBUDDY_SERVER_URL:-http://memory.example}/mcp"}},
        },
    )
    _save_shared(tmp_path, "workbuddy", "http://memory.example", True)
    assert _resolve("workbuddy") == ("http://memory.example", True)
    monkeypatch.setenv("POWERCONTEXT_WORKBUDDY_SERVER_URL", "http://another.example")
    assert _resolve("workbuddy") == ("http://another.example", False)


def test_workbuddy_divergent_mcp_and_hook_urls_are_reported_unknown(tmp_path):
    _write(
        tmp_path / "WORKBUDDY_HOME/mcp.json",
        {
            "mcpServers": {"powercontext": {"url": "http://native.example/mcp"}},
        },
    )
    _save_shared(tmp_path, "workbuddy", "https://hook.example", False)
    with pytest.raises(ValueError, match="determine"):
        _resolve("workbuddy")


@pytest.mark.parametrize(
    "relative_path", ["cordis.patch.yml", "profiles/web/cordis.patch.yml", "profiles/custom/cordis.patch.yml"]
)
def test_dsh_runtime_overlays_are_not_assumed_to_use_loopback(tmp_path, monkeypatch, relative_path):
    path = tmp_path / "DSH_HOME" / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("plugins:\n  powercontext:\n    baseUrl: http://memory.example\n")
    if "custom" in relative_path:
        monkeypatch.setenv("DSH_PROFILE", "custom")
    with pytest.raises(ValueError, match="determine"):
        _resolve("dsh")


@pytest.mark.parametrize(
    "contents", ["# No profile overrides\n[]\n", "# No profile overrides\n", "[] # empty patches\n"]
)
def test_dsh_empty_generated_overlay_does_not_hide_saved_transport(tmp_path, contents):
    path = tmp_path / "DSH_HOME/profiles/web/cordis.patch.yml"
    path.parent.mkdir(parents=True)
    path.write_text(contents)
    _save_shared(tmp_path, "dsh", "http://memory.example", True)
    assert _resolve("dsh") == ("http://memory.example", True)


@pytest.mark.parametrize(
    "contents", ["{secret-token: 'secret-value'}", '{"plugins": []}', '{"$include": "private.json"}']
)
def test_unsupported_native_config_is_reported_without_secret_content(tmp_path, contents):
    path = tmp_path / "OPENCLAW_STATE_DIR/openclaw.json"
    path.parent.mkdir(parents=True)
    path.write_text(contents)
    with pytest.raises(ValueError) as caught:
        _resolve("openclaw")
    assert "secret-value" not in str(caught.value)
    assert "private.json" not in str(caught.value)


@pytest.mark.parametrize("consent", ["perhaps", 2, None])
def test_openclaw_native_boolean_is_validated(tmp_path, consent):
    _write(
        tmp_path / "OPENCLAW_STATE_DIR/openclaw.json",
        {
            "plugins": {
                "entries": {
                    "memory-powercontext": {
                        "config": {"endpoint": "https://memory.example", "allowInsecureHttp": consent}
                    }
                }
            },
        },
    )
    with pytest.raises(ValueError):
        _resolve("openclaw")


def test_missing_native_config_preserves_shared_endpoint_and_consent(tmp_path):
    _save_shared(tmp_path, "pi", "http://memory.example", True)
    assert _resolve("pi") == ("http://memory.example", True)


def test_openclaw_without_an_endpoint_is_unconfigured_instead_of_loopback():
    with pytest.raises(ValueError, match="not configured"):
        _resolve("openclaw")


@pytest.mark.parametrize(
    "contents", ["# default profile\n[]\n", "- id: powercontext\n  config:\n    baseUrl: http://old.example\n"]
)
def test_dsh_setup_preflight_accepts_inert_defaults_and_requires_manual_custom_configuration(tmp_path, contents):
    from powercontext.cli.native_transport import validate_dsh_setup_transport

    path = tmp_path / "DSH_HOME/profiles/web/cordis.patch.yml"
    path.parent.mkdir(parents=True)
    path.write_text(contents)
    if "old.example" in contents:
        with pytest.raises(ValueError, match="manually"):
            validate_dsh_setup_transport()
    else:
        validate_dsh_setup_transport()
    assert path.read_text() == contents
