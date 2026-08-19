"""Public batch execution controls and lifecycle derivation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext_eval.benchmarks.swebench_pro.catalog import PUBLIC_V2_TASK_SET, TaskSet
from powercontext_eval.codex import DEFAULT_CODEX_MODEL, is_safe_codex_model
from powercontext_eval.models import PowerContextRef
from powercontext_eval.web.models import TaskStatus

if TYPE_CHECKING:
    from powercontext_eval.web.batches import BatchStatus


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class BatchControlIntent(StrEnum):
    """The operator's durable intent for a batch."""

    RUN = "run"
    PAUSE = "pause"
    CANCEL = "cancel"


class BatchPauseReason(StrEnum):
    """Stable public reasons why a batch stopped starting new tasks."""

    USER = "user"
    USAGE_THRESHOLD = "usage_threshold"
    USAGE_UNAVAILABLE = "usage_unavailable"
    QUOTA_LIMIT = "quota_limit"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    CODEX_CAPACITY = "codex_capacity"
    RESOURCE_PRESSURE = "resource_pressure"


class BatchPreviewRequest(_FrozenModel):
    """Inputs required to preview one pinned task set before confirmation."""

    powercontext_ref: str
    task_set: TaskSet = PUBLIC_V2_TASK_SET
    model: str = DEFAULT_CODEX_MODEL
    usage_pause_percent: Annotated[int, Field(ge=1, le=100)] = 80

    @field_validator("powercontext_ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        PowerContextRef.parse(value)
        if value != "latest" and not value.startswith("commit:"):
            raise ValueError("Web evaluations accept only latest or an exact commit")
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if not is_safe_codex_model(value):
            raise ValueError("Codex model is unsafe")
        return value


class BatchControlPatch(_FrozenModel):
    """Optimistically update a batch's current subscription threshold."""

    usage_pause_percent: Annotated[int, Field(ge=1, le=100)]
    expected_version: Annotated[int, Field(ge=0)]


class BatchControlState(_FrozenModel):
    """Durable operator intent and current threshold for one batch."""

    intent: BatchControlIntent
    usage_pause_percent: Annotated[int, Field(ge=1, le=100)]
    pause_reason: BatchPauseReason | None = None
    updated_at: datetime
    version: Annotated[int, Field(ge=0)]

    @field_validator("updated_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("Control timestamps must use UTC")
        return value


def derive_controlled_batch_status(
    *,
    intent: BatchControlIntent,
    task_statuses: tuple[TaskStatus, ...],
) -> BatchStatus:
    """Derive the visible batch lifecycle from operator intent and child tasks."""

    from powercontext_eval.web.batches import BatchStatus

    if not task_statuses:
        raise ValueError("A batch must contain at least one task")

    if intent is BatchControlIntent.CANCEL:
        if any(status is TaskStatus.RUNNING for status in task_statuses):
            return BatchStatus.CANCELLING
        if all(status is not TaskStatus.QUEUED for status in task_statuses):
            return BatchStatus.CANCELLED
        return BatchStatus.CANCELLING

    terminal = {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.INTERRUPTED,
        TaskStatus.CANCELLED,
    }
    if all(status in terminal for status in task_statuses):
        if all(status is TaskStatus.CANCELLED for status in task_statuses):
            return BatchStatus.CANCELLED
        return BatchStatus.COMPLETED

    if intent is BatchControlIntent.PAUSE:
        if any(status is TaskStatus.RUNNING for status in task_statuses):
            return BatchStatus.PAUSING
        return BatchStatus.PAUSED

    if all(status is TaskStatus.QUEUED for status in task_statuses):
        return BatchStatus.QUEUED
    return BatchStatus.RUNNING
