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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from powercontext_eval_artifact_evolution.privacy import digest_json

BUNDLE_SCHEMA = "powercontext.artifact-evolution/v1"
SHA256_LENGTH = 64


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
    """Declared evaluation limits, kept separate from live Runtime budgets."""

    max_proposal_attempts: int = Field(ge=1)
    max_model_calls: int = Field(ge=1)
    max_input_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    max_wall_time_ms: int | None = Field(default=None, ge=1)
    max_concurrency: int = Field(default=1, ge=1)


class Workload(_ContractModel):
    """Scope-safe workload identity and exact Dream input binding."""

    experiment_id: str = Field(min_length=1)
    scope_digest: str = Field(min_length=SHA256_LENGTH, max_length=SHA256_LENGTH)
    operation: Literal["refine_experience"]
    target_artifact_ref: str | None = None
    expected_head: str | None = None
    fixture_digest: str = Field(min_length=SHA256_LENGTH, max_length=SHA256_LENGTH)
    treatment: str = Field(min_length=1)
    upstream_task_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()
    declared_budget: Budget

    @field_validator("scope_digest", "fixture_digest")
    @classmethod
    def _require_sha256(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value.lower()):
            raise ValueError("digest must be lowercase hexadecimal")
        return value.lower()


class DecisionRequest(_ContractModel):
    """One requested decision and its canonical baseline action."""

    decision_id: str = Field(min_length=1)
    trajectory_id: str = Field(min_length=1)
    observations_digest: str = Field(min_length=SHA256_LENGTH, max_length=SHA256_LENGTH)
    action_signature: str = Field(min_length=1)
    ordering: int = Field(ge=0)

    @field_validator("observations_digest")
    @classmethod
    def _require_sha256(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value.lower()):
            raise ValueError("observations_digest must be lowercase hexadecimal")
        return value.lower()

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


class AttemptResult(_ContractModel):
    """Typed terminal result with a digest of any generated output."""

    outcome: Literal["proposed", "no_change", "needs_evidence", "error"]
    output_digest: str | None = Field(default=None, min_length=SHA256_LENGTH, max_length=SHA256_LENGTH)
    output_payload: dict[str, JsonValue] | None = None
    error_code: str | None = None

    @field_validator("output_digest")
    @classmethod
    def _require_sha256(cls, value: str | None) -> str | None:
        if value is not None and any(character not in "0123456789abcdef" for character in value.lower()):
            raise ValueError("output_digest must be lowercase hexadecimal")
        return None if value is None else value.lower()

    @model_validator(mode="after")
    def _validate_result(self) -> AttemptResult:
        if self.outcome == "proposed" and self.output_digest is None:
            raise ValueError("proposed results require output_digest")
        if self.outcome == "error" and not self.error_code:
            raise ValueError("error results require error_code")
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
        trajectories = {trajectory.trajectory_id: trajectory for trajectory in self.requested_trajectories}
        if len(trajectories) != len(self.requested_trajectories):
            raise ValueError("requested_trajectories must have unique ids")
        for trajectory in self.requested_trajectories:
            if any(decision_id not in decision_ids for decision_id in trajectory.decision_ids):
                raise ValueError(f"trajectory {trajectory.trajectory_id!r} references an unknown decision")
            if any(
                decisions[key].trajectory_id != trajectory.trajectory_id
                for key in decisions
                if decisions[key].decision_id in trajectory.decision_ids
            ):
                raise ValueError(f"trajectory {trajectory.trajectory_id!r} contains a decision from another trajectory")
        seen_attempts: set[tuple[str, str, str, str, int]] = set()
        for attempt in self.attempts:
            if attempt.decision.key not in decisions:
                raise ValueError(f"attempt {attempt.attempt_id!r} is not bound to a requested decision")
            if attempt.decision.key in seen_attempts:
                raise ValueError(f"duplicate attempt for decision {attempt.decision.decision_id!r}")
            seen_attempts.add(attempt.decision.key)
            if self.capture_policy.payloads == "none" and attempt.result.output_payload is not None:
                raise ValueError("output_payload requires an approved or redacted capture policy")
            if attempt.result.output_payload is not None and attempt.result.output_digest != digest_json(
                attempt.result.output_payload
            ):
                raise ValueError(f"attempt {attempt.attempt_id!r} output_digest does not match output_payload")
            if (
                self.capture_policy.payloads == "redacted"
                and attempt.result.output_payload is not None
                and attempt.result.output_payload != _redact_for_validation(attempt.result.output_payload)
            ):
                raise ValueError("redacted payloads must not contain sensitive values")
        return self


def _redact_for_validation(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    # Imported lazily to keep the contract module's public imports small.
    from powercontext_eval_artifact_evolution.privacy import redact_payload

    return redact_payload(value)
