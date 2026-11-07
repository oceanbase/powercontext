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

"""Budget-aware shrinking uses real persisted windows without fragmenting Sources."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput
from powercontext.builtin.inference import GenerationResult
from powercontext.builtin.persistence.memory_windows import MemorySourceWindowRepository
from powercontext.builtin.scope import ScopeDraft

from powercontext_eval_evidence_admission.admission import token_count
from powercontext_eval_evidence_admission.manifests import load_suite
from powercontext_eval_evidence_admission.models import Case, Piece, SourceFixture
from powercontext_eval_evidence_admission.replay import replay_case
from powercontext_eval_evidence_admission.runner import INSTRUCTIONS as SELECTION_INSTRUCTIONS
from powercontext_eval_evidence_admission.v7.fidelity import TemplateOutput
from powercontext_eval_evidence_admission.v8.workflow import coverage
from powercontext_eval_evidence_admission.v10.protocol import pipeline
from powercontext_eval_evidence_admission.v11.window import prepare

pytestmark = pytest.mark.usefixtures("historical_window_context")


def policy(*, calls=16, multiplier=8):
    return SimpleNamespace(max_calls_per_sample=calls, total_input_budget_multiplier=multiplier)


@pytest.mark.parametrize(
    "dataset,case_id",
    [
        ("synthetic-v1", "many-source-overflow"),
        ("validation-v2", "validation-reordered-sources"),
    ],
)
@pytest.mark.parametrize("repetition", range(3))
def test_six_protocol_rejections_shrink_by_source_count_and_preserve_all_facts(dataset, case_id, repetition):
    async def scenario():
        suite = load_suite(experiment_version="v5", dataset=dataset)
        case = next(c for c in suite.cases if c.case_id == case_id)
        async with replay_case(case) as replay:
            result = await prepare(replay, 4096, 512, policy())
            assert result.status == "prepared" and result.admission.completeness == "complete"
            assert any(a["native_fits"] and not a["protocol_fits"] for a in result.attempts)
            assert all(r.cost["single_estimated_input_tokens"] <= 4096 for r in result.requests)
            assert all(token_count(r.encoded.value, SELECTION_INSTRUCTIONS) <= 4096 for r in result.requests)
            assert all(e.evidence_id.startswith("source:") for r in result.requests for e in r.encoded.value.evidence)
            projected = {e.source_ref.source_id: e for e in replay.value.evidence}
            assert all(
                e == projected[e.source_ref.source_id] for r in result.requests for e in r.encoded.value.evidence
            )
            score = coverage(case, [r.encoded for r in result.requests])
            assert len(score["same_request_claim_hashes"]) == len(case.required) == 8
            assert await replay.mutations() == (0, 0)

    asyncio.run(scenario())


def test_recovery_limit_survives_an_interrupted_preparation_and_clears_after_consumption():
    async def scenario():
        case = next(c for c in load_suite(experiment_version="v5").cases if c.case_id == "many-source-overflow")
        async with replay_case(case) as replay:
            windows = MemorySourceWindowRepository()
            limit = None

            async def interrupt(phase):
                nonlocal limit
                if phase == "window_retry":
                    async with replay.contexts.database.transaction() as connection:
                        _, limit = await windows.signal(connection, replay.scope_id, 0, len(case.sources))
                    raise InterruptedError("local interruption before any generation")

            with pytest.raises(InterruptedError):
                await prepare(replay, 4096, 512, policy(), on_phase=interrupt)
            assert limit is not None and 1 <= limit < len(case.sources)
            result = await prepare(replay, 4096, 512, policy())
            assert result.attempts[0]["through"] == limit
            assert result.status == "prepared"
            async with replay.contexts.database.transaction() as connection:
                _, cleared = await windows.signal(connection, replay.scope_id, 0, len(case.sources))
            assert cleared == len(case.sources)
            assert await replay.mutations() == (0, 0)

    asyncio.run(scenario())


def partial_case():
    return Case(
        case_id="prefix-then-oversized",
        categories=("synthetic",),
        completeness_ceiling="budget_dependent",
        reducibility="source_count",
        sources=(
            SourceFixture(source_id="first", pieces=(Piece(text="A durable synthetic fact."),)),
            SourceFixture(source_id="oversized", pieces=(Piece(text="Irrelevant synthetic fill.\n", repeat=3000),)),
            SourceFixture(source_id="deferred", pieces=(Piece(text="Another synthetic fact."),)),
        ),
    )


def test_one_source_floor_keeps_accepted_prefix_and_discloses_blocked_remainder():
    async def scenario():
        async with replay_case(partial_case()) as replay:
            result = await prepare(replay, 2048, 512, policy())
            assert result.status == "budget_rejected" and result.reason == "one_source_floor"
            assert result.consumed_through == 1 and len(result.requests) == 1
            assert result.admission.completeness == "bounded" and not result.admission.accepted
            assert result.admission.omissions and result.admission.metrics.omitted_bytes > 0
            assert result.attempts[-1]["after"] == 1 and result.attempts[-1]["through"] == 2
            assert await replay.mutations() == (0, 0)

    asyncio.run(scenario())


@pytest.mark.parametrize("cap", ["calls", "cumulative"])
def test_resource_rejection_retains_bounded_prefix_without_claiming_complete(cap):
    async def scenario():
        case = next(c for c in load_suite(experiment_version="v5").cases if c.case_id == "many-source-overflow")
        limits = policy(calls=1) if cap == "calls" else policy(multiplier=1)
        async with replay_case(case) as replay:
            result = await prepare(replay, 2048, 512, limits)
            assert result.status == "resource_budget_rejected" and result.reason == "resource_budget"
            assert len(result.requests) == 1 and result.admission.completeness == "bounded"
            assert not result.admission.accepted and result.admission.omissions
            assert (
                sum(r.cost["single_estimated_input_tokens"] for r in result.requests)
                <= 2048 * limits.total_input_budget_multiplier
            )
            assert await replay.mutations() == (0, 0)

    asyncio.run(scenario())


def test_cancel_after_reduction_propagates_without_memory_or_production_cursor():
    async def scenario():
        case = next(c for c in load_suite(experiment_version="v5").cases if c.case_id == "many-source-overflow")
        async with replay_case(case) as replay:

            async def cancel(phase):
                if phase == "window_retry":
                    asyncio.current_task().cancel()
                    await asyncio.sleep(0)

            task = asyncio.create_task(prepare(replay, 4096, 512, policy(), on_phase=cancel))
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await replay.mutations() == (0, 0)

    asyncio.run(scenario())


def test_accepted_partial_window_executes_through_pipeline_without_publishing_memory():
    async def scenario():
        class Empty:
            async def generate(self, value, /):
                return GenerationResult(output=TemplateOutput())

        async with replay_case(partial_case()) as replay:
            result = await prepare(replay, 2048, 512, policy())
            outputs = []
            for request in result.requests:
                outputs.extend(await pipeline(request, Empty()).extract(request.encoded.value))
            assert AtomicMemoryExtractionOutput(candidates=tuple(outputs)).candidates == ()
            assert result.status == "budget_rejected" and len(result.requests) == 1
            assert await replay.mutations() == (0, 0)

    asyncio.run(scenario())


def test_same_database_foreign_scope_is_not_included_in_window_or_support():
    async def scenario():
        case = next(c for c in load_suite(experiment_version="v5").cases if c.case_id == "many-source-overflow")
        async with replay_case(case) as replay:
            foreign = await replay.contexts.scopes.create(
                ScopeDraft(title="Foreign synthetic", summary="isolated", idempotency_key="foreign-fixture")
            )
            await replay.contexts.records.capture_source(
                foreign.scope_id, "content", "foreign-only", "Synthetic foreign fact.", {}
            )
            result = await prepare(replay, 4096, 512, policy())
            assert result.status == "prepared" and result.consumed_through == len(case.sources)
            assert all(
                e.source_ref.source_id != "foreign-only" for r in result.requests for e in r.encoded.value.evidence
            )
            assert await replay.mutations() == (0, 0)

    asyncio.run(scenario())
