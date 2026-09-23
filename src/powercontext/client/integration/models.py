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

"""Wire types for a local hook worker; domain payloads retain OpenAPI names."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, field_validator

from powercontext.client.operations import OPERATIONS


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    authorization: SecretStr | None = None
    allow_insecure_http: bool = False
    request_timeout: float = Field(default=1, gt=0, allow_inf_nan=False)


class HookRequest(BaseModel):
    """One operation, with an absolute deadline covering startup and queuing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: Literal[1] = 1
    id: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    arguments: dict[str, JsonValue]
    connection: Connection
    deadline: float = Field(gt=0, allow_inf_nan=False)
    readiness_response: bool = False
    observe_headers: bool = False

    @field_validator("operation")
    @classmethod
    def known_operation(cls, value: str) -> str:
        if value not in OPERATIONS:
            raise ValueError("Unknown PowerContext operation")  # noqa: TRY003
        return value


class HookResult(BaseModel):
    """Unknown means a submitted write may have completed; never replay it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: Literal[1] = 1
    id: str
    outcome: Literal["ok", "empty", "failed", "unknown"]
    value: JsonValue = None
    kind: Literal["json", "text", "bytes"] = "json"
    error: Literal["deadline", "transport", "invalid_request", "invalid_response", "server", "configuration"] | None = (
        None
    )
    status_code: int | None = None
    code: str | None = None
    message: str | None = None
    request_id: str | None = None
    etag: str | None = None
    body_error: str | None = None
