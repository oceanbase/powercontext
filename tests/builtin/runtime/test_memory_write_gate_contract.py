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

import asyncio

import pytest

from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.runtime.decision_model import (
    DecisionKind,
    DecisionOutcome,
    DecisionRequest,
    DecisionResult,
    FailOpenDecisionModel,
)
from powercontext.builtin.runtime.memory_write_gate import (
    DecisionMemoryWriteGate,
    MemoryWriteGateRequest,
    MemoryWriteRejectionCode,
    MemoryWriteVerdict,
    build_memory_write_gate,
)

# A known-answer pair used to orient the hold direction. A durable preference should pass an
# evidence check; filler should not. The direction must be established from this pair before the
# gate is enabled, so a backend with the opposite polarity is caught instead of silently holding
# the wrong writes.
_PROBE_PREFERENCE = "Always run the full test suite before committing."
_PROBE_FILLER = "ok sounds good sure"


class _StaticDecisionModel:
    """A backend that always returns one prepared verdict."""

    policy_id = "powercontext.decision.static.v1"

    def __init__(self, result: DecisionResult) -> None:
        self._result = result

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        return self._result


class _RecordingDecisionModel(_StaticDecisionModel):
    def __init__(self, result: DecisionResult) -> None:
        super().__init__(result)
        self.requests: list[DecisionRequest] = []

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        self.requests.append(request)
        return await super().evaluate(request)


class _FailingDecisionModel:
    """A backend whose every evaluation raises."""

    policy_id = "powercontext.decision.failing.v1"

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        raise ValueError("backend unavailable")  # noqa: TRY003


class _PolarityBackend:
    """A backend that calls one known answer insufficient and the other sufficient."""

    policy_id = "powercontext.decision.polarity.v1"

    def __init__(self, *, insufficient_for: frozenset[str]) -> None:
        self._insufficient_for = insufficient_for

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        outcome = DecisionOutcome.YES if request.subject in self._insufficient_for else DecisionOutcome.NO
        return DecisionResult(outcome, self.policy_id, InferenceUsage(requests=1))


def _verdict(
    outcome: DecisionOutcome,
    *,
    confidence: float | None = None,
    rationale: str | None = None,
    used_fallback: bool = False,
) -> DecisionResult:
    return DecisionResult(
        outcome,
        "powercontext.decision.static.v1",
        InferenceUsage(requests=1),
        rationale=rationale,
        confidence=confidence,
        used_fallback=used_fallback,
    )


def test_decision_kind_values_are_stable() -> None:
    assert DecisionKind.MEMORY_WRITE_GATE.value == "memory.write-gate"


def test_gate_vocabulary_is_complete() -> None:
    assert {verdict.value for verdict in MemoryWriteVerdict} == {"accept", "flag", "hold"}
    assert {code.value for code in MemoryWriteRejectionCode} == {
        "needs_evidence",
        "evidence_limit_exceeded",
        "insufficient_coverage",
    }


def test_a_supporting_verdict_accepts_the_write() -> None:
    async def scenario() -> None:
        gate = DecisionMemoryWriteGate(_StaticDecisionModel(_verdict(DecisionOutcome.NO)), hold_on=DecisionOutcome.YES)

        assessment = await gate.assess(_request(evidence=("source:task:1",)))

        assert assessment.verdict is MemoryWriteVerdict.ACCEPT
        assert assessment.code is None
        assert assessment.reason is None

    asyncio.run(scenario())


def test_the_hold_direction_is_read_from_configuration() -> None:
    async def scenario() -> None:
        gate = DecisionMemoryWriteGate(_StaticDecisionModel(_verdict(DecisionOutcome.NO)), hold_on=DecisionOutcome.NO)

        assessment = await gate.assess(_request(evidence=("source:task:1",)))

        assert assessment.verdict is MemoryWriteVerdict.HOLD

    asyncio.run(scenario())


def test_a_hold_carries_a_structured_code_and_reason() -> None:
    async def scenario() -> None:
        gate = DecisionMemoryWriteGate(
            _StaticDecisionModel(_verdict(DecisionOutcome.YES, rationale="the citation is thin")),
            hold_on=DecisionOutcome.YES,
        )

        assessment = await gate.assess(_request(evidence=("source:task:1",)))

        assert assessment.verdict is MemoryWriteVerdict.HOLD
        assert assessment.code is MemoryWriteRejectionCode.INSUFFICIENT_COVERAGE
        assert assessment.reason == "the citation is thin"

    asyncio.run(scenario())


def test_a_hold_without_citations_reports_needs_evidence() -> None:
    async def scenario() -> None:
        gate = DecisionMemoryWriteGate(_StaticDecisionModel(_verdict(DecisionOutcome.YES)), hold_on=DecisionOutcome.YES)

        assessment = await gate.assess(_request(evidence=()))

        assert assessment.verdict is MemoryWriteVerdict.HOLD
        assert assessment.code is MemoryWriteRejectionCode.NEEDS_EVIDENCE
        assert assessment.reason is not None

    asyncio.run(scenario())


def test_a_hold_beyond_the_evidence_ceiling_reports_limit_exceeded() -> None:
    async def scenario() -> None:
        gate = DecisionMemoryWriteGate(_StaticDecisionModel(_verdict(DecisionOutcome.YES)), hold_on=DecisionOutcome.YES)

        assessment = await gate.assess(_request(evidence=tuple(f"source:task:{index}" for index in range(40))))

        assert assessment.verdict is MemoryWriteVerdict.HOLD
        assert assessment.code is MemoryWriteRejectionCode.EVIDENCE_LIMIT_EXCEEDED

    asyncio.run(scenario())


def test_an_oversized_candidate_batch_is_held_before_backend_assessment() -> None:
    async def scenario() -> None:
        backend = _RecordingDecisionModel(_verdict(DecisionOutcome.NO))
        gate = DecisionMemoryWriteGate(backend, hold_on=DecisionOutcome.YES)

        assessment = await gate.assess(
            _request(
                candidates=("x" * 4000, "UNASSESSED_TAIL"),
                evidence=("source:task:1\nsupporting text",),
            )
        )

        assert assessment.verdict is MemoryWriteVerdict.HOLD
        assert assessment.code is MemoryWriteRejectionCode.INSUFFICIENT_COVERAGE
        assert assessment.reason == "the candidate batch exceeds the gate assessment budget"
        assert backend.requests == []

    asyncio.run(scenario())


def test_a_low_confidence_hold_is_written_but_flagged() -> None:
    async def scenario() -> None:
        gate = DecisionMemoryWriteGate(
            _StaticDecisionModel(_verdict(DecisionOutcome.YES, confidence=0.2, rationale="uncertain")),
            hold_on=DecisionOutcome.YES,
            threshold=0.5,
        )

        assessment = await gate.assess(_request(evidence=("source:task:1",)))

        assert assessment.verdict is MemoryWriteVerdict.FLAG
        assert assessment.code is None
        assert assessment.reason == "uncertain"

    asyncio.run(scenario())


def test_a_confident_hold_still_holds_with_a_threshold() -> None:
    async def scenario() -> None:
        gate = DecisionMemoryWriteGate(
            _StaticDecisionModel(_verdict(DecisionOutcome.YES, confidence=0.9)),
            hold_on=DecisionOutcome.YES,
            threshold=0.5,
        )

        assessment = await gate.assess(_request(evidence=("source:task:1",)))

        assert assessment.verdict is MemoryWriteVerdict.HOLD

    asyncio.run(scenario())


def test_a_deliberate_abstention_passes_the_write_through() -> None:
    async def scenario() -> None:
        gate = DecisionMemoryWriteGate(
            _StaticDecisionModel(_verdict(DecisionOutcome.ABSTAIN)), hold_on=DecisionOutcome.YES
        )

        assessment = await gate.assess(_request(evidence=("source:task:1",)))

        assert assessment.verdict is MemoryWriteVerdict.ACCEPT
        assert assessment.used_fallback is False

    asyncio.run(scenario())


def test_a_failing_backend_is_fail_open() -> None:
    async def scenario() -> None:
        gate = DecisionMemoryWriteGate(FailOpenDecisionModel(_FailingDecisionModel()), hold_on=DecisionOutcome.YES)

        assessment = await gate.assess(_request(evidence=("source:task:1",)))

        assert assessment.verdict is MemoryWriteVerdict.ACCEPT
        assert assessment.code is None
        assert assessment.reason is None
        assert assessment.used_fallback is True

    asyncio.run(scenario())


def test_the_hold_direction_cannot_be_abstain() -> None:
    with pytest.raises(ValueError, match="hold direction"):
        DecisionMemoryWriteGate(_StaticDecisionModel(_verdict(DecisionOutcome.NO)), hold_on=DecisionOutcome.ABSTAIN)


def test_noul_polarity_probe_pairs_a_known_answer_with_the_direction() -> None:
    async def scenario() -> None:
        backend = _PolarityBackend(insufficient_for=frozenset({_PROBE_FILLER}))
        gate = DecisionMemoryWriteGate(backend, hold_on=DecisionOutcome.YES)

        preference = await gate.assess(_request(candidates=(_PROBE_PREFERENCE,)))
        filler = await gate.assess(_request(candidates=(_PROBE_FILLER,)))

        assert preference.verdict is MemoryWriteVerdict.ACCEPT
        assert filler.verdict is MemoryWriteVerdict.HOLD

    asyncio.run(scenario())


def test_noul_polarity_probe_exposes_a_contradictory_backend() -> None:
    async def scenario() -> None:
        # The same pair with the opposite polarity: the probe must fail rather than let a
        # reversed backend enable the gate against real preferences.
        backend = _PolarityBackend(insufficient_for=frozenset({_PROBE_PREFERENCE}))
        gate = DecisionMemoryWriteGate(backend, hold_on=DecisionOutcome.YES)

        preference = await gate.assess(_request(candidates=(_PROBE_PREFERENCE,)))

        assert preference.verdict is MemoryWriteVerdict.HOLD

    asyncio.run(scenario())


def test_the_gate_stays_disabled_without_an_enabled_flag() -> None:
    assert build_memory_write_gate(_StaticDecisionModel(_verdict(DecisionOutcome.YES)), enabled=False) is None


def test_the_gate_stays_disabled_without_a_backend() -> None:
    assert build_memory_write_gate(None, enabled=True) is None


def test_the_gate_is_built_from_configuration() -> None:
    gate = build_memory_write_gate(
        _StaticDecisionModel(_verdict(DecisionOutcome.NO)),
        enabled=True,
        hold_on="no",
        threshold=0.4,
    )

    assert isinstance(gate, DecisionMemoryWriteGate)
    assert gate.policy_id == "powercontext.decision.static.v1"


def _request(
    *,
    candidates: tuple[str, ...] = ("Remember the contract change.",),
    evidence: tuple[str, ...] = (),
) -> MemoryWriteGateRequest:
    return MemoryWriteGateRequest(candidates=candidates, evidence=evidence, expected_revision=1)
