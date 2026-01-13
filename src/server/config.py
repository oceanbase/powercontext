"""
Configuration management for PowerMem API Server.
"""

from __future__ import annotations
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_boolish(value: object) -> object:
    """
    Backward-compatible boolean parsing.

    Historically we accepted values like: true/1/yes/on/enabled.
    `pydantic` already accepts many truthy strings, but "enabled"/"disabled" are not
    guaranteed across versions, so we normalize explicitly.
    """

    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "t", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "f", "no", "n", "off", "disabled"}:
            return False

    return value


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="POWERMEM_SERVER_",
        case_sensitive=False,
        extra="ignore",
    )

    # Server settings
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    workers: int = Field(default=4)
    reload: bool = Field(default=False)

    # Authentication settings
    auth_enabled: bool = Field(default=True)
    api_keys: str = Field(default="")

    # Rate limiting settings
    rate_limit_enabled: bool = Field(default=True)
    rate_limit_per_minute: int = Field(default=100)

    # Logging settings
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")
    log_file: Optional[str] = Field(default="server.log")

    # API settings
    api_title: str = Field(default="PowerMem API")
    api_version: str = Field(default="v1")
    api_description: str = Field(
        default="PowerMem HTTP API Server - Intelligent Memory System"
    )

    # CORS settings
    cors_enabled: bool = Field(default=True)
    cors_origins: str = Field(default="*")

    @field_validator(
        "reload",
        "auth_enabled",
        "rate_limit_enabled",
        "cors_enabled",
        mode="before",
    )
    @classmethod
    def normalize_bool_fields(cls, value: object) -> object:
        return _parse_boolish(value)

    @field_validator("log_file", mode="before")
    @classmethod
    def normalize_log_file(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value  # type: ignore[return-value]

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped.upper() if stripped else value
        return value

    @field_validator("log_format", mode="before")
    @classmethod
    def normalize_log_format(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped.lower() if stripped else value
        return value

    def get_api_keys_list(self) -> List[str]:
        """Get list of API keys"""
        if not self.api_keys:
            return []
        return [key.strip() for key in self.api_keys.split(",") if key.strip()]

    def get_cors_origins_list(self) -> List[str]:
        """Get list of CORS origins"""
        if self.cors_origins == "*":
            return ["*"]
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]


config = ServerSettings()
