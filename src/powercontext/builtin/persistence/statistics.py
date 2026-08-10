"""Relational reads and atomic daily model-usage increments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError
from powercontext.builtin.persistence.tables import (
    ARTIFACT_CANDIDATE_HEADS_TABLE,
    ARTIFACT_HEADS_TABLE,
    MODEL_USAGE_DAILY_TABLE,
    RECALL_TOKEN_DAILY_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
)
from powercontext.builtin.sources import validate_scope_id
from powercontext.builtin.statistics import ModelUsageOperation, ModelUsagePurpose, RecallTokenMeasurement


@dataclass(frozen=True, slots=True)
class StoredInventoryCounts:
    """Current relational head counts, before Memory manifest expansion."""

    sources: int
    artifacts: tuple[tuple[str, int], ...]
    candidates: tuple[tuple[str, str, int], ...]


@dataclass(frozen=True, slots=True)
class StoredModelUsage:
    """One persisted daily model-usage aggregate."""

    usage_date: date
    purpose: ModelUsagePurpose
    operation: ModelUsageOperation
    requests: int
    input_tokens: int
    output_tokens: int
    input_complete: bool
    output_complete: bool


@dataclass(frozen=True, slots=True)
class StoredRecallTokenUsage:
    """One persisted daily recall-token aggregate."""

    usage_date: date
    preparations: int
    ready_preparations: int
    comparable_preparations: int
    baseline_tokens: int
    recalled_tokens: int


class StatisticsRepository:
    """Read scoped product heads and maintain bounded usage aggregates."""

    async def inventory(self, connection: AsyncConnection, scope_id: str, /) -> StoredInventoryCounts:
        scope = validate_scope_id(scope_id)
        source_position = await connection.scalar(
            select(SOURCE_JOURNAL_HEADS_TABLE.c.position).where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope)
        )
        artifact_rows = (
            await connection.execute(
                select(ARTIFACT_HEADS_TABLE.c.family, func.count())
                .where(ARTIFACT_HEADS_TABLE.c.scope_id == scope)
                .group_by(ARTIFACT_HEADS_TABLE.c.family)
                .order_by(ARTIFACT_HEADS_TABLE.c.family)
            )
        ).all()
        candidate_rows = (
            await connection.execute(
                select(
                    ARTIFACT_CANDIDATE_HEADS_TABLE.c.family,
                    ARTIFACT_CANDIDATE_HEADS_TABLE.c.status,
                    func.count(),
                )
                .where(ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id == scope)
                .group_by(
                    ARTIFACT_CANDIDATE_HEADS_TABLE.c.family,
                    ARTIFACT_CANDIDATE_HEADS_TABLE.c.status,
                )
                .order_by(
                    ARTIFACT_CANDIDATE_HEADS_TABLE.c.family,
                    ARTIFACT_CANDIDATE_HEADS_TABLE.c.status,
                )
            )
        ).all()
        return StoredInventoryCounts(
            sources=0 if source_position is None else int(source_position),
            artifacts=tuple((str(family), int(total)) for family, total in artifact_rows),
            candidates=tuple((str(family), str(status), int(total)) for family, status, total in candidate_rows),
        )

    async def record(
        self,
        connection: AsyncConnection,
        scope_id: str,
        usage_date: date,
        purpose: ModelUsagePurpose,
        operation: ModelUsageOperation,
        usage: InferenceUsage,
        /,
    ) -> None:
        scope = validate_scope_id(scope_id)
        if usage.requests < 0:
            raise InvalidRepositoryArgumentError("requests", "must be non-negative")
        if usage.input_tokens is not None and usage.input_tokens < 0:
            raise InvalidRepositoryArgumentError("input_tokens", "must be non-negative")
        if usage.output_tokens is not None and usage.output_tokens < 0:
            raise InvalidRepositoryArgumentError("output_tokens", "must be non-negative")
        if usage.requests == 0:
            return

        values = {
            "scope_id": scope,
            "usage_date": usage_date,
            "purpose": purpose.value,
            "operation": operation.value,
            "requests": usage.requests,
            "input_tokens": 0 if usage.input_tokens is None else usage.input_tokens,
            "output_tokens": 0 if usage.output_tokens is None else usage.output_tokens,
            "input_complete": usage.input_tokens is not None,
            "output_complete": usage.output_tokens is not None,
        }
        dialect = connection.dialect.name
        if dialect == "sqlite":
            statement = sqlite_insert(MODEL_USAGE_DAILY_TABLE).values(**values)
            incoming = statement.excluded
            statement = statement.on_conflict_do_update(
                index_elements=["scope_id", "usage_date", "purpose", "operation"],
                set_={
                    "requests": MODEL_USAGE_DAILY_TABLE.c.requests + incoming.requests,
                    "input_tokens": MODEL_USAGE_DAILY_TABLE.c.input_tokens + incoming.input_tokens,
                    "output_tokens": MODEL_USAGE_DAILY_TABLE.c.output_tokens + incoming.output_tokens,
                    "input_complete": and_(
                        MODEL_USAGE_DAILY_TABLE.c.input_complete,
                        incoming.input_complete,
                    ),
                    "output_complete": and_(
                        MODEL_USAGE_DAILY_TABLE.c.output_complete,
                        incoming.output_complete,
                    ),
                },
            )
        elif dialect == "mysql":
            statement = mysql_insert(MODEL_USAGE_DAILY_TABLE).values(**values)
            incoming = statement.inserted
            statement = statement.on_duplicate_key_update(
                requests=MODEL_USAGE_DAILY_TABLE.c.requests + incoming.requests,
                input_tokens=MODEL_USAGE_DAILY_TABLE.c.input_tokens + incoming.input_tokens,
                output_tokens=MODEL_USAGE_DAILY_TABLE.c.output_tokens + incoming.output_tokens,
                input_complete=and_(MODEL_USAGE_DAILY_TABLE.c.input_complete, incoming.input_complete),
                output_complete=and_(MODEL_USAGE_DAILY_TABLE.c.output_complete, incoming.output_complete),
            )
        else:
            raise InvalidRepositoryArgumentError("dialect", f"unsupported database dialect: {dialect}")
        await connection.execute(statement)

    async def usage(
        self,
        connection: AsyncConnection,
        scope_id: str,
        start_date: date,
        end_date: date,
        /,
    ) -> tuple[StoredModelUsage, ...]:
        scope = validate_scope_id(scope_id)
        if start_date > end_date:
            raise InvalidRepositoryArgumentError("period", "start_date must not follow end_date")
        rows = (
            await connection.execute(
                select(MODEL_USAGE_DAILY_TABLE)
                .where(
                    MODEL_USAGE_DAILY_TABLE.c.scope_id == scope,
                    MODEL_USAGE_DAILY_TABLE.c.usage_date >= start_date,
                    MODEL_USAGE_DAILY_TABLE.c.usage_date <= end_date,
                )
                .order_by(
                    MODEL_USAGE_DAILY_TABLE.c.usage_date,
                    MODEL_USAGE_DAILY_TABLE.c.purpose,
                    MODEL_USAGE_DAILY_TABLE.c.operation,
                )
            )
        ).mappings()
        return tuple(
            StoredModelUsage(
                usage_date=row["usage_date"],
                purpose=ModelUsagePurpose(str(row["purpose"])),
                operation=ModelUsageOperation(str(row["operation"])),
                requests=int(row["requests"]),
                input_tokens=int(row["input_tokens"]),
                output_tokens=int(row["output_tokens"]),
                input_complete=bool(row["input_complete"]),
                output_complete=bool(row["output_complete"]),
            )
            for row in rows
        )

    async def record_recall(
        self,
        connection: AsyncConnection,
        scope_id: str,
        usage_date: date,
        measurement: RecallTokenMeasurement,
        /,
    ) -> None:
        scope = validate_scope_id(scope_id)
        values = {
            "scope_id": scope,
            "usage_date": usage_date,
            "estimator_id": measurement.estimator.estimator_id,
            "estimator_version": measurement.estimator.version,
            "preparations": 1,
            "ready_preparations": int(measurement.ready),
            "comparable_preparations": int(measurement.comparable),
            "baseline_tokens": measurement.baseline_tokens,
            "recalled_tokens": measurement.recalled_tokens,
        }
        dialect = connection.dialect.name
        if dialect == "sqlite":
            statement = sqlite_insert(RECALL_TOKEN_DAILY_TABLE).values(**values)
            incoming = statement.excluded
            statement = statement.on_conflict_do_update(
                index_elements=["scope_id", "usage_date", "estimator_id", "estimator_version"],
                set_={
                    name: RECALL_TOKEN_DAILY_TABLE.c[name] + incoming[name]
                    for name in (
                        "preparations",
                        "ready_preparations",
                        "comparable_preparations",
                        "baseline_tokens",
                        "recalled_tokens",
                    )
                },
            )
        elif dialect == "mysql":
            statement = mysql_insert(RECALL_TOKEN_DAILY_TABLE).values(**values)
            incoming = statement.inserted
            statement = statement.on_duplicate_key_update(**{
                name: RECALL_TOKEN_DAILY_TABLE.c[name] + incoming[name]
                for name in (
                    "preparations",
                    "ready_preparations",
                    "comparable_preparations",
                    "baseline_tokens",
                    "recalled_tokens",
                )
            })
        else:
            raise InvalidRepositoryArgumentError("dialect", f"unsupported database dialect: {dialect}")
        await connection.execute(statement)

    async def recall_usage(
        self,
        connection: AsyncConnection,
        scope_id: str,
        start_date: date,
        end_date: date,
        *,
        estimator_id: str,
        estimator_version: str,
    ) -> tuple[StoredRecallTokenUsage, ...]:
        scope = validate_scope_id(scope_id)
        if start_date > end_date:
            raise InvalidRepositoryArgumentError("period", "start_date must not follow end_date")
        rows = (
            await connection.execute(
                select(RECALL_TOKEN_DAILY_TABLE)
                .where(
                    RECALL_TOKEN_DAILY_TABLE.c.scope_id == scope,
                    RECALL_TOKEN_DAILY_TABLE.c.usage_date >= start_date,
                    RECALL_TOKEN_DAILY_TABLE.c.usage_date <= end_date,
                    RECALL_TOKEN_DAILY_TABLE.c.estimator_id == estimator_id,
                    RECALL_TOKEN_DAILY_TABLE.c.estimator_version == estimator_version,
                )
                .order_by(RECALL_TOKEN_DAILY_TABLE.c.usage_date)
            )
        ).mappings()
        return tuple(
            StoredRecallTokenUsage(
                usage_date=row["usage_date"],
                preparations=int(row["preparations"]),
                ready_preparations=int(row["ready_preparations"]),
                comparable_preparations=int(row["comparable_preparations"]),
                baseline_tokens=int(row["baseline_tokens"]),
                recalled_tokens=int(row["recalled_tokens"]),
            )
            for row in rows
        )


__all__ = ["StatisticsRepository", "StoredInventoryCounts", "StoredModelUsage", "StoredRecallTokenUsage"]
