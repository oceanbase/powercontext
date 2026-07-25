"""Storage contracts and values for the built-in scoped Source journal."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from powercontext.limits import MAX_SCOPE_ID_LENGTH
from powercontext.sources import Source


def validate_scope_id(value: str) -> str:
    """Validate an opaque built-in family scope."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("scope_id must not be empty")  # noqa: TRY003
    if len(value) > MAX_SCOPE_ID_LENGTH:
        raise ValueError(f"scope_id must not exceed {MAX_SCOPE_ID_LENGTH} characters")  # noqa: TRY003
    return value


class SourceCursor(BaseModel):
    """The last Source sequence consumed by one family trigger."""

    sequence: int = 0


@runtime_checkable
class SourceJournal(Protocol):
    """Read stable positions from one scoped Source catalog."""

    async def position(self, source: Source, /) -> int: ...
