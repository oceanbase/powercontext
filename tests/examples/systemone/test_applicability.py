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
import hashlib
import json
from collections import deque

import pytest
from pydantic import ValidationError

from examples.systemone.applicability import (
    APPLICABILITY_VERSION,
    PREFERENCE_VERSION,
    ApplicabilityCandidate,
    DecisionApplicabilitySelector,
    SelectionRequest,
)
from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.runtime import DecisionOutcome, DecisionRequest, DecisionResult


class Decisions:
    policy_id = "test.exact-model-version"

    def __init__(self, *answers: DecisionOutcome | Exception) -> None:
        self.answers = deque(answers)
        self.requests: list[DecisionRequest] = []

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        self.requests.append(request)
        answer = self.answers.popleft()
        if isinstance(answer, Exception):
            raise answer
        return DecisionResult(
            outcome=answer,
            policy_id=self.policy_id,
            usage=InferenceUsage(requests=1, input_tokens=20),
            confidence=0.01 if answer is DecisionOutcome.YES else 0.99,
        )


def candidate(
    name: str, *, family: str = "skill", revision: int = 1, text: str | None = None
) -> ApplicabilityCandidate:
    content = text or name
    return ApplicabilityCandidate(
        address=ArtifactAddress(
            scope_id="project", artifact=ArtifactRef(family=family, artifact_id=name, revision=revision)
        ),
        content=content,
        content_digest="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
        package_digest="sha256:" + "a" * 64 if family == "skill" else None,
    )


def test_retains_complementary_experiences_and_ranks_only_applicable_skills() -> None:
    async def scenario() -> None:
        pool = (
            candidate("contract-generation", family="experience"),
            candidate("contract-validation", family="experience"),
            candidate("broad-http"),
            candidate("http-contract"),
            candidate("http-explanation"),
        )
        request = SelectionRequest(
            task="修改 HTTP contract", candidates=pool, environment=("Python 3.12 is available",)
        )
        model = Decisions(*(DecisionOutcome.YES,) * 4, DecisionOutcome.NO, DecisionOutcome.NO)
        result = await DecisionApplicabilitySelector(model, enabled=True).select(request)

        assert result.recommendations == (pool[0].address, pool[1].address, pool[3].address)
        assert result.pool_digest == request.pool_digest
        assert result.applicability_version == APPLICABILITY_VERSION
        assert result.preference_version == PREFERENCE_VERSION
        assert result.preferences[0].first == pool[2].address
        assert result.preferences[0].second == pool[3].address
        assert result.preferences[0].decision.outcome is DecisionOutcome.NO
        assert model.requests[-1].evidence == (pool[2].content, pool[3].content)
        assert all(item.policy_id == model.policy_id for item in result.assessments)
        assert json.loads(model.requests[0].subject)["environment"] == ["Python 3.12 is available"]

    asyncio.run(scenario())


@pytest.mark.parametrize("task", ["Explain the HTTP endpoint", "解释 HTTP 接口的用途", "Translate a poem"])
@pytest.mark.parametrize("outcome,status", [(DecisionOutcome.NO, "none"), (DecisionOutcome.ABSTAIN, "uncertain")])
def test_no_fit_and_unknown_conditions_are_distinct(task: str, outcome: DecisionOutcome, status: str) -> None:
    async def scenario() -> None:
        model = Decisions(outcome)
        result = await DecisionApplicabilitySelector(model, enabled=True).select(
            SelectionRequest(
                task=task,
                candidates=(candidate("http-contract"),),
            )
        )
        assert result.status == status
        assert result.recommendations == ()
        assert result.mode == "decision"
        assert result.used_fallback is False

    asyncio.run(scenario())


def test_default_off_and_backend_failure_preserve_the_identical_retrieval_baseline() -> None:
    async def scenario() -> None:
        request = SelectionRequest(task="Change the contract", candidates=(candidate("a"), candidate("b")))
        unused = Decisions()
        baseline = await DecisionApplicabilitySelector(unused).select(request)
        assert unused.requests == []  # The default-off contract promises no model requests.
        assert baseline.mode == "retrieval"
        failed = await DecisionApplicabilitySelector(Decisions(RuntimeError("secret")), enabled=True).select(request)
        absent = await DecisionApplicabilitySelector(enabled=True).select(request)
        for result in (failed, absent):
            assert result.recommendations == baseline.recommendations
            assert result.pool_digest == baseline.pool_digest
            assert result.used_fallback
            assert "secret" not in result.model_dump_json()
        assert failed.assessments[0].used_fallback

    asyncio.run(scenario())


def test_preference_abstention_uses_retrieval_order_without_confidence_ranking() -> None:
    async def scenario() -> None:
        first, second = candidate("first"), candidate("second")
        model = Decisions(DecisionOutcome.YES, DecisionOutcome.YES, DecisionOutcome.ABSTAIN)
        result = await DecisionApplicabilitySelector(model, enabled=True).select(
            SelectionRequest(
                task="HTTP contract",
                candidates=(first, second),
            )
        )
        assert result.recommendations == (first.address,)
        assert result.preferences[0].decision.outcome is DecisionOutcome.ABSTAIN
        assert result.used_fallback is False

    asyncio.run(scenario())


def test_complete_oversized_evidence_is_unknown_without_a_provider_call() -> None:
    async def scenario() -> None:
        model = Decisions()
        result = await DecisionApplicabilitySelector(model, enabled=True, max_evidence_bytes=32).select(
            SelectionRequest(
                task="HTTP contract",
                candidates=(candidate("large", text="完整证据" * 100),),
            )
        )
        assert result.status == "uncertain"
        assert result.assessments[0].reason == "complete evidence exceeds the selection input budget"
        assert result.recommendations == ()
        assert model.requests == []  # Oversized evidence must not incur an external request.

    asyncio.run(scenario())


def test_total_deadline_falls_back_and_caller_cancellation_propagates() -> None:
    class Blocked:
        policy_id = "blocked"

        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def scenario() -> None:
        request = SelectionRequest(task="HTTP contract", candidates=(candidate("first"),))
        timed_out = await DecisionApplicabilitySelector(Blocked(), enabled=True, timeout_seconds=0.01).select(request)
        assert timed_out.used_fallback
        assert timed_out.reason == "selection deadline exceeded"
        blocked = Blocked()
        task = asyncio.create_task(DecisionApplicabilitySelector(blocked, enabled=True).select(request))
        await blocked.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_pin_includes_exact_revision_content_environment_and_candidate_order() -> None:
    first, second = candidate("first"), candidate("second")
    original = SelectionRequest(task="HTTP contract", candidates=(first, second))
    variants = (
        SelectionRequest(task="HTTP contract", candidates=(second, first)),
        SelectionRequest(task="HTTP contract", candidates=(candidate("first", revision=2), second)),
        SelectionRequest(task="HTTP contract", candidates=(candidate("first", text="changed"), second)),
        SelectionRequest(task="HTTP contract", candidates=(first, second), environment=("Python 3.11",)),
    )
    assert len({original.pool_digest, *(item.pool_digest for item in variants)}) == 5
    with pytest.raises(ValidationError, match="unique"):
        SelectionRequest(task="HTTP contract", candidates=(first, first))


def test_empty_candidate_pool_explicitly_returns_none() -> None:
    result = asyncio.run(
        DecisionApplicabilitySelector(Decisions(), enabled=True).select(SelectionRequest(task="A task"))
    )
    assert result.status == "none"
    assert result.recommendations == ()


def test_preference_backend_failure_preserves_the_same_retrieval_baseline() -> None:
    async def scenario() -> None:
        request = SelectionRequest(task="Change HTTP contract", candidates=(candidate("broad"), candidate("specific")))
        baseline = await DecisionApplicabilitySelector().select(request)
        model = Decisions(DecisionOutcome.YES, DecisionOutcome.YES, RuntimeError("private provider details"))
        failed = await DecisionApplicabilitySelector(model, enabled=True).select(request)
        assert failed.used_fallback
        assert failed.recommendations == baseline.recommendations
        assert failed.pool_digest == baseline.pool_digest
        assert failed.reason == "preference backend failed"
        assert failed.preferences[0].decision.used_fallback
        assert "private provider details" not in failed.model_dump_json()

    asyncio.run(scenario())
