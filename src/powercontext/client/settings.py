"""Environment-backed settings for Client CLI requests."""

from __future__ import annotations

from pydantic import Field, HttpUrl, TypeAdapter, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HTTP_URL = TypeAdapter(HttpUrl)


class ClientSettings(BaseSettings):
    """Configuration for Client CLI requests."""

    model_config = SettingsConfigDict(
        env_prefix="POWERCONTEXT_CLIENT_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    server_url: str = "http://127.0.0.1:8000"
    timeout: float = Field(default=10.0, gt=0)

    @field_validator("server_url")
    @classmethod
    def validate_server_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        normalized = str(_HTTP_URL.validate_python(value)).rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("PowerContext Server URL must not contain credentials")  # noqa: TRY003
        if parsed.query or parsed.fragment:
            raise ValueError("PowerContext Server URL must not contain a query or fragment")  # noqa: TRY003
        return normalized


__all__ = ["ClientSettings"]
