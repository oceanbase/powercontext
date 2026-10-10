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

"""Benchmark-only Memory selection through bounded, provider-neutral decisions."""

from __future__ import annotations

import asyncio
import math
from itertools import islice

from powercontext.builtin.artifacts.memory import MemoryRerankDecision
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankText
from powercontext.builtin.inference import InferenceUnavailableError, InferenceUsage, InvalidInferenceOutputError
from powercontext.builtin.runtime.decision_model import (
    DecisionModel,
    DecisionOutcome,
    DecisionRequest,
    DecisionResult,
)

MEMORY_RERANK_DECISION_KIND = "memory.rerank"


class DecisionMemoryReranker:
    """Keep relevant coarse candidates with one decision per inspected entry.

    Inject the decision backend directly. A healthy abstention preserves evidence,
    while backend failures, degraded results, and the whole-search deadline fail
    the search. Other Runtime consumers may independently use a fail-open handle.

    Batches inspect at most ``concurrency`` entries at once. Every batch is fully
    accounted for before selecting entries in their original coarse order. When
    enough entries are retained, this can spend at most ``concurrency - 1`` more
    calls than a sequential scan. A failure anywhere in a batch fails the search.

    Opt-in ``fill_to_limit`` appends unselected coarse candidates when a healthy
    scan retains fewer than ``limit`` entries. These supplements follow all kept
    entries, in their original coarse order, without additional decisions. Empty
    selections still use the existing marked coarse fallback.
    """

    policy_id = "powercontext.memory.rerank.decision.v2"
    supports_atomic_memory = True
    fill_policy_id = "powercontext.memory.rerank.decision.fill-to-limit.v1"

    def __init__(
        self,
        decision_model: DecisionModel,
        /,
        *,
        timeout_seconds: float = 30.0,
        concurrency: int = 1,
        request_timeout_seconds: float | None = None,
        fill_to_limit: bool = False,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("rerank timeout must be finite and positive")  # noqa: TRY003
        if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
            raise ValueError("rerank concurrency must be a positive integer")  # noqa: TRY003
        if request_timeout_seconds is not None and (
            not math.isfinite(request_timeout_seconds) or request_timeout_seconds <= 0
        ):
            raise ValueError("rerank request timeout must be finite and positive")  # noqa: TRY003
        if not isinstance(fill_to_limit, bool):
            raise TypeError("rerank fill_to_limit must be a boolean")  # noqa: TRY003
        self._decision_model = decision_model
        self._timeout_seconds = timeout_seconds
        self._concurrency = concurrency
        self._request_timeout_seconds = request_timeout_seconds
        self._fill_to_limit = fill_to_limit
        if fill_to_limit:
            self.policy_id = self.fill_policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryRerankText, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Return original ranks and usage for the decisions actually issued."""

        if not candidates or not 1 <= limit <= len(candidates):
            raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003
        selected: list[int] = []
        usages: list[InferenceUsage] = []
        async with asyncio.timeout(self._timeout_seconds):
            for offset in range(0, len(candidates), self._concurrency):
                tasks = [
                    asyncio.create_task(self._evaluate(query, candidate))
                    for candidate in candidates[offset : offset + self._concurrency]
                ]
                try:
                    decisions = await asyncio.gather(*tasks)
                finally:
                    # gather propagates failures without cancelling siblings.
                    for task in tasks:
                        if not task.done() and not task.cancelling():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                for rank, decision in enumerate(decisions, start=offset + 1):
                    usages.append(decision.usage)
                    if len(selected) < limit and decision.outcome in (DecisionOutcome.YES, DecisionOutcome.ABSTAIN):
                        selected.append(rank)
                if len(selected) == limit:
                    break
        used_fallback = not selected
        if selected and len(selected) < limit and self._fill_to_limit:
            retained = set(selected)
            coarse_remainder = (rank for rank in range(1, len(candidates) + 1) if rank not in retained)
            selected.extend(islice(coarse_remainder, limit - len(selected)))
        return MemoryRerankDecision(
            selected_ranks=tuple(selected) or tuple(range(1, limit + 1)),
            usage=InferenceUsage(
                requests=sum(usage.requests for usage in usages),
                input_tokens=None
                if any(usage.input_tokens is None for usage in usages)
                else sum(usage.input_tokens or 0 for usage in usages),
                output_tokens=None
                if any(usage.output_tokens is None for usage in usages)
                else sum(usage.output_tokens or 0 for usage in usages),
            ),
            used_fallback=used_fallback,
        )

    async def _evaluate(self, query: str, candidate: MemoryRerankText) -> DecisionResult:
        async with asyncio.timeout(self._request_timeout_seconds):
            decision = await self._decision_model.evaluate(
                DecisionRequest(
                    decision_kind=MEMORY_RERANK_DECISION_KIND,
                    question="Does the supplied memory evidence contain information that helps answer the query in the subject?",
                    subject=query,
                    evidence=(candidate.text,),
                )
            )
        if decision.used_fallback:
            raise InferenceUnavailableError("memory-rerank", "decision backend degraded")
        if decision.outcome not in (DecisionOutcome.YES, DecisionOutcome.NO, DecisionOutcome.ABSTAIN):
            raise InvalidInferenceOutputError("memory-rerank", "invalid decision outcome")
        return decision


__all__ = ["DecisionMemoryReranker"]
