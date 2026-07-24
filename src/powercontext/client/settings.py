"""Environment-backed Client process settings."""

from __future__ import annotations

from pydantic import AliasChoices, Field, HttpUrl, TypeAdapter, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HTTP_URL = TypeAdapter(HttpUrl)


class ClientSettings(BaseSettings):
    """Configuration owned by remote Client entry points."""

    model_config = SettingsConfigDict(
        env_prefix="POWERCONTEXT_CLIENT_",
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    server_url: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias=AliasChoices("POWERCONTEXT_CLIENT_SERVER_URL", "POWERCONTEXT_SERVER_URL"),
    )
    timeout: float = Field(default=10.0, gt=0)

    @field_validator("server_url")
    @classmethod
    def validate_server_url(cls, value: str) -> str:
        return str(_HTTP_URL.validate_python(value)).rstrip("/")


__all__ = ["ClientSettings"]
