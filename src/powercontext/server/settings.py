"""Environment-backed Server process settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class _SettingsGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HttpSettings(_SettingsGroup):
    """HTTP listener settings owned by the Server process."""

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)


class McpSettings(_SettingsGroup):
    """MCP transport settings owned by the Server process."""

    path: str = "/mcp"

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith("/") or normalized == "":
            raise ValueError("MCP path must be an absolute non-root path")  # noqa: TRY003
        return normalized


class RuntimeSettings(_SettingsGroup):
    """Source-to-Memory runtime policy settings."""

    source_window_limit: int = Field(default=100, ge=1)
    schedule_seconds: float | None = Field(default=None, gt=0)


class SQLiteStorageSettings(_SettingsGroup):
    """SQLite storage settings for the current Runtime profile."""

    kind: Literal["sqlite"] = "sqlite"
    path: Path = Path("powercontext.db")
    vec1_extension: Path | None = None


class InferenceSettings(_SettingsGroup):
    """Optional model capabilities assembled by the Server."""

    generation_model: str | None = None
    generation_timeout_seconds: float = Field(default=30.0, gt=0)
    generation_max_requests: int = Field(default=2, ge=1)
    embedding_model: str | None = None
    embedding_profile_id: str | None = None
    embedding_dimension: int | None = Field(default=None, ge=1)
    embedding_normalization: str = "none"
    embedding_timeout_seconds: float = Field(default=30.0, gt=0)

    @field_validator("generation_model", "embedding_model", "embedding_profile_id")
    @classmethod
    def validate_optional_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("inference identifiers must not be empty")  # noqa: TRY003
        return normalized

    @field_validator("embedding_normalization")
    @classmethod
    def validate_normalization(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("embedding normalization must not be empty")  # noqa: TRY003
        return normalized

    @model_validator(mode="after")
    def validate_profiles(self) -> Self:
        embedding_profile = (
            self.embedding_model,
            self.embedding_profile_id,
            self.embedding_dimension,
        )
        if any(value is not None for value in embedding_profile) and not all(
            value is not None for value in embedding_profile
        ):
            raise ValueError(  # noqa: TRY003
                "embedding_model, embedding_profile_id, and embedding_dimension must be configured together"
            )
        return self


class ServerSettings(BaseSettings):
    """Declarative configuration consumed by the Server composition root."""

    model_config = SettingsConfigDict(
        env_prefix="POWERCONTEXT_SERVER_",
        env_nested_delimiter="_",
        env_nested_max_split=1,
        extra="ignore",
        frozen=True,
    )

    http: HttpSettings = Field(default_factory=HttpSettings)
    mcp: McpSettings = Field(default_factory=McpSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    storage: SQLiteStorageSettings = Field(default_factory=SQLiteStorageSettings)
    inference: InferenceSettings = Field(default_factory=InferenceSettings)


__all__ = [
    "HttpSettings",
    "InferenceSettings",
    "McpSettings",
    "RuntimeSettings",
    "SQLiteStorageSettings",
    "ServerSettings",
]
