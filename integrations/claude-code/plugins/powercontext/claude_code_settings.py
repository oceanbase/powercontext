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

"""Validated process configuration for the PowerContext Claude Code plugin."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class ClaudeCodePluginSettings:
    """Configuration loaded once by a plugin entry point."""

    server_url: str = "http://127.0.0.1:8000"
    authorization: str | None = None
    scope_id: str | None = None
    capture_prompts: bool = True
    flush_on_capture: bool = False
    request_timeout_seconds: float = 1.0
    http_budget_seconds: float = 4.0
    flush_max_calls: int = 4

    def __post_init__(self) -> None:
        object.__setattr__(self, "server_url", _http_base_url(self.server_url))
        object.__setattr__(self, "authorization", _authorization_header(self.authorization))
        object.__setattr__(self, "scope_id", _optional_text(self.scope_id))
        if self.request_timeout_seconds <= 0 or self.http_budget_seconds <= 0:
            raise ValueError("PowerContext HTTP timeouts must be positive")  # noqa: TRY003
        if not 1 <= self.flush_max_calls <= 16:
            raise ValueError("PowerContext flush_max_calls must be between 1 and 16")  # noqa: TRY003

    @classmethod
    def from_environment(cls) -> ClaudeCodePluginSettings:
        """Load Claude user options and integration-specific environment values."""

        return cls(
            server_url=_first_environment(
                "POWERCONTEXT_CLAUDE_SERVER_URL",
                "CLAUDE_PLUGIN_OPTION_SERVER_URL",
            )
            or "http://127.0.0.1:8000",
            authorization=_first_environment("POWERCONTEXT_CLAUDE_AUTHORIZATION"),
            scope_id=_first_environment("POWERCONTEXT_CLAUDE_SCOPE_ID"),
            capture_prompts=_environment_bool(
                "POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS",
                "CLAUDE_PLUGIN_OPTION_CAPTURE_PROMPTS",
                default=True,
            ),
            flush_on_capture=_environment_bool(
                "POWERCONTEXT_CLAUDE_FLUSH_ON_CAPTURE",
                default=False,
            ),
            request_timeout_seconds=_environment_float(
                "POWERCONTEXT_CLAUDE_REQUEST_TIMEOUT_SECONDS",
                default=1.0,
            ),
            http_budget_seconds=_environment_float(
                "POWERCONTEXT_CLAUDE_HTTP_BUDGET_SECONDS",
                default=4.0,
            ),
            flush_max_calls=_environment_int(
                "POWERCONTEXT_CLAUDE_FLUSH_MAX_CALLS",
                default=4,
            ),
        )


def _first_environment(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return None


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
        raise ValueError("Claude Code authorization must be a valid Bearer header")  # noqa: TRY003
    return normalized


def _http_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("PowerContext Server URL must not contain credentials")  # noqa: TRY003
    if parsed.hostname is None or parsed.scheme not in {"http", "https"}:
        raise ValueError("PowerContext Server URL must use HTTP or HTTPS")  # noqa: TRY003
    if parsed.query or parsed.fragment:
        raise ValueError("PowerContext Server URL must not contain a query or fragment")  # noqa: TRY003
    if parsed.scheme == "http" and parsed.hostname.lower() not in _LOOPBACK_HOSTS:
        raise ValueError("unencrypted PowerContext URLs must be loopback addresses")  # noqa: TRY003
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path.removesuffix("/mcp")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


__all__ = ["ClaudeCodePluginSettings"]
