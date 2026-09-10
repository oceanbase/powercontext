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

"""User-owned PowerContext authorization persistence for Agent Hosts."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from powercontext.client.settings import normalize_server_url

AuthorizationStatus = Literal["configured", "not_configured", "url_mismatch", "invalid", "unsafe_permissions"]
SetupAuthorizationStatus = Literal[
    "configured", "preserved", "cleared", "not_configured", "url_mismatch", "invalid", "unsafe_permissions"
]

_AUTHORIZATION_ENVIRONMENTS = {
    "codex": "POWERCONTEXT_CODEX_AUTHORIZATION",
    "claude-code": "POWERCONTEXT_CLAUDE_AUTHORIZATION",
    "opencode": "POWERCONTEXT_OPENCODE_AUTHORIZATION",
    "pi": "POWERCONTEXT_PI_AUTHORIZATION",
    "workbuddy": "POWERCONTEXT_WORKBUDDY_AUTHORIZATION",
    "dsh": "POWERCONTEXT_DSH_AUTHORIZATION",
}
_SERVER_URL_ENVIRONMENTS = {
    "codex": "POWERCONTEXT_CODEX_SERVER_URL",
    "claude-code": "POWERCONTEXT_CLAUDE_SERVER_URL",
    "opencode": "POWERCONTEXT_OPENCODE_BASE_URL",
    "pi": "POWERCONTEXT_PI_BASE_URL",
    "workbuddy": "POWERCONTEXT_WORKBUDDY_SERVER_URL",
    "dsh": "POWERCONTEXT_DSH_BASE_URL",
}


@dataclass(frozen=True, slots=True)
class AuthorizationResolution:
    """Redacted result of resolving one persisted credential."""

    status: AuthorizationStatus
    authorization: str | None


def normalize_authorization(value: str) -> str:
    """Return a complete Bearer header without exposing invalid input in errors."""

    if not isinstance(value, str) or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("authorization must be a non-empty single-token credential")  # noqa: TRY003
    normalized = value.strip()
    if normalized.casefold() == "bearer":
        raise ValueError("authorization must be a non-empty single-token credential")  # noqa: TRY003
    credential = normalized[7:] if normalized.casefold().startswith("bearer ") else normalized
    if (
        not credential
        or not credential.isascii()
        or not credential.isprintable()
        or any(character.isspace() for character in credential)
        or len(credential) > 8192
    ):
        raise ValueError("authorization must be a non-empty single-token credential")  # noqa: TRY003
    return f"Bearer {credential}"


def credential_path(host: str) -> Path:
    """Return a PowerContext-owned credential path below the host config root."""

    roots = {
        "codex": Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser(),
        "claude-code": Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")).expanduser(),
        "opencode": Path(os.environ.get("OPENCODE_CONFIG_DIR", Path.home() / ".config" / "opencode")).expanduser(),
        "pi": Path(os.environ.get("PI_CODING_AGENT_DIR", Path.home() / ".pi" / "agent")).expanduser(),
        "workbuddy": Path(os.environ.get("WORKBUDDY_HOME", Path.home() / ".workbuddy")).expanduser(),
        "dsh": Path(os.environ.get("DSH_HOME", Path.home() / ".dsh")).expanduser(),
    }
    try:
        root = roots[host]
    except KeyError as error:
        raise ValueError("unsupported persisted authorization host") from error  # noqa: TRY003
    return root / "powercontext" / "credentials.json"


def write_stored_authorization(path: Path, *, server_url: str, value: str) -> None:
    """Atomically persist one URL-bound authorization record."""

    path = Path(path).expanduser()
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise ValueError("credential path must be a regular file")  # noqa: TRY003
    normalized_url = normalize_server_url(server_url)
    authorization = normalize_authorization(value)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        parent.chmod(0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        payload = {
            "version": 1,
            "server_url": normalized_url,
            "authorization": authorization,
        }
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_stored_authorization(path: Path, *, server_url: str) -> AuthorizationResolution:
    """Resolve a credential only when its file and URL binding are safe."""

    path = Path(path).expanduser()
    if not path.exists():
        return AuthorizationResolution("not_configured", None)
    if path.is_symlink() or not path.is_file():
        return AuthorizationResolution("invalid", None)
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        return AuthorizationResolution("unsafe_permissions", None)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("invalid persisted authorization record")  # noqa: TRY003, TRY301
        stored_url = normalize_server_url(payload["server_url"])
        authorization = normalize_authorization(payload["authorization"])
        effective_url = normalize_server_url(server_url)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return AuthorizationResolution("invalid", None)
    if stored_url != effective_url:
        return AuthorizationResolution("url_mismatch", None)
    return AuthorizationResolution("configured", authorization)


def clear_stored_authorization(path: Path) -> Literal["cleared", "not_configured"]:
    """Delete a PowerContext-owned credential without following symlinks."""

    path = Path(path).expanduser()
    if not path.exists():
        return "not_configured"
    if path.is_symlink() or not path.is_file():
        raise ValueError("credential path must be a regular file")  # noqa: TRY003
    path.unlink()
    return "cleared"


def configure_stored_authorization(host: str, *, server_url: str, value: str | None = None) -> SetupAuthorizationStatus:
    """Persist a setup-only token, or report the existing URL-bound credential state."""

    path = credential_path(host)
    if value is not None and value.strip():
        write_stored_authorization(path, server_url=server_url, value=value)
        return "configured"
    resolution = read_stored_authorization(path, server_url=server_url)
    if resolution.status == "configured":
        return "preserved"
    return resolution.status


def setup_authorization_value(host: str) -> str | None:
    """Read the host-specific setup token, falling back to the shared token."""

    return os.environ.get(_AUTHORIZATION_ENVIRONMENTS[host]) or os.environ.get("POWERCONTEXT_CLIENT_API_TOKEN")


def setup_server_url(host: str, default: str) -> str:
    """Resolve the endpoint used by a host before binding its credential."""

    return os.environ.get(_SERVER_URL_ENVIRONMENTS[host], default)


__all__ = [
    "AuthorizationResolution",
    "AuthorizationStatus",
    "clear_stored_authorization",
    "configure_stored_authorization",
    "credential_path",
    "normalize_authorization",
    "read_stored_authorization",
    "setup_authorization_value",
    "setup_server_url",
    "write_stored_authorization",
]
