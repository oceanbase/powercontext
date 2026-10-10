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

"""DecisionModel assembly and local evidence for controlled reranking experiments."""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any, cast

import httpx
from pydantic_ai.settings import ModelSettings

from powercontext.builtin.artifacts.memory import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUnavailableError
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from powercontext.builtin.runtime import DecisionModel, DecisionRequest, DecisionResult
from powercontext.builtin.runtime.config import InferenceConfig
from powercontext.builtin.runtime.decision_model import (
    DECISION_INSTRUCTIONS,
    DecisionInput,
    DecisionOutput,
    LLMDecisionModel,
)

from .jev import JevConfig, JevDecisionModel
from .jev_diagnostics import CURRENT_JEV_TRACE, JevTransportTrace
from .jev_transport import JevClientPool
from .memory_reranking import DecisionMemoryReranker, MemoryRerankText
from .models import open_model

CURRENT_DECISION_CASE: ContextVar[str | None] = ContextVar("locomo_plus_decision_case", default=None)
# A mutable, search-local sink also works when Runtime invokes the reranker in a child task.
CURRENT_RERANK_USAGE: ContextVar[list[dict[str, Any]] | None] = ContextVar("locomo_plus_rerank_usage", default=None)


class AuditedDecisionModel:
    """Retain every attempted decision without saving provider exceptions or headers."""

    def __init__(
        self,
        delegate: DecisionModel,
        path: Path,
        *,
        case_id: str | None = None,
        slots: asyncio.Semaphore | None = None,
    ) -> None:
        self.delegate = delegate
        self.policy_id = delegate.policy_id
        self.path = path
        self.case_id = case_id
        self.slots = slots
        self.sequence = 0
        self.records: list[dict[str, Any]] = []

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        started = perf_counter()
        self.sequence += 1
        row: dict[str, Any] = {
            "case_id": self.case_id,
            "sequence": self.sequence,
            "request": asdict(request),
            "policy_id": self.policy_id,
            "usage": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
        }
        acquired = False
        admitted = None
        transport_trace = JevTransportTrace() if isinstance(self.delegate, JevDecisionModel) else None
        trace_token = CURRENT_JEV_TRACE.set(transport_trace)
        try:
            if self.slots is not None:
                await self.slots.acquire()
                acquired = True
            admitted = perf_counter()
            row["queue_ms"] = (admitted - started) * 1000
            row["usage"] = {"requests": None, "input_tokens": None, "output_tokens": None}
            result = await self.delegate.evaluate(request)
            row.update({
                "outcome": result.outcome.value,
                "confidence": result.confidence,
                "rationale": result.rationale,
                "used_fallback": result.used_fallback,
                "usage": result.usage.model_dump(),
            })
        except (Exception, asyncio.CancelledError) as error:
            row["error"] = {"type": type(error).__name__}
            if (
                isinstance(self.delegate, JevDecisionModel)
                and isinstance(error, InferenceUnavailableError)
                and error.detail is not None
            ):
                # Jev constructs this detail from fixed HTTP error names or a numeric status.
                row["error"]["detail"] = error.detail
            raise
        else:
            return result
        finally:
            CURRENT_JEV_TRACE.reset(trace_token)
            if acquired and self.slots is not None:
                self.slots.release()
            row["provider_latency_ms"] = None if admitted is None else (perf_counter() - admitted) * 1000
            row["latency_ms"] = (perf_counter() - started) * 1000
            row["resolved_models"] = list(getattr(self.delegate, "resolved_models", ()))
            if transport_trace is not None:
                row["transport"] = transport_trace.snapshot()
                row["usage"]["requests"] = transport_trace.post_invocations
                if not transport_trace.post_invocations:
                    row["usage"].update(input_tokens=0, output_tokens=0)
            self.records.append(row)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")


class AuditedDecisionReranker:
    """Expose the exact coarse pool and decisions used by the public Runtime search."""

    supports_atomic_memory = True

    def __init__(
        self,
        model: AuditedDecisionModel,
        *,
        timeout_seconds: float,
        concurrency: int = 1,
        request_timeout_seconds: float | None = None,
        fill_to_limit: bool = False,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.concurrency = concurrency
        self.request_timeout_seconds = request_timeout_seconds
        self.fill_to_limit = fill_to_limit
        self.delegate = DecisionMemoryReranker(
            model,
            timeout_seconds=timeout_seconds,
            concurrency=concurrency,
            request_timeout_seconds=request_timeout_seconds,
            fill_to_limit=fill_to_limit,
        )
        self.policy_id = self.delegate.policy_id
        self.trace: dict[str, Any] = {}

    def for_case(self, case_id: str) -> AuditedDecisionReranker:
        """Share the provider client, not mutable per-query audit state."""
        return AuditedDecisionReranker(
            AuditedDecisionModel(self.model.delegate, self.model.path, case_id=case_id, slots=self.model.slots),
            timeout_seconds=self.timeout_seconds,
            concurrency=self.concurrency,
            request_timeout_seconds=self.request_timeout_seconds,
            fill_to_limit=self.fill_to_limit,
        )

    def reset(self) -> None:
        self.model.records.clear()
        self.model.sequence = 0
        self.trace = {"selected_ranks": [], "latency_ms": 0.0}

    async def rerank(self, query: str, candidates: tuple[MemoryRerankText, ...], limit: int, /) -> MemoryRerankDecision:
        started = perf_counter()
        first_sequence = self.model.sequence
        self.trace["candidates"] = [{"text": candidate.text} for candidate in candidates]
        self.trace["query"] = query
        self.trace["policy_id"] = self.policy_id
        self.trace["fill_to_limit"] = self.fill_to_limit
        try:
            result = await self.delegate.rerank(query, candidates, limit)
            rejected = {
                row["sequence"] - first_sequence
                for row in self.model.records
                if row["sequence"] > first_sequence and row.get("outcome") == "no"
            }
            supplemental = tuple(
                rank
                for rank in result.selected_ranks
                if self.fill_to_limit and not result.used_fallback and rank in rejected
            )
            self.trace.update({
                "selected_ranks": list(result.selected_ranks),
                "used_fallback": result.used_fallback,
                "retained_ranks": []
                if result.used_fallback
                else [rank for rank in result.selected_ranks if rank not in supplemental],
                "supplemental_ranks": list(supplemental),
                "fallback_ranks": list(result.selected_ranks) if result.used_fallback else [],
            })
            return result
        finally:
            self.trace["latency_ms"] = (perf_counter() - started) * 1000

    def snapshot(self) -> dict[str, Any]:
        usage = {}
        for key in ("requests", "input_tokens", "output_tokens"):
            values = [row["usage"].get(key) for row in self.model.records]
            usage[key] = None if None in values else sum(values)
        return {
            **self.trace,
            "decision_count": len(self.model.records),
            "requests": usage["requests"],
            "usage": usage,
            "decisions": sorted(self.model.records, key=lambda row: row["sequence"]),
        }


class ConcurrentDecisionReranker:
    """Isolate audit state for each concurrent Runtime search, including failed searches."""

    supports_atomic_memory = True

    def __init__(self, template: AuditedDecisionReranker, directory: Path) -> None:
        self.template = template
        self.directory = directory
        self.policy_id = template.policy_id

    async def rerank(self, query: str, candidates: tuple[MemoryRerankText, ...], limit: int, /) -> MemoryRerankDecision:
        case_id = CURRENT_DECISION_CASE.get()
        if case_id is None:
            raise ValueError("an audited benchmark search requires a case identity")  # noqa: TRY003
        reranker = self.template.for_case(case_id)
        reranker.reset()
        try:
            return await reranker.rerank(query, candidates, limit)
        finally:
            snapshot = reranker.snapshot()
            usage_sink = CURRENT_RERANK_USAGE.get()
            if usage_sink is not None:
                usage_sink.append(snapshot["usage"])
            with (self.directory / "decision-searches.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"case_id": case_id, **snapshot}, ensure_ascii=False) + "\n")


async def open_decision_reranker(
    backend: str,
    *,
    inference: InferenceConfig,
    jev: JevConfig | None,
    timeout_seconds: float,
    output_directory: Path,
    resources: AsyncExitStack,
    concurrency: int = 1,
    request_timeout_seconds: float | None = None,
    max_inflight: int = 10,
    fill_to_limit: bool = False,
    connect_attempts: int = 1,
) -> AuditedDecisionReranker:
    """Swap a backend while keeping the same decision consumer and failure policy."""
    if type(max_inflight) is not int or max_inflight < 1:
        raise ValueError("decision max inflight must be a positive integer")  # noqa: TRY003
    model: DecisionModel
    if backend == "jev":
        if jev is None:
            raise ValueError("Jev reranking requires Jev configuration")  # noqa: TRY003
        pool = await resources.enter_async_context(
            JevClientPool(
                max_clients=max_inflight,
                client_factory=lambda: httpx.AsyncClient(timeout=request_timeout_seconds or timeout_seconds),
            )
        )
        model = JevDecisionModel(jev, client_factory=pool.lease, connect_attempts=connect_attempts)
    elif backend == "llm":
        if inference.generation_model is None:
            raise ValueError("LLM decision reranking requires a generation model")  # noqa: TRY003
        provider = await open_model(inference.generation_model, inference, resources)
        generator = PydanticAIStructuredGenerator(
            model=provider,
            instructions=DECISION_INSTRUCTIONS,
            input_type=DecisionInput,
            output_type=DecisionOutput,
            limits=InferenceLimits(timeout_seconds=request_timeout_seconds or timeout_seconds, max_requests=1),
            model_settings=cast(
                ModelSettings,
                {**inference.generation_model_settings, "temperature": 0.0, "max_tokens": 512},
            ),
            name="locomo_plus_decision",
        )
        model = LLMDecisionModel(generator)
    else:
        raise ValueError("unsupported decision backend")  # noqa: TRY003
    return AuditedDecisionReranker(
        AuditedDecisionModel(model, output_directory / "decisions.jsonl", slots=asyncio.Semaphore(max_inflight)),
        timeout_seconds=timeout_seconds,
        concurrency=concurrency,
        request_timeout_seconds=request_timeout_seconds,
        fill_to_limit=fill_to_limit,
    )
