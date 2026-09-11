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

"""Transport policy at independently installed Python plugin entry points."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOSTS = {
    "codex": ("settings.py", "CodexPluginSettings", "CODEX"),
    "claude-code": ("claude_code_settings.py", "ClaudeCodePluginSettings", "CLAUDE"),
    "workbuddy": ("hooks/workbuddy_settings.py", "WorkBuddyPluginSettings", "WORKBUDDY"),
    "hermes": ("__init__.py", "PowerContextClient", "HERMES"),
}


@pytest.fixture(params=HOSTS)
def plugin(request, monkeypatch, tmp_path):
    host = request.param
    filename, class_name, prefix = HOSTS[host]
    for key in os.environ:
        if key.startswith(("POWERCONTEXT_", "CLAUDE_PLUGIN_OPTION_")):
            monkeypatch.delenv(key)
    config_path = tmp_path / "clients.json"
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(config_path))
    plugin_path = ROOT / "integrations" / host / "plugins" / "powercontext"
    monkeypatch.syspath_prepend(str(plugin_path))
    monkeypatch.syspath_prepend(str((plugin_path / filename).parent))
    name = f"transport_test_{prefix.lower()}"
    spec = importlib.util.spec_from_file_location(name, plugin_path / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    constructor = getattr(module, class_name)

    def create(url="http://memory.example:8000", **kwargs):
        if host == "codex":
            mcp_path = tmp_path / ".mcp.json"
            value = json.loads((plugin_path / ".mcp.json").read_text())
            value["mcpServers"]["powercontext"]["url"] = url.rstrip("/").removesuffix("/mcp") + "/mcp"
            mcp_path.write_text(json.dumps(value))
            monkeypatch.setattr(module, "_MCP_CONFIGURATION_PATH", mcp_path)
            return constructor(**kwargs)
        if host == "hermes":
            return constructor(url, **kwargs)
        return constructor(server_url=url, **kwargs)

    def save(url="http://memory.example:8000/mcp/", consent=True):
        config_path.write_text(
            json.dumps({"version": 1, "hosts": {host: {"server_url": url, "allow_insecure_http": consent}}})
        )

    return host, prefix, constructor, create, save


def test_remote_http_needs_explicit_consent(plugin):
    _host, _prefix, _constructor, create, _save = plugin
    with pytest.raises(ValueError):
        create()
    settings = create(allow_insecure_http=True)
    assert settings.allow_insecure_http is True


def test_common_environment_allows_http_and_host_false_overrides_it(plugin, monkeypatch):
    _host, prefix, _constructor, create, _save = plugin
    monkeypatch.setenv("POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP", "true")
    assert create().allow_insecure_http is True
    monkeypatch.setenv(f"POWERCONTEXT_{prefix}_ALLOW_INSECURE_HTTP", "false")
    with pytest.raises(ValueError):
        create()
    assert create(allow_insecure_http=True).allow_insecure_http is True


def test_explicit_false_overrides_environment_consent(plugin, monkeypatch):
    _host, prefix, _constructor, create, _save = plugin
    monkeypatch.setenv(f"POWERCONTEXT_{prefix}_ALLOW_INSECURE_HTTP", "true")
    with pytest.raises(ValueError):
        create(allow_insecure_http=False)


@pytest.mark.parametrize("value", ["perhaps", "", "2"])
def test_invalid_transport_boolean_is_rejected_even_for_https(plugin, monkeypatch, value):
    _host, prefix, _constructor, create, _save = plugin
    monkeypatch.setenv(f"POWERCONTEXT_{prefix}_ALLOW_INSECURE_HTTP", value)
    with pytest.raises(ValueError, match="bool"):
        create("https://memory.example")


def test_saved_consent_is_bound_to_the_normalized_server_endpoint(plugin):
    _host, _prefix, _constructor, create, save = plugin
    save()
    assert create("http://memory.example:8000/").allow_insecure_http is True
    with pytest.raises(ValueError):
        create("http://another.example:8000")
    with pytest.raises(ValueError):
        create("http://memory.example:8000/another")


def test_environment_false_overrides_saved_consent(plugin, monkeypatch):
    _host, _prefix, _constructor, create, save = plugin
    save()
    monkeypatch.setenv("POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP", "false")
    with pytest.raises(ValueError):
        create()


def test_saved_transport_boolean_requires_a_json_boolean(plugin):
    _host, _prefix, _constructor, create, save = plugin
    save(consent="false")
    with pytest.raises(ValueError, match="bool"):
        create("https://memory.example")


@pytest.mark.parametrize("url", ["http://localhost:8000", "http://127.23.4.5:8000", "http://[::1]:8000"])
def test_loopback_stays_available_without_consent(plugin, url):
    _host, _prefix, _constructor, create, _save = plugin
    assert create(url).allow_insecure_http is False


def test_nonloopback_addresses_are_not_accepted_by_string_prefix(plugin):
    _host, _prefix, _constructor, create, _save = plugin
    for url in ("http://localhost.example", "http://127.0.0.1.example", "http://[::]"):
        with pytest.raises(ValueError):
            create(url)


@pytest.mark.parametrize("url", ["http://user:secret@memory.example", "http://memory.example?token=secret"])
def test_opt_in_does_not_allow_credentials_or_ambiguous_urls(plugin, url):
    _host, _prefix, _constructor, create, _save = plugin
    with pytest.raises(ValueError):
        create(url, allow_insecure_http=True)


def test_environment_loader_uses_the_persisted_url(plugin):
    host, _prefix, constructor, _create, save = plugin
    if host not in {"claude-code", "workbuddy"}:
        pytest.skip("Codex uses its MCP file; Hermes uses its provider configuration")
    save()
    settings = constructor.from_environment()
    assert settings.server_url == "http://memory.example:8000"
    assert settings.allow_insecure_http is True


def test_environment_endpoint_change_does_not_reuse_saved_consent(plugin, monkeypatch):
    host, prefix, constructor, _create, save = plugin
    if host not in {"claude-code", "workbuddy"}:
        pytest.skip("Endpoint source is tested through the constructor")
    save()
    monkeypatch.setenv(f"POWERCONTEXT_{prefix}_SERVER_URL", "http://another.example:8000")
    with pytest.raises(ValueError):
        constructor.from_environment()
