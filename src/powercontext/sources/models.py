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

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator

from powercontext.errors import InvalidSourceReferenceError
from powercontext.limits import MAX_SOURCE_ID_LENGTH, MAX_SOURCE_TYPE_LENGTH


class SourceMaterialization(StrEnum):
    """Describe where the value read for a Source comes from."""

    CAPTURED = "captured"
    REFERENCED = "referenced"


class SourceRef(BaseModel):
    """A stable reference to one Source in the current catalog view."""

    source_type: str
    source_id: str

    @field_validator("source_type", "source_id")
    @classmethod
    def validate_reference_part(cls, value: str, info) -> str:
        _validate_reference_part(info.field_name, value)
        return value


class SourceProjectionKey(BaseModel):
    """Select one independently versioned named projection capability."""

    model_config = ConfigDict(frozen=True)

    name: str
    version: str

    @field_validator("name", "version")
    @classmethod
    def validate_key_part(cls, value: str, info) -> str:
        _validate_reference_part(info.field_name, value)
        return value


class Source(BaseModel):
    """Base value for an adapter-owned Source description."""

    name: str
    definition_version: str = "1"
    materialization: SourceMaterialization
    description: str | None = None

    @field_validator("definition_version")
    @classmethod
    def validate_definition_version(cls, value: str) -> str:
        _validate_reference_part("definition_version", value)
        return value


def _validate_reference_part(field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidSourceReferenceError(field, "must be a non-empty string")
    if value != value.strip():
        raise InvalidSourceReferenceError(field, "must not contain leading or trailing whitespace")
    maximum = MAX_SOURCE_TYPE_LENGTH if field == "source_type" else MAX_SOURCE_ID_LENGTH
    if len(value) > maximum:
        raise InvalidSourceReferenceError(field, f"must not exceed {maximum} characters")
