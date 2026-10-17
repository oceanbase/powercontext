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

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from powercontext_eval_artifact_evolution.bundle import (
    BundleIOError,
    BundleValidationError,
    load_bundle,
    record_attempt,
    write_bundle,
)
from powercontext_eval_artifact_evolution.models import (
    ArtifactEvolutionBundle,
    AttemptCost,
    AttemptResult,
    Budget,
    CapturePolicy,
    DecisionRequest,
    DreamRunRef,
    GenerationIdentity,
    Nomination,
    ProposalAttempt,
    TrajectoryRequest,
    ValidationResult,
    Workload,
)
from powercontext_eval_artifact_evolution.privacy import digest_json

DIGEST = "0123456789abcdef" * 4


def decision(
    *,
    action: str = "refine-experience:v1",
    decision_id: str = "decision-1",
    trajectory_id: str = "trajectory-1",
) -> DecisionRequest:
    return DecisionRequest(
        decision_id=decision_id,
        trajectory_id=trajectory_id,
        observations_digest=DIGEST,
        action_signature=action,
        ordering=0,
    )


def attempt(request: DecisionRequest | None = None, *, payload: dict[str, object] | None = None) -> ProposalAttempt:
    return ProposalAttempt(
        attempt_id="attempt-1",
        decision=decision() if request is None else request,
        parent_dream_run_ref=DreamRunRef(scope_id="scope-1", run_id="dream-run-1"),
        input_refs=("experience:exp-1", "source:source-1"),
        generation=GenerationIdentity(
            policy_id="baseline-policy-v1",
            generator_id="experience-generator-v1",
            model_id="fixture-model",
            prompt_schema_version="dream-input-v1",
            runtime_version="runtime-fixture-v1",
        ),
        result=AttemptResult(
            outcome="proposed",
            output_digest=DIGEST if payload is None else digest_json(payload),
            output_payload=payload,
        ),
        validators=(ValidationResult(validator_id="schema", version="v1", status="passed"),),
        cost=AttemptCost(model_calls=1, retries=0, input_tokens=100, output_tokens=20, wall_time_ms=25),
        nomination=Nomination(status="not_nominated", reason="baseline recording"),
    )


def bundle(*, attempts: tuple[ProposalAttempt, ...] = ()) -> ArtifactEvolutionBundle:
    request = decision()
    return ArtifactEvolutionBundle(
        schema="powercontext.artifact-evolution/v1",
        bundle_id="bundle-1",
        created_at=datetime(2026, 10, 17, tzinfo=UTC),
        workload=Workload(
            experiment_id="experiment-1",
            scope_digest=DIGEST,
            operation="refine_experience",
            fixture_digest=DIGEST,
            treatment="baseline",
            declared_budget=Budget(max_proposal_attempts=1, max_model_calls=2),
        ),
        capture_policy=CapturePolicy(mode="controlled", payloads="none", retention_class="test"),
        requested_decisions=(request,),
        requested_trajectories=(TrajectoryRequest(trajectory_id="trajectory-1", decision_ids=(request.decision_id,)),),
        attempts=attempts,
    )


def test_bundle_round_trips_and_refuses_overwrite(tmp_path: Path) -> None:
    path = write_bundle(bundle(attempts=(attempt(),)), tmp_path / "bundle.json")

    loaded = load_bundle(path)

    assert loaded == bundle(attempts=(attempt(),))
    with pytest.raises(BundleIOError, match="refusing to overwrite"):
        write_bundle(loaded, path)


def test_load_rejects_unsupported_schema_and_unknown_body_fields(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    payload = bundle(attempts=(attempt(),)).model_dump(mode="json", by_alias=True)
    payload["schema"] = "powercontext.artifact-evolution/v2"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BundleValidationError, match="invalid artifact-evolution bundle"):
        load_bundle(path)

    payload["schema"] = "powercontext.artifact-evolution/v1"
    payload["attempts"][0]["prompt"] = "must not be retained"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BundleValidationError, match="invalid artifact-evolution bundle"):
        load_bundle(path)


def test_record_attempt_revalidates_retention_and_references() -> None:
    with pytest.raises(ValidationError, match="output_payload requires"):
        record_attempt(bundle(), attempt(payload={"redacted": True}))

    approved = bundle().model_copy(
        update={
            "capture_policy": CapturePolicy(
                mode="controlled", payloads="approved", retention_class="test", approval_ref="review-1"
            )
        }
    )
    recorded = record_attempt(approved, attempt(payload={"safe": "keep"}))
    assert recorded.attempts[0].result.output_payload == {"safe": "keep"}


def test_redacted_capture_removes_sensitive_keys_and_credentials(tmp_path: Path) -> None:
    redacted = bundle().model_copy(
        update={"capture_policy": CapturePolicy(mode="controlled", payloads="redacted", retention_class="test")}
    )
    recorded = record_attempt(
        redacted,
        attempt(
            payload={
                "prompt": "SECRET TASK BODY",
                "authorization": "Bearer sk-live-123",
                "safe": "keep",
            }
        ),
    )

    payload = recorded.attempts[0].result.output_payload
    assert payload == {"prompt": "[REDACTED]", "authorization": "[REDACTED]", "safe": "keep"}
    assert "SECRET TASK BODY" not in json.dumps(payload)
    assert "sk-live-123" not in json.dumps(payload)
    persisted = write_bundle(recorded, tmp_path / "redacted.json").read_text(encoding="utf-8")
    assert "SECRET TASK BODY" not in persisted
    assert "sk-live-123" not in persisted


def test_attempt_must_belong_to_a_requested_decision() -> None:
    other = decision(decision_id="other")
    with pytest.raises(ValidationError, match="not bound to a requested decision"):
        record_attempt(bundle(), attempt(other))
