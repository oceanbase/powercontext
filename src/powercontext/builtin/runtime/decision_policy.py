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

"""Shared governance vocabulary for narrow runtime decision policies."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from powercontext._logging import log_safely
from powercontext.builtin.decision_observations import (
    DecisionAssessment,
    DecisionAssessmentSource,
    DecisionCoverage,
    DecisionFailurePolicy,
    DecisionObservation,
    DecisionPolicy,
    DecisionPolicyMode,
    DecisionPrivacyBoundary,
    DecisionVerdict,
)
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.runtime.decision_model import DecisionOutcome, DecisionResult

logger = logging.getLogger(__name__)


_DEFAULT_OUTCOME_MAPPING: Mapping[DecisionOutcome, DecisionVerdict] = {
    DecisionOutcome.YES: DecisionVerdict.ALLOW,
    DecisionOutcome.NO: DecisionVerdict.DENY,
    DecisionOutcome.ABSTAIN: DecisionVerdict.UNKNOWN,
}


def assess_decision_result(
    policy: DecisionPolicy,
    result: DecisionResult,
    /,
    *,
    outcome_mapping: Mapping[DecisionOutcome | str, DecisionVerdict | str] | None = None,
    latency_ms: float | None = None,
) -> DecisionAssessment:
    """Map a low-level decision result into the RFC 1770 assessment vocabulary."""

    mapping = _normalized_outcome_mapping(policy, outcome_mapping)
    if result.used_fallback:
        return DecisionAssessment(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            mode=policy.mode,
            coverage=DecisionCoverage.UNADJUDICATED,
            verdict=DecisionVerdict.UNKNOWN,
            source=DecisionAssessmentSource.NONE,
            reason="decision_model_fallback",
            confidence=result.confidence,
            used_fallback=True,
            usage=result.usage,
            latency_ms=latency_ms,
        )
    if result.outcome is DecisionOutcome.ABSTAIN:
        return DecisionAssessment(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            mode=policy.mode,
            coverage=DecisionCoverage.UNADJUDICATED,
            verdict=DecisionVerdict.UNKNOWN,
            source=DecisionAssessmentSource.DECISION_MODEL,
            reason=_bounded_reason(result.rationale, default="abstain"),
            confidence=result.confidence,
            usage=result.usage,
            latency_ms=latency_ms,
        )
    return DecisionAssessment(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        mode=policy.mode,
        coverage=DecisionCoverage.ADJUDICATED,
        verdict=mapping[result.outcome],
        source=DecisionAssessmentSource.DECISION_MODEL,
        reason=_bounded_reason(result.rationale),
        confidence=result.confidence,
        usage=result.usage,
        latency_ms=latency_ms,
    )


def unadjudicated_decision_assessment(
    policy: DecisionPolicy,
    /,
    *,
    reason: str,
    used_fallback: bool = True,
) -> DecisionAssessment:
    """Record a no-call policy outcome without claiming the content was judged."""

    return DecisionAssessment(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        mode=policy.mode,
        coverage=DecisionCoverage.UNADJUDICATED,
        verdict=DecisionVerdict.UNKNOWN,
        source=DecisionAssessmentSource.NONE,
        reason=reason,
        used_fallback=used_fallback,
        usage=InferenceUsage(requests=0),
    )


def assess_local_rule(
    policy: DecisionPolicy,
    /,
    *,
    verdict: DecisionVerdict,
    reason: str,
) -> DecisionAssessment:
    """Record a deterministic policy rule that substantively judged the content."""

    return DecisionAssessment(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        mode=policy.mode,
        coverage=DecisionCoverage.ADJUDICATED,
        verdict=verdict,
        source=DecisionAssessmentSource.LOCAL_RULE,
        reason=reason,
        usage=InferenceUsage(requests=0),
    )


def emit_decision_observation(observation: DecisionObservation, /) -> None:
    """Log bounded policy outcome fields without turning logs into a content ledger."""

    log_safely(
        logger,
        logging.INFO,
        "Decision policy evaluated",
        extra={
            "event": "decision.observation",
            "consumer": observation.consumer,
            "policy_id": observation.policy_id,
            "policy_version": observation.policy_version,
            "mode": observation.mode.value,
            "coverage": observation.assessment.coverage.value,
            "verdict": observation.assessment.verdict.value,
            "final_action": observation.final_action,
            "fallback_reason": observation.fallback_reason,
            "candidate_count": observation.metadata.get("candidate_count", 0),
            "evidence_count": observation.metadata.get("evidence_count", 0),
        },
    )


def _normalized_outcome_mapping(
    policy: DecisionPolicy,
    override: Mapping[DecisionOutcome | str, DecisionVerdict | str] | None,
) -> dict[DecisionOutcome, DecisionVerdict]:
    raw = override if override is not None else policy.outcome_mapping
    normalized = dict(_DEFAULT_OUTCOME_MAPPING)
    for key, value in raw.items():
        outcome = key if isinstance(key, DecisionOutcome) else DecisionOutcome(str(key))
        verdict = value if isinstance(value, DecisionVerdict) else DecisionVerdict(str(value))
        normalized[outcome] = verdict
    normalized[DecisionOutcome.ABSTAIN] = DecisionVerdict.UNKNOWN
    return normalized


def _bounded_reason(value: str | None, *, default: str | None = None) -> str | None:
    if value is None:
        return default
    normalized = value.strip()
    return normalized[:4096] if normalized else default


__all__ = [
    "DecisionAssessment",
    "DecisionAssessmentSource",
    "DecisionCoverage",
    "DecisionFailurePolicy",
    "DecisionObservation",
    "DecisionPolicy",
    "DecisionPolicyMode",
    "DecisionPrivacyBoundary",
    "DecisionVerdict",
    "assess_decision_result",
    "assess_local_rule",
    "emit_decision_observation",
    "unadjudicated_decision_assessment",
]
