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
visible refusal — it carries a structured code and a bounded reason back to its caller — and
never a silent drop or an automatic approval. Any backend failure degrades to ``ACCEPT`` so an
unavailable judge can never block a write.
"""

from __future__ import annotations

import logging
from typing import Literal

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


def build_memory_write_gate(
    decision_model: DecisionModel | None,
    *,
    enabled: bool,
    hold_on: Literal["yes", "no"] = "yes",
    threshold: float | None = None,
) -> MemoryWriteGate | None:
    """Build the opt-in gate, or return ``None`` while it stays disabled.

    The direction is supplied by configuration and must be calibrated before the gate is
    enabled; a disabled gate and a missing backend both resolve to ``None``.
    """

    if not enabled or decision_model is None:
        return None
    return DecisionMemoryWriteGate(decision_model, hold_on=DecisionOutcome(hold_on), threshold=threshold)


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
        decision_model: DecisionModel,
        /,
        *,
        hold_on: DecisionOutcome = DecisionOutcome.YES,
        threshold: float | None = None,
    ) -> None:
        if hold_on is DecisionOutcome.ABSTAIN:
            raise ValueError("the hold direction cannot be abstain")  # noqa: TRY003
        self._decision_model = decision_model
        self._hold_on = hold_on
        self._threshold = threshold
        self.policy_id = decision_model.policy_id

    async def assess(self, request: MemoryWriteGateRequest, /) -> MemoryWriteAssessment:
        """Judge one pending write and return a caller-visible verdict."""

        if _subject_exceeds_limit(request.candidates):
            assessment = MemoryWriteAssessment(
                verdict=MemoryWriteVerdict.HOLD,
                policy_id=self.policy_id,
                code=_rejection_code(request),
                reason="the candidate batch exceeds the gate assessment budget",
            )
            self._log(assessment)
            return assessment
        decision = await self._decision_model.evaluate(
            DecisionRequest(
                decision_kind=DecisionKind.MEMORY_WRITE_GATE.value,
                question=_GATE_QUESTION,
                subject=_bounded_subject(request.candidates),
                evidence=request.evidence,
            )
        )
        assessment = self._map(request, decision)
        self._log(assessment)
        return assessment

    def _map(self, request: MemoryWriteGateRequest, decision: DecisionResult) -> MemoryWriteAssessment:
        if decision.used_fallback or decision.outcome is DecisionOutcome.ABSTAIN:
            return MemoryWriteAssessment(
                verdict=MemoryWriteVerdict.ACCEPT,
                policy_id=decision.policy_id,
                used_fallback=decision.used_fallback,
            )
        if decision.outcome is not self._hold_on:
            return MemoryWriteAssessment(verdict=MemoryWriteVerdict.ACCEPT, policy_id=decision.policy_id)
        reason = _bounded_reason(decision.rationale)
        if self._threshold is not None and decision.confidence is not None and decision.confidence < self._threshold:
            return MemoryWriteAssessment(verdict=MemoryWriteVerdict.FLAG, policy_id=decision.policy_id, reason=reason)
        return MemoryWriteAssessment(
            verdict=MemoryWriteVerdict.HOLD,
            policy_id=decision.policy_id,
            code=_rejection_code(request),
            reason=reason,
        )

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


def _bounded_subject(candidates: tuple[str, ...]) -> str:
    return "\n".join(candidates)[:_MAX_SUBJECT_LENGTH]


def _subject_exceeds_limit(candidates: tuple[str, ...]) -> bool:
    return len("\n".join(candidates)) > _MAX_SUBJECT_LENGTH


def _bounded_reason(value: str | None) -> str:
    if value is None:
        return _DEFAULT_REASON
    normalized = value.strip()
    return normalized[:_MAX_REASON_LENGTH] if normalized else _DEFAULT_REASON


__all__ = [
    "DecisionMemoryWriteGate",
    "MemoryWriteAssessment",
    "MemoryWriteGate",
    "MemoryWriteGateRequest",
    "MemoryWriteRejectionCode",
    "MemoryWriteVerdict",
    "build_memory_write_gate",
]
