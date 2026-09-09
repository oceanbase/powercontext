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
import json
from dataclasses import replace

import pytest
from sqlalchemy import select

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING, TopicMemoryContent
from powercontext.builtin.artifacts.topic_memory.generation import (
    MAX_TOPIC_MEMORY_STAGE_ITEMS,
    TOPIC_MEMORY_EVOLVE_INSTRUCTIONS,
    TOPIC_MEMORY_PROBE_INSTRUCTIONS,
    TOPIC_MEMORY_REDUCTION_INSTRUCTIONS,
    TOPIC_MEMORY_TEMPORARY_INSTRUCTIONS,
    TopicMemoryEvolveInput,
    TopicMemoryEvolveOutput,
    TopicMemoryGenerationError,
    TopicMemoryPlanItem,
    TopicMemoryPlannerOutput,
    TopicMemoryProbe,
    TopicMemoryProbeInput,
    TopicMemoryProbeOutput,
    TopicMemoryProposal,
    TopicMemoryReductionInput,
    TopicMemoryReductionOutput,
    TopicMemoryTemporaryInput,
    TopicMemoryTemporaryOutput,
    topic_memory_stage_fixed_prompt,
)
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import TOPIC_MEMORY_WORK_BUDGETS_TABLE
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingWorkerOutcome
from powercontext.builtin.runtime.topic_memory_processing import (
    TopicMemoryAtomicPublisher,
    TopicMemoryProcessor,
    TopicMemoryStageSet,
    TopicMemoryWindowSelector,
)
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER, ContentCapture
from tests.builtin.persistence.contract import SOURCE_ADAPTERS
from tests.builtin.runtime.test_topic_memory_processing import _assignment, _QueueGenerator, _repositories


def _proposal(detail: str) -> TopicMemoryProposal:
    return TopicMemoryProposal(
        content=TopicMemoryContent(title="Fragmented evidence", summary="Complete observations", detail=detail),
        evidence_ids=("evidence-0001",),
    )


class _FragmentStages:
    """Deterministic model outputs retaining a distinct marker for every fragment."""

    def __init__(self, *, repeated: bool = False, failure: str | None = None, padded: bool = False) -> None:
        self.repeated = repeated
        self.failure = failure
        self.padding = "shared-context " * 2_800 if padded else ""
        self.probes: list[TopicMemoryProbeInput] = []
        self.temporaries: list[TopicMemoryTemporaryInput] = []
        self.reductions: list[TopicMemoryReductionInput] = []

    async def generate(self, value, /):  # noqa: C901
        if isinstance(value, TopicMemoryProbeInput):
            self.probes.append(value)
            fragment = value.evidence[0].metadata
            # The ordinary tail remains independently processable as a NOOP.
            if "fragment_start" not in fragment:
                return GenerationResult(output=TopicMemoryProbeOutput())
            query = "repeated observation" if self.repeated else f"observation-{fragment['fragment_start']}"
            return GenerationResult(
                output=TopicMemoryProbeOutput(probes=(TopicMemoryProbe(query=query, evidence_ids=("evidence-0001",)),))
            )
        if isinstance(value, TopicMemoryTemporaryInput):
            self.temporaries.append(value)
            return GenerationResult(
                output=TopicMemoryTemporaryOutput(
                    proposals=(
                        _proposal(f"fact-{value.evidence[0].metadata['fragment_start']}\n{self.padding}".strip()),
                    )
                )
            )
        if isinstance(value, TopicMemoryReductionInput):
            self.reductions.append(value)
            inputs = value.probes or value.temporary
            indices = tuple(range(len(inputs)))
            probe = (
                TopicMemoryProbe(query=" ".join(item.query for item in value.probes), evidence_ids=("evidence-0001",))
                if value.probes
                else None
            )
            proposal = _proposal(_combined_detail(value.temporary)) if value.temporary else None
            # Fail in the temporary path, after successful Probe accumulation.
            if value.temporary:
                if self.failure == "unavailable":
                    raise RuntimeError("reduction unavailable")  # noqa: TRY003
                if self.failure == "coverage":
                    indices = indices[:-1]
                if self.failure == "evidence":
                    proposal = _proposal("bad").model_copy(update={"evidence_ids": ("unissued-evidence",)})
                if self.failure == "target":
                    proposal = _proposal("bad").model_copy(update={"candidate_id": "unissued-target"})
                if self.failure == "budget":
                    proposal = _proposal("界" * value.max_result_tokens)
            return GenerationResult(
                output=TopicMemoryReductionOutput(covered_indices=indices, probe=probe, temporary=proposal)
            )
        if isinstance(value, TopicMemoryEvolveInput):
            return GenerationResult(
                output=TopicMemoryEvolveOutput(proposal=_proposal(_combined_detail(value.temporary)))
            )
        return GenerationResult(
            output=TopicMemoryPlannerOutput(
                items=(TopicMemoryPlanItem(probe_ids=tuple(p.probe_id for p in value.probes)),)
            )
        )


def _combined_detail(items: tuple[TopicMemoryProposal, ...]) -> str:
    return "\n".join(dict.fromkeys(line for item in items for line in item.content.detail.splitlines()))


@pytest.mark.parametrize(
    "repeated,input_limit,characters",
    [(True, 100_000, 2_100_000), (False, 100_000, 2_100_000), (False, 20_000, 500_000)],
)
def test_fragment_reduction_publishes_all_material_then_processes_scope_tail(repeated, input_limit, characters) -> None:
    asyncio.run(_scenario(repeated=repeated, input_limit=input_limit, characters=characters))


@pytest.mark.parametrize("failure", ["coverage", "evidence", "target", "budget", "unavailable"])
def test_fragment_reduction_failure_preserves_cursor_and_can_retry(failure) -> None:
    asyncio.run(_scenario(failure=failure))


def test_fragment_reduction_enforces_token_budget_before_item_count_limit() -> None:
    asyncio.run(_scenario(padded=True))


async def _scenario(*, repeated=False, input_limit=100_000, characters=2_100_000, failure=None, padded=False) -> None:
    manager, profile, _, topics = await _repositories()
    sources = SourceRepository((*SOURCE_ADAPTERS, CONTENT_SOURCE_ADAPTER))
    fake = _FragmentStages(repeated=repeated, failure=failure, padded=padded)
    try:
        original = "界" * characters + "TAIL-OF-LARGE-SOURCE"
        async with profile.database.transaction() as connection:
            source = await sources.add(
                connection,
                "scope-a",
                await CONTENT_SOURCE_ADAPTER.resolve(
                    ContentCapture(source_id="large", content="body", metadata={"context": original})
                ),
            )
            tail = await sources.add(
                connection,
                "scope-a",
                await CONTENT_SOURCE_ADAPTER.resolve(ContentCapture(source_id="tail", content="ordinary tail")),
            )
            term = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "holder")
        estimator = character_token_estimator()
        stages = TopicMemoryStageSet(
            probe=fake,
            planner=fake,
            temporary=fake,
            evolver=fake,
            reducer=fake,
            global_evolver=_QueueGenerator(),
            reconciler=_QueueGenerator(),
            estimator=estimator,
            input_tokens_limit=input_limit,
            fixed_prompts={
                name: topic_memory_stage_fixed_prompt(instructions, inputs, outputs)
                for name, instructions, inputs, outputs in (
                    ("probe", TOPIC_MEMORY_PROBE_INSTRUCTIONS, TopicMemoryProbeInput, TopicMemoryProbeOutput),
                    (
                        "temporary",
                        TOPIC_MEMORY_TEMPORARY_INSTRUCTIONS,
                        TopicMemoryTemporaryInput,
                        TopicMemoryTemporaryOutput,
                    ),
                    ("evolve", TOPIC_MEMORY_EVOLVE_INSTRUCTIONS, TopicMemoryEvolveInput, TopicMemoryEvolveOutput),
                    (
                        "reduce",
                        TOPIC_MEMORY_REDUCTION_INSTRUCTIONS,
                        TopicMemoryReductionInput,
                        TopicMemoryReductionOutput,
                    ),
                )
            },
        )
        selector = TopicMemoryWindowSelector(
            profile.database, sources, estimator, context_window_tokens=input_limit * 5 // 4
        )
        selected = await selector.select("scope-a", 0, tail.journal_position)
        assert selected == source.journal_position
        processor = TopicMemoryProcessor(
            database=profile.database,
            sources=sources,
            topics=topics,
            stages=stages,
            publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics),
        )
        assignment = _assignment(term.fence("single-process"), through=selected)
        if failure:
            with pytest.raises((TopicMemoryGenerationError, RuntimeError), match="reduction"):
                await processor.process(assignment)
            async with profile.database.transaction() as connection:
                assert (
                    await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                    is None
                )
                assert await topics.browse_current(connection, "scope-a", limit=10) == ()
                budget = (await connection.execute(select(TOPIC_MEMORY_WORK_BUDGETS_TABLE))).mappings().one()
                # Every stage, including the failed reduction and Planner, is
                # precharged. A new processor must retain those reservations.
                assert budget["attempts"] == 1
                assert budget["requests"] == 2 * (len(fake.probes) + len(fake.temporaries) + len(fake.reductions) + 1)
                assert budget["tokens"] == budget["requests"] * (input_limit * 5 // 4)
            fake.failure = None
            fake.probes.clear()
            fake.temporaries.clear()
            fake.reductions.clear()
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=stages,
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics),
            )
        assert (await processor.process(assignment)).outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
        for requests, stage in ((fake.probes, "probe"), (fake.temporaries, "temporary")):
            assert len(requests) > 20
            assert all(stages.fits(request, stage) for request in requests)
            canonical = "".join(request.evidence[0].content for request in requests)
            assert json.loads(canonical) == {"content": "body", "metadata": {"context": original}}
        assert fake.reductions
        assert all(stages.fits(request, "reduce") for request in fake.reductions)
        assert all(
            len(request.probes or request.temporary) <= MAX_TOPIC_MEMORY_STAGE_ITEMS for request in fake.reductions
        )
        if padded:
            assert any(1 < len(request.temporary) < MAX_TOPIC_MEMORY_STAGE_ITEMS for request in fake.reductions)
        # Intermediate reductions must terminate; no model-controlled retry loop.
        assert len(fake.reductions) <= 2 * (len(fake.probes) + len(fake.temporaries))
        async with profile.database.transaction() as connection:
            hits = await topics.browse_current(connection, "scope-a", limit=10)
            assert len(hits) == 1
            published = await topics.get_exact(connection, "scope-a", hits[0].artifact_ref)
            assert published.topic.lineage.sources == (source.ref,)
            expected = {f"fact-{value.evidence[0].metadata['fragment_start']}" for value in fake.temporaries}
            if fake.padding:
                expected.add(fake.padding.strip())
            assert set(published.topic.content.detail.splitlines()) == expected
            cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
            assert cursor is not None and cursor.cursor.sequence == selected
        tail_assignment = replace(
            assignment,
            source_after=selected,
            source_through=tail.journal_position,
            wave_target=tail.journal_position,
            cursor_generation=cursor.generation,
        )
        assert (await processor.process(tail_assignment)).outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
        async with profile.database.transaction() as connection:
            cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
            assert cursor is not None and cursor.cursor.sequence == tail.journal_position
    finally:
        await manager.__aexit__(None, None, None)
