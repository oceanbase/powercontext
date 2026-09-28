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

from typing import ClassVar, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import SettingsConfigDict

from powercontext.client.transport_policy import ClientTransportSettings, normalize_client_url
from powercontext.transport import is_plaintext_non_loopback


def normalize_server_url(value: str, *, allow_insecure_http: bool = False) -> str:
    """Validate and normalize a Server URL for outbound Client and CLI requests."""

    normalized = normalize_client_url(value)
    if not allow_insecure_http and is_plaintext_non_loopback(normalized):
        raise ValueError("Unencrypted PowerContext Server URLs must be loopback addresses")  # noqa: TRY003
    return normalized


class ClientSettings(ClientTransportSettings):
    """Configuration for Client CLI requests."""

    model_config = SettingsConfigDict(
        env_prefix="POWERCONTEXT_CLIENT_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    transport_url_field: ClassVar[str] = "server_url"
    server_url: str = "http://127.0.0.1:8000"
    api_token: SecretStr | None = Field(default=None, repr=False)
    timeout: float = Field(default=10.0, gt=0)

    @model_validator(mode="after")
    def validate_server_url(self) -> Self:
        self.server_url = normalize_server_url(self.server_url, allow_insecure_http=self.allow_insecure_http)
        return self


__all__ = ["ClientSettings", "normalize_server_url"]
