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
import stat
from pathlib import Path

import pytest

from powercontext.cli.authorization import (
    AuthorizationResolution,
    clear_stored_authorization,
    credential_path,
    normalize_authorization,
    read_stored_authorization,
    setup_authorization_value,
    setup_server_url,
    write_stored_authorization,
)


def test_normalize_authorization_accepts_bare_tokens_and_headers() -> None:
    assert normalize_authorization("  secret-token  ") == "Bearer secret-token"
    assert normalize_authorization("bearer secret-token") == "Bearer secret-token"


@pytest.mark.parametrize("value", ["", "Bearer", "Bearer a b", "token\n", "Bearer \x00token"])
def test_normalize_authorization_rejects_invalid_values_without_echoing(value: str) -> None:
    with pytest.raises(ValueError, match="authorization") as error:
        normalize_authorization(value)
    if value:
        assert value not in str(error.value)


def test_write_and_read_stored_authorization_is_atomic_and_private(tmp_path: Path) -> None:
    path = tmp_path / "powercontext" / "credentials.json"

    write_stored_authorization(path, server_url="http://127.0.0.1:8000", value="secret-token")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {
        "version": 1,
        "server_url": "http://127.0.0.1:8000",
        "authorization": "Bearer secret-token",
    }
    resolution = read_stored_authorization(path, server_url="http://127.0.0.1:8000")
    assert resolution == AuthorizationResolution("configured", "Bearer secret-token")


def test_read_stored_authorization_ignores_a_different_server_url(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    write_stored_authorization(path, server_url="https://one.example", value="secret-token")

    assert read_stored_authorization(path, server_url="https://two.example") == AuthorizationResolution(
        "url_mismatch", None
    )


def test_read_stored_authorization_fails_closed_for_unsafe_files(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps({"version": 1, "server_url": "https://one.example", "authorization": "Bearer secret-token"})
    )
    path.chmod(0o644)

    assert read_stored_authorization(path, server_url="https://one.example") == AuthorizationResolution(
        "unsafe_permissions", None
    )


def test_write_stored_authorization_rejects_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}")
    path = tmp_path / "credentials.json"
    path.symlink_to(target)

    with pytest.raises(ValueError, match="credential"):
        write_stored_authorization(path, server_url="https://one.example", value="secret-token")


def test_clear_stored_authorization_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    write_stored_authorization(path, server_url="https://one.example", value="secret-token")

    assert clear_stored_authorization(path) == "cleared"
    assert clear_stored_authorization(path) == "not_configured"


def test_setup_uses_host_specific_credentials_and_endpoints(monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_OPENCODE_AUTHORIZATION", "Bearer host-token")
    monkeypatch.setenv("POWERCONTEXT_OPENCODE_BASE_URL", "https://memory.example/api")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "Bearer shared-token")

    assert setup_authorization_value("opencode") == "Bearer host-token"
    assert setup_server_url("opencode", "http://127.0.0.1:8000") == "https://memory.example/api"


def test_credential_path_uses_host_owned_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))

    assert credential_path("codex") == tmp_path / "codex" / "powercontext" / "credentials.json"
    assert credential_path("claude-code") == tmp_path / "claude" / "powercontext" / "credentials.json"
    assert credential_path("pi") == tmp_path / "pi" / "powercontext" / "credentials.json"
    assert credential_path("workbuddy") == tmp_path / "workbuddy" / "powercontext" / "credentials.json"
