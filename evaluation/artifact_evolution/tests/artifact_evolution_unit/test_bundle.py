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
    CandidateOutput,
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


def attempt(
    request: DecisionRequest | None = None,
    *,
    attempt_id: str = "attempt-1",
    payload: dict[str, object] | None = None,
) -> ProposalAttempt:
    output = CandidateOutput(candidate_id="candidate-1", candidate_version=1)
    return ProposalAttempt(
        attempt_id=attempt_id,
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
            output=output,
            output_digest=digest_json(output),
            output_payload=payload,
            payload_digest=None if payload is None else digest_json(payload),
        ),
        validators=(ValidationResult(validator_id="schema", version="v1", status="passed"),),
        cost=AttemptCost(model_calls=1, retries=0, input_tokens=100, output_tokens=20, wall_time_ms=25),
        nomination=Nomination(
            status="nominated",
            reason="baseline recording",
            candidate_ref="candidate-1@1",
        ),
    )


def bundle(*, attempts: tuple[ProposalAttempt, ...] = ()) -> ArtifactEvolutionBundle:
    request = decision()
    return ArtifactEvolutionBundle(
        schema="powercontext.artifact-evolution/v1",
        bundle_id="bundle-1",
        created_at=datetime(2026, 10, 17, tzinfo=UTC),
        workload=Workload(
            experiment_id="experiment-1",
            scope_digest=digest_json("scope-1"),
            operation="refine_experience",
            fixture_digest=DIGEST,
            treatment="baseline",
            evidence_refs=("experience:exp-1", "source:source-1"),
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


def test_load_validation_error_does_not_echo_rejected_content(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    payload = bundle().model_dump(mode="json", by_alias=True)
    payload["prompt"] = "SECRET-TASK-BODY"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BundleValidationError) as raised:
        load_bundle(path)

    assert "prompt" in str(raised.value)
    assert "SECRET-TASK-BODY" not in str(raised.value)
    assert "input_value" not in str(raised.value)


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
                "openai_api_key": "not-credential-shaped",
                "session_token": "session-sensitive",
                "id_token": "identity-sensitive",
                "source_task": "task-sensitive",
                "review_evidence": "evidence-sensitive",
                "authorization_header": "Bearer other-secret",
                "token_value": "token-sensitive",
                "task_description": "task-description-sensitive",
                "evidence_text": "evidence-text-sensitive",
                "safe": "keep",
                "prompt_version": "powercontext.dream.v1.4",
                "prompt_tokens": 20,
                "token_count": 70,
                "token_budget": 100,
                "authorization_at": "2026-10-22T00:00:00Z",
                "credential_type": "api-key",
                "output_tokens": 50,
                "task_type": "refine",
            }
        ),
    )

    payload = recorded.attempts[0].result.output_payload
    result = recorded.attempts[0].result
    assert payload == {
        "prompt": "[REDACTED]",
        "authorization": "[REDACTED]",
        "openai_api_key": "[REDACTED]",
        "session_token": "[REDACTED]",
        "id_token": "[REDACTED]",
        "source_task": "[REDACTED]",
        "review_evidence": "[REDACTED]",
        "authorization_header": "[REDACTED]",
        "token_value": "[REDACTED]",
        "task_description": "[REDACTED]",
        "evidence_text": "[REDACTED]",
        "safe": "keep",
        "prompt_version": "powercontext.dream.v1.4",
        "prompt_tokens": 20,
        "token_count": 70,
        "token_budget": 100,
        "authorization_at": "2026-10-22T00:00:00Z",
        "credential_type": "api-key",
        "output_tokens": 50,
        "task_type": "refine",
    }
    assert "SECRET TASK BODY" not in json.dumps(payload)
    assert "sk-live-123" not in json.dumps(payload)
    assert result.output_digest == digest_json(result.output)
    assert result.payload_digest == digest_json(payload)
    persisted = write_bundle(recorded, tmp_path / "redacted.json").read_text(encoding="utf-8")
    assert "SECRET TASK BODY" not in persisted
    assert "sk-live-123" not in persisted


def test_attempt_must_belong_to_a_requested_decision() -> None:
    other = decision(decision_id="other")
    with pytest.raises(ValidationError, match="not bound to a requested decision"):
        record_attempt(bundle(), attempt(other))


@pytest.mark.parametrize(
    ("budget", "cost", "message"),
    [
        (Budget(max_proposal_attempts=1, max_model_calls=1), AttemptCost(model_calls=2, retries=1), "model calls"),
        (
            Budget(max_proposal_attempts=1, max_model_calls=2, max_input_tokens=10),
            AttemptCost(model_calls=1, retries=0, input_tokens=11),
            "input_tokens",
        ),
        (
            Budget(max_proposal_attempts=1, max_model_calls=2, max_input_tokens=10),
            AttemptCost(model_calls=1, retries=0, input_tokens=None),
            "input_tokens is required",
        ),
        (
            Budget(max_proposal_attempts=1, max_model_calls=2, max_output_tokens=10),
            AttemptCost(model_calls=1, retries=0, output_tokens=11),
            "output_tokens",
        ),
        (
            Budget(max_proposal_attempts=1, max_model_calls=2, max_wall_time_ms=10),
            AttemptCost(model_calls=1, retries=0, wall_time_ms=11),
            "wall_time_ms",
        ),
        (
            Budget(max_proposal_attempts=1, max_model_calls=2, max_concurrency=1),
            AttemptCost(model_calls=1, retries=0, concurrency=2),
            "concurrency",
        ),
    ],
)
def test_bundle_rejects_recorded_cost_over_declared_budget(budget: Budget, cost: AttemptCost, message: str) -> None:
    base = bundle().model_copy(update={"workload": bundle().workload.model_copy(update={"declared_budget": budget})})
    over_budget = attempt().model_copy(update={"cost": cost})

    with pytest.raises(ValidationError, match=message):
        record_attempt(base, over_budget)


def test_bundle_rejects_more_attempts_than_declared() -> None:
    second = decision(decision_id="decision-2", trajectory_id="trajectory-2")
    expanded = bundle().model_copy(
        update={
            "requested_decisions": (decision(), second),
            "requested_trajectories": (
                TrajectoryRequest(trajectory_id="trajectory-1", decision_ids=("decision-1",)),
                TrajectoryRequest(trajectory_id="trajectory-2", decision_ids=("decision-2",)),
            ),
            "attempts": (attempt(), attempt(second, attempt_id="attempt-2")),
        }
    )

    with pytest.raises(ValidationError, match="proposal_attempts"):
        ArtifactEvolutionBundle.model_validate(expanded.model_dump(mode="python", by_alias=True))


def test_bundle_rejects_incomplete_trajectory_mapping() -> None:
    second = decision(decision_id="decision-2")
    incomplete = bundle().model_copy(
        update={
            "requested_decisions": (decision(), second),
            "requested_trajectories": (TrajectoryRequest(trajectory_id="trajectory-1", decision_ids=("decision-1",)),),
        }
    )

    with pytest.raises(ValidationError, match="must list every requested decision"):
        ArtifactEvolutionBundle.model_validate(incomplete.model_dump(mode="python", by_alias=True))


def test_digest_fields_reject_uppercase_hex() -> None:
    raw = decision().model_dump(mode="python")
    raw["observations_digest"] = DIGEST.upper()

    with pytest.raises(ValidationError, match="String should match pattern"):
        DecisionRequest.model_validate(raw)


@pytest.mark.parametrize(
    ("workload_update", "message"),
    [
        ({"scope_digest": "0" * 64}, "Scope"),
        ({"fixture_digest": "0" * 64}, "observations"),
        ({"target_artifact_ref": "unrelated"}, "target"),
        ({"expected_head": "unrelated"}, "expected head"),
        ({"evidence_refs": ("unrelated",)}, "input_refs"),
    ],
)
def test_bundle_rejects_tampered_workload_binding(workload_update: dict[str, object], message: str) -> None:
    recorded = bundle(attempts=(attempt(),))
    tampered = recorded.model_copy(update={"workload": recorded.workload.model_copy(update=workload_update)})

    with pytest.raises(ValidationError, match=message):
        ArtifactEvolutionBundle.model_validate(tampered.model_dump(mode="python", by_alias=True))


def test_bundle_rejects_duplicate_attempt_ids() -> None:
    second = decision(decision_id="decision-2", trajectory_id="trajectory-2")
    expanded = bundle().model_copy(
        update={
            "workload": bundle().workload.model_copy(
                update={"declared_budget": Budget(max_proposal_attempts=2, max_model_calls=2)}
            ),
            "requested_decisions": (decision(), second),
            "requested_trajectories": (
                TrajectoryRequest(trajectory_id="trajectory-1", decision_ids=("decision-1",)),
                TrajectoryRequest(trajectory_id="trajectory-2", decision_ids=("decision-2",)),
            ),
            "attempts": (attempt(), attempt(second)),
        }
    )

    with pytest.raises(ValidationError, match="duplicate attempt_id"):
        ArtifactEvolutionBundle.model_validate(expanded.model_dump(mode="python", by_alias=True))


def test_bundle_rejects_attempt_from_another_scope() -> None:
    foreign = attempt().model_copy(
        update={"parent_dream_run_ref": DreamRunRef(scope_id="scope-2", run_id="dream-run-2")}
    )
    mixed = bundle().model_copy(update={"attempts": (foreign,)})

    with pytest.raises(ValidationError, match="Scope does not match workload"):
        ArtifactEvolutionBundle.model_validate(mixed.model_dump(mode="python", by_alias=True))


def test_bundle_rejects_output_digest_that_does_not_match_typed_output() -> None:
    recorded = bundle(attempts=(attempt(),))
    raw = recorded.model_dump(mode="python", by_alias=True)
    raw["attempts"][0]["result"]["output_digest"] = "f" * 64

    with pytest.raises(ValidationError, match="output_digest does not match typed output"):
        ArtifactEvolutionBundle.model_validate(raw)
