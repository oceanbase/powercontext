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

"""Versioned data contracts for Artifact-evolution recording.

The models deliberately contain references and digests rather than prompt,
task, or evidence bodies. They are evaluation artifacts, not Runtime records.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from powercontext_eval_artifact_evolution.privacy import digest_json, redact_payload

BUNDLE_SCHEMA = "powercontext.artifact-evolution/v1"
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, populate_by_name=True)


class CapturePolicy(_ContractModel):
    """Retention policy for a controlled evaluation bundle."""

    mode: Literal["controlled"]
    payloads: Literal["none", "redacted", "approved"] = "none"
    retention_class: str = Field(min_length=1)
    approval_ref: str | None = None

    @model_validator(mode="after")
    def _validate_approval(self) -> CapturePolicy:
        if self.payloads == "approved" and not self.approval_ref:
            raise ValueError("approved payload capture requires approval_ref")
        if self.payloads != "approved" and self.approval_ref is not None:
            raise ValueError("approval_ref is only valid for approved payload capture")
        return self


class Budget(_ContractModel):
    """Enforced evaluation limits, kept separate from live Runtime budgets."""

    max_proposal_attempts: int = Field(ge=1)
    max_model_calls: int = Field(ge=1)
    max_input_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    max_wall_time_ms: int | None = Field(default=None, ge=1)
    max_concurrency: int = Field(default=1, ge=1)


class Workload(_ContractModel):
    """Scope-safe workload identity and exact Dream input binding."""

    experiment_id: str = Field(min_length=1)
    scope_digest: Digest
    operation: Literal["refine_experience"]
    target_artifact_ref: str | None = None
    expected_head: str | None = None
    fixture_digest: Digest
    treatment: str = Field(min_length=1)
    upstream_task_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()
    declared_budget: Budget

    @model_validator(mode="after")
    def _validate_v1_head_alias(self) -> Workload:
        if self.target_artifact_ref != self.expected_head:
            raise ValueError("v1 workload target does not match expected head")
        return self


class DecisionRequest(_ContractModel):
    """One requested decision and its canonical baseline action."""

    decision_id: str = Field(min_length=1)
    trajectory_id: str = Field(min_length=1)
    observations_digest: Digest
    action_signature: str = Field(min_length=1)
    ordering: int = Field(ge=0)

    @property
    def key(self) -> tuple[str, str, str, str, int]:
        return (self.trajectory_id, self.decision_id, self.observations_digest, self.action_signature, self.ordering)


class TrajectoryRequest(_ContractModel):
    """The ordered decisions requested for one replay trajectory."""

    trajectory_id: str = Field(min_length=1)
    decision_ids: tuple[str, ...] = Field(min_length=1)


class GenerationIdentity(_ContractModel):
    """Identities needed to interpret a realized generation result."""

    policy_id: str = Field(min_length=1)
    generator_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    prompt_schema_version: str = Field(min_length=1)
    runtime_version: str = Field(min_length=1)


class ValidationResult(_ContractModel):
    """One deterministic validator/scorer result saved with an attempt."""

    validator_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: Literal["passed", "failed", "not_run"]
    details: dict[str, JsonValue] = Field(default_factory=dict)


class CandidateOutput(_ContractModel):
    """Content-free identity of the Candidate produced by the live Dream run."""

    candidate_id: str = Field(min_length=1)
    candidate_version: int = Field(ge=1)


class AttemptResult(_ContractModel):
    """Typed result with stable output identity and optional retained-payload identity."""

    outcome: Literal["proposed", "no_change", "needs_evidence", "error"]
    output: CandidateOutput | None = None
    output_digest: Digest | None = None
    output_payload: dict[str, JsonValue] | None = None
    payload_digest: Digest | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def _validate_result(self) -> AttemptResult:
        if self.outcome == "proposed":
            if self.output is None or self.output_digest is None:
                raise ValueError("proposed results require typed output and output_digest")
            if self.output_digest != digest_json(self.output):
                raise ValueError("output_digest does not match typed output")
        elif self.output is not None or self.output_digest is not None:
            raise ValueError("only proposed results may contain typed output or output_digest")
        if self.outcome == "error" and not self.error_code:
            raise ValueError("error results require error_code")
        if self.output_payload is None and self.payload_digest is not None:
            raise ValueError("payload_digest requires output_payload")
        if self.output_payload is not None and self.payload_digest is None:
            raise ValueError("output_payload requires payload_digest")
        return self


class AttemptCost(_ContractModel):
    """Saved cost of one realized proposal attempt."""

    model_calls: int = Field(ge=0)
    retries: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    wall_time_ms: int | None = Field(default=None, ge=0)
    concurrency: int = Field(default=1, ge=1)
    validation_failures: int = Field(default=0, ge=0)


class Nomination(_ContractModel):
    """The proposal's relationship to the existing Candidate path."""

    status: Literal["not_nominated", "nominated", "rejected"]
    reason: str = Field(min_length=1)
    candidate_ref: str | None = None


class DreamRunRef(_ContractModel):
    """Stable reference to the exact Runtime Dream run used for recording."""

    scope_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)


class ProposalAttempt(_ContractModel):
    """One realized action; this record never grants Runtime authority."""

    attempt_id: str = Field(min_length=1)
    decision: DecisionRequest
    parent_dream_run_ref: DreamRunRef
    input_refs: tuple[str, ...] = ()
    target_artifact_ref: str | None = None
    expected_head: str | None = None
    generation: GenerationIdentity
    result: AttemptResult
    validators: tuple[ValidationResult, ...] = ()
    cost: AttemptCost
    nomination: Nomination


class ArtifactEvolutionBundle(_ContractModel):
    """Evaluation-owned recording and replay bundle."""

    schema_version: Literal["powercontext.artifact-evolution/v1"] = Field(alias="schema")
    bundle_id: str = Field(min_length=1)
    created_at: datetime
    workload: Workload
    capture_policy: CapturePolicy
    requested_decisions: tuple[DecisionRequest, ...] = Field(min_length=1)
    requested_trajectories: tuple[TrajectoryRequest, ...] = Field(min_length=1)
    attempts: tuple[ProposalAttempt, ...] = ()

    @model_validator(mode="after")
    def _validate_references(self) -> ArtifactEvolutionBundle:
        decisions = {decision.key: decision for decision in self.requested_decisions}
        if len(decisions) != len(self.requested_decisions):
            raise ValueError("requested_decisions must have unique keys")
        decision_ids = {decision.decision_id for decision in self.requested_decisions}
        if len(decision_ids) != len(self.requested_decisions):
            raise ValueError("requested_decisions must have unique decision ids")
        trajectories = {trajectory.trajectory_id: trajectory for trajectory in self.requested_trajectories}
        if len(trajectories) != len(self.requested_trajectories):
            raise ValueError("requested_trajectories must have unique ids")
        decision_trajectory_ids = {decision.trajectory_id for decision in self.requested_decisions}
        if decision_trajectory_ids != set(trajectories):
            raise ValueError("requested_trajectories must exactly cover requested decision trajectories")
        for trajectory in self.requested_trajectories:
            expected_ids = tuple(
                decision.decision_id
                for decision in sorted(self.requested_decisions, key=lambda value: value.ordering)
                if decision.trajectory_id == trajectory.trajectory_id
            )
            if trajectory.decision_ids != expected_ids:
                raise ValueError(
                    f"trajectory {trajectory.trajectory_id!r} must list every requested decision in ordering order"
                )
        if any(decision.observations_digest != self.workload.fixture_digest for decision in self.requested_decisions):
            raise ValueError("requested decision observations must match workload fixture_digest")
        seen_attempts: set[tuple[str, str, str, str, int]] = set()
        attempt_ids: set[str] = set()
        for attempt in self.attempts:
            if attempt.decision.key not in decisions:
                raise ValueError(f"attempt {attempt.attempt_id!r} is not bound to a requested decision")
            if attempt.decision.key in seen_attempts:
                raise ValueError(f"duplicate attempt for decision {attempt.decision.decision_id!r}")
            seen_attempts.add(attempt.decision.key)
            if attempt.attempt_id in attempt_ids:
                raise ValueError(f"duplicate attempt_id {attempt.attempt_id!r}")
            attempt_ids.add(attempt.attempt_id)
            if digest_json(attempt.parent_dream_run_ref.scope_id) != self.workload.scope_digest:
                raise ValueError(f"attempt {attempt.attempt_id!r} Scope does not match workload")
            if attempt.input_refs != self.workload.evidence_refs:
                raise ValueError(f"attempt {attempt.attempt_id!r} input_refs do not match workload")
            if attempt.target_artifact_ref != self.workload.target_artifact_ref:
                raise ValueError(f"attempt {attempt.attempt_id!r} target does not match workload")
            if attempt.expected_head != self.workload.expected_head:
                raise ValueError(f"attempt {attempt.attempt_id!r} expected head does not match workload")
            if attempt.result.output is not None:
                expected_candidate_ref = (
                    f"{attempt.result.output.candidate_id}@{attempt.result.output.candidate_version}"
                )
                if attempt.nomination.candidate_ref != expected_candidate_ref:
                    raise ValueError(f"attempt {attempt.attempt_id!r} Candidate ref does not match typed output")
            elif attempt.nomination.candidate_ref is not None:
                raise ValueError(f"attempt {attempt.attempt_id!r} has a Candidate ref without typed output")
            if self.capture_policy.payloads == "none" and attempt.result.output_payload is not None:
                raise ValueError("output_payload requires an approved or redacted capture policy")
            if attempt.result.output_payload is not None and attempt.result.payload_digest != digest_json(
                attempt.result.output_payload
            ):
                raise ValueError(f"attempt {attempt.attempt_id!r} payload_digest does not match output_payload")
            if (
                self.capture_policy.payloads == "redacted"
                and attempt.result.output_payload is not None
                and attempt.result.output_payload != redact_payload(attempt.result.output_payload)
            ):
                raise ValueError("redacted payloads must not contain sensitive values")
        self._validate_budget()
        return self

    def _validate_budget(self) -> None:
        budget = self.workload.declared_budget
        if len(self.attempts) > budget.max_proposal_attempts:
            raise ValueError("recorded attempts exceed declared max_proposal_attempts")
        if sum(attempt.cost.model_calls for attempt in self.attempts) > budget.max_model_calls:
            raise ValueError("recorded model calls exceed declared max_model_calls")
        _validate_optional_total(self.attempts, "input_tokens", budget.max_input_tokens)
        _validate_optional_total(self.attempts, "output_tokens", budget.max_output_tokens)
        _validate_optional_total(self.attempts, "wall_time_ms", budget.max_wall_time_ms)
        if any(attempt.cost.concurrency > budget.max_concurrency for attempt in self.attempts):
            raise ValueError("recorded concurrency exceeds declared max_concurrency")


def _validate_optional_total(
    attempts: tuple[ProposalAttempt, ...],
    field: Literal["input_tokens", "output_tokens", "wall_time_ms"],
    limit: int | None,
) -> None:
    if limit is None:
        return
    values = tuple(getattr(attempt.cost, field) for attempt in attempts)
    if any(value is None for value in values):
        raise ValueError(f"recorded {field} is required when max_{field} is declared")
    if sum(value for value in values if value is not None) > limit:
        raise ValueError(f"recorded {field} exceeds declared max_{field}")
