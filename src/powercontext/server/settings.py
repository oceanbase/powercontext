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
from contextlib import suppress
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from powercontext.builtin.artifacts.skill import AgentSkillTarget
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import (
    DatabaseConfig,
    ExternalSkillsConfig,
    HandoffReportConfig,
    InferenceConfig,
    RuntimeConfig,
)
from powercontext.paths import default_database_path, default_seekdb_path, sqlite_url
from powercontext.transport import is_loopback_host

_UNSAFE_BIND_MESSAGE = (
    "A non-loopback bind requires bearer authentication; "
    "set allow_unauthenticated_non_loopback to opt in when TLS is "
    "terminated upstream or the network is otherwise controlled"
)


class UnauthenticatedNonLoopbackBindError(ValueError):
    """Raised when a bind would expose an unauthenticated Server off loopback.

    A dedicated type lets callers that assemble the settings (e.g. the CLI) recognise this
    policy failure by identity -- via pydantic's ``ctx['error']`` -- and translate it into an
    actionable message, without matching against the raw validation text.
    """


class MissingBearerTokenError(ValueError):
    """Raised when authentication is enabled but no bearer token is configured.

    Recognised by identity the same way as :class:`UnauthenticatedNonLoopbackBindError`, so the
    CLI can point the operator at the concrete token / disable levers instead of surfacing
    pydantic's raw validation report.
    """


def _default_database() -> SQLiteConfig:
    return SQLiteConfig(url=sqlite_url(default_database_path()))


def _default_local_external_skills(workspace: Path) -> ExternalSkillsConfig:
    return ExternalSkillsConfig(
        host_id="local-workspace",
        targets=(
            AgentSkillTarget(
                target_id="codex-project",
                agent_kind="codex",
                installation_scope="project",
                path=workspace / ".agents" / "skills",
                allow_managed_publish=True,
            ),
            AgentSkillTarget(
                target_id="claude-project",
                agent_kind="claude_code",
                installation_scope="project",
                path=workspace / ".claude" / "skills",
                allow_managed_publish=True,
            ),
        ),
    )


def is_unauthenticated_non_loopback_bind(
    *,
    host: str,
    auth_enabled: bool,
    allow_unauthenticated_non_loopback: bool,
) -> bool:
    """Return whether a bind exposes an unauthenticated Server off loopback."""

    return not is_loopback_host(host) and not auth_enabled and not allow_unauthenticated_non_loopback


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
            raise MissingBearerTokenError("Bearer token is required when authentication is enabled")  # noqa: TRY003
        return self


class DashboardConfig(BaseModel):
    """Personal Dashboard served by the local Server."""

    enabled: bool = True


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
    workspace: Path = Field(default_factory=Path.cwd)
    public_url: str | None = None
    allow_insecure_http: bool = False
    mcp: McpConfig = Field(default_factory=McpConfig)
    auth: BearerAuthConfig = Field(default_factory=BearerAuthConfig)
    allow_unauthenticated_non_loopback: bool = False
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    logging: ServerLoggingConfig = Field(default_factory=ServerLoggingConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    database: DatabaseConfig = Field(default_factory=_default_database, discriminator="kind")
    handoff_report: HandoffReportConfig = Field(default_factory=HandoffReportConfig)
    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    external_skills: ExternalSkillsConfig = Field(default_factory=ExternalSkillsConfig)

    @field_validator("workspace")
    @classmethod
    def resolve_workspace(cls, value: Path) -> Path:
        workspace = value.expanduser().resolve(strict=False)
        if not workspace.is_dir():
            raise ValueError("Server workspace must be an existing directory")  # noqa: TRY003
        return workspace

    @model_validator(mode="after")
    def configure_default_local_skill_targets(self) -> ServerSettings:
        if "external_skills" not in self.model_fields_set:
            self.external_skills = _default_local_external_skills(self.workspace)
        return self

    @field_validator("public_url")
    @classmethod
    def validate_public_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None
        try:
            parsed = urlsplit(normalized)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as error:
            raise ValueError("public URL must be a valid absolute HTTP URL") from error  # noqa: TRY003
        if (
            parsed.scheme not in {"http", "https"}
            or hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("public URL must be an absolute HTTP URL without credentials, query, or fragment")  # noqa: TRY003
        return normalized

    @model_validator(mode="after")
    def require_secure_public_url_by_default(self) -> ServerSettings:
        if self.public_url is None or self.allow_insecure_http:
            return self
        parsed = urlsplit(self.public_url)
        hostname = parsed.hostname
        loopback = hostname is not None and hostname.lower() == "localhost"
        if hostname is not None:
            with suppress(ValueError):
                loopback = loopback or ip_address(hostname).is_loopback
        if parsed.scheme != "https" and not loopback:
            raise ValueError("public URL must use HTTPS unless it points to loopback")  # noqa: TRY003
        return self

    @field_validator("database", mode="before")
    @classmethod
    def default_seekdb_database_path(cls, value: object) -> object:
        if not isinstance(value, Mapping) or value.get("kind") != "seekdb":
            return value
        path = value.get("path")
        if "path" in value and not (isinstance(path, str) and not path.strip()):
            return value
        normalized = dict(value)
        normalized["path"] = default_seekdb_path()
        return normalized

    @field_validator("database", mode="before")
    @classmethod
    def default_database_to_sqlite(cls, value: object) -> object:
        if not isinstance(value, Mapping) or value.get("kind", "sqlite") != "sqlite":
            return value
        return {"kind": "sqlite", "url": _default_database().url, **value}

    @model_validator(mode="after")
    def reject_unauthenticated_non_loopback_bind(self) -> ServerSettings:
        if is_unauthenticated_non_loopback_bind(
            host=self.http.host,
            auth_enabled=self.auth.enabled,
            allow_unauthenticated_non_loopback=self.allow_unauthenticated_non_loopback,
        ):
            raise UnauthenticatedNonLoopbackBindError(_UNSAFE_BIND_MESSAGE)
        return self


__all__ = [
    "BearerAuthConfig",
    "DashboardConfig",
    "HandoffReportConfig",
    "HttpConfig",
    "McpConfig",
    "MetricsConfig",
    "MissingBearerTokenError",
    "ServerLoggingConfig",
    "ServerSettings",
    "TracingConfig",
    "UnauthenticatedNonLoopbackBindError",
    "is_unauthenticated_non_loopback_bind",
]
