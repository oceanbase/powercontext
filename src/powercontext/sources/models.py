from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, field_validator

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


class Source(BaseModel):
    """Base value for an adapter-owned Source description."""

    name: str
    materialization: SourceMaterialization
    description: str | None = None


def _validate_reference_part(field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidSourceReferenceError(field, "must be a non-empty string")
    if value != value.strip():
        raise InvalidSourceReferenceError(field, "must not contain leading or trailing whitespace")
    maximum = MAX_SOURCE_TYPE_LENGTH if field == "source_type" else MAX_SOURCE_ID_LENGTH
    if len(value) > maximum:
        raise InvalidSourceReferenceError(field, f"must not exceed {maximum} characters")
