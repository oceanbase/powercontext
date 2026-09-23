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
import re
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPOSITORY_ROOT / "integrations" / "claude-code" / "plugins" / "powercontext"
_WINDOWS_DRIVE_PATH = re.compile(r"(?:^|[\"'\s(=])[A-Za-z]:[\\/]", re.MULTILINE)
_WINDOWS_UNC_PATH = re.compile(r"(?:^|[\"'\s(=])\\\\[A-Za-z0-9_.-]+\\[A-Za-z0-9_$.-]+", re.MULTILINE)


def test_repository_exposes_a_claude_marketplace() -> None:
    marketplace = json.loads((REPOSITORY_ROOT / ".claude-plugin" / "marketplace.json").read_text())

    assert marketplace["name"] == "powercontext"
    assert marketplace["plugins"] == [
        {
            "name": "powercontext",
            "source": "./integrations/claude-code/plugins/powercontext",
            "description": "Restore project memory and transfer current work from Claude Code",
            "version": "0.1.2",
            "category": "Productivity",
        }
    ]


def test_plugin_uses_standard_component_discovery() -> None:
    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())

    assert manifest["name"] == "powercontext"
    assert manifest["version"] == "0.1.2"
    assert "hooks" not in manifest
    assert "mcpServers" not in manifest
    assert (PLUGIN_ROOT / "hooks" / "hooks.json").is_file()
    assert (PLUGIN_ROOT / ".mcp.json").is_file()
    assert (PLUGIN_ROOT / "scripts" / "statusline.py").is_file()


def test_hook_uses_exec_form_and_does_not_capture_stop() -> None:
    configuration = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())

    assert set(configuration["hooks"]) == {"UserPromptSubmit"}
    hook = configuration["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert hook["command"] == "powercontext-hook"
    assert hook["args"] == ["--script", "${CLAUDE_PLUGIN_ROOT}/hooks/user_prompt_submit.py"]


def test_mcp_uses_claude_top_level_server_map_and_environment_header() -> None:
    configuration = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())

    assert set(configuration) == {"powercontext"}
    assert configuration["powercontext"] == {
        "type": "http",
        "url": "${user_config.server_url}/mcp",
        "headers": {"Authorization": "${POWERCONTEXT_CLAUDE_AUTHORIZATION:-}"},
    }


def test_scope_resolver_and_workspace_binding_use_the_server(
    scope_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[tuple[str, dict[str, object], str]] = []

    def request(path, payload, *, settings, deadline, method="POST"):
        requests.append((path, payload, method))
        return {"scope_id": "scp_00000000000000000000000000"}

    monkeypatch.setattr(scope_module, "_request_json", request)
    settings = scope_module.PluginSettings(scope_id="scp_00000000000000000000000000")

    resolved = scope_module.resolve_scope_id(
        str(tmp_path),
        session_id="session-1",
        settings=settings,
        deadline=float("inf"),
    )
    bound = scope_module.bind_scope(
        str(tmp_path),
        resolved,
        settings=settings,
        deadline=float("inf"),
    )

    assert bound == resolved
    assert requests[0][0] == "/v1/scope-bindings/resolve"
    assert requests[0][1]["explicit_scope_id"] == resolved
    binding_keys = cast(list[dict[str, Any]], requests[0][1]["binding_keys"])
    assert [key["kind"] for key in binding_keys] == ["session", "workspace"]
    assert binding_keys[0]["external_id"] == "session-1"
    assert binding_keys[1]["external_id"] != str(tmp_path)
    assert requests[1][0] == "/v1/scope-bindings"
    assert requests[1][2] == "PUT"


@pytest.mark.parametrize(
    "value",
    [
        "http://memory.example.com",
        "https://user:password@memory.example.com",
        "https://memory.example.com?token=secret",
        "https://memory.example.com#fragment",
        "file:///tmp/socket",
    ],
)
def test_settings_reject_unsafe_server_urls(settings_module: ModuleType, value: str) -> None:
    with pytest.raises(ValueError):
        settings_module.ClaudeCodePluginSettings(server_url=value)


def test_environment_override_controls_prompt_capture(
    settings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CAPTURE_PROMPTS", "true")
    monkeypatch.setenv("POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS", "false")

    assert settings_module.ClaudeCodePluginSettings.from_environment().capture_prompts is False


def test_saved_plugin_http_option_only_authorizes_its_own_endpoint(settings_module, monkeypatch, tmp_path):
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SERVER_URL", "http://memory.example:8000")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_ALLOW_INSECURE_HTTP", "true")
    assert settings_module.ClaudeCodePluginSettings.from_environment().allow_insecure_http is True

    monkeypatch.setenv("POWERCONTEXT_CLAUDE_SERVER_URL", "http://another.example:8000")
    with pytest.raises(ValueError):
        settings_module.ClaudeCodePluginSettings.from_environment()

    monkeypatch.setenv("POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP", "true")
    assert settings_module.ClaudeCodePluginSettings.from_environment().server_url == "http://another.example:8000"
    monkeypatch.setenv("POWERCONTEXT_CLAUDE_ALLOW_INSECURE_HTTP", "false")
    with pytest.raises(ValueError):
        settings_module.ClaudeCodePluginSettings.from_environment()


@pytest.mark.parametrize(
    "record",
    [[], {"version": 1}, {"version": 1, "server_url": "http://127.0.0.1:8000"}],
)
def test_claude_settings_ignore_malformed_persisted_authorization_records(
    settings_module: ModuleType,
    tmp_path: Path,
    record: object,
) -> None:
    credential = tmp_path / "powercontext" / "credentials.json"
    credential.parent.mkdir()
    credential.write_text(json.dumps(record), encoding="utf-8")
    credential.chmod(0o600)

    assert settings_module._stored_authorization(server_url="http://127.0.0.1:8000", root=tmp_path) is None


def _write_credentials(root: Path, *, server_url: str, authorization: str) -> None:
    credential = root / "powercontext" / "credentials.json"
    credential.parent.mkdir()
    credential.write_text(
        json.dumps({"version": 1, "server_url": server_url, "authorization": authorization}),
        encoding="utf-8",
    )
    credential.chmod(0o600)


def _isolate_claude_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (
        "POWERCONTEXT_CLAUDE_SERVER_URL",
        "POWERCONTEXT_CLAUDE_AUTHORIZATION",
        "CLAUDE_PLUGIN_OPTION_SERVER_URL",
        "POWERCONTEXT_CLIENT_SERVER_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))


def test_effective_client_server_url_loads_its_own_persisted_authorization(
    settings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_claude_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "https://memory.example:8443")
    _write_credentials(tmp_path, server_url="https://memory.example:8443", authorization="Bearer memory-token")

    settings = settings_module.ClaudeCodePluginSettings.from_environment()

    assert settings.server_url == "https://memory.example:8443"
    assert settings.authorization == "Bearer memory-token"


def test_effective_address_does_not_reuse_another_servers_persisted_authorization(
    settings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_claude_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "https://memory.example:8443")
    _write_credentials(tmp_path, server_url="http://127.0.0.1:8000", authorization="Bearer loopback-token")

    settings = settings_module.ClaudeCodePluginSettings.from_environment()

    assert settings.server_url == "https://memory.example:8443"
    assert settings.authorization is None


def test_non_canonical_effective_address_still_matches_its_persisted_authorization(
    settings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_claude_environment(monkeypatch, tmp_path)
    _write_credentials(tmp_path, server_url="https://memory.example", authorization="Bearer memory-token")

    settings = settings_module.ClaudeCodePluginSettings.from_environment(server_url="https://MEMORY.example:443/")

    assert settings.server_url == "https://memory.example"
    assert settings.authorization == "Bearer memory-token"


def test_http_base_url_canonicalizes_case_and_default_ports(settings_module: ModuleType) -> None:
    assert settings_module._http_base_url("https://MEMORY.example:443/") == "https://memory.example"
    assert settings_module._http_base_url("http://127.0.0.1:80") == "http://127.0.0.1"
    assert settings_module._http_base_url("http://[::1]:80/") == "http://[::1]"
    assert settings_module._http_base_url("https://memory.example:8443") == "https://memory.example:8443"


def test_explicit_entry_point_server_url_loads_its_own_persisted_authorization(
    settings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_claude_environment(monkeypatch, tmp_path)
    _write_credentials(tmp_path, server_url="https://memory.example:8443", authorization="Bearer memory-token")

    settings = settings_module.ClaudeCodePluginSettings.from_environment(server_url="https://memory.example:8443")

    assert settings.server_url == "https://memory.example:8443"
    assert settings.authorization == "Bearer memory-token"

    explicit = settings_module.ClaudeCodePluginSettings.from_environment(server_url="https://other.example:9443")

    assert explicit.server_url == "https://other.example:9443"
    assert explicit.authorization is None


def test_claude_integration_does_not_embed_machine_specific_windows_paths() -> None:
    roots = (
        REPOSITORY_ROOT / ".claude-plugin",
        REPOSITORY_ROOT / "integrations" / "claude-code",
    )
    files = [path for root in roots for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
    matches = {
        str(path.relative_to(REPOSITORY_ROOT)): match.group(0).strip()
        for path in files
        for pattern in (_WINDOWS_DRIVE_PATH, _WINDOWS_UNC_PATH)
        if (match := pattern.search(path.read_text(encoding="utf-8"))) is not None
    }

    assert matches == {}
