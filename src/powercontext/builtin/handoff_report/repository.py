"""Repository boundary for Report-owned activity observations.

The boundary accepts structural event values and validates their complete payload
against the canonical domain model at the persistence edge.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, TypeAlias, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.handoff_report.errors import HandoffReportError
from powercontext.builtin.handoff_report.models import ReportTimeBasis

ActivityTimeBasis: TypeAlias = ReportTimeBasis


@runtime_checkable
class ActivityEventLike(Protocol):
    """Structural input required by an activity repository."""

    @property
    def event_id(self) -> str: ...

    @property
    def project_id(self) -> str: ...

    @property
    def scope_id(self) -> str | None: ...

    @property
    def source(self) -> str: ...

    @property
    def source_event_id(self) -> str: ...

    @property
    def occurred_at(self) -> datetime | None: ...

    @property
    def observed_at(self) -> datetime: ...

    @property
    def time_basis(self) -> ActivityTimeBasis: ...

    @property
    def trust(self) -> str: ...

    def model_dump(self, *, mode: Literal["json"], by_alias: Literal[True]) -> dict[str, object]:
        """Return the complete canonical event payload."""


@dataclass(frozen=True, slots=True)
class StoredActivityEvent:
    """One stored observation plus its stable per-Project cursor."""

    cursor: int
    event_id: str
    project_id: str
    scope_id: str | None
    source: str
    source_event_id: str
    occurred_at: datetime | None
    observed_at: datetime
    time_basis: ActivityTimeBasis
    payload: Mapping[str, object]


class ActivityEventConflictError(HandoffReportError, ValueError):
    """An idempotency key was reused for a different canonical event."""

    def __init__(self, source: str, source_event_id: str) -> None:
        super().__init__(f"activity event conflict for ({source!r}, {source_event_id!r})")
        self.source = source
        self.source_event_id = source_event_id


class InvalidActivityRepositoryArgumentError(HandoffReportError, ValueError):
    """A repository argument is structurally invalid."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"invalid {field}: {reason}")


class InvalidActivityEventError(HandoffReportError, ValueError):
    """An activity does not satisfy the persistence boundary."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"invalid activity event {field}: {reason}")


class ActivityEventSerializationError(HandoffReportError, TypeError):
    """An activity cannot be converted to its canonical JSON payload."""

    def __init__(self, operation: str, reason: str) -> None:
        super().__init__(f"activity event serialization failed during {operation}: {reason}")


class StoredActivityEventError(HandoffReportError, RuntimeError):
    """Persisted activity data violates the store schema."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"invalid stored activity event {field}: {reason}")


class ActivityEventRepository(Protocol):
    """Persistence operations needed by Activity capture and report assembly."""

    async def record(self, connection: AsyncConnection, event: ActivityEventLike, /) -> StoredActivityEvent:
        """Record an event or return an identical existing capture."""

    async def list(
        self,
        connection: AsyncConnection,
        project_id: str,
        /,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        sources: Iterable[str] | None = None,
        after_cursor: int = 0,
        through_cursor: int | None = None,
        limit: int | None = 50,
    ) -> tuple[StoredActivityEvent, ...]:
        """List a stable cursor-ordered Project page."""

    async def high_watermark(self, connection: AsyncConnection, project_id: str, /) -> int:
        """Return the latest allocated cursor without regressing after retention purge."""

    async def purge(self, connection: AsyncConnection, project_id: str, observed_before: datetime, /) -> int:
        """Delete expired Report-owned events and return the deleted row count."""
