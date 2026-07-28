"""Validated configuration for one built-in runtime instance."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig


class RuntimeConfig(BaseModel):
    """Built-in runtime policy and scheduler configuration."""

    source_window_limit: int = Field(default=100, ge=1)
    schedule_seconds: float | None = Field(default=None, gt=0)
    scheduler_path: Path = Path("powercontext.scheduler.db")


class InferenceConfig(BaseModel):
    """Optional generation and embedding configuration."""

    generation_model: str | None = None
    generation_timeout_seconds: float = Field(default=30.0, gt=0)
    generation_max_requests: int = Field(default=2, ge=1)
    embedding_model: str | None = None
    embedding_profile_id: str | None = None
    embedding_dimension: int | None = Field(default=None, ge=1)
    embedding_normalization: Literal["none", "unit"] = "unit"
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

    @field_validator("embedding_normalization", mode="before")
    @classmethod
    def validate_normalization(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if normalized not in {"none", "unit"}:
            raise ValueError("embedding normalization must be 'none' or 'unit'")  # noqa: TRY003
        return normalized

    @model_validator(mode="after")
    def validate_embedding_profile(self) -> Self:
        values = (self.embedding_model, self.embedding_profile_id, self.embedding_dimension)
        if any(value is not None for value in values) and not all(value is not None for value in values):
            raise ValueError(  # noqa: TRY003
                "embedding_model, embedding_profile_id, and embedding_dimension must be configured together"
            )
        return self


DatabaseConfig = SQLiteConfig | OceanBaseConfig


def normalize_database_discriminator(value: Any) -> Any:
    """Use SQLite when a partial database mapping omits its kind."""

    if not isinstance(value, Mapping):
        return value
    database = value.get("database")
    if not isinstance(database, Mapping) or "kind" in database:
        return value
    normalized = dict(value)
    normalized["database"] = {"kind": "sqlite", **database}
    return normalized


class BuiltinConfig(BaseModel):
    """Configuration for one built-in runtime and its database."""

    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    database: DatabaseConfig = Field(default_factory=SQLiteConfig, discriminator="kind")
    inference: InferenceConfig = Field(default_factory=InferenceConfig)

    @model_validator(mode="before")
    @classmethod
    def default_database_to_sqlite(cls, value: Any) -> Any:
        return normalize_database_discriminator(value)


__all__ = [
    "BuiltinConfig",
    "DatabaseConfig",
    "InferenceConfig",
    "RuntimeConfig",
]
