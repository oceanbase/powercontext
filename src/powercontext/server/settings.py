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

"""Environment-backed settings for the PowerContext Server."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import (
    DatabaseConfig,
    ExternalSkillsConfig,
    HandoffReportConfig,
    InferenceConfig,
    RuntimeConfig,
)
from powercontext.paths import default_database_path, sqlite_url


def _default_database() -> SQLiteConfig:
    return SQLiteConfig(url=sqlite_url(default_database_path()))


class HttpConfig(BaseModel):
    """HTTP listener configuration."""

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)


class McpConfig(BaseModel):
    """Optional MCP projection configuration."""

    enabled: bool = True
    path: str = "/mcp"

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith("/") or normalized == "":
            raise ValueError("MCP path must be an absolute non-root path")  # noqa: TRY003
        return normalized


class BearerAuthConfig(BaseModel):
    """Optional static bearer authentication for the local Server."""

    enabled: bool = False
    token: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def require_token_when_enabled(self) -> BearerAuthConfig:
        if self.enabled and (self.token is None or not self.token.get_secret_value()):
            raise ValueError("Bearer token is required when authentication is enabled")  # noqa: TRY003
        return self


class DashboardScopeConfig(BaseModel):
    """One scope exposed by the personal Dashboard."""

    scope_id: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("scope_id", "display_name")
    @classmethod
    def strip_non_empty_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Dashboard scope values must not be empty")  # noqa: TRY003
        return stripped


class DashboardConfig(BaseModel):
    """Optional personal Dashboard served by the local Server."""

    enabled: bool = False
    scopes: list[DashboardScopeConfig] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_scopes(self) -> DashboardConfig:
        if self.enabled and not self.scopes:
            raise ValueError("Enabled Dashboard requires at least one scope")  # noqa: TRY003
        scope_ids = [scope.scope_id for scope in self.scopes]
        if len(scope_ids) != len(set(scope_ids)):
            raise ValueError("Dashboard scope IDs must be unique")  # noqa: TRY003
        return self


class ServerLoggingConfig(BaseModel):
    """Operational log output owned by the Server process."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    format: Literal["console", "json"] = "console"
    access: bool = True

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


class MetricsConfig(BaseModel):
    """Prometheus metrics exposed by the Server."""

    enabled: bool = True


class TracingConfig(BaseModel):
    """Optional span recording and OTLP export configured through standard OTel environment variables."""

    enabled: bool = False


class ServerSettings(BaseSettings):
    """Configuration for the Server process and its built-in runtime."""

    model_config = SettingsConfigDict(
        env_prefix="POWERCONTEXT_SERVER_",
        env_nested_delimiter="_",
        env_nested_max_split=1,
        extra="ignore",
        hide_input_in_errors=True,
        nested_model_default_partial_update=True,
        populate_by_name=True,
    )

    http: HttpConfig = Field(default_factory=HttpConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    auth: BearerAuthConfig = Field(default_factory=BearerAuthConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    logging: ServerLoggingConfig = Field(default_factory=ServerLoggingConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    database: DatabaseConfig = Field(default_factory=_default_database, discriminator="kind")
    handoff_report: HandoffReportConfig = Field(default_factory=HandoffReportConfig)
    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    external_skills: ExternalSkillsConfig = Field(default_factory=ExternalSkillsConfig)

    @field_validator("database", mode="before")
    @classmethod
    def default_database_to_sqlite(cls, value: object) -> object:
        if not isinstance(value, Mapping) or value.get("kind", "sqlite") != "sqlite":
            return value
        return {"kind": "sqlite", "url": _default_database().url, **value}

    @model_validator(mode="after")
    def require_authentication_for_dashboard(self) -> ServerSettings:
        if self.dashboard.enabled and not self.auth.enabled:
            raise ValueError("Dashboard requires Server bearer authentication")  # noqa: TRY003
        return self


__all__ = [
    "BearerAuthConfig",
    "DashboardConfig",
    "DashboardScopeConfig",
    "HandoffReportConfig",
    "HttpConfig",
    "McpConfig",
    "MetricsConfig",
    "ServerLoggingConfig",
    "ServerSettings",
    "TracingConfig",
]
