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

"""Strict public domain models for the evaluation console."""

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from powercontext_eval.artifacts import ArmState
from powercontext_eval.codex import DEFAULT_CODEX_MODEL, DEFAULT_REASONING_EFFORT, is_safe_codex_model
from powercontext_eval.models import Arm, PowerContextRef, TreatmentMode
from powercontext_eval.report import GoldValidationAudit
from powercontext_eval.runner import INSTANCE_ID


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FrozenModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class TaskPhase(StrEnum):
    PREPARING = "preparing"
    VALIDATING_GOLD = "validating_gold"
    RUNNING_OFF = "running_off"
    RUNNING_ON = "running_on"
    OFFICIAL_EVALUATION = "official_evaluation"
    GENERATING_REPORT = "generating_report"


class FailureCategory(StrEnum):
    INVALID_REQUEST = "invalid_request"
    QUEUE_UNAVAILABLE = "queue_unavailable"
    SOURCE_RESOLUTION = "source_resolution_failure"
    ENVIRONMENT_PREPARATION = "environment_preparation_failure"
    GOLD_VALIDATION = "gold_validation_failure"
    CODEX_EXECUTION = "codex_execution_failure"
    CODEX_CAPACITY = "codex_capacity_failure"
    TREATMENT_VALIDATION = "treatment_validation_failure"
    OFFICIAL_EVALUATOR = "official_evaluator_failure"
    REPORT_GENERATION = "report_generation_failure"
    WORKER_INTERRUPTION = "worker_interruption"
    INTERNAL = "internal"


class RetryDisposition(StrEnum):
    """Internal scheduling decision retained without exposing raw failure details."""

    RETRY = "retry"
    TERMINAL = "terminal"


class FailureCode(StrEnum):
    """Stable internal cause used for retry decisions and sanitized incident correlation."""

    DATASET_SCHEMA = "dataset_schema"
    CATALOG = "catalog"
    UNSAFE_SUT_CONFIGURATION = "unsafe_sut_configuration"
    UNSAFE_CODEX_INVOCATION = "unsafe_codex_invocation"
    INVALID_TREATMENT_CONTRACT = "invalid_treatment_contract"
    ARTIFACTS_ALREADY_EXIST = "artifacts_already_exist"
    SOURCE_RESOLUTION = "source_resolution"
    GOLD_VALIDATION = "gold_validation"
    CODEX_EXECUTION = "codex_execution"
    CODEX_CAPACITY = "codex_capacity"
    READINESS = "readiness"
    PLUGIN_INSPECTION = "plugin_inspection"
    OFFICIAL_EVALUATOR = "official_evaluator"
    REPORT_GENERATION = "report_generation"
    WORKER_INTERRUPTION = "worker_interruption"
    INTERNAL = "internal"


RETRYABLE_FAILURES = frozenset(
    {
        FailureCategory.SOURCE_RESOLUTION,
        FailureCategory.ENVIRONMENT_PREPARATION,
        FailureCategory.GOLD_VALIDATION,
        FailureCategory.CODEX_EXECUTION,
        FailureCategory.CODEX_CAPACITY,
        FailureCategory.TREATMENT_VALIDATION,
        FailureCategory.OFFICIAL_EVALUATOR,
        FailureCategory.REPORT_GENERATION,
        FailureCategory.WORKER_INTERRUPTION,
        FailureCategory.INTERNAL,
    }
)


class TaskCreate(FrozenModel):
    powercontext_ref: str
    benchmark: Literal["swebench-pro"]
    instance_id: str = Field(min_length=1, max_length=300, pattern=r"^[A-Za-z0-9._-]+$")
    model: str = DEFAULT_CODEX_MODEL
    reasoning_effort: Literal["medium"] = DEFAULT_REASONING_EFFORT
    treatment_mode: TreatmentMode
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    container_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("treatment_mode", mode="before")
    @classmethod
    def parse_treatment_mode(cls, value: object) -> object:
        return TreatmentMode(value) if isinstance(value, str) else value

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


def _require_utc(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)):
        raise ValueError("Timestamps must use UTC")
    return value


class SafeFailure(FrozenModel):
    category: FailureCategory
    failure_code: FailureCode = FailureCode.INTERNAL
    phase: TaskPhase | None = None
    summary: str = Field(min_length=1, max_length=500)
    retry_disposition: RetryDisposition = RetryDisposition.RETRY


class TaskResult(FrozenModel):
    artifact_dir: str
    report_path: str
    off_resolved: bool | None = None
    on_resolved: bool | None = None

    def resolved_for(self, arm: Arm) -> bool | None:
        return self.off_resolved if arm is Arm.OFF else self.on_resolved


class TaskRecord(FrozenModel):
    task_id: str
    attempt_id: str | None = None
    attempt_number: Annotated[int, Field(ge=1)] = 1
    attempt_count: Annotated[int, Field(ge=1)] = 1
    retryable: bool = False
    request: TaskCreate
    status: TaskStatus
    batch_id: str | None = None
    instance_id: str | None = None
    source_index: Annotated[int, Field(ge=0)] | None = None
    phase: TaskPhase | None = None
    created_at: datetime
    eligible_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    version: Annotated[int, Field(ge=0)] = 0
    failure_category: FailureCategory | None = None
    failure_code: FailureCode | None = None
    retry_disposition: RetryDisposition | None = None
    failure_phase: TaskPhase | None = None
    failure_summary: str | None = Field(default=None, max_length=500)
    result: TaskResult | None = None

    _utc_timestamps = field_validator("created_at", "eligible_at", "started_at", "finished_at")(_require_utc)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.attempt_count < self.attempt_number:
            raise ValueError("Attempt count cannot be smaller than the current attempt number")
        if self.eligible_at < self.created_at:
            raise ValueError("Task eligibility cannot precede creation")
        has_category = self.failure_category is not None
        has_code = self.failure_code is not None
        has_disposition = self.retry_disposition is not None
        has_summary = self.failure_summary is not None
        has_failure = has_category and has_code and has_disposition and has_summary
        has_partial_failure = len({has_category, has_code, has_disposition, has_summary}) != 1 or (
            self.failure_phase is not None and not has_failure
        )
        if has_partial_failure:
            raise ValueError("Failure category, code, disposition, and summary must be provided together")

        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("Task start time cannot precede creation")
        if self.finished_at is not None:
            previous = self.started_at if self.started_at is not None else self.created_at
            if self.finished_at < previous:
                raise ValueError("Task finish time cannot precede its prior lifecycle timestamp")

        if self.status is TaskStatus.QUEUED:
            if any((self.phase, self.started_at, self.finished_at, self.result, has_failure)):
                raise ValueError("Queued tasks cannot contain lifecycle outcomes")
        elif self.status is TaskStatus.RUNNING:
            if self.started_at is None or self.finished_at is not None or self.result is not None or has_failure:
                raise ValueError("Running tasks require only a start time")
        elif self.status is TaskStatus.SUCCEEDED:
            if self.started_at is None or self.finished_at is None or self.result is None or has_failure:
                raise ValueError("Succeeded tasks require start, finish, and result data")
        elif self.status in {TaskStatus.FAILED, TaskStatus.INTERRUPTED}:
            if self.started_at is None or self.finished_at is None or not has_failure or self.result is not None:
                raise ValueError("Failed and interrupted tasks require complete failure data")
        elif self.status is TaskStatus.CANCELLED and (
            self.started_at is not None
            or self.finished_at is None
            or self.result is not None
            or has_failure
            or self.phase is not None
        ):
            raise ValueError("Cancelled tasks require only a finish time")
        return self


class TaskAttemptRecord(FrozenModel):
    attempt_id: str
    task_id: str
    attempt_number: Annotated[int, Field(ge=1)]
    status: TaskStatus
    phase: TaskPhase | None = None
    created_at: datetime
    eligible_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    version: Annotated[int, Field(ge=0)] = 0
    failure_category: FailureCategory | None = None
    failure_code: FailureCode | None = None
    retry_disposition: RetryDisposition | None = None
    failure_phase: TaskPhase | None = None
    failure_summary: str | None = Field(default=None, max_length=500)
    result: TaskResult | None = None
    retryable: bool = False

    _utc_timestamps = field_validator("created_at", "eligible_at", "started_at", "finished_at")(_require_utc)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        record = TaskRecord(
            task_id=self.task_id,
            attempt_id=self.attempt_id,
            attempt_number=self.attempt_number,
            attempt_count=self.attempt_number,
            retryable=self.retryable,
            request=TaskCreate(
                powercontext_ref="latest",
                benchmark="swebench-pro",
                instance_id="attempt-validation",
                treatment_mode="off_on",
                idempotency_key="attempt-validation",
            ),
            status=self.status,
            phase=self.phase,
            created_at=self.created_at,
            eligible_at=self.eligible_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            version=self.version,
            failure_category=self.failure_category,
            failure_code=self.failure_code,
            retry_disposition=self.retry_disposition,
            failure_phase=self.failure_phase,
            failure_summary=self.failure_summary,
            result=self.result,
        )
        del record
        return self


class TaskSummary(FrozenModel):
    task_id: str
    attempt_id: str | None = None
    attempt_number: Annotated[int, Field(ge=1)] = 1
    attempt_count: Annotated[int, Field(ge=1)] = 1
    retryable: bool = False
    powercontext_ref: str
    instance_id: str
    model: str
    status: TaskStatus
    phase: TaskPhase | None = None
    created_at: datetime
    eligible_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    version: Annotated[int, Field(ge=0)]
    off_resolved: bool | None = None
    on_resolved: bool | None = None

    _utc_timestamps = field_validator("created_at", "eligible_at", "started_at", "finished_at")(_require_utc)


class TaskEvent(FrozenModel):
    task_id: str
    status: TaskStatus
    phase: TaskPhase | None = None
    version: Annotated[int, Field(ge=0)]
    occurred_at: datetime

    _utc_timestamp = field_validator("occurred_at")(_require_utc)


class Capabilities(FrozenModel):
    benchmarks: tuple[Literal["swebench-pro"], ...] = ("swebench-pro",)
    instances: tuple[Literal["instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9"], ...] = (INSTANCE_ID,)
    models: tuple[str, ...] = (DEFAULT_CODEX_MODEL,)
    reasoning_efforts: tuple[Literal["medium"], ...] = ("medium",)
    treatment_modes: tuple[TreatmentMode, ...] = tuple(TreatmentMode)

    @field_validator("treatment_modes", mode="before")
    @classmethod
    def parse_treatment_modes(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(TreatmentMode(item) if isinstance(item, str) else item for item in value)
        return value


class HealthResponse(FrozenModel):
    service: Literal["ok"]
    worker_lease_active: bool
    active_task_pairs: Annotated[int, Field(ge=0)]
    task_parallelism: Annotated[int, Field(ge=1, le=20)]
    queued_tasks: Annotated[int, Field(ge=0)]
    running_tasks: Annotated[int, Field(ge=0)]
    web_revision: str | None
    worker_revision: str | None
    web_schema_version: Annotated[int, Field(ge=1)] | None
    worker_schema_version: Annotated[int, Field(ge=1)] | None
    deployment_consistent: bool
    resource_admission_open: bool
    filesystem_free_bytes: Annotated[int, Field(ge=0)] | None
    filesystem_total_bytes: Annotated[int, Field(ge=0)] | None
    filesystem_min_free_bytes: Annotated[int, Field(ge=1)]
    filesystem_free_inodes: Annotated[int, Field(ge=0)] | None
    filesystem_total_inodes: Annotated[int, Field(ge=0)] | None
    filesystem_min_free_inodes: Annotated[int, Field(ge=1)]


class MetricValue(FrozenModel):
    value: Annotated[int | float, Field(ge=0, allow_inf_nan=False)] | None


class ArmResponse(FrozenModel):
    arm: Literal["off", "on"]
    state: ArmState
    resolution: Literal["resolved", "unresolved"]
    passed: bool | None
    treatment_valid: bool
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    patch_bytes: Annotated[int, Field(ge=0)] | None = None


class MetricComparison(FrozenModel):
    off: Annotated[int | float, Field(ge=0, allow_inf_nan=False)]
    on: Annotated[int | float, Field(ge=0, allow_inf_nan=False)]
    delta: Annotated[int | float, Field(allow_inf_nan=False)]
    percent: Annotated[float, Field(allow_inf_nan=False)] | None


class ComparisonResponse(FrozenModel):
    input_tokens: MetricComparison | None = None
    output_tokens: MetricComparison | None = None
    elapsed_seconds: MetricComparison | None = None
    patch_bytes: MetricComparison | None = None


class TreatmentEvidence(FrozenModel):
    mcp_requests: Annotated[int, Field(ge=0)]
    prompt_sources: Annotated[int, Field(ge=0)]
    plugin_checkout_sha: str
    plugin_id: str
    plugin_installed: bool
    plugin_version: str
    scope_id: str
    server_ready: bool


class EvidenceResponse(FrozenModel):
    off: TreatmentEvidence | None = None
    on: TreatmentEvidence | None = None


class ReportResponse(FrozenModel):
    task_id: str
    acceptance_valid: bool
    treatment_mode: TreatmentMode = TreatmentMode.OFF_ON
    off: ArmResponse | None = None
    on: ArmResponse | None = None
    comparison: ComparisonResponse | None = None
    evidence: EvidenceResponse
    gold_validation: GoldValidationAudit | None = None
    revisions: Mapping[str, str]
    configuration: Mapping[str, str]
    generated_at: datetime

    _utc_timestamp = field_validator("generated_at")(_require_utc)

    @field_validator("treatment_mode", mode="before")
    @classmethod
    def parse_report_treatment_mode(cls, value: object) -> object:
        return TreatmentMode(value) if isinstance(value, str) else value

    @field_validator("revisions", "configuration")
    @classmethod
    def freeze_mapping(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return MappingProxyType(dict(value))

    @field_serializer("revisions", "configuration")
    def serialize_mapping(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)

    @model_validator(mode="after")
    def require_distinct_arms(self) -> Self:
        present = {arm for arm, response in ((Arm.OFF, self.off), (Arm.ON, self.on)) if response is not None}
        evidence = {
            arm for arm, response in ((Arm.OFF, self.evidence.off), (Arm.ON, self.evidence.on)) if response is not None
        }
        if present != set(self.treatment_mode.arms) or evidence != present:
            raise ValueError("Report arms and evidence must match the treatment mode")
        if self.off is not None and self.off.arm != "off":
            raise ValueError("Report OFF arm has the wrong role")
        if self.on is not None and self.on.arm != "on":
            raise ValueError("Report ON arm has the wrong role")
        if (self.comparison is None) is (self.treatment_mode is TreatmentMode.OFF_ON):
            raise ValueError("Only paired reports contain an OFF/ON comparison")
        return self
