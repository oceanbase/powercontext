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

"""Relational reads and atomic daily model-usage increments."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.database import SELECTION_BATCH_SIZE
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
        counts = await self.inventory_many(connection, (scope_id,))
        return counts[validate_scope_id(scope_id)]

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
        for batch in _batches(scopes):
            for scope, position in (
                await connection.execute(
                    select(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id, SOURCE_JOURNAL_HEADS_TABLE.c.position).where(
                        SOURCE_JOURNAL_HEADS_TABLE.c.scope_id.in_(batch)
                    )
                )
            ).all():
                positions[str(scope)] = int(position)
            for scope, family, total in (
                await connection.execute(
                    select(ARTIFACT_HEADS_TABLE.c.scope_id, ARTIFACT_HEADS_TABLE.c.family, func.count())
                    .where(ARTIFACT_HEADS_TABLE.c.scope_id.in_(batch))
                    .group_by(ARTIFACT_HEADS_TABLE.c.scope_id, ARTIFACT_HEADS_TABLE.c.family)
                    .order_by(ARTIFACT_HEADS_TABLE.c.scope_id, ARTIFACT_HEADS_TABLE.c.family)
                )
            ).all():
                artifacts[str(scope)].append((str(family), int(total)))
            for scope, family, status, total in (
                await connection.execute(
                    select(
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id,
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.family,
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.status,
                        func.count(),
                    )
                    .where(ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id.in_(batch))
                    .group_by(
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id,
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.family,
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.status,
                    )
                    .order_by(
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id,
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.family,
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.status,
                    )
                )
            ).all():
                candidates[str(scope)].append((str(family), str(status), int(total)))
        return {
            scope: StoredInventoryCounts(
                sources=positions.get(scope, 0),
                artifacts=tuple(artifacts[scope]),
                candidates=tuple(candidates[scope]),
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

    async def usage(
        self,
        connection: AsyncConnection,
        scope_id: str,
        start_date: date,
        end_date: date,
        /,
    ) -> tuple[StoredModelUsage, ...]:
        rows = await self.usage_many(connection, (scope_id,), start_date, end_date)
        return rows[validate_scope_id(scope_id)]

    async def usage_many(
        self,
        connection: AsyncConnection,
        scope_ids: Sequence[str],
        start_date: date,
        end_date: date,
        /,
    ) -> dict[str, tuple[StoredModelUsage, ...]]:
        """Return each requested Scope's daily model usage over one shared period."""

        scopes = _validated_scopes(scope_ids)
        if start_date > end_date:
            raise InvalidRepositoryArgumentError("period", "start_date must not follow end_date")
        collected: dict[str, list[StoredModelUsage]] = {scope: [] for scope in scopes}
        for batch in _batches(scopes):
            rows = (
                await connection.execute(
                    select(MODEL_USAGE_DAILY_TABLE)
                    .where(
                        MODEL_USAGE_DAILY_TABLE.c.scope_id.in_(batch),
                        MODEL_USAGE_DAILY_TABLE.c.usage_date >= start_date,
                        MODEL_USAGE_DAILY_TABLE.c.usage_date <= end_date,
                    )
                    .order_by(
                        MODEL_USAGE_DAILY_TABLE.c.scope_id,
                        MODEL_USAGE_DAILY_TABLE.c.usage_date,
                        MODEL_USAGE_DAILY_TABLE.c.purpose,
                        MODEL_USAGE_DAILY_TABLE.c.operation,
                    )
                )
            ).mappings()
            for row in rows:
                collected[str(row["scope_id"])].append(
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
                )
        return {scope: tuple(rows) for scope, rows in collected.items()}

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
        rows = await self.recall_usage_many(
            connection,
            (scope_id,),
            start_date,
            end_date,
            estimator_id=estimator_id,
            estimator_version=estimator_version,
        )
        return rows[validate_scope_id(scope_id)]

    async def recall_usage_many(
        self,
        connection: AsyncConnection,
        scope_ids: Sequence[str],
        start_date: date,
        end_date: date,
        *,
        estimator_id: str,
        estimator_version: str,
    ) -> dict[str, tuple[StoredRecallTokenUsage, ...]]:
        """Return each requested Scope's daily recall-token aggregates for one estimator."""

        scopes = _validated_scopes(scope_ids)
        if start_date > end_date:
            raise InvalidRepositoryArgumentError("period", "start_date must not follow end_date")
        collected: dict[str, list[StoredRecallTokenUsage]] = {scope: [] for scope in scopes}
        for batch in _batches(scopes):
            rows = (
                await connection.execute(
                    select(RECALL_TOKEN_DAILY_TABLE)
                    .where(
                        RECALL_TOKEN_DAILY_TABLE.c.scope_id.in_(batch),
                        RECALL_TOKEN_DAILY_TABLE.c.usage_date >= start_date,
                        RECALL_TOKEN_DAILY_TABLE.c.usage_date <= end_date,
                        RECALL_TOKEN_DAILY_TABLE.c.estimator_id == estimator_id,
                        RECALL_TOKEN_DAILY_TABLE.c.estimator_version == estimator_version,
                    )
                    .order_by(RECALL_TOKEN_DAILY_TABLE.c.scope_id, RECALL_TOKEN_DAILY_TABLE.c.usage_date)
                )
            ).mappings()
            for row in rows:
                collected[str(row["scope_id"])].append(
                    StoredRecallTokenUsage(
                        usage_date=row["usage_date"],
                        preparations=int(row["preparations"]),
                        ready_preparations=int(row["ready_preparations"]),
                        comparable_preparations=int(row["comparable_preparations"]),
                        baseline_tokens=int(row["baseline_tokens"]),
                        recalled_tokens=int(row["recalled_tokens"]),
                    )
                )
        return {scope: tuple(rows) for scope, rows in collected.items()}


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
