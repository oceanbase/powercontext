"""Environment-backed settings for the PowerContext Server."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import (
    DatabaseConfig,
    InferenceConfig,
    RuntimeConfig,
    normalize_database_discriminator,
)
from powercontext.paths import default_database_path, default_scheduler_path, sqlite_url


def _default_database() -> SQLiteConfig:
    return SQLiteConfig(url=sqlite_url(default_database_path()))


def _default_runtime() -> RuntimeConfig:
    return RuntimeConfig(scheduler_path=default_scheduler_path())


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
    runtime: RuntimeConfig = Field(default_factory=_default_runtime)
    database: DatabaseConfig = Field(default_factory=_default_database, discriminator="kind")
    inference: InferenceConfig = Field(default_factory=InferenceConfig)

    @model_validator(mode="before")
    @classmethod
    def default_database_to_sqlite(cls, value: object) -> object:
        return normalize_database_discriminator(value)


__all__ = ["BearerAuthConfig", "HttpConfig", "McpConfig", "ServerSettings"]
