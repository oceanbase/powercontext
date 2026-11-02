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

"""Gate Memory writes on a decision-model evidence-sufficiency verdict.

The gate answers one narrow question: are the citations behind a pending Memory write strong
enough to record it now? It never decides content, only whether to proceed. A ``HOLD`` is a
visible refusal — it carries a structured code and a bounded reason back to its caller — and never
an automatic approval. The gate does not decide what happens to the refused window: the Runtime
reports the hold through ``MemoryFlushResult`` and consumes the window instead of retrying it. Any
backend failure degrades to ``ACCEPT`` so an unavailable judge can never block a write.
"""

from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Literal, cast

from powercontext._logging import log_safely
from powercontext.builtin.artifacts.memory.protocols import (
    MemoryWriteAssessment,
    MemoryWriteGate,
    MemoryWriteGateRequest,
    MemoryWriteRejectionCode,
    MemoryWriteVerdict,
)
from powercontext.builtin.runtime.decision_model import (
    DecisionKind,
    DecisionModel,
    DecisionOutcome,
    DecisionRequest,
    DecisionResult,
)
from powercontext.builtin.runtime.decision_policy import (
    DecisionAssessment,
    DecisionCoverage,
    DecisionFailurePolicy,
    DecisionObservation,
    DecisionPolicy,
    DecisionPolicyMode,
    DecisionPrivacyBoundary,
    DecisionVerdict,
    assess_decision_result,
    assess_local_rule,
    emit_decision_observation,
    unadjudicated_decision_assessment,
)
from powercontext.builtin.runtime.decision_projection import (
    DecisionProjectionReason,
    DecisionRequestProjectionSanitizer,
    InvalidDecisionProjectionError,
    apply_decision_request_sanitizer,
)

logger = logging.getLogger(__name__)

_GATE_QUESTION = (
    "Do the cited evidence items fail to support the proposed memory candidates, so that "
    "writing them now would record unsupported content?"
)
# A held write cites the same bounded vocabulary as evidence selection; the ceiling mirrors
# that selector so an over-wide citation set is reported instead of silently accepted.
_MAX_EVIDENCE_ITEMS = 32
_MAX_SUBJECT_LENGTH = 4000
_MAX_REASON_LENGTH = 512
_DEFAULT_REASON = "the cited evidence does not clearly support this memory write"
_MEMORY_WRITE_POLICY = DecisionPolicy(
    policy_id="memory.write.evidence_sufficiency.v1",
    decision_kind=DecisionKind.MEMORY_WRITE_GATE.value,
    version="1",
    consumer="memory_write_gate",
    mode=DecisionPolicyMode.ENFORCING,
    failure_policy=DecisionFailurePolicy.FAIL_OPEN,
    privacy_boundary=DecisionPrivacyBoundary.LOCAL_ONLY,
    local_rules=("candidate_batch_budget", "evidence_projection_budget"),
    question=_GATE_QUESTION,
    subject_selector="candidate_text",
    evidence_selector="candidate_citations",
    outcome_mapping={
        DecisionOutcome.YES.value: DecisionVerdict.DENY.value,
        DecisionOutcome.NO.value: DecisionVerdict.ALLOW.value,
        DecisionOutcome.ABSTAIN.value: DecisionVerdict.UNKNOWN.value,
    },
    promotion_criteria=(
        "labeled calibration and held-out evaluation",
        "measured fallback, latency, cost, and friction",
        "rollback path",
    ),
)


def memory_write_policy(
    *,
    hold_on: DecisionOutcome = DecisionOutcome.YES,
    threshold: float | None = None,
    mode: DecisionPolicyMode = DecisionPolicyMode.SHADOW,
    privacy_boundary: DecisionPrivacyBoundary = DecisionPrivacyBoundary.LOCAL_ONLY,
) -> DecisionPolicy:
    """Return the immutable Memory write policy with its configured runtime boundaries."""

    if hold_on is DecisionOutcome.ABSTAIN:
        raise ValueError("the hold direction cannot be abstain")  # noqa: TRY003
    pass_on = DecisionOutcome.NO if hold_on is DecisionOutcome.YES else DecisionOutcome.YES
    return _MEMORY_WRITE_POLICY.model_copy(
        update={
            "version": _policy_version(hold_on, threshold),
            "mode": mode,
            "privacy_boundary": privacy_boundary,
            "outcome_mapping": {
                hold_on.value: DecisionVerdict.DENY.value,
                pass_on.value: DecisionVerdict.ALLOW.value,
                DecisionOutcome.ABSTAIN.value: DecisionVerdict.UNKNOWN.value,
            },
        }
    )


def build_memory_write_gate(
    decision_model: DecisionModel | None,
    *,
    enabled: bool,
    hold_on: Literal["yes", "no"] = "yes",
    threshold: float | None = None,
    mode: DecisionPolicyMode = DecisionPolicyMode.SHADOW,
    privacy_boundary: DecisionPrivacyBoundary = DecisionPrivacyBoundary.LOCAL_ONLY,
    decision_request_sanitizer: DecisionRequestProjectionSanitizer | None = None,
) -> MemoryWriteGate | None:
    """Build the opt-in gate, or return ``None`` while it stays disabled.

    The direction is supplied by configuration and must be calibrated before the gate is
    enabled; a disabled gate and a missing backend both resolve to ``None``.
    """

    if not enabled or mode is DecisionPolicyMode.DISABLED:
        return None
    policy = memory_write_policy(
        hold_on=DecisionOutcome(hold_on),
        threshold=threshold,
        mode=mode,
        privacy_boundary=privacy_boundary,
    )
    if decision_model is None and privacy_boundary is not DecisionPrivacyBoundary.NO_EXTERNAL_CALL:
        return None
    return DecisionMemoryWriteGate(
        decision_model,
        hold_on=DecisionOutcome(hold_on),
        threshold=threshold,
        policy=policy,
        decision_request_sanitizer=decision_request_sanitizer,
    )


class DecisionMemoryWriteGate:
    """Map one decision-model verdict onto a Memory write verdict.

    ``hold_on`` is the calibrated outcome that means "the cited evidence is insufficient"; a
    clear verdict in that direction becomes ``HOLD``, the opposite becomes ``ACCEPT``, and an
    abstention or fallback passes the write through untouched. When a threshold is configured,
    a hold-direction verdict whose confidence falls below it is downgraded to ``FLAG`` — written,
    but annotated as uncertain.
    """

    def __init__(
        self,
        decision_model: DecisionModel | None,
        /,
        *,
        hold_on: DecisionOutcome | None = None,
        threshold: float | None = None,
        policy: DecisionPolicy | None = None,
        allow_model_evaluation: bool | None = None,
        decision_request_sanitizer: DecisionRequestProjectionSanitizer | None = None,
    ) -> None:
        if hold_on is DecisionOutcome.ABSTAIN:
            raise ValueError("the hold direction cannot be abstain")  # noqa: TRY003
        if policy is None:
            resolved_hold_on = DecisionOutcome.YES if hold_on is None else hold_on
            policy = memory_write_policy(
                hold_on=resolved_hold_on,
                threshold=threshold,
                mode=DecisionPolicyMode.ENFORCING,
                privacy_boundary=DecisionPrivacyBoundary.LOCAL_ONLY,
            )
        else:
            if policy.mode is DecisionPolicyMode.DISABLED:
                raise ValueError("a disabled policy cannot construct a memory write gate")  # noqa: TRY003
            resolved_hold_on = _policy_hold_on(policy)
            if hold_on is not None and hold_on is not resolved_hold_on:
                raise ValueError("the hold direction must match the supplied policy")  # noqa: TRY003
            _validate_memory_write_policy(policy, hold_on=resolved_hold_on, threshold=threshold)
        self._decision_model = decision_model
        self._hold_on = resolved_hold_on
        self._threshold = threshold
        self._policy = policy
        self._decision_request_sanitizer = decision_request_sanitizer
        if (
            self._policy.privacy_boundary is DecisionPrivacyBoundary.HOSTED_REDACTED
            and decision_model is not None
            and not bool(getattr(decision_model, "is_local_only", False))
            and decision_request_sanitizer is None
        ):
            # Composition raises the named configuration error. Direct construction remains
            # fail-open and simply refuses the hosted call below when no sanitizer is supplied.
            self._sanitizer_missing = True
        else:
            self._sanitizer_missing = False
        self._allow_model_evaluation = (
            decision_model is not None
            and (
                (
                    self._policy.privacy_boundary is DecisionPrivacyBoundary.LOCAL_ONLY
                    and bool(getattr(decision_model, "is_local_only", False))
                )
                or (
                    self._policy.privacy_boundary is DecisionPrivacyBoundary.HOSTED_REDACTED
                    and (
                        bool(getattr(decision_model, "is_local_only", False)) or decision_request_sanitizer is not None
                    )
                )
            )
            and allow_model_evaluation is not False
        )
        self.policy_id = "memory.write-gate.no-backend" if decision_model is None else decision_model.policy_id

    @property
    def mode(self) -> DecisionPolicyMode:
        """Expose the configured mode for composition and focused diagnostics."""

        return self._policy.mode

    async def assess(self, request: MemoryWriteGateRequest, /) -> MemoryWriteAssessment:
        """Judge one pending write and return a caller-visible verdict."""

        if _subject_exceeds_limit(request.candidates):
            policy_assessment = assess_local_rule(
                self._policy,
                verdict=DecisionVerdict.DENY,
                reason="candidate_batch_budget",
            )
            held = MemoryWriteAssessment(
                verdict=MemoryWriteVerdict.HOLD,
                policy_id=self.policy_id,
                code=_rejection_code(request),
                reason="the candidate batch exceeds the gate assessment budget",
            )
            assessment = self._mode_assessment(held)
            await self._observe(request, policy_assessment, assessment)
            self._log(assessment)
            return assessment
        if not self._allow_model_evaluation:
            reason = "projection_sanitizer_missing" if self._sanitizer_missing else "privacy_boundary"
            return await self._privacy_fallback(request, reason=reason)
        decision_request = DecisionRequest(
            decision_kind=DecisionKind.MEMORY_WRITE_GATE.value,
            question=_GATE_QUESTION,
            subject=_bounded_subject(request.candidates),
            evidence=request.evidence,
        )
        redaction_facts: tuple[str, ...] = ()
        projection_applied = False
        if self._policy.privacy_boundary is DecisionPrivacyBoundary.HOSTED_REDACTED and not bool(
            getattr(self._decision_model, "is_local_only", False)
        ):
            sanitizer = self._decision_request_sanitizer
            if sanitizer is None:
                return await self._privacy_fallback(
                    request,
                    reason=DecisionProjectionReason.SANITIZER_FAILED.value,
                )
            try:
                projection = apply_decision_request_sanitizer(sanitizer, decision_request)
            except InvalidDecisionProjectionError:
                return await self._privacy_fallback(
                    request,
                    reason=DecisionProjectionReason.SANITIZER_INVALID.value,
                )
            except Exception:
                return await self._privacy_fallback(
                    request,
                    reason=DecisionProjectionReason.SANITIZER_FAILED.value,
                )
            if not projection.is_permitted:
                return await self._privacy_fallback(
                    request,
                    reason=projection.reason_code or DecisionProjectionReason.SANITIZER_INVALID.value,
                )
            sanitized_request = projection.request
            if sanitized_request is None:
                return await self._privacy_fallback(
                    request,
                    reason=DecisionProjectionReason.SANITIZER_INVALID.value,
                )
            decision_request = sanitized_request
            redaction_facts = projection.redaction_facts
            projection_applied = True
        started = perf_counter()
        decision_model = cast(DecisionModel, self._decision_model)
        decision = await decision_model.evaluate(decision_request)
        latency_ms = (perf_counter() - started) * 1000
        assessment = self._map(request, decision)
        policy_assessment = _assess_memory_write_decision(
            decision, hold_on=self._hold_on, policy=self._policy
        ).model_copy(update={"latency_ms": latency_ms})
        await self._observe(
            request,
            policy_assessment,
            assessment,
            redaction_facts=redaction_facts,
            projection_applied=projection_applied,
        )
        self._log(assessment)
        return assessment

    async def _privacy_fallback(
        self,
        request: MemoryWriteGateRequest,
        *,
        reason: str,
        redaction_facts: tuple[str, ...] = (),
        projection_applied: bool = False,
    ) -> MemoryWriteAssessment:
        policy_assessment = unadjudicated_decision_assessment(self._policy, reason=reason)
        assessment = MemoryWriteAssessment(
            verdict=MemoryWriteVerdict.ACCEPT,
            policy_id=self.policy_id,
            used_fallback=True,
        )
        await self._observe(
            request,
            policy_assessment,
            assessment,
            redaction_facts=redaction_facts,
            projection_applied=projection_applied,
        )
        self._log(assessment)
        return assessment

    async def assess_preflight(
        self, request: MemoryWriteGateRequest, rejection: MemoryWriteAssessment, /
    ) -> MemoryWriteAssessment:
        """Apply this policy's mode and observation handling to a service-side evidence rejection."""

        policy_assessment = assess_local_rule(
            self._policy,
            verdict=DecisionVerdict.DENY,
            reason="evidence_projection_budget",
        )
        assessment = self._mode_assessment(rejection)
        await self._observe(request, policy_assessment, assessment)
        self._log(assessment)
        return assessment

    def _map(self, request: MemoryWriteGateRequest, decision: DecisionResult) -> MemoryWriteAssessment:
        assessment = _assess_memory_write_decision(decision, hold_on=self._hold_on, policy=self._policy)
        if assessment.coverage is DecisionCoverage.UNADJUDICATED:
            return MemoryWriteAssessment(
                verdict=MemoryWriteVerdict.ACCEPT,
                policy_id=decision.policy_id,
                used_fallback=assessment.used_fallback,
            )
        if self._policy.mode is DecisionPolicyMode.SHADOW:
            return MemoryWriteAssessment(
                verdict=MemoryWriteVerdict.ACCEPT,
                policy_id=decision.policy_id,
                used_fallback=assessment.used_fallback,
            )
        if assessment.verdict is DecisionVerdict.ALLOW:
            return MemoryWriteAssessment(verdict=MemoryWriteVerdict.ACCEPT, policy_id=decision.policy_id)
        reason = _bounded_reason(decision.rationale)
        if self._threshold is not None and decision.confidence is not None and decision.confidence < self._threshold:
            return MemoryWriteAssessment(verdict=MemoryWriteVerdict.FLAG, policy_id=decision.policy_id, reason=reason)
        return self._mode_assessment(
            MemoryWriteAssessment(
                verdict=MemoryWriteVerdict.HOLD,
                policy_id=decision.policy_id,
                code=_rejection_code(request),
                reason=reason,
            )
        )

    def _mode_assessment(self, assessment: MemoryWriteAssessment) -> MemoryWriteAssessment:
        if self._policy.mode is DecisionPolicyMode.SHADOW:
            return MemoryWriteAssessment(verdict=MemoryWriteVerdict.ACCEPT, policy_id=assessment.policy_id)
        if self._policy.mode is DecisionPolicyMode.ADVISORY and assessment.verdict is MemoryWriteVerdict.HOLD:
            return MemoryWriteAssessment(
                verdict=MemoryWriteVerdict.FLAG,
                policy_id=assessment.policy_id,
                reason=assessment.reason,
            )
        return assessment

    async def _observe(
        self,
        request: MemoryWriteGateRequest,
        policy_assessment: DecisionAssessment,
        assessment: MemoryWriteAssessment,
        *,
        redaction_facts: tuple[str, ...] = (),
        projection_applied: bool = False,
    ) -> None:
        observation = DecisionObservation(
            operation_id=request.operation_id or f"memory-write:{request.expected_revision or 'new'}",
            scope_id=request.scope_id,
            consumer=self._policy.consumer,
            policy_id=self._policy.policy_id,
            policy_version=self._policy.version,
            mode=self._policy.mode,
            subject_refs=request.subject_refs
            or tuple(f"candidate:{index}" for index in range(1, len(request.candidates) + 1)),
            evidence_refs=request.evidence_refs
            or tuple(f"evidence:{index}" for index in range(1, len(request.evidence) + 1)),
            privacy_boundary=self._policy.privacy_boundary,
            privacy_outcome=self._privacy_outcome(policy_assessment, projection_applied=projection_applied),
            provider_id=_model_identity(self._decision_model, "provider_id"),
            backend_model_id=_model_identity(self._decision_model, "backend_model_id"),
            model_policy_id=None if policy_assessment.source.value == "none" else self.policy_id,
            assessment=_safe_observation_assessment(policy_assessment),
            final_action=f"memory_write_{assessment.verdict.value}",
            fallback_reason=_safe_observation_reason(policy_assessment.reason)
            if policy_assessment.used_fallback
            else None,
            metadata={
                "candidate_count": len(request.candidates),
                "evidence_count": len(request.evidence),
                "redaction_facts": list(redaction_facts),
            },
        )
        if not request.defer_observation_emission:
            emit_decision_observation(observation)
        if request.observation_sink is None:
            return
        try:
            await request.observation_sink.record(observation)
        except Exception as error:
            log_safely(
                logger,
                logging.WARNING,
                "Decision observation sink failed",
                extra={
                    "event": "decision.observation_sink_failed",
                    "consumer": observation.consumer,
                    "policy_id": observation.policy_id,
                    "operation_id": observation.operation_id,
                    "error_type": type(error).__name__,
                },
            )

    def _privacy_outcome(self, assessment: DecisionAssessment, *, projection_applied: bool = False) -> str:
        if assessment.source.value == "local_rule":
            return "local_rule"
        if assessment.source.value == "decision_model":
            return (
                "hosted_redacted_model_called"
                if projection_applied and self._policy.privacy_boundary is DecisionPrivacyBoundary.HOSTED_REDACTED
                else "local_model_called"
            )
        if self._policy.privacy_boundary is DecisionPrivacyBoundary.HOSTED_REDACTED:
            if assessment.reason == "decision_model_fallback":
                return (
                    "local_model_fallback"
                    if bool(getattr(self._decision_model, "is_local_only", False))
                    else "hosted_redacted_model_fallback"
                )
            return {
                DecisionProjectionReason.MATERIAL_REMOVED.value: "hosted_redacted_withheld",
                DecisionProjectionReason.SANITIZER_FAILED.value: "hosted_redacted_sanitizer_failed",
                DecisionProjectionReason.SANITIZER_INVALID.value: "hosted_redacted_sanitizer_invalid",
                "projection_sanitizer_missing": "hosted_redacted_sanitizer_missing",
            }.get(assessment.reason, "hosted_redacted_no_call")
        if self._policy.privacy_boundary is DecisionPrivacyBoundary.NO_EXTERNAL_CALL:
            return "no_external_call"
        if not self._allow_model_evaluation:
            return "local_only_refused"
        if assessment.used_fallback:
            return "local_model_fallback"
        return "no_call"

    def _log(self, assessment: MemoryWriteAssessment) -> None:
        event = {
            MemoryWriteVerdict.HOLD: "memory.write-gate.hold",
            MemoryWriteVerdict.FLAG: "memory.write-gate.flag",
        }.get(assessment.verdict, "memory.write-gate.assess")
        log_safely(
            logger,
            logging.INFO,
            "Memory write gate assessed a pending write",
            extra={
                "event": event,
                "decision_kind": DecisionKind.MEMORY_WRITE_GATE.value,
                "policy_id": assessment.policy_id,
                "verdict": assessment.verdict.value,
                "code": None if assessment.code is None else assessment.code.value,
                "used_fallback": assessment.used_fallback,
            },
        )


def _rejection_code(request: MemoryWriteGateRequest) -> MemoryWriteRejectionCode:
    if not request.evidence:
        return MemoryWriteRejectionCode.NEEDS_EVIDENCE
    if len(request.evidence) > _MAX_EVIDENCE_ITEMS:
        return MemoryWriteRejectionCode.EVIDENCE_LIMIT_EXCEEDED
    return MemoryWriteRejectionCode.INSUFFICIENT_COVERAGE


def _assess_memory_write_decision(
    decision: DecisionResult,
    /,
    *,
    hold_on: DecisionOutcome,
    policy: DecisionPolicy = _MEMORY_WRITE_POLICY,
) -> DecisionAssessment:
    if hold_on is DecisionOutcome.ABSTAIN:
        raise ValueError("the hold direction cannot be abstain")  # noqa: TRY003
    pass_on = DecisionOutcome.NO if hold_on is DecisionOutcome.YES else DecisionOutcome.YES
    return assess_decision_result(
        policy,
        decision,
        outcome_mapping={
            hold_on: DecisionVerdict.DENY,
            pass_on: DecisionVerdict.ALLOW,
            DecisionOutcome.ABSTAIN: DecisionVerdict.UNKNOWN,
        },
    )


def _policy_version(hold_on: DecisionOutcome, threshold: float | None) -> str:
    """Bind every action-changing calibration input into the reviewable policy version."""

    threshold_value = "none" if threshold is None else format(threshold, ".17g")
    return f"1;hold_on={hold_on.value};threshold={threshold_value}"


def _policy_hold_on(policy: DecisionPolicy, /) -> DecisionOutcome:
    """Validate and recover the one decision outcome that the policy maps to a hold."""

    holding = [
        DecisionOutcome(outcome)
        for outcome, verdict in policy.outcome_mapping.items()
        if verdict == DecisionVerdict.DENY.value
    ]
    if len(holding) != 1 or holding[0] is DecisionOutcome.ABSTAIN:
        raise ValueError("the supplied policy must map exactly one decision outcome to deny")  # noqa: TRY003
    return holding[0]


def _validate_memory_write_policy(
    policy: DecisionPolicy,
    /,
    *,
    hold_on: DecisionOutcome,
    threshold: float | None,
) -> None:
    """Reject a supplied policy whenever the specialized gate would not execute it literally."""

    if policy.decision_kind != DecisionKind.MEMORY_WRITE_GATE.value:
        raise ValueError("the supplied policy must describe the memory write gate")  # noqa: TRY003
    if policy.consumer != "memory_write_gate":
        raise ValueError("the supplied policy must use the memory write gate consumer")  # noqa: TRY003
    if policy.failure_policy is not DecisionFailurePolicy.FAIL_OPEN:
        raise ValueError("the supplied policy must fail open")  # noqa: TRY003
    if policy.question != _GATE_QUESTION:
        raise ValueError("the supplied policy question must match the memory write gate")  # noqa: TRY003
    if policy.subject_selector != "candidate_text" or policy.evidence_selector != "candidate_citations":
        raise ValueError("the supplied policy selectors must match the memory write gate")  # noqa: TRY003
    pass_on = DecisionOutcome.NO if hold_on is DecisionOutcome.YES else DecisionOutcome.YES
    expected_mapping = {
        hold_on.value: DecisionVerdict.DENY.value,
        pass_on.value: DecisionVerdict.ALLOW.value,
        DecisionOutcome.ABSTAIN.value: DecisionVerdict.UNKNOWN.value,
    }
    if dict(policy.outcome_mapping) != expected_mapping:
        raise ValueError("the supplied policy outcome mapping must match the memory write gate")  # noqa: TRY003
    if policy.version != _policy_version(hold_on, threshold):
        raise ValueError("the operational parameters must match the supplied policy version")  # noqa: TRY003


def _model_identity(model: DecisionModel | None, attribute: str, /) -> str | None:
    value = None if model is None else getattr(model, attribute, None)
    return value if isinstance(value, str) and value.strip() else None


def _bounded_subject(candidates: tuple[str, ...]) -> str:
    return _candidate_subject(candidates)[:_MAX_SUBJECT_LENGTH]


def _subject_exceeds_limit(candidates: tuple[str, ...]) -> bool:
    return len(_candidate_subject(candidates)) > _MAX_SUBJECT_LENGTH


def _candidate_subject(candidates: tuple[str, ...]) -> str:
    return json.dumps(
        [{"candidate": index, "text": text} for index, text in enumerate(candidates, start=1)],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _bounded_reason(value: str | None) -> str:
    if value is None:
        return _DEFAULT_REASON
    normalized = value.strip()
    return normalized[:_MAX_REASON_LENGTH] if normalized else _DEFAULT_REASON


def _safe_observation_assessment(assessment: DecisionAssessment, /) -> DecisionAssessment:
    """Keep durable sidecars free of model-provided rationale text."""

    return assessment.model_copy(update={"reason": _safe_observation_reason(assessment.reason)})


def _safe_observation_reason(reason: str | None, /) -> str | None:
    """Map gate-internal explanations to bounded machine-readable audit codes."""

    return {
        "candidate_batch_budget": "candidate_batch_budget",
        "evidence_projection_budget": "evidence_projection_budget",
        "privacy_boundary": "privacy_boundary",
        "decision_model_fallback": "decision_model_fallback",
        "projection_sanitizer_missing": "projection_sanitizer_missing",
        "projection_material_removed": "projection_material_removed",
        "projection_sanitizer_failed": "projection_sanitizer_failed",
        "projection_sanitizer_invalid": "projection_sanitizer_invalid",
        "abstain": "abstain",
    }.get(reason, "decision_model_verdict" if reason is not None else None)


__all__ = [
    "DecisionMemoryWriteGate",
    "MemoryWriteAssessment",
    "MemoryWriteGate",
    "MemoryWriteGateRequest",
    "MemoryWriteRejectionCode",
    "MemoryWriteVerdict",
    "_assess_memory_write_decision",
    "build_memory_write_gate",
    "memory_write_policy",
]
