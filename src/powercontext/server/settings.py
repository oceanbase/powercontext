"""Environment-backed Server process settings."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    """Configuration owned by the API process rather than Core."""

    model_config = SettingsConfigDict(
        env_prefix="POWERCONTEXT_SERVER_",
        extra="ignore",
        frozen=True,
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
