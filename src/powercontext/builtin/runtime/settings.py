"""Environment-backed settings for a Builtin CLI instance."""

from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import (
    DatabaseConfig,
    InferenceConfig,
    RuntimeConfig,
    normalize_database_discriminator,
)


class BuiltinSettings(BaseSettings):
    """Configuration for Builtin CLI commands."""

    model_config = SettingsConfigDict(
        env_prefix="POWERCONTEXT_BUILTIN_",
        env_nested_delimiter="_",
        env_nested_max_split=1,
        extra="ignore",
        hide_input_in_errors=True,
        nested_model_default_partial_update=True,
    )

    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    database: DatabaseConfig = Field(default_factory=SQLiteConfig, discriminator="kind")
    inference: InferenceConfig = Field(default_factory=InferenceConfig)

    @model_validator(mode="before")
    @classmethod
    def default_database_to_sqlite(cls, value: object) -> object:
        return normalize_database_discriminator(value)


__all__ = ["BuiltinSettings"]
