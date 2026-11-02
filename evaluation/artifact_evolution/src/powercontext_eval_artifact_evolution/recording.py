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

"""Evaluation-owned adapter from a terminal Runtime Dream snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

from powercontext_eval_artifact_evolution.bundle import record_attempt, validation_error_summary
from powercontext_eval_artifact_evolution.models import (
    ArtifactEvolutionBundle,
    AttemptCost,
    AttemptResult,
    CandidateOutput,
    DecisionRequest,
    DreamRunRef,
    GenerationIdentity,
    Nomination,
    ProposalAttempt,
    ValidationResult,
)
from powercontext_eval_artifact_evolution.privacy import canonical_json, digest_json

MAX_SOURCE_ID_LENGTH = 256
MAX_SOURCE_TYPE_LENGTH = 128
MAX_ARTIFACT_FAMILY_LENGTH = 128
MAX_ARTIFACT_ID_LENGTH = 128


class DreamRecordingError(ValueError):
    """A Dream snapshot cannot be recorded under the P1 contract."""


class DreamUsageSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model_calls: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class ArtifactRefSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    family: str = Field(min_length=1, max_length=MAX_ARTIFACT_FAMILY_LENGTH)
    artifact_id: str = Field(min_length=1, max_length=MAX_ARTIFACT_ID_LENGTH)
    revision: int = Field(ge=1)

    @field_validator("family", "artifact_id")
    @classmethod
    def _validate_identity(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("reference identity must not contain leading or trailing whitespace")
        return value


class SourceRefSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_type: str = Field(min_length=1, max_length=MAX_SOURCE_TYPE_LENGTH)
    source_id: str = Field(min_length=1, max_length=MAX_SOURCE_ID_LENGTH)

    @field_validator("source_type", "source_id")
    @classmethod
    def _validate_identity(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("reference identity must not contain leading or trailing whitespace")
        return value


class EvidenceNodeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    evidence_id: str
    kind: Literal[
        "source",
        "experience",
        "memory",
        "profile",
        "topic_memory",
        "handoff",
        "skill",
        "prompt",
        "catalog_target",
        "unresolved",
    ]
    digest: str
    source: SourceRefSnapshot | None = None
    artifact: ArtifactRefSnapshot | None = None
    role: Literal["root", "derived", "target", "lineage_only", "unresolved"]
    historical: bool = False


class EvidenceEdgeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    derived_id: str
    upstream_id: str


class RootEvidenceGroupSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    group_id: str
    sources: tuple[SourceRefSnapshot, ...]
    independence: Literal["attested", "unknown"] = "unknown"


class EvidenceManifestSnapshot(BaseModel):
    """Content-free Runtime manifest fields required by the P1 adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    transform_version: Literal["powercontext.dream.evidence.v1"]
    artifacts: tuple[ArtifactRefSnapshot, ...] = Field(min_length=1)
    sources: tuple[SourceRefSnapshot, ...] = ()
    nodes: tuple[EvidenceNodeSnapshot, ...] = ()
    edges: tuple[EvidenceEdgeSnapshot, ...] = ()
    root_groups: tuple[RootEvidenceGroupSnapshot, ...] = ()
    projection_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    projection_bytes: int = Field(ge=0)
    incomplete: bool = False


class DreamCandidateSnapshot(BaseModel):
    """Candidate shape emitted by the supported ``refine_experience`` operation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["artifact"] = "artifact"
    candidate_id: str = Field(min_length=1, max_length=MAX_ARTIFACT_ID_LENGTH)
    version: int = Field(ge=1)


class DreamRunSnapshot(BaseModel):
    """Minimal structural view of ``powercontext.builtin.dream.DreamRun``.

    The evaluation project intentionally does not import the Runtime package.
    A real DreamRun can be converted with :func:`snapshot_from_dream_run`.
    """

    model_config = ConfigDict(extra="ignore", frozen=True, strict=True, revalidate_instances="always")

    scope_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    operation: Literal["refine_experience"]
    status: Literal["queued", "running", "succeeded", "failed"]
    outcome: Literal["proposed", "no_change", "needs_evidence"] | None = None
    target: ArtifactRefSnapshot | None = None
    input_manifest: EvidenceManifestSnapshot | None = None
    candidate: DreamCandidateSnapshot | None = None
    reason: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempt_count: int = Field(default=0, ge=0)
    usage: DreamUsageSnapshot
    prompt_version: str = Field(min_length=1)
    model_config_id: str | None = None
    reused: bool = False

    @field_validator("input_manifest", mode="before")
    @classmethod
    def _canonicalize_input_manifest(cls, value: Any, info: ValidationInfo) -> Any:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="python", exclude_none=False)
        value = _strip_null_mapping_values(value)
        return _tupleize_json_arrays(value) if info.mode == "json" else value


def snapshot_from_dream_run(run: object) -> DreamRunSnapshot:
    """Read only the stable fields needed from a real or fixture DreamRun."""

    return DreamRunSnapshot.model_validate(
        {
            "scope_id": _required_attribute(run, "scope_id"),
            "run_id": _required_attribute(run, "run_id"),
            "operation": _required_attribute(run, "operation"),
            "status": _required_attribute(run, "status"),
            "outcome": getattr(run, "outcome", None),
            "target": _dump(getattr(run, "target", None)),
            "input_manifest": _dump(getattr(run, "input_manifest", None)),
            "candidate": _dump(getattr(run, "candidate", None)),
            "reason": getattr(run, "reason", None),
            "error": getattr(run, "error", None),
            "started_at": getattr(run, "started_at", None),
            "completed_at": getattr(run, "completed_at", None),
            "attempt_count": getattr(run, "attempt_count", 0),
            "usage": _dump(getattr(run, "usage", None)) or {},
            "prompt_version": _required_attribute(run, "prompt_version"),
            "model_config_id": getattr(run, "model_config_id", None),
            "reused": getattr(run, "reused", False),
        }
    )


def decision_for_dream_run(
    run: DreamRunSnapshot,
    *,
    action_signature: str,
    ordering: int = 0,
    decision_id: str | None = None,
    trajectory_id: str | None = None,
) -> DecisionRequest:
    """Build the exact decision key while computing its observation digest."""

    if run.input_manifest is None:
        raise DreamRecordingError("refine_experience run has no input_manifest")
    return DecisionRequest(
        decision_id=decision_id or run.run_id,
        trajectory_id=trajectory_id or run.scope_id,
        observations_digest=digest_json(run.input_manifest),
        action_signature=action_signature,
        ordering=ordering,
    )


def record_dream_attempt(
    bundle: ArtifactEvolutionBundle,
    run: DreamRunSnapshot | object,
    *,
    decision: DecisionRequest,
    policy_id: str,
    generator_id: str,
    runtime_version: str,
    attempt_id: str | None = None,
) -> ArtifactEvolutionBundle:
    """Map one terminal ``refine_experience`` Dream run into an attempt."""

    try:
        snapshot = (
            DreamRunSnapshot.model_validate(run) if isinstance(run, DreamRunSnapshot) else snapshot_from_dream_run(run)
        )
    except ValidationError as error:
        raise DreamRecordingError(f"invalid Dream run snapshot: {validation_error_summary(error)}") from error
    if snapshot.operation != "refine_experience":
        raise DreamRecordingError("only refine_experience Dream runs are supported")
    if snapshot.status not in {"succeeded", "failed"}:
        raise DreamRecordingError("only terminal Dream runs can be recorded")
    if snapshot.attempt_count < 1:
        raise DreamRecordingError("terminal Dream run has no execution attempt")
    if snapshot.reused:
        raise DreamRecordingError("reused Dream runs are not realized proposal attempts")
    if snapshot.input_manifest is None:
        raise DreamRecordingError("refine_experience run has no input_manifest")
    if snapshot.status == "succeeded" and snapshot.outcome is None:
        raise DreamRecordingError("succeeded Dream run has no outcome")
    if snapshot.status == "failed" and (snapshot.outcome is not None or snapshot.candidate is not None):
        raise DreamRecordingError("failed Dream run must not contain an outcome or candidate")
    if snapshot.status == "succeeded" and snapshot.outcome != "proposed" and snapshot.candidate is not None:
        raise DreamRecordingError("non-proposed Dream run must not contain a candidate")
    if snapshot.model_config_id is None:
        raise DreamRecordingError("Dream run has no model_config_id")
    _validate_workload_binding(bundle, snapshot)
    expected_digest = digest_json(snapshot.input_manifest)
    if decision.observations_digest != expected_digest:
        raise DreamRecordingError("decision observations_digest does not match input_manifest")

    typed_output: CandidateOutput | None = None
    if snapshot.outcome == "proposed":
        if snapshot.candidate is None:
            raise DreamRecordingError("proposed Dream run has no candidate reference")
        typed_output = CandidateOutput(
            candidate_id=snapshot.candidate.candidate_id,
            candidate_version=snapshot.candidate.version,
        )
    outcome: Literal["proposed", "no_change", "needs_evidence", "error"]
    if snapshot.status == "failed":
        outcome = "error"
    else:
        assert snapshot.outcome is not None
        outcome = snapshot.outcome
    candidate_ref = _candidate_ref(snapshot.candidate)
    output_payload = (
        typed_output.model_dump(mode="json")
        if typed_output is not None and bundle.capture_policy.payloads != "none"
        else None
    )
    target_ref = None if snapshot.target is None else canonical_json(snapshot.target).decode("utf-8")
    wall_time_ms = None
    if snapshot.started_at is not None and snapshot.completed_at is not None:
        wall_time_ms = max(0, int((snapshot.completed_at - snapshot.started_at).total_seconds() * 1000))
    attempt = ProposalAttempt(
        attempt_id=attempt_id or f"{snapshot.scope_id}:{snapshot.run_id}",
        decision=decision,
        parent_dream_run_ref=DreamRunRef(scope_id=snapshot.scope_id, run_id=snapshot.run_id),
        input_refs=_input_refs(snapshot.input_manifest),
        target_artifact_ref=target_ref,
        # Runtime currently represents the admitted head as the exact target revision.
        expected_head=target_ref,
        generation=GenerationIdentity(
            policy_id=policy_id,
            generator_id=generator_id,
            model_id=snapshot.model_config_id,
            prompt_schema_version=snapshot.prompt_version,
            runtime_version=runtime_version,
        ),
        result=AttemptResult(
            outcome=outcome,
            output=typed_output,
            output_digest=digest_json(typed_output) if typed_output is not None else None,
            output_payload=output_payload,
            payload_digest=digest_json(output_payload) if output_payload is not None else None,
            # Runtime error text is intentionally not copied into an evaluation artifact.
            error_code="dream_failed" if outcome == "error" else None,
        ),
        validators=(
            ValidationResult(
                validator_id="dream-run-terminal",
                version="v1",
                status="passed" if snapshot.status == "succeeded" else "failed",
                details={"source": "DreamRun.status"},
            ),
        ),
        cost=AttemptCost(
            model_calls=snapshot.usage.model_calls,
            retries=max(snapshot.attempt_count - 1, 0),
            input_tokens=snapshot.usage.input_tokens,
            output_tokens=snapshot.usage.output_tokens,
            wall_time_ms=wall_time_ms,
        ),
        nomination=Nomination(
            status="nominated" if candidate_ref is not None else "not_nominated",
            reason=(
                "existing live Dream result created a Candidate"
                if candidate_ref is not None
                else "Dream run produced no Candidate"
            ),
            candidate_ref=candidate_ref,
        ),
    )
    return record_attempt(bundle, attempt)


def _required_attribute(value: object, name: str):
    result = getattr(value, name, None)
    if result is None:
        raise DreamRecordingError(f"Dream run is missing {name}")
    return result


def _dump(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python", exclude_none=False)
    if not isinstance(value, dict):
        raise DreamRecordingError("Dream run nested value must be an object")
    return cast(dict[str, Any], value)


def _candidate_ref(candidate: DreamCandidateSnapshot | None) -> str | None:
    if candidate is None:
        return None
    return f"{candidate.candidate_id}@{candidate.version}"


def _input_refs(manifest: EvidenceManifestSnapshot) -> tuple[str, ...]:
    return tuple(canonical_json(value).decode("utf-8") for value in (*manifest.artifacts, *manifest.sources))


def _validate_workload_binding(bundle: ArtifactEvolutionBundle, snapshot: DreamRunSnapshot) -> None:
    workload = bundle.workload
    if workload.operation != snapshot.operation:
        raise DreamRecordingError("workload operation does not match Dream run")
    if workload.scope_digest != digest_json(snapshot.scope_id):
        raise DreamRecordingError("workload scope_digest does not match Dream run")
    assert snapshot.input_manifest is not None
    if workload.fixture_digest != digest_json(snapshot.input_manifest):
        raise DreamRecordingError("workload fixture_digest does not match Dream input_manifest")

    target_ref = None if snapshot.target is None else canonical_json(snapshot.target).decode("utf-8")
    if workload.target_artifact_ref != target_ref:
        raise DreamRecordingError("workload target_artifact_ref does not match Dream run")
    if workload.expected_head != target_ref:
        raise DreamRecordingError("workload expected_head does not match Dream run")

    input_refs = _input_refs(snapshot.input_manifest)
    if workload.evidence_refs != input_refs:
        raise DreamRecordingError("workload evidence_refs do not match Dream input_manifest")


def _strip_null_mapping_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _strip_null_mapping_values(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_strip_null_mapping_values(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_null_mapping_values(item) for item in value)
    return value


def _tupleize_json_arrays(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _tupleize_json_arrays(item) for key, item in value.items()}
    if isinstance(value, list):
        return tuple(_tupleize_json_arrays(item) for item in value)
    return value
