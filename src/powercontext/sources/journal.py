"""Integration-owned values and storage contracts for a scoped Source journal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from powercontext.sources.models import Source


class _InvalidScopeError(ValueError):
    def __init__(self, code: str) -> None:
        message = "scope_id must not be empty" if code == "empty" else "scope_id must not exceed 256 characters"
        super().__init__(message)


def validate_scope_id(value: str) -> str:
    """Validate an opaque family scope without assigning global semantics."""

    if not isinstance(value, str) or not value.strip():
        raise _InvalidScopeError("empty")
    if len(value) > 256:
        raise _InvalidScopeError("length")
    return value


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """One Source at a stable position in a scoped journal."""

    sequence: int
    source: Source


@dataclass(frozen=True, slots=True)
class SourceCursor:
    """The last Source sequence consumed by one family trigger."""

    sequence: int = 0


class SourceJournal(Protocol):
    """Read stable positions from one scoped Source catalog."""

    async def position(self, source: Source, /) -> int: ...

    async def high_watermark(self) -> int: ...

    async def list_between(self, after: int, through: int, /) -> tuple[SourceRecord, ...]: ...


class TriggerCursorStore(Protocol):
    """Persist the successful state of one scoped Trigger."""

    async def load_cursor(self, trigger_name: str, /) -> SourceCursor: ...

    async def save_cursor(self, trigger_name: str, cursor: SourceCursor, /) -> None: ...
