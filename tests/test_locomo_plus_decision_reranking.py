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

"""Decision reranking preserves evidence and exposes failed model decisions."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import cast

import pytest

from evaluation.memory.locomo_plus.memory_reranking import DecisionMemoryReranker
from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.inference import InferenceUnavailableError, InferenceUsage, InvalidInferenceOutputError
from powercontext.builtin.runtime import DecisionOutcome, DecisionRequest, DecisionResult


class _Decisions:
    policy_id = "test.decision.v1"

    def __init__(self, *outcomes: DecisionOutcome) -> None:
        self.results = [
            DecisionResult(
                outcome=outcome,
                policy_id=self.policy_id,
                usage=InferenceUsage(requests=1, input_tokens=10, output_tokens=2),
            )
            for outcome in outcomes
        ]
        self.requests: list[DecisionRequest] = []

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        self.requests.append(request)
        return self.results[len(self.requests) - 1]


class _WaitingDecision:
    policy_id = "test.waiting.v1"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()
        raise AssertionError("unreachable")


class _ControlledDecisions(_Decisions):
    def __init__(self, *outcomes: DecisionOutcome) -> None:
        super().__init__(*outcomes)
        self.started = [asyncio.Event() for _ in outcomes]
        self.release = [asyncio.Event() for _ in outcomes]
        self.finished = [asyncio.Event() for _ in outcomes]
        self.errors: dict[int, Exception] = {}
        self.cancelled: list[int] = []
        self.completion_order: list[int] = []
        self.active = 0
        self.peak_active = 0

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        index = len(self.requests)
        result = await super().evaluate(request)
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        self.started[index].set()
        try:
            await self.release[index].wait()
            if index in self.errors:
                raise self.errors[index]
            self.completion_order.append(index + 1)
        except asyncio.CancelledError:
            self.cancelled.append(index + 1)
            raise
        else:
            return result
        finally:
            self.active -= 1
            self.finished[index].set()


def _candidates(count: int) -> tuple[MemoryHit, ...]:
    return tuple(
        MemoryHit(
            memory_ref=ArtifactRef(family="atomic-memory", artifact_id="memory", revision=2),
            entry_id=f"entry-{rank}",
            entry_version_id=f"entry-{rank}-v2",
            text=f"Candidate {rank}",
            score=1 / rank,
            matched_by=("fts",),
        )
        for rank in range(1, count + 1)
    )


@pytest.mark.parametrize("fill_to_limit", [False, True])
def test_preserves_coarse_identity_and_stops_spending_after_enough_keeps(fill_to_limit: bool) -> None:
    async def scenario() -> None:
        backend = _Decisions(DecisionOutcome.NO, DecisionOutcome.YES, DecisionOutcome.ABSTAIN)
        candidates = _candidates(4)

        result = await DecisionMemoryReranker(backend, fill_to_limit=fill_to_limit).rerank(
            "Which evidence matters?", candidates, 2
        )

        assert result.selected_ranks == (2, 3)
        assert tuple(candidates[rank - 1].entry_version_id for rank in result.selected_ranks) == (
            "entry-2-v2",
            "entry-3-v2",
        )
        assert result.usage == InferenceUsage(requests=3, input_tokens=30, output_tokens=6)
        assert len(backend.requests) == 3
        assert result.used_fallback is False
        assert result.discarded_rank_count == 0
        assert len(backend.requests) == 3
        assert all(request.subject == "Which evidence matters?" for request in backend.requests)
        assert [request.evidence for request in backend.requests] == [(hit.text,) for hit in candidates[:3]]

    asyncio.run(scenario())


def test_decision_request_uses_query_as_subject_and_memory_as_evidence() -> None:
    async def scenario() -> None:
        query = "Which city does Alice live in?"
        memory = "Alice lives in Paris."
        candidate = _candidates(1)[0].model_copy(update={"text": memory})
        backend = _Decisions(DecisionOutcome.YES)
        reranker = DecisionMemoryReranker(backend)

        await reranker.rerank(query, (candidate,), 1)

        assert reranker.policy_id == "powercontext.memory.rerank.decision.v2"
        assert backend.requests == [
            DecisionRequest(
                decision_kind="memory.rerank",
                question="Does the supplied memory evidence contain information that helps answer the query in the subject?",
                subject=query,
                evidence=(memory,),
            )
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize("concurrency", [1, 2])
@pytest.mark.parametrize("fill_to_limit", [False, True])
def test_no_keeps_returns_marked_coarse_fallback_and_full_scan_usage(concurrency: int, fill_to_limit: bool) -> None:
    async def scenario() -> None:
        backend = _Decisions(*(DecisionOutcome.NO for _ in range(3)))
        backend.results[1] = replace(backend.results[1], usage=InferenceUsage(requests=1, input_tokens=5))

        result = await DecisionMemoryReranker(backend, concurrency=concurrency, fill_to_limit=fill_to_limit).rerank(
            "query", _candidates(3), 2
        )

        assert result.selected_ranks == (1, 2)
        assert result.used_fallback is True
        assert result.discarded_rank_count == 0
        assert result.usage == InferenceUsage(requests=3, input_tokens=25, output_tokens=None)
        assert len(backend.requests) == 3

    asyncio.run(scenario())


def test_sparse_selection_is_not_padded_with_rejected_candidates() -> None:
    async def scenario() -> None:
        backend = _Decisions(DecisionOutcome.NO, DecisionOutcome.YES, DecisionOutcome.NO)

        result = await DecisionMemoryReranker(backend).rerank("query", _candidates(3), 2)

        assert result.selected_ranks == (2,)
        assert result.used_fallback is False

    asyncio.run(scenario())


@pytest.mark.parametrize("concurrency", [1, 4])
@pytest.mark.parametrize("limit", [3, 6])
def test_opt_in_fill_appends_coarse_candidates_after_keeps_without_more_decisions(concurrency: int, limit: int) -> None:
    async def scenario() -> None:
        backend = _Decisions(
            DecisionOutcome.NO,
            DecisionOutcome.NO,
            DecisionOutcome.NO,
            DecisionOutcome.YES,
            DecisionOutcome.NO,
            DecisionOutcome.ABSTAIN,
        )
        candidates = _candidates(6)
        reranker = DecisionMemoryReranker(backend, concurrency=concurrency, fill_to_limit=True)

        result = await reranker.rerank("query", candidates, limit)

        assert reranker.policy_id == "powercontext.memory.rerank.decision.fill-to-limit.v1"
        assert result.selected_ranks == (4, 6, 1, 2, 3, 5)[:limit]
        assert result.used_fallback is False
        assert result.discarded_rank_count == 0
        assert len(backend.requests) == 6
        assert result.usage == InferenceUsage(requests=6, input_tokens=60, output_tokens=12)
        assert candidates == _candidates(6)

    asyncio.run(scenario())


@pytest.mark.parametrize("fill_to_limit", [False, True])
def test_degraded_abstention_fails_instead_of_returning_partial_or_coarse_results(fill_to_limit: bool) -> None:
    async def scenario() -> None:
        backend = _Decisions(DecisionOutcome.YES, DecisionOutcome.ABSTAIN)
        backend.results[1] = replace(backend.results[1], used_fallback=True)

        with pytest.raises(InferenceUnavailableError, match="decision backend degraded"):
            await DecisionMemoryReranker(backend, fill_to_limit=fill_to_limit).rerank("query", _candidates(2), 2)

    asyncio.run(scenario())


def test_backend_failure_propagates() -> None:
    class UnavailableDecision:
        policy_id = "test.unavailable.v1"

        async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
            raise ConnectionError("decision backend unavailable")  # noqa: TRY003

    async def scenario() -> None:
        with pytest.raises(ConnectionError):
            await DecisionMemoryReranker(UnavailableDecision()).rerank("query", _candidates(1), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("fill_to_limit", [False, True])
def test_timeout_terminates_the_decision_backend(fill_to_limit: bool) -> None:
    async def scenario() -> None:
        backend = _WaitingDecision()
        reranker = DecisionMemoryReranker(backend, timeout_seconds=0.01, fill_to_limit=fill_to_limit)

        with pytest.raises(TimeoutError):
            await reranker.rerank("query", _candidates(1), 1)

        assert backend.started.is_set()
        assert backend.cancelled.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("fill_to_limit", [False, True])
def test_caller_cancellation_propagates_to_the_decision_backend(fill_to_limit: bool) -> None:
    async def scenario() -> None:
        backend = _WaitingDecision()
        task = asyncio.create_task(
            DecisionMemoryReranker(backend, fill_to_limit=fill_to_limit).rerank("query", _candidates(1), 1)
        )
        await backend.started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert backend.cancelled.is_set()

    asyncio.run(scenario())


def test_concurrent_completion_preserves_coarse_order_and_accounts_for_unselected_calls() -> None:
    async def scenario() -> None:
        backend = _ControlledDecisions(*(DecisionOutcome.YES for _ in range(4)))
        backend.results[1] = replace(backend.results[1], outcome=DecisionOutcome.ABSTAIN)
        backend.results[2] = replace(backend.results[2], usage=InferenceUsage(requests=2, output_tokens=5))
        task = asyncio.create_task(DecisionMemoryReranker(backend, concurrency=3).rerank("query", _candidates(4), 2))
        for started in backend.started[:3]:
            await started.wait()
        for index in (2, 1, 0):
            backend.release[index].set()
            await backend.finished[index].wait()

        result = await task

        assert backend.completion_order == [3, 2, 1]
        assert backend.peak_active == 3
        assert backend.active == 0
        assert not backend.started[3].is_set()
        assert result.selected_ranks == (1, 2)
        assert len(backend.requests) == 3
        assert result.usage == InferenceUsage(requests=4, input_tokens=None, output_tokens=9)

    asyncio.run(scenario())


def test_concurrency_bound_holds_across_batches_and_stops_after_final_batch() -> None:
    async def scenario() -> None:
        backend = _ControlledDecisions(
            DecisionOutcome.NO,
            DecisionOutcome.NO,
            DecisionOutcome.NO,
            DecisionOutcome.YES,
            DecisionOutcome.YES,
            DecisionOutcome.YES,
            DecisionOutcome.YES,
        )
        task = asyncio.create_task(DecisionMemoryReranker(backend, concurrency=2).rerank("query", _candidates(7), 2))
        for offset in (0, 2, 4):
            await backend.started[offset].wait()
            await backend.started[offset + 1].wait()
            assert len(backend.requests) == offset + 2
            assert backend.active == 2
            backend.release[offset + 1].set()
            await backend.finished[offset + 1].wait()
            assert len(backend.requests) == offset + 2
            backend.release[offset].set()

        result = await task

        assert backend.peak_active == 2
        assert backend.active == 0
        assert len(backend.requests) == 6
        assert result.selected_ranks == (4, 5)
        assert result.usage == InferenceUsage(requests=6, input_tokens=60, output_tokens=12)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["unavailable", "degraded", "invalid"])
@pytest.mark.parametrize("fill_to_limit", [False, True])
def test_failure_in_tail_of_batch_is_not_hidden_by_enough_keeps(failure: str, fill_to_limit: bool) -> None:
    async def scenario() -> None:
        backend = _ControlledDecisions(*(DecisionOutcome.YES for _ in range(4)))
        expected_error: type[Exception]
        if failure == "unavailable":
            backend.errors[1] = ConnectionError("decision backend unavailable")
            expected_error = ConnectionError
        elif failure == "degraded":
            backend.results[1] = replace(backend.results[1], used_fallback=True)
            expected_error = InferenceUnavailableError
        else:
            backend.results[1] = replace(backend.results[1], outcome=cast(DecisionOutcome, "invalid"))
            expected_error = InvalidInferenceOutputError
        task = asyncio.create_task(
            DecisionMemoryReranker(backend, concurrency=3, fill_to_limit=fill_to_limit).rerank(
                "query", _candidates(4), 1
            )
        )
        for started in backend.started[:3]:
            await started.wait()
        backend.release[0].set()
        await backend.finished[0].wait()
        backend.release[1].set()

        with pytest.raises(expected_error):
            await task

        assert backend.cancelled == [3]
        assert backend.active == 0
        assert not backend.started[3].is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("deadline", ["whole-search", "per-request", "caller"])
def test_concurrent_deadlines_and_caller_cancellation_drain_all_started_tasks(deadline: str) -> None:
    async def scenario() -> None:
        backend = _ControlledDecisions(*(DecisionOutcome.YES for _ in range(4)))
        reranker = DecisionMemoryReranker(
            backend,
            concurrency=3,
            timeout_seconds=0.1 if deadline == "whole-search" else 10,
            request_timeout_seconds=0.1 if deadline == "per-request" else None,
        )
        task = asyncio.create_task(reranker.rerank("query", _candidates(4), 1))
        for started in backend.started[:3]:
            await started.wait()
        backend.release[0].set()
        await backend.finished[0].wait()
        if deadline == "caller":
            task.cancel()

        with pytest.raises(asyncio.CancelledError if deadline == "caller" else TimeoutError):
            await task

        assert set(backend.cancelled) == {2, 3}
        assert backend.active == 0
        assert not backend.started[3].is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancellation", ["deadline", "caller"])
def test_cancellation_waits_for_asynchronous_backend_cleanup(cancellation: str) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        slow_cleanup_started = asyncio.Event()
        finish_cleanup = asyncio.Event()
        cleaned: list[str] = []
        requests: list[str] = []

        class CleaningDecision:
            policy_id = "test.async-cleanup.v1"

            async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
                requests.append(request.evidence[0])
                if len(requests) == 2:
                    started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    if request.evidence[0] == "Candidate 2":
                        slow_cleanup_started.set()
                        await finish_cleanup.wait()
                    cleaned.append(request.evidence[0])
                raise AssertionError("unreachable")

        reranker = DecisionMemoryReranker(
            CleaningDecision(), concurrency=2, timeout_seconds=0.01 if cancellation == "deadline" else 10
        )
        task = asyncio.create_task(reranker.rerank("query", _candidates(2), 1))
        await started.wait()
        if cancellation == "caller":
            task.cancel()
        await slow_cleanup_started.wait()
        await asyncio.sleep(0.01)
        assert not task.done()
        finish_cleanup.set()

        with pytest.raises(TimeoutError if cancellation == "deadline" else asyncio.CancelledError):
            await task

        assert cleaned == ["Candidate 1", "Candidate 2"]

    asyncio.run(scenario())


def test_caller_can_cancel_again_while_backend_cleanup_is_pending() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        cleaning = asyncio.Event()
        interrupted = asyncio.Event()

        class CleaningDecision:
            policy_id = "test.caller-controls-cleanup.v1"

            async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        interrupted.set()
                        raise
                raise AssertionError("unreachable")

        task = asyncio.create_task(DecisionMemoryReranker(CleaningDecision()).rerank("query", _candidates(1), 1))
        await started.wait()
        task.cancel()
        await cleaning.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert interrupted.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize(("candidate_count", "limit"), [(0, 1), (1, 0), (1, 2)])
def test_invalid_selection_bounds_fail_before_spending(candidate_count: int, limit: int) -> None:
    async def scenario() -> None:
        backend = _Decisions()

        with pytest.raises(ValueError, match="rerank limit"):
            await DecisionMemoryReranker(backend).rerank("query", _candidates(candidate_count), limit)

        assert backend.requests == []

    asyncio.run(scenario())


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValueError, match="rerank timeout"):
        DecisionMemoryReranker(_Decisions(), timeout_seconds=timeout)


@pytest.mark.parametrize("concurrency", [0, -1, 1.5, True])
def test_invalid_concurrency_is_rejected(concurrency: int) -> None:
    with pytest.raises(ValueError, match="rerank concurrency"):
        DecisionMemoryReranker(_Decisions(), concurrency=concurrency)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_request_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValueError, match="rerank request timeout"):
        DecisionMemoryReranker(_Decisions(), request_timeout_seconds=timeout)
