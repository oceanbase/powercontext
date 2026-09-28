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

"""Normalized evidence and evaluation models for end-to-end workloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .catalog import E2ETask


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunEnvironment(EvidenceModel):
    commit: str
    database: str
    adapter_version: str
    adapter_protocol_version: str
    agent_model: str | None = None
    started_at: datetime
    finished_at: datetime


class SourceReferenceSnapshot(EvidenceModel):
    name: str
    source_id: str


class MemoryEntrySnapshot(EvidenceModel):
    entry_id: str
    entry_version_id: str
    version: int
    kind: str
    text: str
    state: str
    source_refs: tuple[SourceReferenceSnapshot, ...] = ()


class MemorySnapshot(EvidenceModel):
    entries: tuple[MemoryEntrySnapshot, ...] = ()


class PreparedContextSnapshot(EvidenceModel):
    status: str
    content: str = ""


class CaptureRecord(EvidenceModel):
    schema_: Literal["powercontext.bub-capture-event/v1"] = Field(alias="schema")
    recorded_at: datetime
    event: Literal["user_prompt", "llm_result", "tool_result", "checkpoint", "context"]
    status: str
    sequence: int | None = None
    source_id: str | None = None
    source_position: int | None = None
    error: str | None = None
    final: bool | None = None
    target_position: int | None = None
    previous_cursor: int | None = None
    current_cursor: int | None = None
    high_watermark: int | None = None
    processed_source_count: int | None = None
    memory_created: bool | None = None
    content_bytes: int | None = None
    captured_events: int | None = None
    flushed_position: int | None = None


class RecallProbeObservation(EvidenceModel):
    id: str
    query: str
    prepared_context: PreparedContextSnapshot
    forbidden_context_matched: bool | None = None


class SessionSnapshot(EvidenceModel):
    """Server-side state of one Scope after an agent session and the flush that followed it."""

    session: int = Field(ge=0)
    flush_rounds: int = Field(ge=0)
    sources: int = Field(ge=0)
    memory_pending: int = Field(ge=0)
    memory_entries: int = Field(ge=0)
    preparations: int = Field(ge=0)
    ready_preparations: int = Field(ge=0)


class HarborTrialObservation(EvidenceModel):
    job_id: str | None = None
    trial_name: str | None = None
    trial_uri: str | None = None
    task_checksum: str | None = None
    source_task_checksum: str | None = None
    rewards: dict[str, float | int] = Field(default_factory=dict)
    exception_type: str | None = None
    exception_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class NativeArtifact(EvidenceModel):
    name: str
    sha256: str
    bytes: int = Field(ge=0)


class ResolvedInstruction(EvidenceModel):
    step: str | None = None
    artifact: str
    content: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TaskObservation(EvidenceModel):
    schema_: Literal["powercontext.e2e-evidence/v1"] = Field(
        default="powercontext.e2e-evidence/v1",
        alias="schema",
    )
    run_id: str
    environment: RunEnvironment
    task: E2ETask
    status: Literal["completed", "failed", "skipped"]
    errors: tuple[str, ...] = ()
    harbor: HarborTrialObservation
    capture_records: tuple[CaptureRecord, ...] = ()
    native_artifacts: tuple[NativeArtifact, ...] = ()
    resolved_instructions: tuple[ResolvedInstruction, ...] = ()
    memory_before: MemorySnapshot
    memory_after: MemorySnapshot
    probes: tuple[RecallProbeObservation, ...] = ()


class EvaluationValue(EvidenceModel):
    value: bool | float | str
    reason: str | None = None


class CaseEvaluation(EvidenceModel):
    name: str
    assertions: dict[str, EvaluationValue] = Field(default_factory=dict)
    scores: dict[str, EvaluationValue] = Field(default_factory=dict)
    labels: dict[str, EvaluationValue] = Field(default_factory=dict)
    metrics: dict[str, int | float] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


class EvaluationReport(EvidenceModel):
    schema_: Literal["powercontext.e2e-evaluation/v1"] = Field(
        default="powercontext.e2e-evaluation/v1",
        alias="schema",
    )
    experiment: str
    cases: tuple[CaseEvaluation, ...] = Field(min_length=1)

    @property
    def accepted(self) -> bool:
        return all(bool(result.value) for case in self.cases for result in case.assertions.values())


Arm = Literal["off", "on"]
ArmOutcome = Literal["passed", "failed", "timeout", "error", "integration_failed"]


class PairedArmObservation(EvidenceModel):
    """One arm of one OFF/ON trial for a continuation workload."""

    schema_: Literal["powercontext.e2e-paired-arm/v1"] = Field(
        default="powercontext.e2e-paired-arm/v1",
        alias="schema",
    )
    run_id: str
    task_id: str
    trial: int = Field(ge=1)
    arm: Arm
    position: int = Field(ge=1, le=2, description="Whether this arm ran first or second within its trial.")
    environment: RunEnvironment
    scope_id: str | None = None
    harbor: HarborTrialObservation
    step_rewards: dict[str, float] = Field(default_factory=dict)
    outcome: ArmOutcome
    errors: tuple[str, ...] = ()
    sessions: tuple[SessionSnapshot, ...] = ()
    treatment_failures: tuple[str, ...] = ()


class ArmSummary(EvidenceModel):
    scored: int = Field(ge=0, description="Runs that count toward the success rate: passed, failed, or timed out.")
    passed: int = Field(ge=0)
    timeouts: int = Field(ge=0)
    errors: int = Field(ge=0)
    integration_failures: int = Field(ge=0)


class PairedSummary(EvidenceModel):
    off: ArmSummary
    on: ArmSummary
    pairs: int = Field(ge=0, description="Trials in which both arms were scored.")
    mean_delta: float | None = Field(default=None, description="Mean ON minus OFF score over scored pairs.")


class PairedTaskSummary(PairedSummary):
    task_id: str


class PairedReport(EvidenceModel):
    schema_: Literal["powercontext.e2e-paired-report/v1"] = Field(
        default="powercontext.e2e-paired-report/v1",
        alias="schema",
    )
    experiment: str
    trials: int = Field(ge=1)
    tasks: tuple[PairedTaskSummary, ...] = Field(min_length=1)
    total: PairedSummary
