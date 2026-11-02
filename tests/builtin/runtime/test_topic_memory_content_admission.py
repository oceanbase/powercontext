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
from dataclasses import replace

import pytest

from powercontext.builtin.artifacts.topic_memory import (
    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
    TopicMemoryContent,
    TopicMemoryDraft,
    prepare_topic_memory_projection,
)
from powercontext.builtin.artifacts.topic_memory.generation import (
    TopicMemoryEvolveOutput,
    TopicMemoryGenerationError,
    TopicMemoryGlobalOutput,
    TopicMemoryPlanItem,
    TopicMemoryPlannerOutput,
    TopicMemoryProbe,
    TopicMemoryProbeOutput,
    TopicMemoryProposal,
    TopicMemoryReconcileOutput,
    TopicMemoryReductionOutput,
    TopicMemoryTemporaryOutput,
)
from powercontext.builtin.inference import TokenEstimator, character_token_estimator
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.topic_memory_budget import require_topic_memory_work_available
from powercontext.builtin.runtime.topic_memory_processing import TopicMemoryAtomicPublisher, TopicMemoryProcessor
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER, ContentCapture
from powercontext.sources import SourceMaterialization
from tests.builtin.persistence.contract import SOURCE_ADAPTERS, NoteSource
from tests.builtin.runtime.test_topic_memory_processing import (
    _assignment,
    _budget_row,
    _QueueGenerator,
    _repositories,
    _stages,
)


def _placeholder():
    return TopicMemoryContent(
        title="T-19",
        summary="S-19 coordinated without merging historical identities.",
        detail="D-19 coordinated detail preserving distinct historical identity.",
    )


@pytest.mark.parametrize("path", ["create", "update", "reconcile", "evolve", "mixed"])
@pytest.mark.parametrize("formatted", [False, True])
def test_invalid_content_preserves_topics_cursor_and_search(path, formatted):
    async def scenario():
        manager, profile, sources, topics = await _repositories()
        try:
            leases = ArtifactProcessingLeaseRepository()
            valid = TopicMemoryContent(
                title="zircon release",
                summary="Zircon deployment uses SQLite.",
                detail="Run the deployment checks before release.",
            )
            async with profile.database.transaction() as connection:
                old = await topics.publish_create(
                    connection,
                    "scope-a",
                    "topic-old",
                    TopicMemoryDraft(content=valid),
                    prepare_topic_memory_projection(valid),
                )
                await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(
                        name="target",
                        materialization=SourceMaterialization.CAPTURED,
                        body="在dashboard中检查是否符合预期",
                    ),
                )
                term = await leases.start_single_process_term(connection, "holder")
            placeholder = _placeholder()
            if formatted:
                placeholder = TopicMemoryContent(**{
                    key: f"**{value.rstrip('.')}!**" if key != "title" else f"**{value}**"
                    for key, value in placeholder.model_dump().items()
                })
            bad = TopicMemoryProposal(
                content=placeholder,
                evidence_ids=("evidence-0001",),
                candidate_id="candidate-0001" if path == "update" else None,
            )
            provisional = TopicMemoryProposal(content=valid, evidence_ids=("evidence-0001",))
            outputs = (provisional,) if path == "reconcile" else (provisional, bad) if path == "mixed" else (bad,)
            stages = _stages(
                probe=TopicMemoryProbeOutput(
                    probes=(TopicMemoryProbe(query="zircon", evidence_ids=("evidence-0001",)),)
                ),
                global_output=TopicMemoryGlobalOutput(ambiguous=path == "evolve", proposals=outputs),
                planner=TopicMemoryPlannerOutput(items=(TopicMemoryPlanItem(probe_ids=("probe-0001",)),)),
                evolve=(TopicMemoryEvolveOutput(proposal=bad),),
                reconcile=(
                    TopicMemoryReconcileOutput(
                        proposals=(
                            bad.model_copy(update={"proposal_id": "proposal-0001", "candidate_id": "candidate-0001"}),
                        )
                    ),
                ),
            )
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=stages,
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases),
            )
            with pytest.raises(TopicMemoryGenerationError, match="invalid_topic_placeholder"):
                await processor.process(_assignment(term.fence("single-process")))
            async with profile.database.transaction() as connection:
                heads = await topics.browse_current(connection, "scope-a", limit=20)
                exact = await topics.get_exact(connection, "scope-a", old.topic.as_ref())
                hits = await topics.search(connection, "scope-a", "zircon", limit=10, mode="fts")
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                await require_topic_memory_work_available(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING, 0)
            assert [item.artifact_ref for item in heads] == [old.topic.as_ref()]
            assert exact.topic.content == valid
            assert [hit.artifact_ref for hit in hits.hits] == [old.topic.as_ref()]
            assert cursor is None
            row = await _budget_row(profile)
            assert row["failure_code"] == "invalid_topic_placeholder"
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize("recover", [True, False])
def test_rejected_content_recovers_or_exhausts_the_original_budget(recover):
    async def scenario():
        manager, profile, sources, topics = await _repositories()
        try:
            leases = ArtifactProcessingLeaseRepository()
            async with profile.database.transaction() as connection:
                await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(
                        name="target",
                        materialization=SourceMaterialization.CAPTURED,
                        body="Zircon deployment checks pass.",
                    ),
                )
                term = await leases.start_single_process_term(connection, "holder")
            assignment = _assignment(term.fence("single-process"))
            for attempt in range(3):
                content = (
                    TopicMemoryContent(
                        title="Zircon release",
                        summary="Deployment checks pass.",
                        detail="Zircon deployment checks pass.",
                    )
                    if recover and attempt
                    else _placeholder()
                )
                stages = _stages(
                    probe=TopicMemoryProbeOutput(
                        probes=(TopicMemoryProbe(query="zircon", evidence_ids=("evidence-0001",)),)
                    ),
                    global_output=TopicMemoryGlobalOutput(
                        proposals=(TopicMemoryProposal(content=content, evidence_ids=("evidence-0001",)),)
                    ),
                )
                processor = TopicMemoryProcessor(
                    database=profile.database,
                    sources=sources,
                    topics=topics,
                    stages=stages,
                    publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases),
                )
                if recover and attempt:
                    assert (await processor.process(assignment)).outcome.value == "succeeded"
                    async with profile.database.transaction() as connection:
                        head = (await topics.browse_current(connection, "scope-a", limit=10))[0]
                        exact = await topics.get_exact(connection, "scope-a", head.artifact_ref)
                        cursor = await SourceCursorRepository().load(
                            connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING
                        )
                    assert exact.topic.content == content
                    assert cursor is not None and cursor.cursor.sequence == 1
                    assert await _budget_row(profile) is None
                    break
                with pytest.raises(TopicMemoryGenerationError, match="invalid_topic_placeholder"):
                    await processor.process(assignment)
                row = await _budget_row(profile)
                assert row["attempts"] == attempt + 1
            if not recover:
                async with profile.database.transaction() as connection:
                    with pytest.raises(TopicMemoryGenerationError, match="invalid_topic_placeholder"):
                        await require_topic_memory_work_available(
                            connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING, 0
                        )
                    assert await topics.browse_current(connection, "scope-a", limit=10) == ()
                    assert (
                        await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                        is None
                    )
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["create", "update", "reconcile"])
@pytest.mark.parametrize(
    "material",
    ["policy", "paraphrased-policy", "business-coordination", "split-quotes", "joined-quotes", "captured-multiline"],
)
def test_grounded_identity_content_publishes_with_exact_sources(path, material):
    async def scenario():
        manager, profile, sources, topics = await _repositories()
        sources = SourceRepository((*SOURCE_ADAPTERS, CONTENT_SOURCE_ADAPTER))
        try:
            content = (
                TopicMemoryContent(
                    title="Customer import identity policy",
                    summary="Retain historical identities."
                    if material == "paraphrased-policy"
                    else "Preserve historical identities.",
                    detail="Update existing identities without merging historical identities.",
                )
                if material in {"policy", "paraphrased-policy"}
                else _placeholder().model_copy(update={"title": "Placeholder investigation"})
            )
            if material == "business-coordination":
                content = TopicMemoryContent(
                    title="Customer import identity policy",
                    summary="Reconcile existing identities.",
                    detail="Reconciliation preserving distinct historical identities.",
                )
            if material == "captured-multiline":
                content = content.model_copy(
                    update={
                        "summary": "S-19 coordinated\nwithout merging historical identities.",
                        "detail": "D-19 coordinated detail\npreserving distinct historical identity.",
                    }
                )
            bodies = (
                (f"{content.title}: {content.summary} {content.detail}",)
                if material != "split-quotes"
                else (
                    "Placeholder investigation captured summary: " + content.summary,
                    "Placeholder investigation captured detail: " + content.detail,
                )
            )
            if material == "paraphrased-policy":
                bodies = tuple(
                    body.replace("Retain historical identities.", "Preserve historical identities.") for body in bodies
                )
            if material == "business-coordination":
                bodies = (
                    "Customer import: reconcile existing identities; reconciliation preserving distinct historical identities.",
                )
            prior = TopicMemoryContent(
                title="zircon import",
                summary="Zircon customer import is pending.",
                detail="Customer import requirements need confirmation.",
            )
            leases = ArtifactProcessingLeaseRepository()
            async with profile.database.transaction() as connection:
                old = (
                    None
                    if path == "create"
                    else await topics.publish_create(
                        connection,
                        "scope-a",
                        "topic-old",
                        TopicMemoryDraft(content=prior),
                        prepare_topic_memory_projection(prior),
                    )
                )
                stored = []
                for index, body in enumerate(bodies, start=1):
                    stored.append(
                        await sources.add(
                            connection,
                            "scope-a",
                            await CONTENT_SOURCE_ADAPTER.resolve(
                                ContentCapture(source_id=f"policy-{index}", content=body)
                            )
                            if material == "captured-multiline"
                            else NoteSource(
                                name=f"policy-{index}", materialization=SourceMaterialization.CAPTURED, body=body
                            ),
                        )
                    )
                term = await leases.start_single_process_term(connection, "holder")
            evidence_ids = tuple(f"evidence-{index:04d}" for index in range(1, len(stored) + 1))
            proposal = TopicMemoryProposal(
                content=content, evidence_ids=evidence_ids, candidate_id="candidate-0001" if path == "update" else None
            )
            provisional = proposal.model_copy(update={"content": prior}) if path == "reconcile" else proposal
            stages = _stages(
                probe=TopicMemoryProbeOutput(probes=(TopicMemoryProbe(query="zircon", evidence_ids=evidence_ids),)),
                global_output=TopicMemoryGlobalOutput(proposals=(provisional,)),
                reconcile=(
                    TopicMemoryReconcileOutput(
                        proposals=(
                            proposal.model_copy(
                                update={"proposal_id": "proposal-0001", "candidate_id": "candidate-0001"}
                            ),
                        )
                    ),
                ),
            )
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=stages,
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases),
            )
            assignment = _assignment(term.fence("single-process"), through=len(stored))
            assert (await processor.process(assignment)).outcome.value == "succeeded"
            async with profile.database.transaction() as connection:
                heads = await topics.browse_current(connection, "scope-a", limit=10)
                assert len(heads) == 1
                published = await topics.get_exact(connection, "scope-a", heads[0].artifact_ref)
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                if old is not None:
                    assert (await topics.get_exact(connection, "scope-a", old.topic.as_ref())).topic.content == prior
                    assert published.topic.revision == 2
                    assert published.topic.lineage.artifacts == (old.topic.as_ref(),)
            assert published.topic.content == content
            assert published.topic.lineage.sources == tuple(item.ref for item in stored)
            assert cursor is not None and cursor.cursor.sequence == len(stored)
            assert await _budget_row(profile) is None
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize("identifier", [None, "proposal-0002", "component-0001", "work-0001", "temp-0001-0001-0000"])
def test_generation_retains_stage_ids_and_merged_inputs(identifier):
    boilerplate = identifier is not None

    async def scenario():
        manager, profile, sources, topics = await _repositories()
        try:
            leases = ArtifactProcessingLeaseRepository()
            prior = TopicMemoryContent(
                title="Zircon import", summary="Zircon import is pending.", detail="Check the import dashboard."
            )
            valid = TopicMemoryContent(
                title="Zircon import",
                summary="Zircon import succeeded.",
                detail="The dashboard confirms the Zircon import succeeded.",
            )
            async with profile.database.transaction() as connection:
                old = await topics.publish_create(
                    connection,
                    "scope-a",
                    "topic-old",
                    TopicMemoryDraft(content=prior),
                    prepare_topic_memory_projection(prior),
                )
                await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="result", materialization=SourceMaterialization.CAPTURED, body=valid.detail),
                )
                term = await leases.start_single_process_term(connection, "holder")
            content = (
                TopicMemoryContent(
                    title="Zircon import",
                    summary=f"Coordinated with {identifier} without merging identities.",
                    detail=f"Coordination details preserving the distinct historical identity of {identifier}.",
                )
                if boilerplate
                else valid
            )
            provisional = TopicMemoryProposal(content=valid, evidence_ids=("evidence-0001",))
            planned = identifier in {"work-0001", "temp-0001-0001-0000"}
            compacted = identifier == "temp-0001-0001-0000"
            estimator = (
                TokenEstimator(
                    character_token_estimator().profile,
                    lambda text: (
                        100
                        if (
                            ('"work_id"' in text and '"temporary":[]' in text)
                            or ('"proposal_id":"temp-' in text and ('"work_id"' in text or '"temporary"' not in text))
                        )
                        else 1
                    ),
                )
                if compacted
                else None
            )
            stages = _stages(
                probe=TopicMemoryProbeOutput(
                    probes=(TopicMemoryProbe(query="zircon", evidence_ids=("evidence-0001",)),)
                ),
                global_output=TopicMemoryGlobalOutput(ambiguous=planned, proposals=(provisional, provisional)),
                planner=TopicMemoryPlannerOutput(items=(TopicMemoryPlanItem(probe_ids=("probe-0001",)),)),
                evolve=(TopicMemoryEvolveOutput(proposal=provisional.model_copy(update={"content": content})),),
                temporary=(TopicMemoryTemporaryOutput(proposals=(provisional,)),),
                estimator=estimator,
                limit=10 if compacted else 100_000,
                reconcile=(
                    TopicMemoryReconcileOutput(
                        proposals=(
                            TopicMemoryProposal(
                                proposal_id="proposal-0001",
                                candidate_id="candidate-0001",
                                content=content,
                                evidence_ids=("evidence-0001",),
                            ),
                        )
                    ),
                ),
            )
            if compacted:
                stages = replace(
                    stages,
                    reducer=_QueueGenerator(TopicMemoryReductionOutput(covered_indices=(0,), temporary=provisional)),
                )
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=stages,
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases),
            )
            assignment = _assignment(term.fence("single-process"))
            if boilerplate:
                with pytest.raises(TopicMemoryGenerationError, match="invalid_topic_coordination_content"):
                    await processor.process(assignment)
                assert (await _budget_row(profile))["failure_code"] == "invalid_topic_coordination_content"
            else:
                assert (await processor.process(assignment)).outcome.value == "succeeded"
                assert await _budget_row(profile) is None
            async with profile.database.transaction() as connection:
                heads = await topics.browse_current(connection, "scope-a", limit=10)
                assert len(heads) == 1
                assert heads[0].artifact_ref.revision == (1 if boilerplate else 2)
                assert (await topics.get_exact(connection, "scope-a", old.topic.as_ref())).topic.content == prior
                published = await topics.get_exact(connection, "scope-a", heads[0].artifact_ref)
                assert published.topic.content == (prior if boilerplate else valid)
                hits = await topics.search(connection, "scope-a", "zircon", limit=10, mode="fts")
                assert [hit.artifact_ref for hit in hits.hits] == [heads[0].artifact_ref]
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                if boilerplate:
                    assert cursor is None
                else:
                    assert cursor is not None and cursor.cursor.sequence == 1
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())
