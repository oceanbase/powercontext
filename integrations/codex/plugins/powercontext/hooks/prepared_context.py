"""Strict parsing for final context prepared by the PowerContext Runtime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

PREPARED_CONTEXT_SCHEMA = "powercontext.prepared-context.v1"
MAX_CONTEXT_BYTES = 8_000

_PREPARED_CONTEXT_FIELDS = frozenset({"schema", "status", "content", "content_bytes"})


class InvalidPreparedContextResponse(RuntimeError):
    """Raised when a Server response does not satisfy the prepared-context contract."""


def validate_prepared_context(response: Mapping[str, object]) -> dict[str, object]:
    """Return a safe copy after validating the complete v1 response contract."""

    if not isinstance(response, dict) or set(response) != _PREPARED_CONTEXT_FIELDS:
        raise InvalidPreparedContextResponse
    prepared = cast(dict[str, object], response)
    if prepared["schema"] != PREPARED_CONTEXT_SCHEMA:
        raise InvalidPreparedContextResponse

    status = prepared["status"]
    content = prepared["content"]
    content_bytes = prepared["content_bytes"]
    if not isinstance(content_bytes, int) or isinstance(content_bytes, bool) or content_bytes < 0:
        raise InvalidPreparedContextResponse
    if status == "empty":
        if content is not None or content_bytes != 0:
            raise InvalidPreparedContextResponse
    elif status == "ready":
        if not isinstance(content, str) or not content.strip():
            raise InvalidPreparedContextResponse
        if len(content.encode("utf-8")) != content_bytes or content_bytes > MAX_CONTEXT_BYTES:
            raise InvalidPreparedContextResponse
    else:
        raise InvalidPreparedContextResponse
    return {
        "schema": prepared["schema"],
        "status": status,
        "content": content,
        "content_bytes": content_bytes,
    }


__all__ = [
    "MAX_CONTEXT_BYTES",
    "PREPARED_CONTEXT_SCHEMA",
    "InvalidPreparedContextResponse",
    "validate_prepared_context",
]
