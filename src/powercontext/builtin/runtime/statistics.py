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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience.models import ExperienceContent
from powercontext.builtin.artifacts.experience.recurrence import (
    MAX_RECURRENCE_HANDOFF_SCAN,
    RECURRENCE_REVIEW_STREAK_THRESHOLD,
    RecurrenceObservation,
    signature_key,
    terminal_streak,
)
from powercontext.builtin.artifacts.handoff import Handoff
from powercontext.builtin.artifacts.handoff.models import HandoffContent
from powercontext.builtin.artifacts.memory import MemoryService
from powercontext.builtin.inference import InferenceUsage, TokenEstimatorProfile
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.cursors import SourceCursorRepository, StoredSourceCursor
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.recurrence import RecurrenceRepository
from powercontext.builtin.persistence.statistics import (
    StatisticsRepository,
    StoredInventoryCounts,
    StoredModelUsage,
    StoredRecallTokenUsage,
)
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE
from powercontext.builtin.runtime.recurrence import handoff_experience_citations
from powercontext.builtin.scope import ScopeSelection
from powercontext.builtin.statistics import (
    MAX_RECURRENCE_TOP_REVISIONS,
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
    RecurrenceStatistics,
    RecurrenceStreak,
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


@dataclass(frozen=True, slots=True)
class _ScopeReads:
    """One Scope's raw statistics rows, before projection into the contract."""

    inventory: StoredInventoryCounts
    processed_sources: int
    memory_entries: tuple[tuple[str, str], ...]
    usage: tuple[StoredModelUsage, ...]
    recall: tuple[StoredRecallTokenUsage, ...]
    observations: tuple[RecurrenceObservation, ...]
    unlinked_handoff_citations: int


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
        recurrence: RecurrenceRepository,
        artifacts: ArtifactRepository,
        token_estimator: TokenEstimatorProfile | None,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._memory_artifact_id = memory_artifact_id
        self._memory_service = memory_service
        self._cursors = cursors
        self._repository = repository
        self._recurrence = recurrence
        self._artifacts = artifacts
        self._token_estimator = token_estimator

    async def overview(self, period: StatisticsPeriod, as_of: datetime, /) -> Statistics:
        captured_at = _as_utc(as_of)
        resolved_period = _resolve_period(period, captured_at.date())
        async with self._database.transaction() as connection:
            reads = await self._read(connection, resolved_period)
        return self._assemble(reads, resolved_period, captured_at)

    async def _read(self, connection: AsyncConnection, period: ResolvedUsagePeriod, /) -> _ScopeReads:
        """Read one Scope's raw statistics rows on an already-open connection."""

        cursor = await self._cursors.load(connection, self._scope_id, SOURCE_WINDOW_TRIGGER_NAME)
        return _ScopeReads(
            inventory=await self._repository.inventory(connection, self._scope_id),
            processed_sources=_processed_sources(cursor),
            memory_entries=await self._memory_entries(connection),
            usage=await self._repository.usage(connection, self._scope_id, period.start_date, period.end_date),
            recall=(
                ()
                if self._token_estimator is None
                else await self._repository.recall_usage(
                    connection,
                    self._scope_id,
                    period.start_date,
                    period.end_date,
                    estimator_id=self._token_estimator.estimator_id,
                    estimator_version=self._token_estimator.version,
                )
            ),
            observations=await self._recurrence.observations(connection, self._scope_id),
            unlinked_handoff_citations=await self._recurrence.unlinked_handoff_citations(
                connection,
                self._scope_id,
                await self._cited_keys(connection),
            ),
        )

    def _assemble(self, reads: _ScopeReads, period: ResolvedUsagePeriod, captured_at: datetime, /) -> Statistics:
        """Project one Scope's raw rows into the statistics contract."""

        artifacts = tuple(FamilyCount(family=family, total=total) for family, total in reads.inventory.artifacts)
        candidates = _candidate_inventory(reads.inventory.candidates)
        inventory = InventoryStatistics(
            sources=SourceInventoryStatistics(
                total=reads.inventory.sources,
                memory_processed=reads.processed_sources,
                memory_pending=max(reads.inventory.sources - reads.processed_sources, 0),
            ),
            artifacts=ArtifactInventoryStatistics(
                total=sum(item.total for item in artifacts),
                by_family=artifacts,
            ),
            candidates=candidates,
            memory=MemoryInventoryStatistics(entries=_memory_inventory(reads.memory_entries)),
        )
        usage = _usage_statistics(period, reads.usage)
        recall = _recall_statistics(period, self._token_estimator, reads.recall)
        recurrence = _recurrence_statistics(
            reads.observations,
            unlinked_handoff_citations=reads.unlinked_handoff_citations,
        )
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
                    recurrence=recurrence,
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

    async def _cited_keys(self, connection: AsyncConnection) -> tuple[tuple[str, str, int, str], ...]:
        """Return the Experience signature keys this scope's Handoffs currently cite.

        The scan is bounded and read-only: it only reports the provenance coverage
        gap between citations the ledger can see and Observations it could attach.
        """

        rows = (
            await connection.execute(
                select(
                    ARTIFACT_HEADS_TABLE.c.artifact_id,
                    ARTIFACT_HEADS_TABLE.c.revision,
                )
                .where(
                    ARTIFACT_HEADS_TABLE.c.scope_id == self._scope_id,
                    ARTIFACT_HEADS_TABLE.c.family == Handoff.family,
                    ARTIFACT_HEADS_TABLE.c.lifecycle_state == "active",
                )
                .order_by(ARTIFACT_HEADS_TABLE.c.artifact_id, ARTIFACT_HEADS_TABLE.c.revision)
                .limit(MAX_RECURRENCE_HANDOFF_SCAN)
            )
        ).all()
        if not rows:
            return ()
        handoffs = await self._artifacts.get_many(
            connection,
            self._scope_id,
            tuple(ArtifactRef(family=Handoff.family, artifact_id=str(row[0]), revision=int(row[1])) for row in rows),
        )
        cited: list[ArtifactRef] = []
        for handoff in handoffs:
            if isinstance(handoff.content, HandoffContent):
                cited.extend(handoff_experience_citations(handoff.content))
        if not cited:
            return ()
        experiences = []
        for ref in cited:
            try:
                experiences.append(await self._artifacts.get(connection, self._scope_id, ref))
            except RepositoryNotFoundError:
                continue
        keys: set[tuple[str, str, int, str]] = set()
        for experience in experiences:
            content = experience.content
            if not isinstance(content, ExperienceContent) or content.failure is None:
                continue
            ref = experience.as_ref()
            keys.add((
                ref.family,
                ref.artifact_id,
                ref.revision,
                signature_key(content.failure.signature.recall_cue),
            ))
        return tuple(sorted(keys))

    async def _memory_entries(self, connection: AsyncConnection) -> tuple[tuple[str, str], ...]:
        service = self._memory_service(connection)
        try:
            memory, entries = await service.head_entries(self._memory_artifact_id)
        except ArtifactNotFoundError:
            return ()
        states = {item.entry_id: item.state for item in memory.content.manifest.entries}
        return tuple((entry.kind, states[entry.entry_id]) for entry in entries)


def _recurrence_statistics(
    observations: tuple[RecurrenceObservation, ...],
    /,
    *,
    unlinked_handoff_citations: int,
) -> RecurrenceStatistics:
    """Derive every recurrence reading from positive evidence already in the ledger.

    ``unknown`` is reported alongside the counters but is never a ledger row: it
    counts linked selections whose Task Outcome never received a terminal verdict.
    """

    selected = tuple(item for item in observations if item.event == "selected")
    recurred = tuple(item for item in observations if item.event == "recurred")
    avoided = tuple(item for item in observations if item.event == "avoided")
    verdicted = {
        (
            verdict.artifact_ref.family,
            verdict.artifact_ref.artifact_id,
            verdict.artifact_ref.revision,
            verdict.signature_key,
            verdict.task_outcome_ref.source_type,
            verdict.task_outcome_ref.source_id,
        )
        for verdict in (*recurred, *avoided)
    }
    unknown = sum(
        1
        for item in selected
        if (
            item.artifact_ref.family,
            item.artifact_ref.artifact_id,
            item.artifact_ref.revision,
            item.signature_key,
            item.task_outcome_ref.source_type,
            item.task_outcome_ref.source_id,
        )
        not in verdicted
    )
    return RecurrenceStatistics(
        selected=len(selected),
        recurred=len(recurred),
        avoided=len(avoided),
        unknown=unknown,
        unlinked_handoff_citations=unlinked_handoff_citations,
        needing_review=_needing_review(observations),
        top_revisions=_top_revisions(observations),
    )


def _streaks(observations: tuple[RecurrenceObservation, ...], /) -> tuple[RecurrenceStreak, ...]:
    groups: dict[tuple[str, str, int, str], list[RecurrenceObservation]] = defaultdict(list)
    for item in observations:
        if item.event == "selected":
            continue
        groups[
            (
                item.artifact_ref.family,
                item.artifact_ref.artifact_id,
                item.artifact_ref.revision,
                item.signature_key,
            )
        ].append(item)
    return tuple(
        RecurrenceStreak(
            artifact_ref=ArtifactRef(family=family, artifact_id=artifact_id, revision=revision),
            signature_key=key,
            terminal_recurred_streak=terminal_streak(tuple(verdicts)),
        )
        for (family, artifact_id, revision, key), verdicts in sorted(groups.items())
    )


def _needing_review(observations: tuple[RecurrenceObservation, ...], /) -> int:
    return sum(
        1 for streak in _streaks(observations) if streak.terminal_recurred_streak >= RECURRENCE_REVIEW_STREAK_THRESHOLD
    )


def _top_revisions(observations: tuple[RecurrenceObservation, ...], /) -> tuple[RecurrenceStreak, ...]:
    """Order by streak descending, then identity ascending, and only then truncate."""

    ordered = sorted(
        _streaks(observations),
        key=lambda streak: (
            -streak.terminal_recurred_streak,
            streak.artifact_ref.family,
            streak.artifact_ref.artifact_id,
            streak.artifact_ref.revision,
            streak.signature_key,
        ),
    )
    return tuple(ordered[:MAX_RECURRENCE_TOP_REVISIONS])


async def overview_selection(
    services: Sequence[RelationalScopedStatistics],
    period: StatisticsPeriod,
    as_of: datetime,
    /,
) -> tuple[Statistics, ...]:
    """Read a whole Scope selection in one transaction, one query per table instead of per Scope.

    The per-Scope reads answer the same question for every Scope, so issuing them once per
    Scope makes a selection cost ``scopes x queries`` round trips and one transaction each.
    Reading them together keeps the cost of a selection close to the cost of one Scope, and
    the shared transaction gives every Scope the same snapshot. Services must belong to one
    Runtime, which is what ``StatisticsApplication`` hands over.
    """

    if not services:
        return ()
    captured_at = _as_utc(as_of)
    resolved_period = _resolve_period(period, captured_at.date())
    shared = services[0]
    scope_ids = tuple(service._scope_id for service in services)
    async with shared._database.transaction() as connection:
        inventories = await shared._repository.inventory_many(connection, scope_ids)
        cursors = await shared._cursors.load_many(connection, scope_ids, SOURCE_WINDOW_TRIGGER_NAME)
        usage = await shared._repository.usage_many(
            connection,
            scope_ids,
            resolved_period.start_date,
            resolved_period.end_date,
        )
        recall = await _recall_by_scope(connection, shared._repository, services, resolved_period)
        reads = [
            _ScopeReads(
                inventory=inventories[service._scope_id],
                processed_sources=_processed_sources(cursors.get(service._scope_id)),
                memory_entries=await service._memory_entries(connection),
                usage=usage[service._scope_id],
                recall=recall.get(service._scope_id, ()),
                observations=await service._recurrence.observations(connection, service._scope_id),
                unlinked_handoff_citations=await service._recurrence.unlinked_handoff_citations(
                    connection,
                    service._scope_id,
                    await service._cited_keys(connection),
                ),
            )
            for service in services
        ]
    return tuple(
        service._assemble(scope_reads, resolved_period, captured_at)
        for service, scope_reads in zip(services, reads, strict=True)
    )


async def _recall_by_scope(
    connection: AsyncConnection,
    repository: StatisticsRepository,
    services: Sequence[RelationalScopedStatistics],
    period: ResolvedUsagePeriod,
    /,
) -> dict[str, tuple[StoredRecallTokenUsage, ...]]:
    """Read recall aggregates once per estimator profile present in the selection."""

    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for service in services:
        estimator = service._token_estimator
        if estimator is not None:
            grouped[estimator.estimator_id, estimator.version].append(service._scope_id)
    recall: dict[str, tuple[StoredRecallTokenUsage, ...]] = {}
    for (estimator_id, estimator_version), scope_ids in grouped.items():
        recall.update(
            await repository.recall_usage_many(
                connection,
                tuple(scope_ids),
                period.start_date,
                period.end_date,
                estimator_id=estimator_id,
                estimator_version=estimator_version,
            )
        )
    return recall


def _processed_sources(cursor: StoredSourceCursor | None, /) -> int:
    return 0 if cursor is None else cursor.cursor.sequence


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


__all__ = ["RelationalScopedStatistics", "overview_selection"]
