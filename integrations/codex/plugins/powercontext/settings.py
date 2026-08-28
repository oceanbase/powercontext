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

"""Validated process configuration for the PowerContext Codex plugin."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from typing_extensions import override

_MCP_CONFIGURATION_PATH = Path(__file__).with_name(".mcp.json")
# Kept in lockstep with powercontext.transport.LOOPBACK_HOSTS; the plugin ships
# isolated and cannot import powercontext, so tests pin the two copies together.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_AUTHORIZATION_ENVIRONMENT = {"Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"}


def _is_loopback_host(host: str) -> bool:
    """Mirror ``powercontext.transport.is_loopback_host`` for the isolated plugin."""

    normalized = host.strip().lower()
    if normalized in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


class _McpEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["http"]
    url: str
    required: bool
    env_http_headers: dict[str, str]

    @model_validator(mode="after")
    def validate_url(self) -> _McpEndpoint:
        _http_base_url(self.url)
        if self.env_http_headers != _AUTHORIZATION_ENVIRONMENT:
            raise ValueError("MCP authorization must use the PowerContext Codex environment")  # noqa: TRY003
        return self


class _McpConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mcp_servers: dict[str, _McpEndpoint] = Field(alias="mcpServers")

    @model_validator(mode="after")
    def validate_server_set(self) -> _McpConfiguration:
        if set(self.mcp_servers) != {"powercontext"}:
            raise ValueError("MCP configuration must define only the PowerContext server")  # noqa: TRY003
        return self


class _McpEndpointSettingsSource(PydanticBaseSettingsSource):
    """Load the hook endpoint from the same file consumed by Codex MCP."""

    @override
    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        if field_name == "server_url":
            return _server_url_from_mcp_configuration(), field_name, False
        return None, field_name, False

    @override
    def __call__(self) -> dict[str, Any]:
        return {"server_url": _server_url_from_mcp_configuration()}


class CodexPluginSettings(BaseSettings):
    """Validated configuration loaded once by a plugin entry point."""

    model_config = SettingsConfigDict(
        env_prefix="POWERCONTEXT_CODEX_",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    server_url: str = Field(default="", repr=False)
    authorization: SecretStr | None = Field(default=None, repr=False)
    scope_id: str | None = None
    capture_prompts: bool = True
    flush_on_capture: bool = False
    request_timeout_seconds: float = Field(default=1.0, gt=0)
    http_budget_seconds: float = Field(default=4.0, gt=0)
    flush_max_calls: int = Field(default=4, ge=1, le=16)

    @field_validator("server_url")
    @classmethod
    def require_mcp_endpoint_source(cls, value: str) -> str:
        if not value:
            raise ValueError("PowerContext Server URL must come from MCP configuration")  # noqa: TRY003
        return value

    @field_validator("authorization")
    @classmethod
    def validate_authorization(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value().strip():
            return None
        authorization = value.get_secret_value()
        scheme, separator, credential = authorization.partition(" ")
        if (
            not separator
            or scheme.casefold() != "bearer"
            or not credential
            or not credential.isascii()
            or not credential.isprintable()
            or any(character.isspace() for character in credential)
        ):
            raise ValueError("Codex authorization must be a valid Bearer header")  # noqa: TRY003
        return value

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            _McpEndpointSettingsSource(settings_cls),
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    @field_validator("scope_id")
    @classmethod
    def normalize_scope_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


def _server_url_from_mcp_configuration() -> str:
    configuration = _McpConfiguration.model_validate_json(_MCP_CONFIGURATION_PATH.read_text())
    return _http_base_url(configuration.mcp_servers["powercontext"].url)


def _http_base_url(mcp_url: str) -> str:
    normalized = mcp_url.rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("PowerContext MCP URL must not contain credentials")  # noqa: TRY003
    if parsed.hostname is None or parsed.scheme not in {"http", "https"}:
        raise ValueError("PowerContext MCP URL must use HTTP or HTTPS")  # noqa: TRY003
    if parsed.query or parsed.fragment:
        raise ValueError("PowerContext MCP URL must not contain a query or fragment")  # noqa: TRY003
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        raise ValueError("unencrypted PowerContext MCP URLs must be loopback addresses")  # noqa: TRY003
    mcp_path = parsed.path.rstrip("/")
    if not mcp_path.endswith("/mcp"):
        raise ValueError("PowerContext MCP URL path must end with /mcp")  # noqa: TRY003
    base_path = mcp_path.removesuffix("/mcp")
    return urlunsplit((parsed.scheme, parsed.netloc, base_path, "", "")).rstrip("/")


__all__ = ["CodexPluginSettings"]
