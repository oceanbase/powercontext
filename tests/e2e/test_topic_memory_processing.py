# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

from __future__ import annotations

import asyncio
from collections import deque

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
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
)
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.topic_memory_processing import (
    TopicMemoryAtomicPublisher,
    TopicMemoryProcessor,
    TopicMemoryStageSet,
    TopicMemoryWindowSelector,
)
from powercontext.builtin.sources import ContentCapture


class _QueueGenerator:
    def __init__(self, *outputs) -> None:
        self.outputs = deque(outputs)

    async def generate(self, _value, /):
        if not self.outputs:
            raise AssertionError("unexpected generation stage")  # noqa: TRY003
        return GenerationResult(output=self.outputs.popleft())


class _Launcher:
    def __init__(self, processor: TopicMemoryProcessor) -> None:
        self.processor = processor

    async def start(self, assignment: ArtifactProcessingWorkAssignment):
        return _Handle(self.processor, assignment)


class _Handle:
    def __init__(self, processor: TopicMemoryProcessor, assignment: ArtifactProcessingWorkAssignment) -> None:
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


def test_source_capture_to_multi_window_create_update_noop(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topic-e2e.db'}"),
        )
        async with open_builtin_contexts(config) as contexts:
            scope = await contexts.get("scope-a")
            for index, content in enumerate(("create zircon", "update zircon", "no durable topic"), start=1):
                await scope.sources.capture(ContentCapture(source_id=f"source-{index}", content=content))
            pending = contexts.repositories.processing_pending
            async with contexts.database.transaction() as connection:
                await pending.request_flush(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)

            unexpected = _QueueGenerator()
            stages = TopicMemoryStageSet(
                probe=_QueueGenerator(
                    TopicMemoryProbeOutput(
                        probes=(TopicMemoryProbe(query="zircon", evidence_ids=("evidence-0001",)),)
                    ),
                    TopicMemoryProbeOutput(
                        probes=(TopicMemoryProbe(query="zircon", evidence_ids=("evidence-0001",)),)
                    ),
                    TopicMemoryProbeOutput(probes=()),
                ),
                global_evolver=_QueueGenerator(
                    TopicMemoryGlobalOutput(
                        proposals=(
                            TopicMemoryProposal(content=_content("created"), evidence_ids=("evidence-0001",)),
                        )
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
            )
            binding = ArtifactProcessingBinding(
                binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                source_window_limit=1,
                launcher=_Launcher(processor),
                window_selector=TopicMemoryWindowSelector(
                    contexts.database,
                    contexts.repositories.sources,
                    contexts.token_estimator,
                    context_window_tokens=10_000,
                ),
            )
            async with ArtifactProcessingSupervisor(
                database=contexts.database,
                bindings=(binding,),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=5,
                pending=pending,
                cursors=contexts.repositories.cursors,
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

            provisional = TopicMemoryProposal(content=old_content, evidence_ids=("evidence-0001",))
            reconciled = provisional.model_copy(
                update={"proposal_id": "proposal-0001", "candidate_id": "candidate-0001"}
            )
            unexpected = _QueueGenerator()
            stages = TopicMemoryStageSet(
                probe=_QueueGenerator(
                    TopicMemoryProbeOutput(
                        probes=(
                            TopicMemoryProbe(query="no-historical-match", evidence_ids=("evidence-0001",)),
                        )
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
                source_window_limit=1,
                launcher=_Launcher(processor),
                window_selector=TopicMemoryWindowSelector(
                    contexts.database,
                    contexts.repositories.sources,
                    contexts.token_estimator,
                    context_window_tokens=10_000,
                ),
            )
            async with ArtifactProcessingSupervisor(
                database=contexts.database,
                bindings=(binding,),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=5,
                pending=pending,
                cursors=contexts.repositories.cursors,
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
