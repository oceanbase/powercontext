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

"""Immutable single-arm baseline and historical-comparison contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from powercontext_eval.benchmarks.swebench_pro.catalog import TaskSet
from powercontext_eval.models import Arm
from powercontext_eval.web.models import TaskStatus


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("Timestamps must use UTC")
    return value


class BaselineCreate(_FrozenModel):
    name: str = Field(min_length=1, max_length=120)
    source_batch_id: str = Field(min_length=1, max_length=200)
    source_arm: Arm
    expected_report_revision: Annotated[int, Field(ge=0)]
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Baseline name must not be blank")
        return normalized

    @field_validator("source_arm", mode="before")
    @classmethod
    def parse_arm(cls, value: object) -> object:
        return Arm(value) if isinstance(value, str) else value


class BaselineRecord(_FrozenModel):
    baseline_id: str
    name: str
    source_batch_id: str
    source_arm: Arm
    source_report_revision: Annotated[int, Field(ge=0)]
    benchmark: Literal["swebench-pro"]
    task_set: TaskSet
    instance_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_tasks: Annotated[int, Field(ge=1)]
    resolved_tasks: Annotated[int, Field(ge=0)]
    execution_failures: Annotated[int, Field(ge=0)]
    model: str
    reasoning_effort: Literal["medium"]
    dataset_revision: str
    harness_revision: str
    powercontext_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    codex_version: str | None = None
    created_at: datetime

    _created_at_utc = field_validator("created_at")(_require_utc)

    @field_validator("source_arm", mode="before")
    @classmethod
    def parse_arm(cls, value: object) -> object:
        return Arm(value) if isinstance(value, str) else value


class BaselineItemRecord(_FrozenModel):
    baseline_id: str
    instance_id: str
    source_index: Annotated[int, Field(ge=0)]
    source_task_id: str
    source_attempt_id: str | None = None
    status: TaskStatus
    resolved: bool | None = None
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    total_tokens: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> BaselineItemRecord:
        if self.status is TaskStatus.SUCCEEDED:
            if self.source_attempt_id is None or self.resolved is None:
                raise ValueError("Successful baseline items require an exact attempt and result")
        elif any(
            value is not None for value in (self.resolved, self.input_tokens, self.output_tokens, self.total_tokens)
        ):
            raise ValueError("Unsuccessful baseline items cannot contain arm outcomes")
        if (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.total_tokens != self.input_tokens + self.output_tokens
        ):
            raise ValueError("Baseline token totals are inconsistent")
        return self


class BaselineSnapshot(_FrozenModel):
    benchmark: Literal["swebench-pro"]
    task_set: TaskSet
    instance_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_tasks: Annotated[int, Field(ge=1)]
    resolved_tasks: Annotated[int, Field(ge=0)]
    execution_failures: Annotated[int, Field(ge=0)]
    model: str
    reasoning_effort: Literal["medium"]
    dataset_revision: str
    harness_revision: str
    powercontext_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    codex_version: str | None = None
    items: tuple[BaselineItemRecord, ...]


class CompatibilityStatus(StrEnum):
    COMPATIBLE = "compatible"
    WARNING = "warning"
    INCOMPATIBLE = "incompatible"


class BaselineCompatibility(_FrozenModel):
    status: CompatibilityStatus
    reasons: tuple[str, ...] = ()


class BaselineCandidate(_FrozenModel):
    baseline: BaselineRecord
    compatibility: BaselineCompatibility


class BaselineSelection(_FrozenModel):
    baseline_id: str
    current_arm: Arm

    @field_validator("current_arm", mode="before")
    @classmethod
    def parse_arm(cls, value: object) -> object:
        return Arm(value) if isinstance(value, str) else value


class BaselineSelectionUpdate(_FrozenModel):
    selections: tuple[BaselineSelection, ...] = Field(max_length=10)

    @field_validator("selections", mode="before")
    @classmethod
    def parse_json_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def unique_selections(self) -> BaselineSelectionUpdate:
        keys = {(selection.baseline_id, selection.current_arm) for selection in self.selections}
        if len(keys) != len(self.selections):
            raise ValueError("Baseline selections must be unique")
        return self


class ComparisonCoverage(_FrozenModel):
    matched_tasks: Annotated[int, Field(ge=0)]
    comparable_tasks: Annotated[int, Field(ge=0)]
    current_execution_failures: Annotated[int, Field(ge=0)]
    baseline_execution_failures: Annotated[int, Field(ge=0)]


class HistoricalResolutionComparison(_FrozenModel):
    baseline_resolved: Annotated[int, Field(ge=0)]
    current_resolved: Annotated[int, Field(ge=0)]
    total: Annotated[int, Field(ge=0)]
    baseline_rate_percent: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    current_rate_percent: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    delta_points: Annotated[float, Field(allow_inf_nan=False)]


class HistoricalTokenComparison(_FrozenModel):
    baseline: Annotated[int, Field(ge=0)]
    current: Annotated[int, Field(ge=0)]
    delta: int
    baseline_measured_tasks: Annotated[int, Field(ge=0)]
    current_measured_tasks: Annotated[int, Field(ge=0)]


class BaselineComparison(_FrozenModel):
    baseline: BaselineRecord
    current_arm: Arm
    compatibility: BaselineCompatibility
    coverage: ComparisonCoverage
    resolution: HistoricalResolutionComparison
    outcome_categories: dict[
        Literal[
            "baseline_fail_current_pass",
            "baseline_pass_current_fail",
            "both_pass",
            "both_fail",
        ],
        Annotated[int, Field(ge=0)],
    ]
    input_tokens: HistoricalTokenComparison | None = None
    output_tokens: HistoricalTokenComparison | None = None
    total_tokens: HistoricalTokenComparison | None = None

    @field_validator("current_arm", mode="before")
    @classmethod
    def parse_arm(cls, value: object) -> object:
        return Arm(value) if isinstance(value, str) else value


class BaselineComparisonResponse(_FrozenModel):
    batch_id: str
    report_revision: Annotated[int, Field(ge=0)]
    comparisons: tuple[BaselineComparison, ...]
