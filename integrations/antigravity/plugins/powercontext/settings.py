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

"""Validated process configuration for the PowerContext Antigravity plugin."""

from __future__ import annotations

import ipaddress
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from powercontext_lifecycle.client_config import (
    load_client_settings,
    normalize_server_url,
    parse_boolean,
    resolve_allow_insecure_http,
)
from powercontext_lifecycle.scope_binding import ScopeBindingSettings
from powercontext_lifecycle.user_prompt_submit import PromptSettings

_MCP_CONFIGURATION_PATH = Path(__file__).with_name("mcp_config.json")
_PLUGIN_SETTINGS_PATH = Path(__file__).with_name("powercontext.json")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_HOOK_HOST_TIMEOUT_SECONDS = 12.0
PRE_TOOL_USE_HOST_TIMEOUT_SECONDS = 8.0
_HOOK_STARTUP_AND_OUTPUT_MARGIN_SECONDS = 2.0


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized in _LOOPBACK_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if getattr(address, "ipv4_mapped", None) is not None:
        return False
    return address.is_loopback


def host_bounded_http_budget(
    configured_seconds: float,
    *,
    host_timeout: float = _HOOK_HOST_TIMEOUT_SECONDS,
    margin: float = _HOOK_STARTUP_AND_OUTPUT_MARGIN_SECONDS,
) -> float:
    """Bound a deadline-gated hook's HTTP budget below the host process deadline."""

    return max(0.5, min(configured_seconds, host_timeout - margin))


@dataclass(frozen=True)
class BootstrapHandoff:
    """One explicitly selected exact committed Handoff."""

    artifact_id: str
    revision: int
    family: str = "handoff"

    def __post_init__(self) -> None:
        if (
            self.family != "handoff"
            or not self.artifact_id
            or self.artifact_id != self.artifact_id.strip()
            or len(self.artifact_id) > 128
            or not self.artifact_id.isascii()
            or not self.artifact_id.isprintable()
            or not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 1
        ):
            raise ValueError("invalid bootstrap Handoff identity")  # noqa: TRY003

    def as_request(self) -> dict[str, object]:
        return {"family": self.family, "artifact_id": self.artifact_id, "revision": self.revision}


@dataclass(frozen=True)
class AntigravityPluginSettings:
    """Configuration loaded once by an Antigravity plugin entry point."""

    server_url: str = "http://127.0.0.1:8000"
    authorization: str | None = None
    scope_id: str | None = None
    context_assembly: dict[str, object] | None = None
    bootstrap_context: bool = False
    bootstrap_max_bytes: int = 4096
    bootstrap_handoff: BootstrapHandoff | None = None
    include_code: bool = False
    prompt_recall: bool = True
    capture_prompts: bool = True
    flush_on_capture: bool = False
    request_timeout_seconds: float = 6.0
    http_budget_seconds: float = 9.0
    flush_max_calls: int = 4
    allow_insecure_http: bool | None = None

    def __post_init__(self) -> None:
        saved = load_client_settings("antigravity")
        allow_insecure_http = resolve_allow_insecure_http(
            self.server_url,
            host="antigravity",
            host_environment="POWERCONTEXT_ANTIGRAVITY_ALLOW_INSECURE_HTTP",
            explicit=self.allow_insecure_http,
            saved=saved,
        )
        object.__setattr__(self, "allow_insecure_http", allow_insecure_http)
        object.__setattr__(self, "server_url", _http_base_url(self.server_url, allow_insecure_http=allow_insecure_http))
        object.__setattr__(self, "authorization", _authorization_header(self.authorization))
        object.__setattr__(self, "scope_id", _optional_text(self.scope_id))
        if self.request_timeout_seconds <= 0 or self.http_budget_seconds <= 0:
            raise ValueError("PowerContext HTTP timeouts must be positive")  # noqa: TRY003
        if not 512 <= self.bootstrap_max_bytes <= 8192:
            raise ValueError("PowerContext bootstrap_max_bytes must be between 512 and 8192")  # noqa: TRY003
        if not 1 <= self.flush_max_calls <= 16:
            raise ValueError("PowerContext flush_max_calls must be between 1 and 16")  # noqa: TRY003

    @classmethod
    def from_environment(cls, *, server_url: str | None = None) -> AntigravityPluginSettings:
        """Load Antigravity plugin settings from env, native powercontext.json, client config, and mcp_config.json."""

        saved = load_client_settings("antigravity")
        native = _native_plugin_settings()
        raw_url = native.get("server_url")
        native_url = raw_url if isinstance(raw_url, str) else None
        raw_insecure = native.get("allow_insecure_http")
        native_allow_insecure = raw_insecure if isinstance(raw_insecure, bool) else None
        raw_recall = native.get("prompt_recall")
        native_prompt_recall = raw_recall if isinstance(raw_recall, bool) else True
        raw_capture = native.get("capture_prompts")
        native_capture_prompts = raw_capture if isinstance(raw_capture, bool) else True
        resolved_server_url = (
            _first_environment("POWERCONTEXT_ANTIGRAVITY_SERVER_URL", "POWERCONTEXT_CLIENT_SERVER_URL")
            or _optional_text(server_url)
            or _optional_text(native_url)
            or _optional_text(saved.get("server_url") if isinstance(saved.get("server_url"), str) else None)
            or _server_url_from_mcp_configuration()
            or "http://127.0.0.1:8000"
        )
        return cls(
            server_url=resolved_server_url,
            authorization=_first_environment("POWERCONTEXT_ANTIGRAVITY_AUTHORIZATION")
            or _stored_authorization(server_url=resolved_server_url),
            scope_id=_first_environment("POWERCONTEXT_ANTIGRAVITY_SCOPE_ID", "POWERCONTEXT_SCOPE_ID"),
            context_assembly=_environment_object("POWERCONTEXT_ANTIGRAVITY_CONTEXT_ASSEMBLY"),
            bootstrap_context=_environment_bool("POWERCONTEXT_ANTIGRAVITY_BOOTSTRAP_CONTEXT", default=False),
            bootstrap_max_bytes=_environment_int("POWERCONTEXT_ANTIGRAVITY_BOOTSTRAP_MAX_BYTES", default=4096),
            bootstrap_handoff=_environment_bootstrap_handoff("POWERCONTEXT_ANTIGRAVITY_BOOTSTRAP_HANDOFF"),
            include_code=_environment_bool("POWERCONTEXT_ANTIGRAVITY_INCLUDE_CODE", default=False),
            prompt_recall=_environment_bool("POWERCONTEXT_ANTIGRAVITY_PROMPT_RECALL", default=native_prompt_recall),
            capture_prompts=_environment_bool(
                "POWERCONTEXT_ANTIGRAVITY_CAPTURE_PROMPTS", default=native_capture_prompts
            ),
            flush_on_capture=_environment_bool("POWERCONTEXT_ANTIGRAVITY_FLUSH_ON_CAPTURE", default=False),
            request_timeout_seconds=_environment_float("POWERCONTEXT_ANTIGRAVITY_REQUEST_TIMEOUT_SECONDS", default=6.0),
            http_budget_seconds=_environment_float("POWERCONTEXT_ANTIGRAVITY_HTTP_BUDGET_SECONDS", default=9.0),
            flush_max_calls=_environment_int("POWERCONTEXT_ANTIGRAVITY_FLUSH_MAX_CALLS", default=4),
            allow_insecure_http=native_allow_insecure,
        )

    def scope_binding_settings(self) -> ScopeBindingSettings:
        """Return Scope client configuration."""

        return ScopeBindingSettings(
            server_url=self.server_url,
            authorization=self.authorization,
            scope_id=self.scope_id,
            request_timeout_seconds=self.request_timeout_seconds,
        )

    def prompt_settings(self) -> PromptSettings:
        """Return prompt recall and capture configuration."""

        return PromptSettings(
            scope_binding=self.scope_binding_settings(),
            include_code=self.include_code,
            context_assembly=self.context_assembly,
            prompt_recall=self.prompt_recall,
            capture_prompts=self.capture_prompts,
            flush_on_capture=self.flush_on_capture,
            flush_max_calls=self.flush_max_calls,
        )


def _native_plugin_settings() -> dict[str, object]:
    try:
        loaded = json.loads(_PLUGIN_SETTINGS_PATH.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _server_url_from_mcp_configuration() -> str | None:
    try:
        data = json.loads(_MCP_CONFIGURATION_PATH.read_text(encoding="utf-8"))
        entry = (data.get("mcpServers") or {}).get("powercontext") or {}
        raw = entry.get("url") or entry.get("serverUrl")
        if isinstance(raw, str) and raw.strip():
            return _http_base_url(raw, allow_insecure_http=True)
    except (OSError, ValueError, TypeError):
        return None
    return None


def _first_environment(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value
    return None


def _environment_object(name: str) -> dict[str, object] | None:
    value = _first_environment(name)
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        raise ValueError("PowerContext JSON setting must be a JSON object") from None  # noqa: TRY003
    if not isinstance(parsed, dict):
        raise ValueError("PowerContext JSON setting must be a JSON object")  # noqa: TRY003, TRY004
    return parsed


def _environment_bootstrap_handoff(name: str) -> BootstrapHandoff | None:
    value = _environment_object(name)
    if value is None:
        return None
    if not {"artifact_id", "revision"} <= set(value) or not set(value) <= {"family", "artifact_id", "revision"}:
        raise ValueError("PowerContext bootstrap Handoff must be an exact identity")  # noqa: TRY003
    artifact_id = value.get("artifact_id")
    revision = value.get("revision")
    family = value.get("family", "handoff")
    if not isinstance(artifact_id, str) or not isinstance(revision, int) or not isinstance(family, str):
        raise TypeError("PowerContext bootstrap Handoff must be an exact identity")  # noqa: TRY003
    return BootstrapHandoff(artifact_id=artifact_id, revision=revision, family=family)


def _environment_bool(*names: str, default: bool) -> bool:
    value = _first_environment(*names)
    if value is None:
        return default
    parsed = parse_boolean(value)
    if parsed is not None:
        return parsed
    raise ValueError("invalid boolean PowerContext configuration")  # noqa: TRY003


def _environment_float(name: str, *, default: float) -> float:
    value = _first_environment(name)
    return default if value is None else float(value)


def _environment_int(name: str, *, default: int) -> int:
    value = _first_environment(name)
    return default if value is None else int(value)


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def _authorization_header(value: str | None) -> str | None:
    normalized = _optional_text(value)
    if normalized is None:
        return None
    scheme, separator, credential = normalized.partition(" ")
    if not separator:
        credential = normalized
        if not credential.isascii() or not credential.isprintable() or any(ch.isspace() for ch in credential):
            raise ValueError("Antigravity authorization must be a valid Bearer credential")  # noqa: TRY003
        return f"Bearer {credential}"
    if (
        scheme.casefold() != "bearer"
        or not credential
        or not credential.isascii()
        or not credential.isprintable()
        or any(ch.isspace() for ch in credential)
    ):
        raise ValueError("Antigravity authorization must be a valid Bearer header")  # noqa: TRY003
    return normalized


def _credential_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    plugin_data = os.environ.get("PLUGIN_DATA")
    if plugin_data and plugin_data.strip():
        candidates.append(Path(plugin_data).expanduser() / "credentials.json")
    candidates.append(Path.home() / ".gemini" / "config" / "powercontext" / "credentials.json")
    return tuple(candidates)


def _stored_authorization(*, server_url: str) -> str | None:
    target = _http_base_url(server_url, allow_insecure_http=True)
    for path in _credential_candidates():
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & (stat.S_IRWXG | stat.S_IRWXO):
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                continue
            stored_url, authorization = payload.get("server_url"), payload.get("authorization")
            if not isinstance(stored_url, str) or not isinstance(authorization, str):
                continue
            if _http_base_url(stored_url, allow_insecure_http=True) != target:
                continue
            return _authorization_header(authorization)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def _http_base_url(value: str, *, allow_insecure_http: bool = False) -> str:
    parsed = urlsplit(value.strip().rstrip("/"))
    if (
        parsed.scheme.lower() == "http"
        and parsed.hostname is not None
        and not allow_insecure_http
        and not _is_loopback_host(parsed.hostname)
    ):
        raise ValueError("unencrypted PowerContext URLs must be loopback addresses")  # noqa: TRY003
    return normalize_server_url(value, allow_insecure_http=allow_insecure_http)


__all__ = [
    "PRE_TOOL_USE_HOST_TIMEOUT_SECONDS",
    "AntigravityPluginSettings",
    "BootstrapHandoff",
    "host_bounded_http_budget",
]
