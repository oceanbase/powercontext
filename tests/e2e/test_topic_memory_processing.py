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
from collections import deque

import pytest
from pydantic import BaseModel

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.prompt import PromptContent
from powercontext.builtin.artifacts.topic_memory import (
    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
    TopicMemoryContent,
    TopicMemoryDraft,
    prepare_topic_memory_projection,
)
from powercontext.builtin.artifacts.topic_memory.generation import (
    TopicMemoryGlobalOutput,
    TopicMemoryProbe,
    TopicMemoryProbeOutput,
    TopicMemoryProposal,
    TopicMemoryReconcileOutput,
)
from powercontext.builtin.inference import GenerationResult
from powercontext.builtin.persistence.artifacts import RepositoryArtifactDraft
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
)
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.models import SubmitSourceObservation
from powercontext.builtin.runtime.topic_memory_processing import (
    ArtifactProcessingWaveKind,
    TopicMemoryAtomicPublisher,
    TopicMemoryProcessor,
    TopicMemoryStageSet,
    TopicMemoryWindowAssignment,
    TopicMemoryWindowSelector,
)
from powercontext.builtin.runtime.topic_memory_scope import TopicMemoryScopeProcessor
from powercontext.builtin.sources import ContentCapture
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    SourceDefinitionRegistry,
    SourceMaterialization,
    SourceRef,
    TextEvidence,
)
from powercontext.sources.observations import manifest_for_definition, project_source_for_transport
from tests.builtin.persistence.contract import NoteAdapter, NoteSource


class _QueueGenerator:
    def __init__(self, *outputs) -> None:
        self.outputs = deque(outputs)

    async def generate(self, _value, /):
        if not self.outputs:
            raise AssertionError("unexpected generation stage")  # noqa: TRY003
        return GenerationResult(output=self.outputs.popleft())


class _OneSourceSelector(TopicMemoryWindowSelector):
    async def select(self, scope_id, source_after, source_ceiling, /):
        return await super().select(scope_id, source_after, min(source_after + 1, source_ceiling))


class _Launcher:
    def __init__(self, processor: TopicMemoryScopeProcessor) -> None:
        self.processor = processor

    async def start(self, assignment: ArtifactProcessingWorkAssignment):
        return _Handle(self.processor, assignment)


class _Handle:
    def __init__(self, processor: TopicMemoryScopeProcessor, assignment: ArtifactProcessingWorkAssignment) -> None:
        self.processor = processor
        self.assignment = assignment

    async def wait(self) -> ArtifactProcessingWorkerCompletion:
        return await self.processor.process(self.assignment)

    async def terminate(self) -> None:
        pass


def _content(label: str) -> TopicMemoryContent:
    return TopicMemoryContent(
        title=f"{label} topic",
        summary=f"{label} summary",
        detail=f"# {label}\n\nzircon evidence for {label}",
    )


@pytest.mark.parametrize("with_projection", [False, True], ids=["captured-payload", "text-projection"])
def test_remote_observation_and_content_window_processes_after_restart(tmp_path, with_projection) -> None:
    class NoteProjection:
        name = TEXT_EVIDENCE_PROJECTION_KEY.name
        version = TEXT_EVIDENCE_PROJECTION_KEY.version
        source_class = NoteSource
        output_class: type[BaseModel] = TextEvidence

        def project(self, source):
            return TextEvidence(source_type="note", source_id=source.name, content="Projected release validation")

    class CheckingProbe(_QueueGenerator):
        async def generate(self, value, /):
            note, content = value.evidence
            assert note.source_type == "note" and content.source_type == "content"
            assert "Validate the release with pytest" in content.content
            if with_projection:
                assert "Projected release validation" in note.content
                assert "Raw release evidence" not in note.content
            else:
                assert "Raw release evidence" in note.content
            assert "remote-source-identity" not in note.content
            return await super().generate(value)

    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'remote-topic.db'}"))
        registry = SourceDefinitionRegistry((
            AdapterSourceDefinition(NoteAdapter(), projections=(NoteProjection(),) if with_projection else ()),
        ))
        observation = project_source_for_transport(
            registry,
            NoteSource(
                name="remote-source-identity",
                materialization=SourceMaterialization.CAPTURED,
                body="Raw release evidence",
            ),
        )
        async with open_builtin_contexts(config) as contexts:
            await contexts.register_source_definition(manifest_for_definition(registry.definition_for_name("note")))
            receipt = await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id="scope-a", observation=observation)
            )
            scope = await contexts.get("scope-a")
            await scope.sources.capture(
                ContentCapture(source_id="builtin-control", content="Validate the release with pytest")
            )
            builtin_ref = SourceRef(source_type="content", source_id="builtin-control")

        async with open_builtin_contexts(config) as contexts:
            sources, topics = contexts.repositories.sources, contexts.repositories.topic_memories
            selector = TopicMemoryWindowSelector(
                contexts.database, sources, contexts.token_estimator, context_window_tokens=100_000
            )
            assert await selector.select("scope-a", 0, 2) == 2
            async with contexts.database.transaction() as connection:
                term = await contexts.repositories.processing_leases.start_single_process_term(
                    connection, "remote-test"
                )
            unexpected = _QueueGenerator()
            evidence_ids = ("evidence-0001", "evidence-0002")
            stages = TopicMemoryStageSet(
                probe=CheckingProbe(
                    TopicMemoryProbeOutput(probes=(TopicMemoryProbe(query="release", evidence_ids=evidence_ids),))
                ),
                global_evolver=_QueueGenerator(
                    TopicMemoryGlobalOutput(
                        proposals=(TopicMemoryProposal(content=_content("release"), evidence_ids=evidence_ids),)
                    )
                ),
                planner=unexpected,
                evolver=unexpected,
                temporary=unexpected,
                reconciler=unexpected,
                estimator=contexts.token_estimator,
                input_tokens_limit=100_000,
            )
            processor = TopicMemoryProcessor(
                database=contexts.database,
                sources=sources,
                topics=topics,
                stages=stages,
                publisher=TopicMemoryAtomicPublisher(
                    contexts.database, sources, topics, leases=contexts.repositories.processing_leases
                ),
                id_factory=lambda: "remote-topic",
            )
            result = await processor.process(
                TopicMemoryWindowAssignment(
                    binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                    scope_id="scope-a",
                    source_after=0,
                    source_through=2,
                    wave_target=2,
                    claimed_flush_generation=1,
                    cursor_generation=None,
                    wave_kind=ArtifactProcessingWaveKind.EXPLICIT,
                    fence=term.fence("single-process"),
                    worker_id="worker",
                )
            )
            assert result.outcome.value == "succeeded"

        async with open_builtin_contexts(config) as contexts, contexts.database.transaction() as connection:
            topic = await contexts.repositories.artifacts.latest(connection, "scope-a", "topic-memory", "remote-topic")
            assert topic.lineage.sources == (receipt.source_ref, builtin_ref)
            assert topic.content.title == "release topic"
            cursor = await contexts.repositories.cursors.load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
            assert cursor is not None and cursor.cursor.sequence == 2

    asyncio.run(scenario())


def test_source_capture_to_multi_window_create_update_noop(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topic-e2e.db'}"),
        )
        async with open_builtin_contexts(config) as contexts:
            scope = await contexts.get("scope-a")
            async with contexts.database.transaction() as connection:
                await contexts.repositories.artifacts.create(
                    connection,
                    "scope-a",
                    "topic_memory.probe",
                    RepositoryArtifactDraft(
                        family="prompt",
                        content=PromptContent(
                            schema_version="powercontext.prompt.v1",
                            mode="custom",
                            instructions="Probe for durable topics only.",
                            demonstrations=(),
                        ),
                    ),
                )
            for index, content in enumerate(("create zircon", "update zircon", "no durable topic"), start=1):
                await scope.sources.capture(ContentCapture(source_id=f"source-{index}", content=content))
            pending = contexts.repositories.processing_pending
            async with contexts.database.transaction() as connection:
                await pending.request_flush(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                await ArtifactProcessingIntentRepository().request(
                    connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING
                )

            unexpected = _QueueGenerator()
            stages = TopicMemoryStageSet(
                probe=_QueueGenerator(
                    TopicMemoryProbeOutput(probes=(TopicMemoryProbe(query="zircon", evidence_ids=("evidence-0001",)),)),
                    TopicMemoryProbeOutput(probes=(TopicMemoryProbe(query="zircon", evidence_ids=("evidence-0001",)),)),
                    TopicMemoryProbeOutput(probes=()),
                ),
                global_evolver=_QueueGenerator(
                    TopicMemoryGlobalOutput(
                        proposals=(TopicMemoryProposal(content=_content("created"), evidence_ids=("evidence-0001",)),)
                    ),
                    TopicMemoryGlobalOutput(
                        proposals=(
                            TopicMemoryProposal(
                                candidate_id="candidate-0001",
                                content=_content("updated"),
                                evidence_ids=("evidence-0001",),
                            ),
                        )
                    ),
                ),
                planner=unexpected,
                evolver=unexpected,
                temporary=unexpected,
                reconciler=unexpected,
                estimator=contexts.token_estimator,
                input_tokens_limit=100_000,
            )
            processor = TopicMemoryProcessor(
                database=contexts.database,
                sources=contexts.repositories.sources,
                topics=contexts.repositories.topic_memories,
                stages=stages,
                publisher=TopicMemoryAtomicPublisher(
                    contexts.database,
                    contexts.repositories.sources,
                    contexts.repositories.topic_memories,
                    cursors=contexts.repositories.cursors,
                    leases=contexts.repositories.processing_leases,
                ),
                id_factory=lambda: "topic-e2e",
                prompt_refs={
                    "probe": ArtifactRef(family="prompt", artifact_id="topic_memory.probe", revision=1),
                    "planner": ArtifactRef(family="prompt", artifact_id="topic_memory.planner", revision=1),
                },
            )
            binding = ArtifactProcessingBinding(
                binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                artifact_family="topic-memory",
                max_workers=1,
                worker_timeout_seconds=5,
                launcher=_Launcher(
                    TopicMemoryScopeProcessor(
                        contexts.database,
                        processor,
                        _OneSourceSelector(
                            contexts.database,
                            contexts.repositories.sources,
                            contexts.token_estimator,
                            context_window_tokens=10_000,
                        ),
                    )
                ),
            )
            async with ArtifactProcessingSupervisor(
                database=contexts.database,
                bindings=(binding,),
                lease_mode="single-process",
                leases=contexts.repositories.processing_leases,
                binding_states=contexts.repositories.processing_binding_states,
                retry_base_seconds=0.01,
                retry_cap_seconds=0.01,
                retry_jitter=lambda: 1.0,
            ):
                for _ in range(300):
                    async with contexts.database.transaction() as connection:
                        cursor = await SourceCursorRepository().load(
                            connection,
                            "scope-a",
                            TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                        )
                        pending_row = await pending.load(
                            connection,
                            "scope-a",
                            TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                        )
                    if cursor is not None and cursor.cursor.sequence == 3 and pending_row is None:
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("Topic processing did not finish")  # noqa: TRY003

            async with contexts.database.transaction() as connection:
                revisions = await contexts.repositories.topic_memories.artifacts.revisions(
                    connection,
                    "scope-a",
                    "topic-memory",
                    "topic-e2e",
                )
                search = await contexts.repositories.topic_memories.search(
                    connection,
                    "scope-a",
                    "updated",
                    limit=10,
                )
            assert [revision.content.title for revision in revisions] == ["created topic", "updated topic"]
            assert search.hits[0].artifact_ref.revision == 2
            probe_ref = ArtifactRef(family="prompt", artifact_id="topic_memory.probe", revision=1)
            assert revisions[0].lineage.artifacts == (probe_ref,)
            assert revisions[1].lineage.artifacts == (
                ArtifactRef(family="topic-memory", artifact_id="topic-e2e", revision=1),
                probe_ref,
            )

    asyncio.run(scenario())


def test_secondary_retrieval_reconciles_a_create_into_an_exact_update(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topic-secondary.db'}"),
        )
        async with open_builtin_contexts(config) as contexts:
            old_content = TopicMemoryContent(
                title="zircon release",
                summary="durable zircon release",
                detail="# Zircon\n\nDurable release evidence.",
            )
            async with contexts.database.transaction() as connection:
                old = await contexts.repositories.topic_memories.publish_create(
                    connection,
                    "scope-a",
                    "topic-existing",
                    TopicMemoryDraft(content=old_content),
                    prepare_topic_memory_projection(old_content),
                )
            scope = await contexts.get("scope-a")
            await scope.sources.capture(ContentCapture(source_id="source-new", content="new zircon evidence"))
            pending = contexts.repositories.processing_pending
            async with contexts.database.transaction() as connection:
                await pending.request_flush(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                await ArtifactProcessingIntentRepository().request(
                    connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING
                )

            provisional = TopicMemoryProposal(content=old_content, evidence_ids=("evidence-0001",))
            reconciled = provisional.model_copy(
                update={"proposal_id": "proposal-0001", "candidate_id": "candidate-0001"}
            )
            unexpected = _QueueGenerator()
            stages = TopicMemoryStageSet(
                probe=_QueueGenerator(
                    TopicMemoryProbeOutput(
                        probes=(TopicMemoryProbe(query="no-historical-match", evidence_ids=("evidence-0001",)),)
                    )
                ),
                global_evolver=_QueueGenerator(TopicMemoryGlobalOutput(proposals=(provisional,))),
                planner=unexpected,
                evolver=unexpected,
                temporary=unexpected,
                reconciler=_QueueGenerator(TopicMemoryReconcileOutput(proposals=(reconciled,))),
                estimator=contexts.token_estimator,
                input_tokens_limit=100_000,
            )
            processor = TopicMemoryProcessor(
                database=contexts.database,
                sources=contexts.repositories.sources,
                topics=contexts.repositories.topic_memories,
                stages=stages,
                publisher=TopicMemoryAtomicPublisher(
                    contexts.database,
                    contexts.repositories.sources,
                    contexts.repositories.topic_memories,
                    cursors=contexts.repositories.cursors,
                    leases=contexts.repositories.processing_leases,
                ),
                id_factory=lambda: "must-not-be-published",
            )
            binding = ArtifactProcessingBinding(
                binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                artifact_family="topic-memory",
                max_workers=1,
                worker_timeout_seconds=5,
                launcher=_Launcher(
                    TopicMemoryScopeProcessor(
                        contexts.database,
                        processor,
                        _OneSourceSelector(
                            contexts.database,
                            contexts.repositories.sources,
                            contexts.token_estimator,
                            context_window_tokens=10_000,
                        ),
                    )
                ),
            )
            async with ArtifactProcessingSupervisor(
                database=contexts.database,
                bindings=(binding,),
                lease_mode="single-process",
                leases=contexts.repositories.processing_leases,
                binding_states=contexts.repositories.processing_binding_states,
                retry_base_seconds=0.01,
                retry_cap_seconds=0.01,
                retry_jitter=lambda: 1.0,
            ):
                for _ in range(300):
                    async with contexts.database.transaction() as connection:
                        cursor = await contexts.repositories.cursors.load(
                            connection,
                            "scope-a",
                            TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                        )
                        pending_row = await pending.load(
                            connection,
                            "scope-a",
                            TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                        )
                    if cursor is not None and cursor.cursor.sequence == 1 and pending_row is None:
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("secondary retrieval did not finish")  # noqa: TRY003

            async with contexts.database.transaction() as connection:
                revisions = await contexts.repositories.topic_memories.artifacts.revisions(
                    connection,
                    "scope-a",
                    "topic-memory",
                    "topic-existing",
                )
                created = await contexts.repositories.topic_memories.browse_current(connection, "scope-a", limit=10)
            assert [revision.revision for revision in revisions] == [1, 2]
            assert revisions[1].lineage.artifacts == (old.topic.as_ref(),)
            assert [item.artifact_ref.artifact_id for item in created] == ["topic-existing"]

    asyncio.run(scenario())
