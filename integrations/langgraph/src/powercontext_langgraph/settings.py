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

"""Process configuration for the PowerContext LangGraph adapter."""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

from powercontext.client.transport_policy import ClientTransportSettings

try:
    from powercontext.http import ContextAssembly
except ImportError:
    # Older core releases support legacy recall but cannot accept assembly settings.
    from types import NoneType as ContextAssembly


class PowerContextLangGraphSettings(ClientTransportSettings):
    """PowerContext settings read from the environment.

    ``token`` is a bare token, not a complete ``Authorization`` header value. It is forwarded to
    :class:`~powercontext.client.PowerContextClient`, which composes the header internally.
    """

    model_config = SettingsConfigDict(
        env_prefix="POWERCONTEXT_LANGGRAPH_", extra="ignore", env_ignore_empty=True, frozen=True
    )

    transport_host: ClassVar[str] = "langgraph"
    base_url: str = "http://127.0.0.1:8000"
    # A bearer credential: typed SecretStr and hidden from reprs so it never surfaces in a traceback or trace.
    token: SecretStr | None = Field(default=None, repr=False)
    scope_id: str | None = None
    timeout: float = 10.0
    max_bytes: int = Field(default=8000, ge=512, le=32768)
    context_assembly: ContextAssembly | None = None

    @field_validator("context_assembly", mode="before")
    @classmethod
    def validate_assembly_support(cls, value: object) -> object:
        if value is not None and ContextAssembly is type(None):
            raise ValueError(  # noqa: TRY003
                "context_assembly requires a PowerContext core with text assembly support; "
                "install the core and adapter from the same checkout"
            )
        return value
