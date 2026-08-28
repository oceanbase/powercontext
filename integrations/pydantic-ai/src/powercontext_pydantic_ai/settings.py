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

"""Validated configuration for the Pydantic AI integration."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, HttpUrl, SecretStr, TypeAdapter, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


class PowerContextSettings(BaseSettings):
    """PowerContext settings loaded from constructor values or the environment."""

    model_config = SettingsConfigDict(
        env_prefix="POWERCONTEXT_PYDANTIC_AI_",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    base_url: str = "http://127.0.0.1:8000"
    token: SecretStr | None = Field(default=None, repr=False)
    scope_id: str | None = Field(default=None, min_length=1)
    timeout: float = Field(default=10, gt=0)
    max_bytes: int = Field(default=8000, ge=512, le=32768)
    capture_events: bool = False
    capture_checkpoint_every: int = Field(default=5, ge=1, le=100)
    capture_max_bytes: int = Field(default=8192, ge=512, le=32768)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = str(_HTTP_URL_ADAPTER.validate_python(value.strip())).rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("PowerContext Server URL must not contain credentials")  # noqa: TRY003
        if parsed.hostname is None or parsed.scheme not in {"http", "https"}:
            raise ValueError("PowerContext Server URL must use HTTP or HTTPS")  # noqa: TRY003
        if parsed.query or parsed.fragment:
            raise ValueError("PowerContext Server URL must not contain a query or fragment")  # noqa: TRY003
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))

    @field_validator("token")
    @classmethod
    def validate_bare_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        token = value.get_secret_value()
        if not token:
            return None
        if (
            token != token.strip()
            or token.casefold().startswith("bearer ")
            or not token.isascii()
            or not token.isprintable()
            or any(character.isspace() for character in token)
        ):
            raise ValueError("PowerContext token must be a bare printable token, without the Bearer scheme")  # noqa: TRY003
        return value

    @field_validator("scope_id")
    @classmethod
    def normalize_scope_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("PowerContext scope_id must contain non-whitespace characters")  # noqa: TRY003
        return normalized
