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

"""Relational statistics reads and atomic daily usage increments."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Text, and_, cast, func, literal, null, select, union_all
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.atomic_memory import atomic_memory_state_expression
from powercontext.builtin.persistence.database import SELECTION_BATCH_SIZE
from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACTS_TABLE,
    CANDIDATE_HEADS_TABLE,
    CONTEXT_BOOTSTRAP_RECEIPTS_TABLE,
    MODEL_USAGE_DAILY_TABLE,
    MYSQL_IDENTITY_COLLATION,
    RECALL_EFFORT_DAILY_TABLE,
    RECALL_TOKEN_DAILY_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
)
from powercontext.builtin.sources import validate_scope_id
from powercontext.builtin.statistics import (
    ModelUsageOperation,
    ModelUsagePurpose,
    RecallEffortMeasurement,
    RecallTokenMeasurement,
)
from powercontext.builtin.statistics.models import BootstrapDeliveryReading


@dataclass(frozen=True, slots=True)
class StoredInventoryCounts:
    """Current relational head counts and grouped authoritative Atomic Memory counts."""

    sources: int
    artifacts: tuple[tuple[str, int], ...]
    candidates: tuple[tuple[str, str, int], ...]
    memories: tuple[tuple[str, str, int], ...] = ()


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

    async def bootstrap_delivery_many(
        self,
        connection: AsyncConnection,
        scope_ids: Sequence[str],
        start_date: date,
        end_date: date,
    ) -> dict[str, tuple[BootstrapDeliveryReading, ...]]:
        """Aggregate authorized receipt metadata without reading identities or package items."""
        scopes = _validated_scopes(scope_ids)
        table = CONTEXT_BOOTSTRAP_RECEIPTS_TABLE
        result: dict[str, list[BootstrapDeliveryReading]] = {scope: [] for scope in scopes}
        start = datetime.combine(start_date, time.min, UTC)
        end = datetime.combine(end_date + timedelta(days=1), time.min, UTC)
        for offset in range(0, len(scopes), SELECTION_BATCH_SIZE):
            rows = (
                await connection.execute(
                    select(
                        table.c.scope_id,
                        table.c.integration,
                        table.c.state,
                        func.count().label("receipts"),
                        func.sum(table.c.content_bytes).label("content_bytes"),
                        func.max(table.c.created_at).label("last_prepared_at"),
                    )
                    .where(
                        table.c.scope_id.in_(scopes[offset : offset + SELECTION_BATCH_SIZE]),
                        table.c.created_at >= start,
                        table.c.created_at < end,
                    )
                    .group_by(table.c.scope_id, table.c.integration, table.c.state)
                    .order_by(table.c.scope_id, table.c.integration, table.c.state)
                )
            ).mappings()
            for row in rows:
                prepared_at = row["last_prepared_at"]
                if prepared_at.tzinfo is None:
                    prepared_at = prepared_at.replace(tzinfo=UTC)
                result[row["scope_id"]].append(
                    BootstrapDeliveryReading(
                        integration=row["integration"],
                        state=row["state"],
                        receipts=row["receipts"],
                        content_bytes=row["content_bytes"],
                        last_prepared_at=prepared_at,
                    )
                )
        return {scope: tuple(rows) for scope, rows in result.items()}

    async def inventory_many(
        self,
        connection: AsyncConnection,
        scope_ids: Sequence[str],
        /,
    ) -> dict[str, StoredInventoryCounts]:
        """Return head counts for every requested Scope, grouped in one pass per table."""

        scopes = _validated_scopes(scope_ids)
        positions: dict[str, int] = {}
        artifacts: dict[str, list[tuple[str, int]]] = {scope: [] for scope in scopes}
        candidates: dict[str, list[tuple[str, str, int]]] = {scope: [] for scope in scopes}
        memories: dict[str, list[tuple[str, str, int]]] = {scope: [] for scope in scopes}
        kind = func.json_extract(cast(ARTIFACTS_TABLE.c.content, Text), "$.kind")
        if connection.dialect.name == "mysql":
            kind = func.json_unquote(kind).collate(MYSQL_IDENTITY_COLLATION)
        else:
            kind = kind.collate("BINARY")
        # The categories share a result shape, so fetch their aggregates in one
        # round trip without multiplying rows between independent inventories.
        source = SOURCE_JOURNAL_HEADS_TABLE.c
        head = ARTIFACT_HEADS_TABLE.c
        candidate = CANDIDATE_HEADS_TABLE.c
        batch_size = SELECTION_BATCH_SIZE // 4
        for offset in range(0, len(scopes), batch_size):
            batch = scopes[offset : offset + batch_size]
            counts = union_all(
                select(
                    source.scope_id,
                    literal(0).label("category"),
                    null().label("family"),
                    null().label("kind"),
                    null().label("state"),
                    source.position.label("total"),
                ).where(source.scope_id.in_(batch)),
                select(head.scope_id, literal(1), head.family, null(), null(), func.count())
                .where(head.scope_id.in_(batch), head.family != "memory")
                .group_by(head.scope_id, head.family),
                select(candidate.scope_id, literal(2), candidate.family, null(), candidate.status, func.count())
                .where(candidate.scope_id.in_(batch))
                .group_by(candidate.scope_id, candidate.family, candidate.status),
                # Count all lifecycle states against exact authoritative heads.
                select(head.scope_id, literal(3), null(), kind, atomic_memory_state_expression(), func.count())
                .select_from(
                    ARTIFACT_HEADS_TABLE.join(
                        ARTIFACTS_TABLE,
                        and_(
                            ARTIFACTS_TABLE.c.scope_id == head.scope_id,
                            ARTIFACTS_TABLE.c.family == head.family,
                            ARTIFACTS_TABLE.c.artifact_id == head.artifact_id,
                            ARTIFACTS_TABLE.c.revision == head.revision,
                        ),
                    )
                )
                .where(head.scope_id.in_(batch), head.family == "atomic-memory")
                .group_by(head.scope_id, kind, atomic_memory_state_expression()),
            ).subquery("inventory_counts")
            rows = (
                await connection.execute(
                    select(counts).order_by(
                        counts.c.scope_id,
                        counts.c.category,
                        counts.c.family,
                        counts.c.kind,
                        counts.c.state,
                    )
                )
            ).mappings()
            for row in rows:
                scope, total = str(row["scope_id"]), int(row["total"])
                if row["category"] == 0:
                    positions[scope] = total
                elif row["category"] == 1:
                    artifacts[scope].append((str(row["family"]), total))
                elif row["category"] == 2:
                    candidates[scope].append((str(row["family"]), str(row["state"]), total))
                else:
                    memories[scope].append((str(row["kind"]), str(row["state"]), total))
        return {
            scope: StoredInventoryCounts(
                sources=positions.get(scope, 0),
                artifacts=tuple(artifacts[scope]),
                candidates=tuple(candidates[scope]),
                memories=tuple(memories[scope]),
            )
            for scope in scopes
        }

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

    async def preparation_days(self, connection, scope_id, start_date, end_date):
        """Read preparation counts across estimator versions, without estimating token savings."""
        scope = validate_scope_id(scope_id)
        if not 0 <= (end_date - start_date).days < 92:
            raise InvalidRepositoryArgumentError("period", "requires an inclusive range of at most 92 days")
        table = RECALL_TOKEN_DAILY_TABLE
        rows = await connection.execute(
            select(table.c.usage_date, func.sum(table.c.preparations).label("preparations"))
            .where(table.c.scope_id == scope, table.c.usage_date >= start_date, table.c.usage_date <= end_date)
            .group_by(table.c.usage_date)
            .order_by(table.c.usage_date)
        )
        return tuple((row.usage_date, int(row.preparations)) for row in rows)

    async def daily_usage_many(
        self,
        connection: AsyncConnection,
        scope_ids: Sequence[str],
        start_date: date,
        end_date: date,
        *,
        estimator: tuple[str, str] | None,
    ) -> tuple[dict[str, tuple[StoredModelUsage, ...]], dict[str, tuple[StoredRecallTokenUsage, ...]]]:
        """Read model and recall aggregates together without inventory or recurrence."""
        scopes = _validated_scopes(scope_ids)
        if start_date > end_date:
            raise InvalidRepositoryArgumentError("period", "start_date must not follow end_date")
        usage, recall = {}, {}
        batch_size = SELECTION_BATCH_SIZE // 2
        for offset in range(0, len(scopes), batch_size):
            batch = scopes[offset : offset + batch_size]
            rows = (
                await connection.execute(
                    daily_usage_statement(
                        batch,
                        start_date,
                        end_date,
                        estimator=estimator,
                    )
                )
            ).mappings()
            model_rows, recall_rows = decode_daily_usage(rows, batch)
            usage.update(model_rows)
            recall.update(recall_rows)
        return usage, recall

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

    async def record_recall_effort(
        self,
        connection: AsyncConnection,
        scope_id: str,
        usage_date: date,
        measurement: RecallEffortMeasurement,
        /,
    ) -> None:
        """Atomically add one validated observation to its scope-local UTC daily key."""

        scope = validate_scope_id(scope_id)
        # Validate even an embedding caller's model_construct/model_copy value
        # before issuing SQL; inconsistent counts must never be silently repaired.
        values = RecallEffortMeasurement.model_validate(measurement.model_dump()).model_dump()
        values.update(scope_id=scope, usage_date=usage_date)
        keys = ("scope_id", "usage_date", "policy_id", "assessment")
        counters = tuple(name for name in values if name not in keys)
        dialect = connection.dialect.name
        if dialect == "sqlite":
            statement = sqlite_insert(RECALL_EFFORT_DAILY_TABLE).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=keys,
                set_={name: RECALL_EFFORT_DAILY_TABLE.c[name] + statement.excluded[name] for name in counters},
            )
        elif dialect == "mysql":
            statement = mysql_insert(RECALL_EFFORT_DAILY_TABLE).values(**values)
            statement = statement.on_duplicate_key_update(**{
                name: RECALL_EFFORT_DAILY_TABLE.c[name] + statement.inserted[name] for name in counters
            })
        else:
            raise InvalidRepositoryArgumentError("dialect", f"unsupported database dialect: {dialect}")
        await connection.execute(statement)


def daily_usage_statement(
    scope_ids: Sequence[str],
    start_date: date,
    end_date: date,
    *,
    estimator: tuple[str, str] | None,
):
    """Compose one bounded daily-aggregate read; callers may add a page header."""
    scopes = _validated_scopes(scope_ids)
    if start_date > end_date:
        raise InvalidRepositoryArgumentError("period", "start_date must not follow end_date")
    model, recall = MODEL_USAGE_DAILY_TABLE, RECALL_TOKEN_DAILY_TABLE
    columns = {
        column.name: column
        for table in (model, recall)
        for column in table.c
        if column.name not in {"estimator_id", "estimator_version"}
    }

    def project(table, kind):
        return select(
            literal(kind).label("usage_kind"),
            *(
                table.c[name] if name in table.c else literal(None, type_=column.type).label(name)
                for name, column in columns.items()
            ),
        ).where(
            table.c.scope_id.in_(scopes),
            table.c.usage_date >= start_date,
            table.c.usage_date <= end_date,
        )

    statements = [project(model, "model")]
    if estimator is not None:
        statements.append(
            project(recall, "recall").where(
                recall.c.estimator_id == estimator[0],
                recall.c.estimator_version == estimator[1],
            )
        )
    rows = union_all(*statements).subquery("daily_usage")
    return select(rows).order_by(
        rows.c.scope_id, rows.c.usage_date, rows.c.usage_kind, rows.c.purpose, rows.c.operation
    )


def decode_daily_usage(
    rows: Iterable[Mapping[Any, Any]],
    scope_ids: Sequence[str],
) -> tuple[dict[str, tuple[StoredModelUsage, ...]], dict[str, tuple[StoredRecallTokenUsage, ...]]]:
    """Separate tagged daily rows while preserving their existing typed aggregation inputs."""
    usage: dict[str, list[StoredModelUsage]] = {scope: [] for scope in scope_ids}
    recall: dict[str, list[StoredRecallTokenUsage]] = {scope: [] for scope in scope_ids}
    for row in rows:
        scope = str(row["scope_id"])
        if row["usage_kind"] == "model":
            usage[scope].append(_model_usage(row))
        else:
            recall[scope].append(_recall_usage(row))
    return {scope: tuple(values) for scope, values in usage.items()}, {
        scope: tuple(values) for scope, values in recall.items()
    }


def _model_usage(row: Mapping[Any, Any]) -> StoredModelUsage:
    return StoredModelUsage(
        usage_date=row["usage_date"],
        purpose=ModelUsagePurpose(str(row["purpose"])),
        operation=ModelUsageOperation(str(row["operation"])),
        requests=int(row["requests"]),
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        input_complete=bool(row["input_complete"]),
        output_complete=bool(row["output_complete"]),
    )


def _recall_usage(row: Mapping[Any, Any]) -> StoredRecallTokenUsage:
    return StoredRecallTokenUsage(
        usage_date=row["usage_date"],
        preparations=int(row["preparations"]),
        ready_preparations=int(row["ready_preparations"]),
        comparable_preparations=int(row["comparable_preparations"]),
        baseline_tokens=int(row["baseline_tokens"]),
        recalled_tokens=int(row["recalled_tokens"]),
    )


def _validated_scopes(scope_ids: Sequence[str], /) -> tuple[str, ...]:
    """Validate and de-duplicate a selection while preserving caller order."""

    seen: dict[str, None] = {}
    for scope_id in scope_ids:
        seen.setdefault(validate_scope_id(scope_id), None)
    return tuple(seen)


def _batches(scopes: tuple[str, ...], /) -> Iterator[tuple[str, ...]]:
    """Split a selection so one query never exceeds a backend's bind-parameter limit."""

    for start in range(0, len(scopes), SELECTION_BATCH_SIZE):
        yield scopes[start : start + SELECTION_BATCH_SIZE]


__all__ = ["StatisticsRepository", "StoredInventoryCounts", "StoredModelUsage", "StoredRecallTokenUsage"]
