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
import re
import shlex
import subprocess
import sys
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
            "version": "0.1.0",
            "category": "Productivity",
        }
    ]


def test_plugin_uses_standard_component_discovery() -> None:
    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())

    assert manifest["name"] == "powercontext"
    assert "hooks" not in manifest
    assert "mcpServers" not in manifest
    assert (PLUGIN_ROOT / "hooks" / "hooks.json").is_file()
    assert (PLUGIN_ROOT / ".mcp.json").is_file()


def test_hook_uses_exec_form_and_does_not_capture_stop() -> None:
    configuration = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())

    assert set(configuration["hooks"]) == {"UserPromptSubmit"}
    hook = configuration["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert hook["command"] == "python3"
    assert hook["args"] == ["${CLAUDE_PLUGIN_ROOT}/hooks/user_prompt_submit.py"]


def test_mcp_uses_claude_top_level_server_map_and_optional_header_helper() -> None:
    configuration = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())

    assert set(configuration) == {"powercontext"}
    assert configuration["powercontext"] == {
        "type": "http",
        "url": "${user_config.server_url}/mcp",
        "headersHelper": (
            "python3 -c 'import json, os; value = "
            'os.environ.get("POWERCONTEXT_CLAUDE_AUTHORIZATION"); '
            'print(json.dumps({"Authorization": value} if value else {}))\''
        ),
    }


def test_mcp_header_helper_command_does_not_depend_on_plugin_root() -> None:
    configuration = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())
    helper_command = configuration["powercontext"]["headersHelper"]
    environment = {
        **os.environ,
        "POWERCONTEXT_CLAUDE_AUTHORIZATION": "Bearer test-token",
    }

    completed = subprocess.run(
        shlex.split(helper_command),
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(completed.stdout) == {"Authorization": "Bearer test-token"}
    assert "${CLAUDE_PLUGIN_ROOT}" not in helper_command


def test_header_helper_omits_authorization_when_unset() -> None:
    environment = dict(os.environ)
    environment.pop("POWERCONTEXT_CLAUDE_AUTHORIZATION", None)

    completed = subprocess.run(
        [sys.executable, str(PLUGIN_ROOT / "scripts" / "mcp_headers.py")],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(completed.stdout) == {}


def test_header_helper_emits_configured_authorization_without_logging_it() -> None:
    environment = {
        **os.environ,
        "POWERCONTEXT_CLAUDE_AUTHORIZATION": "Bearer test-token",
    }

    completed = subprocess.run(
        [sys.executable, str(PLUGIN_ROOT / "scripts" / "mcp_headers.py")],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(completed.stdout) == {"Authorization": "Bearer test-token"}
    assert completed.stderr == ""


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
    settings = scope_module.ClaudeCodePluginSettings(scope_id="scp_00000000000000000000000000")

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
