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
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from powercontext_eval_artifact_evolution.bundle import record_attempt
from powercontext_eval_artifact_evolution.models import (
    ArtifactEvolutionBundle,
    AttemptCost,
    AttemptResult,
    DecisionRequest,
    DreamRunRef,
    GenerationIdentity,
    Nomination,
    ProposalAttempt,
    ValidationResult,
)
from powercontext_eval_artifact_evolution.privacy import digest_json


class DreamRecordingError(ValueError):
    """A Dream snapshot cannot be recorded under the P1 contract."""


class DreamUsageSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model_calls: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class DreamRunSnapshot(BaseModel):
    """Minimal structural view of ``powercontext.builtin.dream.DreamRun``.

    The evaluation project intentionally does not import the Runtime package.
    A real DreamRun can be converted with :func:`snapshot_from_dream_run`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scope_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    operation: Literal["refine_experience"]
    status: Literal["queued", "running", "succeeded", "failed"]
    outcome: Literal["proposed", "no_change", "needs_evidence"] | None = None
    input_manifest: dict[str, JsonValue] | None = None
    candidate: dict[str, JsonValue] | None = None
    reason: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempt_count: int = Field(default=0, ge=0)
    usage: DreamUsageSnapshot
    prompt_version: str = Field(min_length=1)
    model_config_id: str | None = None


def snapshot_from_dream_run(run: object) -> DreamRunSnapshot:
    """Read only the stable fields needed from a real or fixture DreamRun."""

    return DreamRunSnapshot(
        scope_id=_required_attribute(run, "scope_id"),
        run_id=_required_attribute(run, "run_id"),
        operation=_required_attribute(run, "operation"),
        status=_required_attribute(run, "status"),
        outcome=getattr(run, "outcome", None),
        input_manifest=_dump(getattr(run, "input_manifest", None)),
        candidate=_dump(getattr(run, "candidate", None)),
        reason=getattr(run, "reason", None),
        error=getattr(run, "error", None),
        started_at=getattr(run, "started_at", None),
        completed_at=getattr(run, "completed_at", None),
        attempt_count=getattr(run, "attempt_count", 0),
        usage=DreamUsageSnapshot.model_validate(_dump(getattr(run, "usage", None)) or {}),
        prompt_version=_required_attribute(run, "prompt_version"),
        model_config_id=getattr(run, "model_config_id", None),
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

    snapshot = run if isinstance(run, DreamRunSnapshot) else snapshot_from_dream_run(run)
    if snapshot.operation != "refine_experience":
        raise DreamRecordingError("only refine_experience Dream runs are supported")
    if snapshot.status not in {"succeeded", "failed"}:
        raise DreamRecordingError("only terminal Dream runs can be recorded")
    if snapshot.input_manifest is None:
        raise DreamRecordingError("refine_experience run has no input_manifest")
    expected_digest = digest_json(snapshot.input_manifest)
    if decision.observations_digest != expected_digest:
        raise DreamRecordingError("decision observations_digest does not match input_manifest")

    output_payload: dict[str, JsonValue] | None = None
    if snapshot.outcome == "proposed":
        if snapshot.candidate is None:
            raise DreamRecordingError("proposed Dream run has no candidate reference")
        output_payload = {
            "candidate_id": snapshot.candidate.get("candidate_id", ""),
            "candidate_version": snapshot.candidate.get("version", 0),
        }
    outcome: Literal["proposed", "no_change", "needs_evidence", "error"]
    if snapshot.status == "failed":
        outcome = "error"
    else:
        outcome = snapshot.outcome or "needs_evidence"
    candidate_ref = _candidate_ref(snapshot.candidate)
    wall_time_ms = None
    if snapshot.started_at is not None and snapshot.completed_at is not None:
        wall_time_ms = max(0, int((snapshot.completed_at - snapshot.started_at).total_seconds() * 1000))
    attempt = ProposalAttempt(
        attempt_id=attempt_id or f"{snapshot.scope_id}:{snapshot.run_id}",
        decision=decision,
        parent_dream_run_ref=DreamRunRef(scope_id=snapshot.scope_id, run_id=snapshot.run_id),
        input_refs=_input_refs(snapshot.input_manifest),
        generation=GenerationIdentity(
            policy_id=policy_id,
            generator_id=generator_id,
            model_id=snapshot.model_config_id or "unknown",
            prompt_schema_version=snapshot.prompt_version,
            runtime_version=runtime_version,
        ),
        result=AttemptResult(
            outcome=outcome,
            output_digest=digest_json(output_payload) if output_payload is not None else None,
            output_payload=output_payload,
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
            retries=max(snapshot.attempt_count - snapshot.usage.model_calls, 0),
            input_tokens=snapshot.usage.input_tokens,
            output_tokens=snapshot.usage.output_tokens,
            wall_time_ms=wall_time_ms,
        ),
        nomination=Nomination(
            status="not_nominated",
            reason="Dream candidate remains outside Runtime nomination during P1 recording",
            candidate_ref=candidate_ref,
        ),
    )
    return record_attempt(bundle, attempt)


def _required_attribute(value: object, name: str):
    result = getattr(value, name, None)
    if result is None:
        raise DreamRecordingError(f"Dream run is missing {name}")
    return result


def _dump(value: object) -> dict[str, JsonValue] | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=True)
    if not isinstance(value, dict):
        raise DreamRecordingError("Dream run nested value must be an object")
    return cast(dict[str, JsonValue], value)


def _candidate_ref(candidate: dict[str, JsonValue] | None) -> str | None:
    if candidate is None:
        return None
    candidate_id = candidate.get("candidate_id")
    version = candidate.get("version")
    if not isinstance(candidate_id, str) or not isinstance(version, int):
        raise DreamRecordingError("candidate reference must include candidate_id and version")
    return f"{candidate_id}@{version}"


def _input_refs(manifest: dict[str, JsonValue]) -> tuple[str, ...]:
    refs: list[str] = []
    for key in ("artifacts", "sources"):
        values = manifest.get(key, [])
        if isinstance(values, list):
            refs.extend(digest_json(value) for value in values)
    return tuple(refs)
