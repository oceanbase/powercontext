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

"""Serializable Source capability, authorization and target contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from powercontext.sources import SourceRef

PROTOCOL_VERSION = "3"
DYNAMIC_PLUGIN_API_VERSION = "2"
MAX_ADD_BYTES = 1024 * 1024
MAX_MESSAGE_BYTES = 2 * 1024 * 1024


class ManagementError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Capability(Model):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=2048)
    input_schema: dict[str, JsonValue]
    kind: Literal["collect", "write"] = "collect"
    definition_name: str | None = Field(default=None, min_length=1, max_length=128)
    definition_version: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_schema(self) -> Capability:
        from jsonschema import Draft202012Validator

        Draft202012Validator.check_schema(self.input_schema)
        return self

    def declaration(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def validate_input(self, value: dict[str, Any]) -> None:
        from jsonschema import Draft202012Validator

        if next(Draft202012Validator(self.input_schema).iter_errors(value), None) is not None:
            raise ManagementError("invalid-input", "Input does not satisfy the discovered capability schema")


class Caller(Model):
    deployment: str
    subject: str
    scope_id: str | None = None
    operation: str
    administrator: bool = False
    operation_authorized: bool = True


class TargetIdentityRequest(Model):
    capability: str
    capability_version: str
    input: dict[str, JsonValue]


class TargetIdentity(Model):
    target_key: str = Field(min_length=1, max_length=512)
    normalized_input: dict[str, JsonValue]

    @model_validator(mode="after")
    def nonblank_key(self) -> TargetIdentity:
        if not self.target_key.strip():
            raise ValueError("target_key must not be blank")  # noqa: TRY003
        return self


class SourceTarget(Model):
    target_id: str
    scope_id: str
    instance_id: str
    capability: str
    capability_version: str
    input: dict[str, JsonValue]


class InstanceRegistration(Model):
    instance_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    credential_digest: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    execution_location: str = Field(min_length=1, max_length=255)
    scope_ids: tuple[str, ...] = ()
    public_principals: tuple[str, ...] = ()
    default_capabilities: tuple[str, ...] = ()
    enabled: bool = True
    configuration_digest: str = Field(min_length=1)


class TargetSubmissionReport(Model):
    generation: str
    target_id: str = Field(min_length=1, max_length=128)
    source_refs: tuple[SourceRef, ...] = Field(min_length=1, max_length=1)
    submitted_at: str


BUILTIN_CAPABILITY = Capability(
    name="content.inline",
    version="2",
    kind="write",
    description="Save supplied text as a captured Source",
    input_schema={
        "type": "object",
        "properties": {
            "source_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "text": {"type": "string", "minLength": 1},
            "metadata": {"type": "object"},
        },
        "required": ["source_id", "text"],
        "additionalProperties": False,
    },
)
