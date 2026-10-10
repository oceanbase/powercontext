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

import pytest
from pydantic import ValidationError

from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.runtime.decision_model import DecisionOutcome, DecisionResult
from powercontext.builtin.runtime.decision_policy import (
    DecisionAssessment,
    DecisionAssessmentSource,
    DecisionCoverage,
    DecisionFailurePolicy,
    DecisionObservation,
    DecisionPolicy,
    DecisionPolicyMode,
    DecisionPrivacyBoundary,
    DecisionVerdict,
    assess_decision_result,
)


def _policy() -> DecisionPolicy:
    return DecisionPolicy(
        policy_id="memory.write.evidence_sufficiency.v1",
        decision_kind="memory.write-gate",
        version="1",
        consumer="memory_write_gate",
        mode=DecisionPolicyMode.SHADOW,
        failure_policy=DecisionFailurePolicy.FAIL_OPEN,
        privacy_boundary=DecisionPrivacyBoundary.REFERENCES_ONLY,
        question="Does the cited evidence fail to support the candidate?",
        subject_selector="candidate_text",
        evidence_selector="candidate_citations",
        outcome_mapping={
            "yes": "deny",
            "no": "allow",
            "abstain": "unknown",
        },
        promotion_criteria=("replay evidence exists",),
    )


def test_fallback_assessment_is_unadjudicated_even_when_domain_will_pass_through() -> None:
    assessment = assess_decision_result(
        _policy(),
        DecisionResult(
            DecisionOutcome.ABSTAIN,
            "backend.policy.v1",
            InferenceUsage(requests=0),
            used_fallback=True,
        ),
    )

    assert assessment.coverage is DecisionCoverage.UNADJUDICATED
    assert assessment.verdict is DecisionVerdict.UNKNOWN
    assert assessment.source is DecisionAssessmentSource.NONE
    assert assessment.used_fallback is True
    assert assessment.reason == "decision_model_fallback"


def test_abstention_is_unknown_without_claiming_content_was_judged() -> None:
    assessment = assess_decision_result(
        _policy(),
        DecisionResult(
            DecisionOutcome.ABSTAIN,
            "backend.policy.v1",
            InferenceUsage(requests=1),
            rationale="not enough evidence",
        ),
    )

    assert assessment.coverage is DecisionCoverage.UNADJUDICATED
    assert assessment.verdict is DecisionVerdict.UNKNOWN
    assert assessment.source is DecisionAssessmentSource.DECISION_MODEL
    assert assessment.used_fallback is False
    assert assessment.reason == "not enough evidence"


def test_substantive_decision_result_uses_policy_outcome_mapping() -> None:
    yes = assess_decision_result(
        _policy(),
        DecisionResult(
            DecisionOutcome.YES,
            "backend.policy.v1",
            InferenceUsage(requests=1, input_tokens=3, output_tokens=1),
            confidence=0.8,
        ),
    )
    no = assess_decision_result(
        _policy(),
        DecisionResult(DecisionOutcome.NO, "backend.policy.v1", InferenceUsage(requests=1)),
    )

    assert yes.coverage is DecisionCoverage.ADJUDICATED
    assert yes.verdict is DecisionVerdict.DENY
    assert yes.confidence == 0.8
    assert yes.usage.input_tokens == 3
    assert no.coverage is DecisionCoverage.ADJUDICATED
    assert no.verdict is DecisionVerdict.ALLOW


def test_assessment_rejects_fallback_that_claims_adjudicated_coverage() -> None:
    with pytest.raises(ValidationError, match="fallback assessments must be unadjudicated"):
        DecisionAssessment(
            policy_id="memory.write.evidence_sufficiency.v1",
            policy_version="1",
            mode=DecisionPolicyMode.SHADOW,
            coverage=DecisionCoverage.ADJUDICATED,
            verdict=DecisionVerdict.ALLOW,
            source=DecisionAssessmentSource.NONE,
            used_fallback=True,
            usage=InferenceUsage(requests=0),
        )


def test_observation_keeps_policy_assessment_distinct_from_domain_action() -> None:
    assessment = assess_decision_result(
        _policy(),
        DecisionResult(DecisionOutcome.YES, "backend.policy.v1", InferenceUsage(requests=1)),
    )

    observation = DecisionObservation(
        operation_id="remember:scope-a:42",
        scope_id="scope-a",
        consumer="memory_write_gate",
        policy_id=assessment.policy_id,
        policy_version=assessment.policy_version,
        mode=assessment.mode,
        subject_refs=("candidate:1",),
        evidence_refs=("source:task:1",),
        privacy_boundary=DecisionPrivacyBoundary.REFERENCES_ONLY,
        assessment=assessment,
        final_action="memory_write_hold",
    )

    assert observation.assessment.verdict is DecisionVerdict.DENY
    assert observation.final_action == "memory_write_hold"
    assert observation.evidence_refs == ("source:task:1",)
