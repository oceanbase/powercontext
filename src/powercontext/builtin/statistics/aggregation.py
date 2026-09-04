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

"""Additive Statistics projection over a frozen Scope selection."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime

from powercontext.builtin.scope.models import ScopeSelection
from powercontext.builtin.statistics.models import (
    ArtifactInventoryStatistics,
    CandidateFamilyCount,
    CandidateInventoryStatistics,
    FamilyCount,
    InventoryStatistics,
    MemoryEntryInventoryStatistics,
    MemoryInventoryStatistics,
    MemoryKindCount,
    ModelUsageDay,
    ModelUsagePurpose,
    ModelUsagePurposeBreakdown,
    ModelUsageStatistics,
    ModelUsageValue,
    RecallTokenDay,
    RecallTokenStatistics,
    RecallTokenValue,
    ScopeStatistics,
    SourceInventoryStatistics,
    Statistics,
    UsageStatistics,
)


def aggregate_statistics(
    selection: ScopeSelection,
    scope_ids: tuple[str, ...],
    snapshots: tuple[Statistics, ...],
    as_of: datetime,
) -> Statistics:
    """Aggregate values that are already bounded to the same reporting period."""

    if len(scope_ids) != len(snapshots):
        raise ValueError("every selected Scope must have one Statistics snapshot")  # noqa: TRY003
    if not snapshots:
        raise ValueError("Statistics selection must resolve at least one Scope")  # noqa: TRY003
    period = snapshots[0].usage.period
    if any(snapshot.usage.period != period or snapshot.recall.period != period for snapshot in snapshots):
        raise ValueError("Statistics snapshots must use the same period")  # noqa: TRY003

    return Statistics(
        selection=selection,
        scope_ids=scope_ids,
        as_of=as_of,
        inventory=_inventory(snapshots),
        usage=_usage(snapshots),
        recall=_recall(snapshots),
        by_scope=tuple(
            ScopeStatistics(
                scope_id=scope_id,
                inventory=snapshot.inventory,
                usage=snapshot.usage,
                recall=snapshot.recall,
            )
            for scope_id, snapshot in zip(scope_ids, snapshots, strict=True)
        ),
    )


def _inventory(snapshots: tuple[Statistics, ...]) -> InventoryStatistics:
    family_counts: dict[str, int] = defaultdict(int)
    candidate_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    kind_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for snapshot in snapshots:
        for item in snapshot.inventory.artifacts.by_family:
            family_counts[item.family] += item.total
        for item in snapshot.inventory.candidates.by_family:
            values = candidate_counts[item.family]
            values[0] += item.pending
            values[1] += item.approved
            values[2] += item.rejected
        for item in snapshot.inventory.memory.entries.by_kind:
            values = kind_counts[item.kind]
            values[0] += item.active
            values[1] += item.inactive
    candidates = tuple(
        CandidateFamilyCount(
            family=family,
            total=sum(values),
            pending=values[0],
            approved=values[1],
            rejected=values[2],
        )
        for family, values in sorted(candidate_counts.items())
    )
    kinds = tuple(
        MemoryKindCount(kind=kind, total=sum(values), active=values[0], inactive=values[1])
        for kind, values in sorted(kind_counts.items())
    )
    return InventoryStatistics(
        sources=SourceInventoryStatistics(
            total=sum(snapshot.inventory.sources.total for snapshot in snapshots),
            memory_processed=sum(snapshot.inventory.sources.memory_processed for snapshot in snapshots),
            memory_pending=sum(snapshot.inventory.sources.memory_pending for snapshot in snapshots),
        ),
        artifacts=ArtifactInventoryStatistics(
            total=sum(family_counts.values()),
            by_family=tuple(FamilyCount(family=family, total=total) for family, total in sorted(family_counts.items())),
        ),
        candidates=CandidateInventoryStatistics(
            total=sum(item.total for item in candidates),
            pending=sum(item.pending for item in candidates),
            approved=sum(item.approved for item in candidates),
            rejected=sum(item.rejected for item in candidates),
            by_family=candidates,
        ),
        memory=MemoryInventoryStatistics(
            entries=MemoryEntryInventoryStatistics(
                total=sum(item.total for item in kinds),
                active=sum(item.active for item in kinds),
                inactive=sum(item.inactive for item in kinds),
                by_kind=kinds,
            )
        ),
    )


def _usage(snapshots: tuple[Statistics, ...]) -> UsageStatistics:
    period = snapshots[0].usage.period
    purposes = tuple(
        purpose
        for purpose in ModelUsagePurpose
        if any(any(item.purpose is purpose for item in snapshot.usage.by_purpose) for snapshot in snapshots)
    )
    return UsageStatistics(
        period=period,
        totals=_model_usage(snapshot.usage.totals for snapshot in snapshots),
        by_purpose=tuple(
            ModelUsagePurposeBreakdown(
                purpose=purpose,
                generation=_usage_value(
                    _purpose(snapshot.usage.by_purpose, purpose).generation for snapshot in snapshots
                ),
                embedding=_usage_value(
                    _purpose(snapshot.usage.by_purpose, purpose).embedding for snapshot in snapshots
                ),
            )
            for purpose in purposes
        ),
        daily=tuple(
            ModelUsageDay(
                date=day.date,
                generation=_usage_value(snapshot.usage.daily[index].generation for snapshot in snapshots),
                embedding=_usage_value(snapshot.usage.daily[index].embedding for snapshot in snapshots),
                by_purpose=tuple(
                    ModelUsagePurposeBreakdown(
                        purpose=purpose,
                        generation=_usage_value(
                            _purpose(snapshot.usage.daily[index].by_purpose, purpose).generation
                            for snapshot in snapshots
                        ),
                        embedding=_usage_value(
                            _purpose(snapshot.usage.daily[index].by_purpose, purpose).embedding
                            for snapshot in snapshots
                        ),
                    )
                    for purpose in ModelUsagePurpose
                    if any(
                        any(item.purpose is purpose for item in snapshot.usage.daily[index].by_purpose)
                        for snapshot in snapshots
                    )
                ),
            )
            for index, day in enumerate(snapshots[0].usage.daily)
        ),
    )


def _recall(snapshots: tuple[Statistics, ...]) -> RecallTokenStatistics:
    first = snapshots[0].recall
    if any(snapshot.recall.estimator != first.estimator for snapshot in snapshots):
        raise ValueError("Statistics snapshots must use the same recall estimator")  # noqa: TRY003
    return RecallTokenStatistics(
        period=first.period,
        estimator=first.estimator,
        totals=_recall_value(snapshot.recall.totals for snapshot in snapshots),
        daily=tuple(
            RecallTokenDay(
                date=day.date,
                **_recall_value(snapshot.recall.daily[index] for snapshot in snapshots).model_dump(),
            )
            for index, day in enumerate(first.daily)
        ),
    )


def _model_usage(values: Iterable[ModelUsageStatistics]) -> ModelUsageStatistics:
    items = tuple(values)
    return ModelUsageStatistics(
        generation=_usage_value(item.generation for item in items),
        embedding=_usage_value(item.embedding for item in items),
    )


def _usage_value(values: Iterable[ModelUsageValue]) -> ModelUsageValue:
    items = tuple(values)
    return ModelUsageValue(
        requests=sum(item.requests for item in items),
        input_tokens=_optional_sum(items, lambda item: item.input_tokens),
        output_tokens=_optional_sum(items, lambda item: item.output_tokens),
    )


def _optional_sum(values: tuple[ModelUsageValue, ...], getter: Callable[[ModelUsageValue], int | None]) -> int | None:
    selected = tuple(getter(value) for value in values)
    return None if any(value is None for value in selected) else sum(value for value in selected if value is not None)


def _purpose(
    values: tuple[ModelUsagePurposeBreakdown, ...],
    purpose: ModelUsagePurpose,
) -> ModelUsagePurposeBreakdown:
    return next(
        (value for value in values if value.purpose is purpose),
        ModelUsagePurposeBreakdown(
            purpose=purpose,
            generation=ModelUsageValue(requests=0, input_tokens=0, output_tokens=0),
            embedding=ModelUsageValue(requests=0, input_tokens=0, output_tokens=0),
        ),
    )


def _recall_value(values: Iterable[RecallTokenValue]) -> RecallTokenValue:
    items = tuple(values)
    baseline = sum(item.baseline_tokens for item in items)
    recalled = sum(item.recalled_tokens for item in items)
    return RecallTokenValue(
        preparations=sum(item.preparations for item in items),
        ready_preparations=sum(item.ready_preparations for item in items),
        comparable_preparations=sum(item.comparable_preparations for item in items),
        baseline_tokens=baseline,
        recalled_tokens=recalled,
        token_reduction=baseline - recalled,
    )


__all__ = ["aggregate_statistics"]
