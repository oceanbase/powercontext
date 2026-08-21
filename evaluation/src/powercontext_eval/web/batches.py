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

"""Public batch contracts and derived lifecycle state."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext_eval.benchmarks.swebench_pro.catalog import TaskSet
from powercontext_eval.codex import DEFAULT_CODEX_MODEL, DEFAULT_REASONING_EFFORT, is_safe_codex_model
from powercontext_eval.models import PowerContextRef
from powercontext_eval.web.controls import BatchControlState
from powercontext_eval.web.estimation import BatchEstimate
from powercontext_eval.web.models import FailureCategory, FailureCode, TaskPhase, TaskStatus
from powercontext_eval.web.usage import UsageSnapshot


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class BatchCreate(_FrozenModel):
    powercontext_ref: str
    benchmark: Literal["swebench-pro"]
    task_set: TaskSet
    model: str = DEFAULT_CODEX_MODEL
    reasoning_effort: Literal["medium"] = DEFAULT_REASONING_EFFORT
    treatment_mode: Literal["off_on"]
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    usage_pause_percent: Annotated[int, Field(ge=1, le=100)] = 80
    initial_control_intent: Literal["run", "pause"] = "run"
    container_env: dict[str, str] = Field(default_factory=dict)

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


class BatchPreviewResponse(_FrozenModel):
    powercontext_ref: str
    benchmark: Literal["swebench-pro"]
    task_set: TaskSet
    model: str
    reasoning_effort: Literal["medium"]
    treatment_mode: Literal["off_on"]
    total_tasks: Annotated[int, Field(ge=1)]
    usage_pause_percent: Annotated[int, Field(ge=1, le=100)]
    usage: UsageSnapshot | None
    estimate: BatchEstimate
    can_start: bool
    block_reason: Literal["usage_threshold_reached"] | None = None


class TaskRetryRequest(_FrozenModel):
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")


class BatchStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PairCategory(StrEnum):
    OFF_FAIL_ON_PASS = "off_fail_on_pass"
    OFF_PASS_ON_FAIL = "off_pass_on_fail"
    BOTH_PASS = "both_pass"
    BOTH_FAIL = "both_fail"
    EXECUTION_FAILURE = "execution_failure"


class BatchControlEventType(StrEnum):
    BATCH_CREATED = "batch_created"
    THRESHOLD_CHANGED = "threshold_changed"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    RESUME_REQUESTED = "resume_requested"
    RESUMED = "resumed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    USAGE_THRESHOLD_REACHED = "usage_threshold_reached"
    USAGE_UNAVAILABLE = "usage_unavailable"
    QUOTA_LIMIT_REACHED = "quota_limit_reached"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    RESOURCE_PRESSURE = "resource_pressure"
    BATCH_COMPLETED = "batch_completed"
    TASK_RETRY_REQUESTED = "task_retry_requested"


class BatchControlEvent(_FrozenModel):
    sequence: Annotated[int, Field(ge=1)]
    batch_id: str
    event_type: BatchControlEventType
    actor: Literal["user", "system"]
    details: dict[str, int | str | None]
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("Control event timestamps must use UTC")
        return value


class BatchRuntimeFailure(_FrozenModel):
    category: FailureCategory
    code: FailureCode
    phase: TaskPhase | None = None
    summary: str = Field(min_length=1, max_length=500)
    finished_at: datetime

    @field_validator("finished_at")
    @classmethod
    def require_failure_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("Runtime failure timestamps must use UTC")
        return value


class BatchRuntimeTask(_FrozenModel):
    task_id: str
    attempt_id: str
    instance_id: str
    source_index: Annotated[int, Field(ge=0)]
    status: Literal[TaskStatus.QUEUED, TaskStatus.RUNNING]
    phase: TaskPhase | None = None
    attempt_number: Annotated[int, Field(ge=1)]
    attempt_count: Annotated[int, Field(ge=1)]
    created_at: datetime
    eligible_at: datetime
    started_at: datetime | None = None
    last_failure: BatchRuntimeFailure | None = None

    @field_validator("created_at", "eligible_at", "started_at")
    @classmethod
    def require_runtime_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)):
            raise ValueError("Runtime task timestamps must use UTC")
        return value


class BatchRuntimeResponse(_FrozenModel):
    batch_id: str
    generated_at: datetime
    status_counts: dict[TaskStatus, Annotated[int, Field(ge=0)]]
    tasks: tuple[BatchRuntimeTask, ...]

    @field_validator("generated_at")
    @classmethod
    def require_generated_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("Runtime response timestamps must use UTC")
        return value


class BatchRecord(_FrozenModel):
    batch_id: str
    request: BatchCreate
    total_tasks: Annotated[int, Field(ge=1)]
    status: BatchStatus
    control: BatchControlState
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    resolved_powercontext_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)):
            raise ValueError("Timestamps must use UTC")
        return value


class ResolutionAggregate(_FrozenModel):
    resolved: Annotated[int, Field(ge=0)]
    total: Annotated[int, Field(ge=0)]
    rate_percent: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class TokenMetricAggregate(_FrozenModel):
    off: Annotated[int, Field(ge=0)]
    on: Annotated[int, Field(ge=0)]
    delta: int
    off_measured_tasks: Annotated[int, Field(ge=0)]
    on_measured_tasks: Annotated[int, Field(ge=0)]


class TokenAggregate(_FrozenModel):
    input: TokenMetricAggregate
    output: TokenMetricAggregate
    total: TokenMetricAggregate


class BatchReportResponse(_FrozenModel):
    batch_id: str
    report_revision: Annotated[int, Field(ge=0)]
    total_tasks: Annotated[int, Field(ge=1)]
    terminal_tasks: Annotated[int, Field(ge=0)]
    comparable_pairs: Annotated[int, Field(ge=0)]
    execution_failures: Annotated[int, Field(ge=0)]
    cancelled_tasks: Annotated[int, Field(ge=0)]
    off: ResolutionAggregate
    on: ResolutionAggregate
    resolution_rate_delta_points: Annotated[float, Field(allow_inf_nan=False)]
    pair_categories: dict[PairCategory, Annotated[int, Field(ge=0)]]
    task_statuses: dict[TaskStatus, Annotated[int, Field(ge=0)]]
    tokens: TokenAggregate
    control: BatchControlState
    latest_usage: UsageSnapshot | None
    estimate: BatchEstimate
    revisions: dict[str, str]
    configuration: dict[str, str]


class TaskArmSummary(_FrozenModel):
    resolved: bool
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    total_tokens: Annotated[int, Field(ge=0)] | None = None


class TaskTokenDelta(_FrozenModel):
    off: Annotated[int, Field(ge=0)] | None = None
    on: Annotated[int, Field(ge=0)] | None = None
    delta: int | None = None


class BatchTaskItem(_FrozenModel):
    task_id: str
    attempt_id: str | None = None
    attempt_number: Annotated[int, Field(ge=1)] = 1
    attempt_count: Annotated[int, Field(ge=1)] = 1
    retryable: bool = False
    instance_id: str
    repository: str
    source_index: Annotated[int, Field(ge=0)]
    model: str
    reasoning_effort: Literal["medium"]
    status: TaskStatus
    pair_category: PairCategory | None = None
    off: TaskArmSummary | None = None
    on: TaskArmSummary | None = None
    tokens: TaskTokenDelta
    failure_category: str | None = None
    failure_summary: str | None = None


class BatchTaskPage(_FrozenModel):
    items: tuple[BatchTaskItem, ...]
    total: Annotated[int, Field(ge=0)]
    limit: Annotated[int, Field(ge=1)]
    offset: Annotated[int, Field(ge=0)]


class OfficialTestGroup(_FrozenModel):
    passed: Annotated[int, Field(ge=0)]
    total: Annotated[int, Field(ge=0)]
    failed: tuple[str, ...]


class TaskDetailArm(_FrozenModel):
    resolved: bool
    patch_applied: bool | None = None
    fail_to_pass: OfficialTestGroup
    pass_to_pass: OfficialTestGroup
    log_excerpt: str | None = Field(default=None, max_length=4_000)
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    total_tokens: Annotated[int, Field(ge=0)] | None = None


class RequiredTests(_FrozenModel):
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    selected_test_files_to_run: str
    test_patch: str


class TokensFlowFinalizationSummary(_FrozenModel):
    state: Literal["pending", "passed", "timed_out", "capacity_evicted", "cleanup_failed"]
    registered_at: datetime
    deadline_at: datetime
    finished_at: datetime | None = None
    attempts: Annotated[int, Field(ge=0)]
    queue_passed: bool
    doctor_rc: int | None = None
    error_category: str | None = None
    reason: str | None = None


class TokensFlowFinalizationPair(_FrozenModel):
    off: TokensFlowFinalizationSummary | None = None
    on: TokensFlowFinalizationSummary | None = None


class BatchTaskDetailResponse(_FrozenModel):
    task: BatchTaskItem
    problem_statement: str
    required_tests: RequiredTests
    off: TaskDetailArm | None = None
    on: TaskDetailArm | None = None
    tokensflow_finalization: TokensFlowFinalizationPair = TokensFlowFinalizationPair()


class ContextEventResponse(_FrozenModel):
    sequence: Annotated[int, Field(ge=1)]
    observed_at: datetime
    elapsed_ms: Annotated[int, Field(ge=0)]
    arm: Literal["off", "on"]
    actor: str
    event_type: str
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    source_artifact: str
    source_sequence: Annotated[int, Field(ge=0)]

    @field_validator("observed_at")
    @classmethod
    def context_timestamp_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("Context timestamps must use UTC")
        return value


class ContextEventPage(_FrozenModel):
    items: tuple[ContextEventResponse, ...]
    total: Annotated[int, Field(ge=0)]
    limit: Annotated[int, Field(ge=1)]
    offset: Annotated[int, Field(ge=0)]


def derive_batch_status(statuses: tuple[TaskStatus, ...]) -> BatchStatus:
    """Derive one batch state from its complete child-state vector."""

    if not statuses:
        raise ValueError("A batch must contain at least one task")
    if all(status is TaskStatus.CANCELLED for status in statuses):
        return BatchStatus.CANCELLED
    terminal = {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.INTERRUPTED,
        TaskStatus.CANCELLED,
    }
    if all(status in terminal for status in statuses):
        return BatchStatus.COMPLETED
    if all(status is TaskStatus.QUEUED for status in statuses):
        return BatchStatus.QUEUED
    return BatchStatus.RUNNING
