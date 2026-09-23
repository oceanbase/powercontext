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
import stat
from pathlib import Path
from unittest.mock import Mock

import pytest
from powercontext_integrations.authorization import (
    AuthorizationResolution,
    clear_stored_authorization,
    configure_codex_desktop_authorization,
    credential_path,
    normalize_authorization,
    read_codex_desktop_authorization,
    read_stored_authorization,
    setup_authorization_value,
    setup_server_url,
    write_stored_authorization,
)


@pytest.fixture(autouse=True)
def isolate_setup_configuration(tmp_path, monkeypatch):
    """Avoid reading real Agent configuration while testing setup authorization."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("port", ["1", "18000", "65535"])
def test_setup_url_uses_local_server_port(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    """Resolve a local listener when no explicit access URL is configured."""
    for name in ("POWERCONTEXT_CODEX_SERVER_URL", "POWERCONTEXT_CLIENT_SERVER_URL", "POWERCONTEXT_SERVER_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", port)
    assert setup_server_url("codex", "http://127.0.0.1:8000") == f"http://127.0.0.1:{port}"


@pytest.mark.parametrize("port", ["", "invalid", "0", "65536", "1.5"])
def test_setup_url_rejects_invalid_server_port(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    """Do not silently bind credentials to a default endpoint for an invalid port."""
    for name in ("POWERCONTEXT_CODEX_SERVER_URL", "POWERCONTEXT_CLIENT_SERVER_URL", "POWERCONTEXT_SERVER_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", port)
    with pytest.raises(ValueError, match="must be an integer between 1 and 65535"):
        setup_server_url("codex", "http://127.0.0.1:8000")


def test_setup_url_preserves_explicit_access_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prefer Agent, client, and proxy URLs over an internal listener port."""
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", "18000")
    monkeypatch.setenv("POWERCONTEXT_CODEX_SERVER_URL", "https://agent.example.com")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "https://client.example.com")
    monkeypatch.setenv("POWERCONTEXT_SERVER_PUBLIC_URL", "https://proxy.example.com")
    with pytest.raises(ValueError, match="Conflicting PowerContext endpoints"):
        setup_server_url("codex", "fallback")
    monkeypatch.delenv("POWERCONTEXT_CODEX_SERVER_URL")
    assert setup_server_url("codex", "fallback") == "https://client.example.com"
    monkeypatch.delenv("POWERCONTEXT_CLIENT_SERVER_URL")
    assert setup_server_url("codex", "fallback") == "https://proxy.example.com"
    monkeypatch.delenv("POWERCONTEXT_SERVER_PUBLIC_URL")
    monkeypatch.delenv("POWERCONTEXT_SERVER_HTTP_PORT")
    assert setup_server_url("codex", "fallback") == "fallback"


def test_normalize_authorization_accepts_bare_tokens_and_headers() -> None:
    assert normalize_authorization("  secret-token  ") == "Bearer secret-token"
    assert normalize_authorization("bearer secret-token") == "Bearer secret-token"


@pytest.mark.parametrize("value", ["", "Bearer", "Bearer a b", "token\n", "Bearer \x00token"])
def test_normalize_authorization_rejects_invalid_values_without_echoing(value: str) -> None:
    with pytest.raises(ValueError, match="authorization") as error:
        normalize_authorization(value)
    if value:
        assert value not in str(error.value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes are not enforced on Windows")
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes are not enforced on Windows")
def test_read_stored_authorization_fails_closed_for_unsafe_files(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps({"version": 1, "server_url": "https://one.example", "authorization": "Bearer secret-token"})
    )
    path.chmod(0o644)

    assert read_stored_authorization(path, server_url="https://one.example") == AuthorizationResolution(
        "unsafe_permissions", None
    )


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks may require elevated Windows privileges")
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


@pytest.mark.parametrize(
    ("process_values", "expected"),
    [
        ({}, "file-host-token"),
        ({"POWERCONTEXT_PI_AUTHORIZATION": "process-host-token"}, "process-host-token"),
        ({"POWERCONTEXT_CLIENT_API_TOKEN": "process-shared-token"}, "file-host-token"),
        ({"POWERCONTEXT_PI_AUTHORIZATION": ""}, "file-shared-token"),
        (
            {"POWERCONTEXT_PI_AUTHORIZATION": "", "POWERCONTEXT_CLIENT_API_TOKEN": "process-shared-token"},
            "process-shared-token",
        ),
    ],
    ids=["file-host", "process-host", "host-before-shared", "empty-host-fallback", "process-shared"],
)
def test_setup_authorization_merges_file_and_process_without_exporting_tokens(
    tmp_path, monkeypatch, process_values, expected
):
    monkeypatch.delenv("POWERCONTEXT_PI_AUTHORIZATION", raising=False)
    monkeypatch.delenv("POWERCONTEXT_CLIENT_API_TOKEN", raising=False)
    path = tmp_path / ".env"
    content = "POWERCONTEXT_PI_AUTHORIZATION=file-host-token\nPOWERCONTEXT_CLIENT_API_TOKEN=file-shared-token\n"
    path.write_text(content)
    for name, value in process_values.items():
        monkeypatch.setenv(name, value)
    environment_before = dict(os.environ)

    assert setup_authorization_value("pi") == expected
    assert dict(os.environ) == environment_before
    assert path.read_text() == content


@pytest.mark.parametrize("server_url", ["http://192.0.2.10:18000/proxy", "https://proxy.example/prefix"])
def test_stored_authorization_remains_bound_to_proxy_path(tmp_path, server_url):
    path = tmp_path / "credentials.json"
    write_stored_authorization(path, server_url=server_url + "/", value="test-token")

    assert read_stored_authorization(path, server_url=server_url) == AuthorizationResolution(
        "configured", "Bearer test-token"
    )
    assert read_stored_authorization(path, server_url=server_url + "-other") == AuthorizationResolution(
        "url_mismatch", None
    )


def test_credential_path_uses_host_owned_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))

    assert credential_path("codex") == tmp_path / "codex" / "powercontext" / "credentials.json"
    assert credential_path("claude-code") == tmp_path / "claude" / "powercontext" / "credentials.json"
    assert credential_path("pi") == tmp_path / "pi" / "powercontext" / "credentials.json"
    assert credential_path("workbuddy") == tmp_path / "workbuddy" / "powercontext" / "credentials.json"


def test_codex_desktop_authorization_uses_the_windows_user_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import powercontext_integrations.authorization as authorization

    write = Mock()
    monkeypatch.setattr(authorization.sys, "platform", "win32")
    monkeypatch.setattr(authorization, "_write_windows_user_environment", write)
    monkeypatch.setattr(
        authorization,
        "_read_windows_user_environment",
        lambda _name: "Bearer saved-token",
    )

    assert configure_codex_desktop_authorization("saved-token") is True
    assert read_codex_desktop_authorization() == "Bearer saved-token"
    write.assert_called_once_with("POWERCONTEXT_CODEX_AUTHORIZATION", "Bearer saved-token")


def test_codex_desktop_authorization_is_windows_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import powercontext_integrations.authorization as authorization

    monkeypatch.setattr(authorization.sys, "platform", "linux")

    assert configure_codex_desktop_authorization("saved-token") is False
    assert read_codex_desktop_authorization() is None
