"""Pydantic model persistence helpers."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from powercontext.builtin.persistence.errors import InvalidStoredColumnError, InvalidStoredPayloadError

ModelT = TypeVar("ModelT", bound=BaseModel)


def stored_bytes(value: object, /, *, column: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray | memoryview):
        return bytes(value)
    raise InvalidStoredColumnError(column, "bytes")


def dump_model(value: BaseModel, /, *, kind: str, name: str) -> bytes:
    try:
        return value.model_dump_json(by_alias=True).encode()
    except (TypeError, ValueError) as error:
        raise InvalidStoredPayloadError(kind, name, "value is not JSON serializable") from error


def load_model(model: type[ModelT], payload: object, /, *, kind: str, name: str) -> ModelT:
    if not isinstance(payload, bytes):
        raise InvalidStoredPayloadError(kind, name, "payload column is not bytes")
    try:
        return model.model_validate_json(payload, strict=True)
    except ValidationError as error:
        raise InvalidStoredPayloadError(kind, name, "payload does not match the model") from error
