"""Scoped product statistics values."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from powercontext.builtin.inference import TokenEstimatorProfile


class StatisticsPeriod(StrEnum):
    """Bounded UTC periods supported by the product statistics contract."""

    TODAY = "today"
    SEVEN_DAYS = "7d"
    THIRTY_DAYS = "30d"


class ModelUsageOperation(StrEnum):
    """Stable model-operation categories exposed by statistics."""

    GENERATION = "generation"
    EMBEDDING = "embedding"


class ModelUsagePurpose(StrEnum):
    """Business purpose responsible for one model capability call."""

    MEMORY_EXTRACTION = "memory_extraction"
    MEMORY_INDEXING = "memory_indexing"
    MEMORY_RECALL = "memory_recall"
    EXPERIENCE_GENERATION = "experience_generation"
    SKILL_GENERATION = "skill_generation"
    HANDOFF_GENERATION = "handoff_generation"


class FamilyCount(BaseModel):
    """Current Artifact count for one open family value."""

    family: str
    total: int = Field(ge=0)


class CandidateFamilyCount(BaseModel):
    """Current Candidate status counts for one family."""

    family: str
    total: int = Field(ge=0)
    pending: int = Field(ge=0)
    approved: int = Field(ge=0)
    rejected: int = Field(ge=0)


class MemoryKindCount(BaseModel):
    """Current Memory entry state counts for one open kind value."""

    kind: str
    total: int = Field(ge=0)
    active: int = Field(ge=0)
    inactive: int = Field(ge=0)


class SourceInventoryStatistics(BaseModel):
    """Current Source journal and Memory cursor positions."""

    total: int = Field(ge=0)
    memory_processed: int = Field(ge=0)
    memory_pending: int = Field(ge=0)


class ArtifactInventoryStatistics(BaseModel):
    """Current Artifact heads grouped by family."""

    total: int = Field(ge=0)
    by_family: tuple[FamilyCount, ...] = ()


class CandidateInventoryStatistics(BaseModel):
    """Current Candidate heads grouped by status and family."""

    total: int = Field(ge=0)
    pending: int = Field(ge=0)
    approved: int = Field(ge=0)
    rejected: int = Field(ge=0)
    by_family: tuple[CandidateFamilyCount, ...] = ()


class MemoryEntryInventoryStatistics(BaseModel):
    """Current logical Memory entries grouped by state and kind."""

    total: int = Field(ge=0)
    active: int = Field(ge=0)
    inactive: int = Field(ge=0)
    by_kind: tuple[MemoryKindCount, ...] = ()


class MemoryInventoryStatistics(BaseModel):
    """Current Memory-specific inventory."""

    entries: MemoryEntryInventoryStatistics


class InventoryStatistics(BaseModel):
    """Current product content inventory for one scope."""

    sources: SourceInventoryStatistics
    artifacts: ArtifactInventoryStatistics
    candidates: CandidateInventoryStatistics
    memory: MemoryInventoryStatistics


class ModelUsageValue(BaseModel):
    """Provider-reported usage for one model-operation category."""

    requests: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class ModelUsageStatistics(BaseModel):
    """Generation and embedding usage for one interval."""

    generation: ModelUsageValue
    embedding: ModelUsageValue


class ModelUsagePurposeBreakdown(BaseModel):
    """Generation and embedding usage attributed to one business purpose."""

    purpose: ModelUsagePurpose
    generation: ModelUsageValue
    embedding: ModelUsageValue


class ResolvedUsagePeriod(BaseModel):
    """Inclusive UTC dates selected by a period preset."""

    preset: StatisticsPeriod
    start_date: date
    end_date: date
    timezone: Literal["UTC"] = "UTC"


class ModelUsageDay(BaseModel):
    """One zero-filled UTC model-usage bucket."""

    date: date
    generation: ModelUsageValue
    embedding: ModelUsageValue
    by_purpose: tuple[ModelUsagePurposeBreakdown, ...]


class UsageStatistics(BaseModel):
    """Resolved period, totals, and daily model usage."""

    period: ResolvedUsagePeriod
    totals: ModelUsageStatistics
    by_purpose: tuple[ModelUsagePurposeBreakdown, ...]
    daily: tuple[ModelUsageDay, ...]


class RecallTokenMeasurement(BaseModel):
    """One successful context preparation measured with one estimator profile."""

    estimator: TokenEstimatorProfile
    ready: bool
    comparable: bool
    baseline_tokens: int = Field(ge=0)
    recalled_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_comparability(self) -> RecallTokenMeasurement:
        if self.comparable and not self.ready:
            raise ValueError("only ready context can be comparable")  # noqa: TRY003
        if not self.comparable and (self.baseline_tokens or self.recalled_tokens):
            raise ValueError("non-comparable context cannot contribute token estimates")  # noqa: TRY003
        return self


class RecallTokenValue(BaseModel):
    """Additive recall-token values for one interval."""

    preparations: int = Field(ge=0)
    ready_preparations: int = Field(ge=0)
    comparable_preparations: int = Field(ge=0)
    baseline_tokens: int = Field(ge=0)
    recalled_tokens: int = Field(ge=0)
    token_reduction: int

    @model_validator(mode="after")
    def validate_preparation_counts(self) -> RecallTokenValue:
        if self.comparable_preparations > self.ready_preparations:
            raise ValueError("comparable preparations cannot exceed ready preparations")  # noqa: TRY003
        if self.ready_preparations > self.preparations:
            raise ValueError("ready preparations cannot exceed total preparations")  # noqa: TRY003
        return self


class RecallTokenDay(RecallTokenValue):
    """One zero-filled UTC recall-token bucket."""

    date: date


class RecallTokenStatistics(BaseModel):
    """Estimated recall token reduction under one deployment-fixed profile."""

    period: ResolvedUsagePeriod
    estimator: TokenEstimatorProfile | None
    totals: RecallTokenValue
    daily: tuple[RecallTokenDay, ...]


class Statistics(BaseModel):
    """Current inventory and bounded model usage for one scope."""

    scope_id: str
    as_of: datetime
    inventory: InventoryStatistics
    usage: UsageStatistics
    recall: RecallTokenStatistics


__all__ = [
    "ArtifactInventoryStatistics",
    "CandidateFamilyCount",
    "CandidateInventoryStatistics",
    "FamilyCount",
    "InventoryStatistics",
    "MemoryEntryInventoryStatistics",
    "MemoryInventoryStatistics",
    "MemoryKindCount",
    "ModelUsageDay",
    "ModelUsageOperation",
    "ModelUsagePurpose",
    "ModelUsagePurposeBreakdown",
    "ModelUsageStatistics",
    "ModelUsageValue",
    "RecallTokenDay",
    "RecallTokenMeasurement",
    "RecallTokenStatistics",
    "RecallTokenValue",
    "ResolvedUsagePeriod",
    "SourceInventoryStatistics",
    "Statistics",
    "StatisticsPeriod",
    "UsageStatistics",
]
