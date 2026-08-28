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

"""Process configuration for the PowerContext LangChain middleware."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PowerContextLangChainSettings(BaseSettings):
    """PowerContext settings read from ``POWERCONTEXT_LANGCHAIN_*`` variables."""

    model_config = SettingsConfigDict(
        env_prefix="POWERCONTEXT_LANGCHAIN_",
        extra="ignore",
        env_ignore_empty=True,
        frozen=True,
        hide_input_in_errors=True,
    )

    base_url: str = "http://127.0.0.1:8000"
    token: SecretStr | None = Field(default=None, repr=False)
    scope_id: str | None = None
    timeout: float = Field(default=10.0, gt=0)
    max_bytes: int = Field(default=8000, ge=512, le=32768)


__all__ = ["PowerContextLangChainSettings"]
