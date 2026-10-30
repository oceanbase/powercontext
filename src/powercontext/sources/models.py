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
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pydantic_core import PydanticCustomError

from powercontext.errors import InvalidSourceReferenceError
from powercontext.limits import MAX_SOURCE_ID_LENGTH, MAX_SOURCE_TYPE_LENGTH


class SourceMaterialization(StrEnum):
    """Describe where the value read for a Source comes from."""

    CAPTURED = "captured"
    REFERENCED = "referenced"


class SourceSequenceLocator(BaseModel):
    """A Source's zero-based-or-greater position in its scope's journal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["sequence"] = "sequence"
    sequence: StrictInt = Field(ge=0)


class SourceLineRangeLocator(BaseModel):
    """Inclusive, one-based lines in the exact text supplied by the Source adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["line_range"] = "line_range"
    start_line: StrictInt = Field(ge=1)
    end_line: StrictInt = Field(ge=1)
    heading_path: str | None = Field(default=None, max_length=1024, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_span(self) -> SourceLineRangeLocator:
        if self.end_line < self.start_line or self.end_line - self.start_line + 1 > 200:
            raise PydanticCustomError("source_locator_range", "Source line range must contain 1 to 200 inclusive lines")
        return self


SourceLocator: TypeAlias = Annotated[SourceSequenceLocator | SourceLineRangeLocator, Field(discriminator="kind")]


class SourceRef(BaseModel):
    """A stable reference to one Source in the current catalog view."""

    source_type: str
    source_id: str
    locator: SourceLocator | None = Field(default=None, exclude_if=lambda value: value is None)

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
