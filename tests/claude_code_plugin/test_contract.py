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

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from tests.git_worktree_support import ScopeBindings, check_worktree_fallback, make_git_layout

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
            "version": "0.1.4",
            "category": "Productivity",
        }
    ]


def test_plugin_uses_standard_component_discovery() -> None:
    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())

    assert manifest["name"] == "powercontext"
    assert manifest["version"] == "0.1.4"
    assert "hooks" not in manifest
    assert "mcpServers" not in manifest
    assert (PLUGIN_ROOT / "hooks" / "hooks.json").is_file()
    assert (PLUGIN_ROOT / ".mcp.json").is_file()
    assert (PLUGIN_ROOT / "scripts" / "statusline.py").is_file()


def test_hook_uses_exec_form_and_does_not_capture_stop() -> None:
    configuration = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())

    assert set(configuration["hooks"]) == {"SessionStart", "UserPromptSubmit"}
    session_start = configuration["hooks"]["SessionStart"][0]
    assert session_start["matcher"] == "startup|resume|clear|compact|fork"
    session_hook = session_start["hooks"][0]
    assert session_hook["command"] == "python3"
    assert session_hook["args"] == ["${CLAUDE_PLUGIN_ROOT}/hooks/session_start.py"]
    hook = configuration["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert hook["command"] == "python3"
    assert hook["args"] == ["${CLAUDE_PLUGIN_ROOT}/hooks/user_prompt_submit.py"]


def test_mcp_uses_claude_top_level_server_map_and_authorization_headers() -> None:
    configuration = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())

    assert set(configuration) == {"powercontext"}
    # The helper's header overrides the static one; a host that cannot run the helper still sends the environment value.
    assert configuration["powercontext"] == {
        "type": "http",
        "url": "${user_config.server_url}/mcp",
        "headers": {"Authorization": "${POWERCONTEXT_CLAUDE_AUTHORIZATION:-}"},
        "headersHelper": 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_headers.py"',
    }


def _mcp_headers(root: Path, *, mcp_url: str | None, authorization: str | None = None) -> dict[str, str]:
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith(("CLAUDE_", "POWERCONTEXT_"))
    }
    environment.update(HOME=str(root), CLAUDE_CONFIG_DIR=str(root))
    if mcp_url is not None:
        environment["CLAUDE_CODE_MCP_SERVER_URL"] = mcp_url
    if authorization is not None:
        environment["POWERCONTEXT_CLAUDE_AUTHORIZATION"] = authorization
    # -S leaves out site-packages, as for the hooks: the helper may import only the standard library.
    result = subprocess.run(
        [sys.executable, "-S", str(PLUGIN_ROOT / "scripts" / "mcp_headers.py")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("mcp_url", "authorization", "expected"),
    [
        # Setup's saved credential is sent to the Server it was saved for.
        ("http://127.0.0.1:8000/mcp", None, {"Authorization": "Bearer saved-token"}),
        # A token is never sent to another endpoint.
        ("https://memory.example/mcp", None, {}),
        # Claude Code may strip credential-like variables from the helper, so the helper never reads the environment;
        # the saved credential overrides the static environment header.
        ("http://127.0.0.1:8000/mcp", "Bearer environment-token", {"Authorization": "Bearer saved-token"}),
        # Without the endpoint, the helper cannot match a saved credential.
        (None, None, {}),
    ],
)
def test_mcp_headers_helper_sends_only_the_credential_for_its_endpoint(
    tmp_path: Path, mcp_url: str | None, authorization: str | None, expected: dict[str, str]
) -> None:
    _write_credentials(tmp_path, server_url="http://127.0.0.1:8000", authorization="Bearer saved-token")

    assert _mcp_headers(tmp_path, mcp_url=mcp_url, authorization=authorization) == expected


@pytest.mark.skipif(sys.platform == "win32", reason="the shell command is checked with a POSIX shell")
def test_mcp_headers_helper_command_runs_from_a_plugin_root_with_spaces(tmp_path: Path) -> None:
    configuration = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())
    root = tmp_path / "Application Support" / "powercontext"
    shutil.copytree(PLUGIN_ROOT, root)
    _write_credentials(tmp_path, server_url="http://127.0.0.1:8000", authorization="Bearer saved-token")
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith(("CLAUDE_", "POWERCONTEXT_"))
    }
    environment.update(
        HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(tmp_path), CLAUDE_CODE_MCP_SERVER_URL="http://127.0.0.1:8000/mcp"
    )
    # Claude Code substitutes the raw plugin root into the command and runs it through a shell.
    command = configuration["powercontext"]["headersHelper"].replace("${CLAUDE_PLUGIN_ROOT}", str(root))

    result = subprocess.run(  # noqa: S602 - the command is the plugin's own shipped helper.
        command, shell=True, env=environment, capture_output=True, text=True, timeout=30, check=True
    )

    assert json.loads(result.stdout) == {"Authorization": "Bearer saved-token"}


def test_scope_resolver_and_workspace_binding_use_the_server(
    scope_module: ModuleType,
    scope_binding_client: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[tuple[str, dict[str, object], str]] = []

    def request(path, payload, *, host, settings, deadline, method="POST"):
        requests.append((path, payload, method))
        return {"scope_id": "scp_00000000000000000000000000"}

    monkeypatch.setattr(scope_binding_client, "_request_json", request)
    settings = scope_module.ClaudeCodePluginSettings(scope_id="scp_00000000000000000000000000").scope_binding_settings()

    resolved = scope_binding_client.resolve_scope_id(
        scope_module.CLAUDE_CODE,
        str(tmp_path),
        session_id="session-1",
        settings=settings,
        deadline=float("inf"),
    )
    bound = scope_binding_client.bind_scope(
        scope_module.CLAUDE_CODE,
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


def test_a_linked_worktree_resolves_to_its_checkouts_binding_until_it_has_its_own(
    scope_module: ModuleType,
    scope_binding_client: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    layout = make_git_layout(tmp_path, monkeypatch)
    bindings = ScopeBindings()

    def request(path, payload, *, host, settings, deadline, method="POST"):
        if path == "/v1/scope-bindings/resolve":
            return {"scope_id": bindings.resolve(payload["binding_keys"])}
        if path == "/v1/scope-bindings":
            bindings.set(payload["key"], payload["scope_id"])
            return {"scope_id": payload["scope_id"]}
        return {"cleared": bindings.clear(payload["key"])}

    monkeypatch.setattr(scope_binding_client, "_request_json", request)
    host = scope_module.CLAUDE_CODE
    options = {"settings": scope_module.ClaudeCodePluginSettings().scope_binding_settings(), "deadline": float("inf")}

    check_worktree_fallback(
        checkout=layout.checkout,
        worktree=layout.worktree_sub,
        bare_worktree=layout.bare_worktree,
        resolve=lambda cwd: scope_binding_client.resolve_scope_id(host, str(cwd), session_id=None, **options),
        bind=lambda cwd, scope_id: scope_binding_client.bind_scope(host, str(cwd), scope_id, **options),
        clear=lambda cwd: scope_binding_client.clear_scope_binding(host, str(cwd), **options),
    )
    # The first workspace key is still the hash of the resolved Git top level, so existing bindings keep matching.
    keys = scope_binding_client.binding_keys(host, str(layout.worktree_sub), session_id="session-a")
    assert [key["external_id"] for key in keys] == [
        "session-a",
        hashlib.sha256(os.fsencode(layout.worktree)).hexdigest(),
        hashlib.sha256(os.fsencode(layout.checkout)).hexdigest(),
    ]
    assert scope_binding_client.binding_keys(host, str(layout.checkout_sub), session_id=None) == [
        {"integration": host.integration, "kind": "workspace", "external_id": keys[2]["external_id"]}
    ]


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
