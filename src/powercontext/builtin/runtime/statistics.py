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

"""Relational scoped statistics assembly."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory import MemoryService
from powercontext.builtin.inference import InferenceUsage, TokenEstimatorProfile
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.statistics import (
    StatisticsRepository,
    StoredModelUsage,
    StoredRecallTokenUsage,
)
from powercontext.builtin.scope import ScopeSelection
from powercontext.builtin.statistics import (
    ArtifactInventoryStatistics,
    CandidateFamilyCount,
    CandidateInventoryStatistics,
    FamilyCount,
    InventoryStatistics,
    MemoryEntryInventoryStatistics,
    MemoryInventoryStatistics,
    MemoryKindCount,
    ModelUsageDay,
    ModelUsageOperation,
    ModelUsagePurpose,
    ModelUsagePurposeBreakdown,
    ModelUsageStatistics,
    ModelUsageValue,
    RecallTokenDay,
    RecallTokenMeasurement,
    RecallTokenStatistics,
    RecallTokenValue,
    ResolvedUsagePeriod,
    ScopeStatistics,
    SourceInventoryStatistics,
    Statistics,
    StatisticsPeriod,
    UsageStatistics,
)
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME
from powercontext.errors import ArtifactNotFoundError

MemoryServiceFactory = Callable[[AsyncConnection], MemoryService]

_PERIOD_DAYS = {
    StatisticsPeriod.TODAY: 1,
    StatisticsPeriod.SEVEN_DAYS: 7,
    StatisticsPeriod.THIRTY_DAYS: 30,
}


class RelationalScopedStatistics:
    """Assemble one scope's inventory and usage in explicit transactions."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        memory_artifact_id: str,
        memory_service: MemoryServiceFactory,
        cursors: SourceCursorRepository,
        repository: StatisticsRepository,
        token_estimator: TokenEstimatorProfile | None,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._memory_artifact_id = memory_artifact_id
        self._memory_service = memory_service
        self._cursors = cursors
        self._repository = repository
        self._token_estimator = token_estimator

    async def overview(self, period: StatisticsPeriod, as_of: datetime, /) -> Statistics:
        captured_at = _as_utc(as_of)
        resolved_period = _resolve_period(period, captured_at.date())
        async with self._database.transaction() as connection:
            stored_inventory = await self._repository.inventory(connection, self._scope_id)
            cursor = await self._cursors.load(
                connection,
                self._scope_id,
                SOURCE_WINDOW_TRIGGER_NAME,
            )
            memory_entries = await self._memory_entries(connection)
            usage_rows = await self._repository.usage(
                connection,
                self._scope_id,
                resolved_period.start_date,
                resolved_period.end_date,
            )
            recall_rows = (
                ()
                if self._token_estimator is None
                else await self._repository.recall_usage(
                    connection,
                    self._scope_id,
                    resolved_period.start_date,
                    resolved_period.end_date,
                    estimator_id=self._token_estimator.estimator_id,
                    estimator_version=self._token_estimator.version,
                )
            )

        processed = 0 if cursor is None else cursor.cursor.sequence
        artifacts = tuple(FamilyCount(family=family, total=total) for family, total in stored_inventory.artifacts)
        candidates = _candidate_inventory(stored_inventory.candidates)
        inventory = InventoryStatistics(
            sources=SourceInventoryStatistics(
                total=stored_inventory.sources,
                memory_processed=processed,
                memory_pending=max(stored_inventory.sources - processed, 0),
            ),
            artifacts=ArtifactInventoryStatistics(
                total=sum(item.total for item in artifacts),
                by_family=artifacts,
            ),
            candidates=candidates,
            memory=MemoryInventoryStatistics(entries=_memory_inventory(memory_entries)),
        )
        usage = _usage_statistics(resolved_period, usage_rows)
        recall = _recall_statistics(resolved_period, self._token_estimator, recall_rows)
        return Statistics(
            selection=ScopeSelection(mode="exact", scope_ids=(self._scope_id,)),
            scope_ids=(self._scope_id,),
            as_of=captured_at,
            inventory=inventory,
            usage=usage,
            recall=recall,
            by_scope=(
                ScopeStatistics(
                    scope_id=self._scope_id,
                    inventory=inventory,
                    usage=usage,
                    recall=recall,
                ),
            ),
        )

    async def record(
        self,
        purpose: ModelUsagePurpose,
        operation: ModelUsageOperation,
        usage: InferenceUsage,
        usage_date: date,
        /,
    ) -> None:
        async with self._database.transaction() as connection:
            await self._repository.record(
                connection,
                self._scope_id,
                usage_date,
                purpose,
                operation,
                usage,
            )

    async def record_recall(self, measurement: RecallTokenMeasurement, usage_date: date, /) -> None:
        if self._token_estimator != measurement.estimator:
            raise ValueError("recall measurement estimator does not match the deployment profile")  # noqa: TRY003
        async with self._database.transaction() as connection:
            await self._repository.record_recall(
                connection,
                self._scope_id,
                usage_date,
                measurement,
            )

    async def _memory_entries(self, connection: AsyncConnection) -> tuple[tuple[str, str], ...]:
        service = self._memory_service(connection)
        try:
            memory = await service.head(self._memory_artifact_id)
        except ArtifactNotFoundError:
            return ()
        entries = await service.entries(memory)
        states = {item.entry_id: item.state for item in memory.content.manifest.entries}
        return tuple((entry.kind, states[entry.entry_id]) for entry in entries)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("statistics as_of must include a timezone")  # noqa: TRY003
    return value.astimezone(UTC)


def _resolve_period(period: StatisticsPeriod, end_date: date) -> ResolvedUsagePeriod:
    days = _PERIOD_DAYS[period]
    return ResolvedUsagePeriod(
        preset=period,
        start_date=end_date - timedelta(days=days - 1),
        end_date=end_date,
    )


def _candidate_inventory(rows: tuple[tuple[str, str, int], ...]) -> CandidateInventoryStatistics:
    by_family: dict[str, dict[str, int]] = defaultdict(lambda: {"pending": 0, "approved": 0, "rejected": 0})
    for family, status, total in rows:
        by_family[family][status] = total
    family_counts = tuple(
        CandidateFamilyCount(
            family=family,
            total=sum(statuses.values()),
            pending=statuses["pending"],
            approved=statuses["approved"],
            rejected=statuses["rejected"],
        )
        for family, statuses in sorted(by_family.items())
    )
    return CandidateInventoryStatistics(
        total=sum(item.total for item in family_counts),
        pending=sum(item.pending for item in family_counts),
        approved=sum(item.approved for item in family_counts),
        rejected=sum(item.rejected for item in family_counts),
        by_family=family_counts,
    )


def _memory_inventory(rows: tuple[tuple[str, str], ...]) -> MemoryEntryInventoryStatistics:
    by_kind: dict[str, dict[str, int]] = defaultdict(lambda: {"active": 0, "inactive": 0})
    for kind, state in rows:
        by_kind[kind][state] += 1
    kind_counts = tuple(
        MemoryKindCount(
            kind=kind,
            total=sum(states.values()),
            active=states["active"],
            inactive=states["inactive"],
        )
        for kind, states in sorted(by_kind.items(), key=lambda item: item[0].encode())
    )
    return MemoryEntryInventoryStatistics(
        total=sum(item.total for item in kind_counts),
        active=sum(item.active for item in kind_counts),
        inactive=sum(item.inactive for item in kind_counts),
        by_kind=kind_counts,
    )


def _usage_statistics(period: ResolvedUsagePeriod, rows: tuple[StoredModelUsage, ...]) -> UsageStatistics:
    daily = tuple(
        _usage_day(usage_date, tuple(row for row in rows if row.usage_date == usage_date))
        for usage_date in _dates(period.start_date, period.end_date)
    )
    return UsageStatistics(
        period=period,
        totals=ModelUsageStatistics(
            generation=_operation_total(rows, ModelUsageOperation.GENERATION),
            embedding=_operation_total(rows, ModelUsageOperation.EMBEDDING),
        ),
        by_purpose=_purpose_breakdown(rows),
        daily=daily,
    )


def _recall_statistics(
    period: ResolvedUsagePeriod,
    estimator: TokenEstimatorProfile | None,
    rows: tuple[StoredRecallTokenUsage, ...],
) -> RecallTokenStatistics:
    by_date = {row.usage_date: row for row in rows}
    daily = tuple(
        _recall_day(usage_date, by_date.get(usage_date)) for usage_date in _dates(period.start_date, period.end_date)
    )
    return RecallTokenStatistics(
        period=period,
        estimator=estimator,
        totals=_recall_total(daily),
        daily=daily,
    )


def _recall_day(usage_date: date, row: StoredRecallTokenUsage | None) -> RecallTokenDay:
    if row is None:
        return RecallTokenDay(
            date=usage_date,
            preparations=0,
            ready_preparations=0,
            comparable_preparations=0,
            baseline_tokens=0,
            recalled_tokens=0,
            token_reduction=0,
        )
    return RecallTokenDay(
        date=usage_date,
        preparations=row.preparations,
        ready_preparations=row.ready_preparations,
        comparable_preparations=row.comparable_preparations,
        baseline_tokens=row.baseline_tokens,
        recalled_tokens=row.recalled_tokens,
        token_reduction=row.baseline_tokens - row.recalled_tokens,
    )


def _recall_total(days: tuple[RecallTokenDay, ...]) -> RecallTokenValue:
    baseline_tokens = sum(day.baseline_tokens for day in days)
    recalled_tokens = sum(day.recalled_tokens for day in days)
    return RecallTokenValue(
        preparations=sum(day.preparations for day in days),
        ready_preparations=sum(day.ready_preparations for day in days),
        comparable_preparations=sum(day.comparable_preparations for day in days),
        baseline_tokens=baseline_tokens,
        recalled_tokens=recalled_tokens,
        token_reduction=baseline_tokens - recalled_tokens,
    )


def _usage_day(usage_date: date, rows: tuple[StoredModelUsage, ...]) -> ModelUsageDay:
    return ModelUsageDay(
        date=usage_date,
        generation=_operation_total(rows, ModelUsageOperation.GENERATION),
        embedding=_operation_total(rows, ModelUsageOperation.EMBEDDING),
        by_purpose=_purpose_breakdown(rows),
    )


def _purpose_breakdown(rows: tuple[StoredModelUsage, ...]) -> tuple[ModelUsagePurposeBreakdown, ...]:
    purposes = sorted({row.purpose for row in rows}, key=lambda purpose: purpose.value)
    return tuple(
        ModelUsagePurposeBreakdown(
            purpose=purpose,
            generation=_operation_total(
                tuple(row for row in rows if row.purpose == purpose),
                ModelUsageOperation.GENERATION,
            ),
            embedding=_operation_total(
                tuple(row for row in rows if row.purpose == purpose),
                ModelUsageOperation.EMBEDDING,
            ),
        )
        for purpose in purposes
    )


def _operation_total(
    rows: tuple[StoredModelUsage, ...],
    operation: ModelUsageOperation,
) -> ModelUsageValue:
    return _usage_total(tuple(_usage_value(row) for row in rows if row.operation == operation))


def _dates(start_date: date, end_date: date) -> tuple[date, ...]:
    return tuple(start_date + timedelta(days=offset) for offset in range((end_date - start_date).days + 1))


def _usage_value(row: StoredModelUsage | None) -> ModelUsageValue:
    if row is None:
        return ModelUsageValue(requests=0, input_tokens=0, output_tokens=0)
    return ModelUsageValue(
        requests=row.requests,
        input_tokens=row.input_tokens if row.input_complete else None,
        output_tokens=row.output_tokens if row.output_complete else None,
    )


def _usage_total(values: tuple[ModelUsageValue, ...]) -> ModelUsageValue:
    non_empty = tuple(value for value in values if value.requests > 0)
    return ModelUsageValue(
        requests=sum(value.requests for value in values),
        input_tokens=(
            None
            if any(value.input_tokens is None for value in non_empty)
            else sum(value.input_tokens or 0 for value in values)
        ),
        output_tokens=(
            None
            if any(value.output_tokens is None for value in non_empty)
            else sum(value.output_tokens or 0 for value in values)
        ),
    )


__all__ = ["RelationalScopedStatistics"]
