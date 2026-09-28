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
import socket
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from pydantic import ValidationError

PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "integrations" / "codex" / "plugins" / "powercontext"
REPOSITORY_ROOT = PLUGIN_ROOT.parents[3]


def test_scope_resolver_uses_server_binding_and_fixes_new_session(
    scope_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[tuple[str, dict[str, object], str]] = []

    def post(path, payload, *, settings, deadline, method="POST"):
        requests.append((path, payload, method))
        return {"scope_id": "scp_00000000000000000000000000"}

    monkeypatch.setattr(scope_module, "_post_json", post)
    monkeypatch.setattr(scope_module, "_git_value", lambda *_args, **_kwargs: None)

    resolved = scope_module.resolve_scope_id(
        str(tmp_path),
        session_id="session-1",
        settings=scope_module.CodexPluginSettings(),
        deadline=float("inf"),
        persist_session=True,
    )

    assert resolved == "scp_00000000000000000000000000"
    assert requests[0][0] == "/v1/scope-bindings/resolve"
    binding_keys = cast(list[dict[str, Any]], requests[0][1]["binding_keys"])
    assert [key["kind"] for key in binding_keys] == ["session", "workspace"]
    assert binding_keys[0]["external_id"] == "session-1"
    assert binding_keys[1]["external_id"] != str(tmp_path)
    assert not binding_keys[1]["external_id"].startswith("scp_")
    assert requests[1] == (
        "/v1/scope-bindings",
        {
            "key": {"integration": "codex", "kind": "session", "external_id": "session-1"},
            "scope_id": resolved,
        },
        "PUT",
    )


def test_open_bounded_enforces_the_deadline_while_headers_trickle(scope_module: ModuleType) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = int(listener.getsockname()[1])

    def trickle() -> None:
        connection, _ = listener.accept()
        with connection:
            connection.recv(65_536)
            connection.sendall(b"HTTP/1.1 200 OK\r\n")
            for _ in range(400):
                try:
                    connection.sendall(b"X")
                except OSError:
                    return
                time.sleep(0.05)

    worker = threading.Thread(target=trickle, daemon=True)
    worker.start()
    request = scope_module.Request(f"http://127.0.0.1:{port}/v1/stats", data=b"{}", method="POST")
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            scope_module.open_bounded(request, timeout=0.3)
        elapsed = time.monotonic() - started
    finally:
        listener.close()

    assert elapsed < 1.0


def test_codex_settings_precedence_and_validation(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_CODEX_SERVER_URL", "https://environment.example/api/")
    monkeypatch.setenv("POWERCONTEXT_CODEX_CAPTURE_PROMPTS", "false")
    monkeypatch.setenv("POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS", "4.5")

    environment = recall_module.CodexPluginSettings()
    explicit = recall_module.CodexPluginSettings(server_url="https://explicit.example/")

    assert environment.server_url == "http://127.0.0.1:8000"
    assert environment.capture_prompts is False
    assert environment.request_timeout_seconds == 4.5
    assert explicit.server_url == "http://127.0.0.1:8000"


def test_codex_settings_load_the_optional_mcp_authorization_environment(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_CODEX_AUTHORIZATION", "Bearer secret-token")

    settings = recall_module.CodexPluginSettings()

    assert settings.authorization is not None
    assert settings.authorization.get_secret_value() == "Bearer secret-token"
    assert "secret-token" not in repr(settings)


def test_codex_mcp_uses_an_optional_authorization_environment() -> None:
    plugin_root = Path(__file__).resolve().parents[2] / "integrations" / "codex" / "plugins" / "powercontext"
    configuration = json.loads((plugin_root / ".mcp.json").read_text())

    assert configuration["mcpServers"]["powercontext"]["env_http_headers"] == {
        "Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"
    }
    assert "http_headers" not in configuration["mcpServers"]["powercontext"]


@pytest.mark.parametrize(
    "authorization",
    ["Basic secret-token", "Bearer ", "Bearer token with spaces", "Bearer token\nsecond-header"],
)
def test_codex_settings_reject_invalid_authorization_headers(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    authorization: str,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_CODEX_AUTHORIZATION", authorization)

    with pytest.raises(ValidationError):
        recall_module.CodexPluginSettings()


def test_codex_settings_ignore_unscoped_legacy_names(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_HTTP_URL", "https://legacy.example")

    assert recall_module.CodexPluginSettings().server_url == "http://127.0.0.1:8000"


@pytest.mark.parametrize(
    "value",
    [
        "http://memory.example.com/mcp",
        "https://user:password@memory.example.com/mcp",
        "https://memory.example.com/mcp?token=secret",
        "https://memory.example.com/mcp#fragment",
        "https://memory.example.com/api",
        "file:///tmp/socket/mcp",
    ],
)
def test_codex_settings_reject_unsafe_or_ambiguous_mcp_urls(
    settings_module: ModuleType,
    value: str,
) -> None:
    with pytest.raises(ValueError):
        settings_module._http_base_url(value)


def test_codex_settings_normalize_the_mcp_path_to_http_base(
    settings_module: ModuleType,
) -> None:
    assert settings_module._http_base_url("https://memory.example/api/mcp/") == "https://memory.example/api"


@pytest.mark.parametrize(
    "record",
    [[], {"version": 1}, {"version": 1, "server_url": "http://127.0.0.1:8000"}],
)
def test_codex_settings_ignore_malformed_persisted_authorization_records(
    settings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    record: object,
) -> None:
    credential = tmp_path / "powercontext" / "credentials.json"
    credential.parent.mkdir()
    credential.write_text(json.dumps(record), encoding="utf-8")
    credential.chmod(0o600)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    assert settings_module._stored_authorization("http://127.0.0.1:8000") is None


def test_codex_settings_match_persisted_base_url_with_mcp_endpoint(
    settings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credential = tmp_path / "powercontext" / "credentials.json"
    credential.parent.mkdir()
    credential.write_text(
        json.dumps({"version": 1, "server_url": "http://127.0.0.1:8000", "authorization": "Bearer saved"}),
        encoding="utf-8",
    )
    credential.chmod(0o600)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    assert settings_module._stored_authorization("http://127.0.0.1:8000/mcp") == "Bearer saved"


def test_codex_settings_match_persisted_base_url_with_hook_base_url(
    settings_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credential = tmp_path / "powercontext" / "credentials.json"
    credential.parent.mkdir()
    credential.write_text(
        json.dumps({"version": 1, "server_url": "http://127.0.0.1:8000", "authorization": "Bearer saved"}),
        encoding="utf-8",
    )
    credential.chmod(0o600)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    assert settings_module._stored_authorization("http://127.0.0.1:8000") == "Bearer saved"


def test_codex_settings_environment_authorization_overrides_stored_authorization(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credential = tmp_path / "powercontext" / "credentials.json"
    credential.parent.mkdir()
    credential.write_text(
        json.dumps({"version": 1, "server_url": "http://127.0.0.1:8000", "authorization": "Bearer saved"}),
        encoding="utf-8",
    )
    credential.chmod(0o600)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("POWERCONTEXT_CODEX_AUTHORIZATION", "Bearer process-token")

    authorization = recall_module.CodexPluginSettings().authorization

    assert authorization is not None
    assert authorization.get_secret_value() == "Bearer process-token"


def test_codex_hooks_fix_session_and_data_plane_bindings() -> None:
    configuration = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())

    assert "session_binding.py" in configuration["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    pre_tool_use = configuration["hooks"]["PreToolUse"][0]
    assert pre_tool_use["matcher"] == "mcp__powercontext__.*"
    assert "bind_tools.py" in pre_tool_use["hooks"][0]["command"]


def test_customer_artifact_workflow_does_not_parse_plugin_root_as_an_actions_expression() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "build-artifacts.yml").read_text()

    assert "${{PLUGIN_ROOT}}" not in workflow


def test_powercontext_plugin_advertises_the_one_turn_handoff() -> None:
    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text())
    prompts = manifest["interface"]["defaultPrompt"]

    assert len(prompts) <= 3
    assert all(len(prompt) <= 128 for prompt in prompts)
    assert "Hand off and commit the current work in one turn." in prompts


def test_plugin_reports_token_savings_from_a_bounded_stop_hook() -> None:
    configuration = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())

    assert set(configuration["hooks"]) == {"UserPromptSubmit", "SessionStart", "PreToolUse", "Stop"}
    hook = configuration["hooks"]["Stop"][0]["hooks"][0]
    assert hook == {
        "type": "command",
        "command": (
            'uv run --frozen --quiet --project "${PLUGIN_ROOT}" python "${PLUGIN_ROOT}/hooks/token_savings.py"'
        ),
        "timeout": 10,
        "statusMessage": "Loading PowerContext token savings",
    }
