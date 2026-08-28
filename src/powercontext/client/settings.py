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

"""Environment-backed settings for Client CLI requests."""

from __future__ import annotations

from pydantic import Field, HttpUrl, SecretStr, TypeAdapter, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from powercontext.transport import is_plaintext_non_loopback

_HTTP_URL = TypeAdapter(HttpUrl)


class ClientSettings(BaseSettings):
    """Configuration for Client CLI requests."""

    model_config = SettingsConfigDict(
        env_prefix="POWERCONTEXT_CLIENT_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    server_url: str = "http://127.0.0.1:8000"
    api_token: SecretStr | None = Field(default=None, repr=False)
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
        if is_plaintext_non_loopback(normalized):
            raise ValueError("Unencrypted PowerContext Server URLs must be loopback addresses")  # noqa: TRY003
        return normalized


__all__ = ["ClientSettings"]
