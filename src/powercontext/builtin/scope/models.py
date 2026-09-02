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

"""Domain values for Scope organization, observation, and external binding."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_serializer, model_validator

from powercontext.builtin.sources import validate_scope_id
from powercontext.limits import (
    MAX_SCOPE_BINDING_EXTERNAL_ID_LENGTH,
    MAX_SCOPE_BINDING_INTEGRATION_LENGTH,
    MAX_SCOPE_BINDING_KIND_LENGTH,
    MAX_SCOPE_EXTERNAL_REFERENCE_KIND_LENGTH,
    MAX_SCOPE_EXTERNAL_REFERENCE_VALUE_LENGTH,
    MAX_SCOPE_IDEMPOTENCY_KEY_LENGTH,
    MAX_SCOPE_SUMMARY_LENGTH,
    MAX_SCOPE_TITLE_LENGTH,
)


class _ScopeValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScopeExternalReference(_ScopeValue):
    kind: str
    value: str

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        return _required_text("kind", value, MAX_SCOPE_EXTERNAL_REFERENCE_KIND_LENGTH)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        return _required_text("value", value, MAX_SCOPE_EXTERNAL_REFERENCE_VALUE_LENGTH)


class ScopeDraft(_ScopeValue):
    title: str
    summary: str
    parent_scope_id: str | None = None
    context_references: tuple[str, ...] = ()
    external_references: tuple[ScopeExternalReference, ...] = ()
    idempotency_key: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _required_text("title", value, MAX_SCOPE_TITLE_LENGTH)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _required_text("summary", value, MAX_SCOPE_SUMMARY_LENGTH)

    @field_validator("parent_scope_id")
    @classmethod
    def validate_parent(cls, value: str | None) -> str | None:
        return None if value is None else validate_scope_id(value)

    @field_validator("context_references")
    @classmethod
    def validate_context_references(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(validate_scope_id(value) for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("Context References must be unique")  # noqa: TRY003
        return tuple(sorted(normalized))

    @field_validator("external_references")
    @classmethod
    def validate_external_references(
        cls,
        values: tuple[ScopeExternalReference, ...],
    ) -> tuple[ScopeExternalReference, ...]:
        if len(set(values)) != len(values):
            raise ValueError("external references must be unique")  # noqa: TRY003
        return values

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _required_text("idempotency_key", value, MAX_SCOPE_IDEMPOTENCY_KEY_LENGTH)


class ScopeMutation(_ScopeValue):
    expected_version: StrictInt = Field(ge=1)
    title: str
    summary: str
    parent_scope_id: str | None = None
    context_references: tuple[str, ...] = ()
    external_references: tuple[ScopeExternalReference, ...] = ()

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _required_text("title", value, MAX_SCOPE_TITLE_LENGTH)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _required_text("summary", value, MAX_SCOPE_SUMMARY_LENGTH)

    @field_validator("parent_scope_id")
    @classmethod
    def validate_parent(cls, value: str | None) -> str | None:
        return None if value is None else validate_scope_id(value)

    @field_validator("context_references")
    @classmethod
    def validate_context_references(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(validate_scope_id(value) for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("Context References must be unique")  # noqa: TRY003
        return tuple(sorted(normalized))

    @field_validator("external_references")
    @classmethod
    def validate_external_references(
        cls,
        values: tuple[ScopeExternalReference, ...],
    ) -> tuple[ScopeExternalReference, ...]:
        if len(set(values)) != len(values):
            raise ValueError("external references must be unique")  # noqa: TRY003
        return values


class ScopeDescriptor(_ScopeValue):
    scope_id: str
    title: str
    summary: str
    parent_scope_id: str | None = None
    context_references: tuple[str, ...] = ()
    external_references: tuple[ScopeExternalReference, ...] = ()
    version: StrictInt = Field(ge=1)

    @field_validator("scope_id")
    @classmethod
    def validate_scope_id(cls, value: str) -> str:
        return validate_scope_id(value)


class ScopeBindingKey(_ScopeValue):
    integration: str
    kind: str
    external_id: str

    @field_validator("integration")
    @classmethod
    def validate_integration(cls, value: str) -> str:
        return _required_text("integration", value, MAX_SCOPE_BINDING_INTEGRATION_LENGTH)

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        return _required_text("kind", value, MAX_SCOPE_BINDING_KIND_LENGTH)

    @field_validator("external_id")
    @classmethod
    def validate_external_id(cls, value: str) -> str:
        return _required_text("external_id", value, MAX_SCOPE_BINDING_EXTERNAL_ID_LENGTH)


class ScopeBinding(_ScopeValue):
    key: ScopeBindingKey
    scope_id: str

    @field_validator("scope_id")
    @classmethod
    def validate_scope_id(cls, value: str) -> str:
        return validate_scope_id(value)


class ScopeSelection(_ScopeValue):
    mode: Literal["all", "exact", "subtree"]
    scope_ids: tuple[str, ...] = ()
    root_scope_id: str | None = None

    @field_validator("scope_ids")
    @classmethod
    def validate_scope_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(validate_scope_id(value) for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("exact Scope selection must be unique")  # noqa: TRY003
        return tuple(sorted(normalized))

    @field_validator("root_scope_id")
    @classmethod
    def validate_root_scope_id(cls, value: str | None) -> str | None:
        return None if value is None else validate_scope_id(value)

    @model_validator(mode="after")
    def validate_shape(self) -> ScopeSelection:
        if self.mode == "all" and (self.scope_ids or self.root_scope_id is not None):
            raise ValueError("all selection accepts no Scope arguments")  # noqa: TRY003
        if self.mode == "exact" and (not self.scope_ids or self.root_scope_id is not None):
            raise ValueError("exact selection requires scope_ids only")  # noqa: TRY003
        if self.mode == "subtree" and (self.root_scope_id is None or self.scope_ids):
            raise ValueError("subtree selection requires root_scope_id only")  # noqa: TRY003
        return self

    @model_serializer(mode="plain")
    def serialize(self) -> dict[str, object]:
        if self.mode == "exact":
            return {"mode": self.mode, "scope_ids": self.scope_ids}
        if self.mode == "subtree":
            return {"mode": self.mode, "root_scope_id": self.root_scope_id}
        return {"mode": self.mode}


def _required_text(field: str, value: str, maximum: int) -> str:
    if not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be non-empty without surrounding whitespace")  # noqa: TRY003
    if len(value) > maximum:
        raise ValueError(f"{field} must not exceed {maximum} characters")  # noqa: TRY003
    return value
