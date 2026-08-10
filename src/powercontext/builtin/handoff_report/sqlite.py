"""SQLite-compatible relational Activity Event Store owned by Handoff Report."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import (
    BigInteger,
    Column,
    Index,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.handoff_report.catalog_store import HANDOFF_REPORT_CATALOG_TABLES
from powercontext.builtin.handoff_report.models import MAX_REPORT_ID_LENGTH, ReportActivityEvent
from powercontext.builtin.handoff_report.report import MAX_REPORT_ACTIVITIES
from powercontext.builtin.handoff_report.repository import (
    ActivityEventConflictError,
    ActivityEventLike,
    ActivityEventSerializationError,
    ActivityTimeBasis,
    InvalidActivityEventError,
    InvalidActivityRepositoryArgumentError,
    StoredActivityEvent,
    StoredActivityEventError,
)
from powercontext.builtin.handoff_report.workspace_store import HANDOFF_REPORT_WORKSPACE_TABLES
from powercontext.limits import MAX_SCOPE_ID_LENGTH

HANDOFF_REPORT_METADATA = MetaData()

HANDOFF_REPORT_ACTIVITY_HEADS_TABLE = Table(
    "pc_handoff_report_activity_heads",
    HANDOFF_REPORT_METADATA,
    Column("project_id", String(MAX_REPORT_ID_LENGTH), primary_key=True),
    Column("cursor", BigInteger, nullable=False),
)

HANDOFF_REPORT_ACTIVITIES_TABLE = Table(
    "pc_handoff_report_activities",
    HANDOFF_REPORT_METADATA,
    Column("project_id", String(MAX_REPORT_ID_LENGTH), primary_key=True),
    Column("cursor", BigInteger, primary_key=True),
    Column("event_id", String(MAX_REPORT_ID_LENGTH), nullable=False, unique=True),
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH)),
    Column("source", String(64), nullable=False),
    Column("source_event_id", String(MAX_REPORT_ID_LENGTH), nullable=False),
    Column("occurred_at", String(32)),
    Column("observed_at", String(32), nullable=False),
    Column("period_at", String(32)),
    Column("time_basis", String(32), nullable=False),
    Column("payload", Text, nullable=False),
    UniqueConstraint("source", "source_event_id", name="uq_pc_handoff_report_activities_source_event"),
)
Index(
    "ix_pc_handoff_report_activities_project_period",
    HANDOFF_REPORT_ACTIVITIES_TABLE.c.project_id,
    HANDOFF_REPORT_ACTIVITIES_TABLE.c.period_at,
    HANDOFF_REPORT_ACTIVITIES_TABLE.c.cursor,
)
Index(
    "ix_pc_handoff_report_activities_project_source_cursor",
    HANDOFF_REPORT_ACTIVITIES_TABLE.c.project_id,
    HANDOFF_REPORT_ACTIVITIES_TABLE.c.source,
    HANDOFF_REPORT_ACTIVITIES_TABLE.c.cursor,
)

HANDOFF_REPORT_TABLES = (
    *HANDOFF_REPORT_CATALOG_TABLES,
    *HANDOFF_REPORT_WORKSPACE_TABLES,
    HANDOFF_REPORT_ACTIVITY_HEADS_TABLE,
    HANDOFF_REPORT_ACTIVITIES_TABLE,
)

_TIME_BASES: frozenset[str] = frozenset({"source_reported", "host_observed", "first_seen", "current_only", "unknown"})
_MAX_ACTIVITY_LIST_LIMIT = MAX_REPORT_ACTIVITIES + 1


class SQLiteActivityEventRepository:
    """Persist activity events without reading or writing Handoff/Core tables.

    The coroutine lock serializes ``record`` calls only through this repository
    instance. Multiple instances or processes still rely on SQLite's writer lock
    and configured busy timeout; deployments must handle lock timeout failures.
    """

    def __init__(self) -> None:
        self._record_lock = asyncio.Lock()

    async def record(self, connection: AsyncConnection, event: ActivityEventLike, /) -> StoredActivityEvent:
        payload, indexed = _canonical_event(event)
        async with self._record_lock:
            return await self._record_locked(connection, payload, indexed)

    async def _record_locked(
        self,
        connection: AsyncConnection,
        payload: str,
        indexed: Mapping[str, Any],
    ) -> StoredActivityEvent:
        # Take SQLite's writer reservation before the idempotency read. This
        # avoids two DEFERRED/WAL transactions reading the same old snapshot
        # and then both attempting to upgrade it to a writer.
        await _reserve_project_writer(connection, str(indexed["project_id"]))
        existing = await self._find_by_source_identity(connection, indexed["source"], indexed["source_event_id"])
        if existing is not None:
            return _idempotent_result(existing, payload)

        cursor = await _allocate_cursor(connection, indexed["project_id"])
        try:
            await connection.execute(
                insert(HANDOFF_REPORT_ACTIVITIES_TABLE).values(
                    project_id=indexed["project_id"],
                    cursor=cursor,
                    event_id=indexed["event_id"],
                    scope_id=indexed["scope_id"],
                    source=indexed["source"],
                    source_event_id=indexed["source_event_id"],
                    occurred_at=indexed["occurred_at"],
                    observed_at=indexed["observed_at"],
                    period_at=indexed["period_at"],
                    time_basis=indexed["time_basis"],
                    payload=payload,
                )
            )
        except IntegrityError:
            existing = await self._find_by_source_identity(connection, indexed["source"], indexed["source_event_id"])
            if existing is None:
                raise
            return _idempotent_result(existing, payload)

        row = await self._find_by_project_cursor(connection, indexed["project_id"], cursor)
        if row is None:  # pragma: no cover - a successful insert must be visible in its transaction
            raise StoredActivityEventError("event", "inserted row is not readable")
        return _decode_row(row)

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
        project_id, start, end, normalized_sources = _validate_list_arguments(
            project_id,
            period_start=period_start,
            period_end=period_end,
            sources=sources,
            after_cursor=after_cursor,
            through_cursor=through_cursor,
            limit=limit,
        )
        if normalized_sources == ():
            return ()
        statement = _activity_list_statement(
            project_id,
            start=start,
            end=end,
            sources=normalized_sources,
            after_cursor=after_cursor,
            through_cursor=through_cursor,
            limit=limit,
        )
        rows = (await connection.execute(statement)).mappings()
        return tuple(_decode_row(row) for row in rows)

    async def high_watermark(self, connection: AsyncConnection, project_id: str, /) -> int:
        project_id = _identifier("project_id", project_id, maximum=MAX_REPORT_ID_LENGTH)
        value = await connection.scalar(
            select(HANDOFF_REPORT_ACTIVITY_HEADS_TABLE.c.cursor).where(
                HANDOFF_REPORT_ACTIVITY_HEADS_TABLE.c.project_id == project_id
            )
        )
        return 0 if value is None else int(value)

    async def purge(self, connection: AsyncConnection, project_id: str, observed_before: datetime, /) -> int:
        project_id = _identifier("project_id", project_id, maximum=MAX_REPORT_ID_LENGTH)
        boundary = _utc_text("observed_before", observed_before)
        result = await connection.execute(
            delete(HANDOFF_REPORT_ACTIVITIES_TABLE).where(
                HANDOFF_REPORT_ACTIVITIES_TABLE.c.project_id == project_id,
                HANDOFF_REPORT_ACTIVITIES_TABLE.c.observed_at < boundary,
            )
        )
        return int(result.rowcount or 0)

    async def _find_by_source_identity(
        self, connection: AsyncConnection, source: str, source_event_id: str
    ) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(HANDOFF_REPORT_ACTIVITIES_TABLE).where(
                        HANDOFF_REPORT_ACTIVITIES_TABLE.c.source == source,
                        HANDOFF_REPORT_ACTIVITIES_TABLE.c.source_event_id == source_event_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def _find_by_project_cursor(
        self, connection: AsyncConnection, project_id: str, cursor: int
    ) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(HANDOFF_REPORT_ACTIVITIES_TABLE).where(
                        HANDOFF_REPORT_ACTIVITIES_TABLE.c.project_id == project_id,
                        HANDOFF_REPORT_ACTIVITIES_TABLE.c.cursor == cursor,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )


def _validate_list_arguments(
    project_id: str,
    *,
    period_start: datetime | None,
    period_end: datetime | None,
    sources: Iterable[str] | None,
    after_cursor: int,
    through_cursor: int | None,
    limit: int | None,
) -> tuple[str, str | None, str | None, tuple[str, ...] | None]:
    project_id = _identifier("project_id", project_id, maximum=MAX_REPORT_ID_LENGTH)
    _require_integer("after_cursor", after_cursor, minimum=0)
    if through_cursor is not None:
        _require_integer("through_cursor", through_cursor, minimum=0)
    if limit is not None:
        _require_integer("limit", limit, minimum=1, maximum=_MAX_ACTIVITY_LIST_LIMIT)
    start = None if period_start is None else _utc_text("period_start", period_start)
    end = None if period_end is None else _utc_text("period_end", period_end)
    if start is not None and end is not None and start >= end:
        raise InvalidActivityRepositoryArgumentError("period", "start must be before end")
    normalized_sources = None
    if sources is not None:
        normalized_sources = tuple(dict.fromkeys(_identifier("source", item, maximum=64) for item in sources))
    return project_id, start, end, normalized_sources


def _activity_list_statement(
    project_id: str,
    *,
    start: str | None,
    end: str | None,
    sources: tuple[str, ...] | None,
    after_cursor: int,
    through_cursor: int | None,
    limit: int | None,
):
    statement = select(HANDOFF_REPORT_ACTIVITIES_TABLE).where(
        HANDOFF_REPORT_ACTIVITIES_TABLE.c.project_id == project_id,
        HANDOFF_REPORT_ACTIVITIES_TABLE.c.cursor > after_cursor,
    )
    if through_cursor is not None:
        statement = statement.where(HANDOFF_REPORT_ACTIVITIES_TABLE.c.cursor <= through_cursor)
    if start is not None:
        statement = statement.where(HANDOFF_REPORT_ACTIVITIES_TABLE.c.period_at >= start)
    if end is not None:
        statement = statement.where(HANDOFF_REPORT_ACTIVITIES_TABLE.c.period_at < end)
    if sources is not None:
        statement = statement.where(HANDOFF_REPORT_ACTIVITIES_TABLE.c.source.in_(sources))
    statement = statement.order_by(HANDOFF_REPORT_ACTIVITIES_TABLE.c.cursor)
    return statement if limit is None else statement.limit(limit)


async def _reserve_project_writer(connection: AsyncConnection, project_id: str) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        statement = sqlite_insert(HANDOFF_REPORT_ACTIVITY_HEADS_TABLE).values(project_id=project_id, cursor=0)
        statement = statement.on_conflict_do_update(
            index_elements=(HANDOFF_REPORT_ACTIVITY_HEADS_TABLE.c.project_id,),
            set_={"cursor": HANDOFF_REPORT_ACTIVITY_HEADS_TABLE.c.cursor},
        )
    elif dialect == "mysql":
        statement = mysql_insert(HANDOFF_REPORT_ACTIVITY_HEADS_TABLE).values(project_id=project_id, cursor=0)
        statement = statement.on_duplicate_key_update(cursor=HANDOFF_REPORT_ACTIVITY_HEADS_TABLE.c.cursor)
    else:
        raise InvalidActivityRepositoryArgumentError("dialect", f"unsupported database dialect: {dialect}")
    await connection.execute(statement)


async def _allocate_cursor(connection: AsyncConnection, project_id: str) -> int:
    result = await connection.execute(
        update(HANDOFF_REPORT_ACTIVITY_HEADS_TABLE)
        .where(HANDOFF_REPORT_ACTIVITY_HEADS_TABLE.c.project_id == project_id)
        .values(cursor=HANDOFF_REPORT_ACTIVITY_HEADS_TABLE.c.cursor + 1)
    )
    if result.rowcount != 1:
        raise StoredActivityEventError("cursor", "Project allocator is missing")
    value = await connection.scalar(
        select(HANDOFF_REPORT_ACTIVITY_HEADS_TABLE.c.cursor).where(
            HANDOFF_REPORT_ACTIVITY_HEADS_TABLE.c.project_id == project_id
        )
    )
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise StoredActivityEventError("cursor", "must be a positive integer")
    return value


def _canonical_event(event: ActivityEventLike) -> tuple[str, dict[str, Any]]:
    try:
        dumped = event.model_dump(mode="json", by_alias=True)
    except (AttributeError, TypeError, ValueError) as error:
        raise ActivityEventSerializationError("model_dump", "JSON mode failed") from error
    if not isinstance(dumped, dict):
        raise ActivityEventSerializationError("model_dump", "result must be a dictionary")
    try:
        candidate_payload = json.dumps(
            dumped,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ActivityEventSerializationError("JSON encoding", "not serializable") from error  # noqa: TRY003
    try:
        validated = ReportActivityEvent.model_validate_json(candidate_payload)
    except ValidationError as error:
        raise InvalidActivityEventError("payload", "does not match ReportActivityEvent") from error

    canonical = validated.model_dump(mode="json", by_alias=True)
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    event_id = _identifier("event_id", validated.event_id, maximum=MAX_REPORT_ID_LENGTH)
    project_id = _identifier("project_id", validated.project_id, maximum=MAX_REPORT_ID_LENGTH)
    scope_id = (
        None if validated.scope_id is None else _identifier("scope_id", validated.scope_id, maximum=MAX_SCOPE_ID_LENGTH)
    )
    source = _identifier("source", validated.source, maximum=64)
    source_event_id = _identifier("source_event_id", validated.source_event_id, maximum=MAX_REPORT_ID_LENGTH)
    observed_at = _utc_text("observed_at", validated.observed_at)
    occurred_at = None if validated.occurred_at is None else _utc_text("occurred_at", validated.occurred_at)
    period_time = validated.effective_period_time()
    period_at = None if period_time is None else _utc_text("period_at", period_time)
    return payload, {
        "event_id": event_id,
        "project_id": project_id,
        "scope_id": scope_id,
        "source": source,
        "source_event_id": source_event_id,
        "occurred_at": occurred_at,
        "observed_at": observed_at,
        "period_at": period_at,
        "time_basis": validated.time_basis,
    }


def _idempotent_result(row: Mapping[Any, Any], payload: str) -> StoredActivityEvent:
    if _semantic_payload(str(row["payload"])) != _semantic_payload(payload):
        raise ActivityEventConflictError(str(row["source"]), str(row["source_event_id"]))
    return _decode_row(row)


def _semantic_payload(payload: str) -> str:
    try:
        value = json.loads(payload)
    except (TypeError, ValueError) as error:
        raise StoredActivityEventError("payload", "must be valid JSON") from error
    if not isinstance(value, dict):
        raise StoredActivityEventError("payload", "must be a JSON object")
    value.pop("event_id", None)
    value.pop("observed_at", None)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _decode_row(row: Mapping[Any, Any]) -> StoredActivityEvent:
    time_basis = str(row["time_basis"])
    if time_basis not in _TIME_BASES:
        raise StoredActivityEventError("time_basis", "unsupported value")
    payload = json.loads(str(row["payload"]))
    if not isinstance(payload, dict):
        raise StoredActivityEventError("payload", "must be a JSON object")
    return StoredActivityEvent(
        cursor=int(row["cursor"]),
        event_id=str(row["event_id"]),
        project_id=str(row["project_id"]),
        scope_id=None if row["scope_id"] is None else str(row["scope_id"]),
        source=str(row["source"]),
        source_event_id=str(row["source_event_id"]),
        occurred_at=None if row["occurred_at"] is None else _parse_utc(str(row["occurred_at"])),
        observed_at=_parse_utc(str(row["observed_at"])),
        time_basis=cast(ActivityTimeBasis, time_basis),
        payload=payload,
    )


def _identifier(field: str, value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InvalidActivityRepositoryArgumentError(field, "must be a non-empty trimmed string")
    if len(value) > maximum:
        raise InvalidActivityRepositoryArgumentError(field, f"must not exceed {maximum} characters")
    return value


def _require_integer(field: str, value: object, *, minimum: int, maximum: int | None = None) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidActivityRepositoryArgumentError(field, "must be an integer")
    if value < minimum:
        raise InvalidActivityRepositoryArgumentError(field, f"must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise InvalidActivityRepositoryArgumentError(field, f"must not exceed {maximum}")


def _utc_text(field: str, value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidActivityRepositoryArgumentError(field, "must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
