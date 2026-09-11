#!/usr/bin/env python3
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

# Adapted for WorkBuddy from the PowerContext Claude Code plugin
# (integrations/claude-code/plugins/powercontext/claude_code_settings.py).

"""Validated process configuration for the PowerContext WorkBuddy hooks driver."""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from powercontext_client_config import load_client_settings, resolve_allow_insecure_http

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class WorkBuddyPluginSettings:
    """Configuration loaded once by a WorkBuddy hooks entry point."""

    server_url: str = "http://127.0.0.1:8000"
    authorization: str | None = None
    scope_id: str | None = None
    context_assembly: dict[str, object] | None = None
    capture_prompts: bool = True
    flush_on_capture: bool = False
    request_timeout_seconds: float = 1.0
    http_budget_seconds: float = 4.0
    flush_max_calls: int = 4
    allow_insecure_http: bool | None = None

    def __post_init__(self) -> None:
        allow_insecure_http = resolve_allow_insecure_http(
            self.server_url,
            host="workbuddy",
            host_environment="POWERCONTEXT_WORKBUDDY_ALLOW_INSECURE_HTTP",
            explicit=self.allow_insecure_http,
        )
        object.__setattr__(self, "allow_insecure_http", allow_insecure_http)
        object.__setattr__(self, "server_url", _http_base_url(self.server_url, allow_insecure_http=allow_insecure_http))
        object.__setattr__(self, "authorization", _authorization_header(self.authorization))
        object.__setattr__(self, "scope_id", _optional_text(self.scope_id))
        if self.request_timeout_seconds <= 0 or self.http_budget_seconds <= 0:
            raise ValueError("PowerContext HTTP timeouts must be positive")  # noqa: TRY003
        if not 1 <= self.flush_max_calls <= 16:
            raise ValueError("PowerContext flush_max_calls must be between 1 and 16")  # noqa: TRY003

    @classmethod
    def from_environment(cls) -> WorkBuddyPluginSettings:
        """Load WorkBuddy user options and integration-specific environment values."""

        saved = load_client_settings("workbuddy")
        return cls(
            server_url=_first_environment("POWERCONTEXT_WORKBUDDY_SERVER_URL", "POWERCONTEXT_CLIENT_SERVER_URL")
            or saved.get("server_url")
            or "http://127.0.0.1:8000",
            authorization=_first_environment("POWERCONTEXT_WORKBUDDY_AUTHORIZATION"),
            scope_id=_first_environment("POWERCONTEXT_WORKBUDDY_SCOPE_ID"),
            context_assembly=_environment_object("POWERCONTEXT_WORKBUDDY_CONTEXT_ASSEMBLY"),
            capture_prompts=_environment_bool(
                "POWERCONTEXT_WORKBUDDY_CAPTURE_PROMPTS",
                default=True,
            ),
            flush_on_capture=_environment_bool(
                "POWERCONTEXT_WORKBUDDY_FLUSH_ON_CAPTURE",
                default=False,
            ),
            request_timeout_seconds=_environment_float(
                "POWERCONTEXT_WORKBUDDY_REQUEST_TIMEOUT_SECONDS",
                default=1.0,
            ),
            http_budget_seconds=_environment_float(
                "POWERCONTEXT_WORKBUDDY_HTTP_BUDGET_SECONDS",
                default=4.0,
            ),
            flush_max_calls=_environment_int(
                "POWERCONTEXT_WORKBUDDY_FLUSH_MAX_CALLS",
                default=4,
            ),
        )


def _first_environment(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return None


def _environment_object(name: str) -> dict[str, object] | None:
    value = _first_environment(name)
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        raise ValueError("PowerContext context assembly must be a JSON object") from None  # noqa: TRY003
    if not isinstance(parsed, dict):
        raise ValueError("PowerContext context assembly must be a JSON object")  # noqa: TRY003, TRY004
    return parsed


def _environment_bool(*names: str, default: bool) -> bool:
    value = _first_environment(*names)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError("invalid boolean PowerContext configuration")  # noqa: TRY003


def _environment_float(name: str, *, default: float) -> float:
    value = os.environ.get(name)
    return default if value is None else float(value)


def _environment_int(name: str, *, default: int) -> int:
    value = os.environ.get(name)
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
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not credential
        or not credential.isascii()
        or not credential.isprintable()
        or any(character.isspace() for character in credential)
    ):
        raise ValueError("WorkBuddy authorization must be a valid Bearer header")  # noqa: TRY003
    return normalized


def _http_base_url(value: str, *, allow_insecure_http: bool = False) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("PowerContext Server URL must not contain credentials")  # noqa: TRY003
    if parsed.hostname is None or parsed.scheme not in {"http", "https"}:
        raise ValueError("PowerContext Server URL must use HTTP or HTTPS")  # noqa: TRY003
    if parsed.query or parsed.fragment:
        raise ValueError("PowerContext Server URL must not contain a query or fragment")  # noqa: TRY003
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname.lower() in _LOOPBACK_HOSTS
    if parsed.scheme == "http" and not loopback and not allow_insecure_http:
        raise ValueError("unencrypted PowerContext URLs must be loopback addresses")  # noqa: TRY003
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path.removesuffix("/mcp")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


__all__ = ["WorkBuddyPluginSettings"]
