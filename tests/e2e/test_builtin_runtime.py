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
from datetime import UTC, datetime, timedelta, timezone

import pytest
import time_machine

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.artifacts.memory import MemoryRerankDecision
from powercontext.builtin.inference import GenerationResult, InferenceUsage
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    CaptureWorkingNote,
    CommitConnectorCheckpoint,
    InvalidRuntimeRequestError,
    PrepareContextRequest,
    SubmitSourceObservation,
    WorkingNoteKind,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft, ScopeMutation, ScopeNotFoundError
from powercontext.builtin.sources import CONTENT_SOURCE_DEFINITION, ContentCapture
from powercontext.errors import SourceConflictError
from powercontext.sources import ConnectorBinding, SourceDefinitionRegistry, project_source_for_transport
from tests.e2e.dream_support import atomic_memory_pipeline, memory_source_text


class _ContentCandidatePipeline:
    async def generate(self, request: AtomicMemoryExtractionInput, /) -> GenerationResult[AtomicMemoryExtractionOutput]:
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(
                        kind="fact",
                        text=text,
                        evidence_ids=(evidence.evidence_id,),
                        description="Extracted fixture fact",
                    )
                    for evidence in request.evidence
                    if (text := memory_source_text(evidence)) is not None
                )
            )
        )


def test_scoped_source_capture_many_keeps_order_and_replay_identity() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Batch capture", summary="Stable ordered input", idempotency_key="batch-capture")
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            values = (
                CaptureSource(source_id="first", content="First fact", metadata={}),
                CaptureSource(source_id="second", content="Second fact", metadata={}),
                CaptureSource(source_id="third", content="Third fact", metadata={}),
            )
            receipts = await sources.capture_many(values)
            assert tuple(receipt.sequence for receipt in receipts) == (1, 2, 3)
            assert await sources.capture_many(values) == receipts
            with pytest.raises(SourceConflictError):
                await sources.capture_many((
                    values[0],
                    CaptureSource(source_id="second", content="Changed", metadata={}),
                ))
            assert (
                await sources.capture(CaptureSource(source_id="fourth", content="Fourth fact", metadata={}))
            ).sequence == 4

    asyncio.run(scenario())


def test_atomic_memory_create_many_returns_exact_current_records() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Memory batch", summary="Complete returned records", idempotency_key="memory-batch")
            )
            memories = runtime.atomic_memory.for_scope(scope.scope_id)
            contents = tuple(AtomicMemoryContent(kind="fact", text=f"Fact {index}") for index in range(3))
            created = await memories.create(contents)
            assert tuple(record.artifact.content for record in created.records) == tuple(
                content.model_copy(update={"description": content.text}) for content in contents
            )
            assert tuple(record.artifact.revision for record in created.records) == (1, 1, 1)
            assert all(len(record.artifact.lineage.sources) == 1 for record in created.records)
            for record in created.records:
                assert await memories.get(record.artifact.artifact_id) == record

    asyncio.run(scenario())


def test_builtin_runtime_rejects_unregistered_scope_without_persisting_data(tmp_path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
        config = BuiltinConfig(database=database)
        orphan_scope_id = "project:orphan"
        binding = ConnectorBinding(
            scope_id=orphan_scope_id,
            binding_id="orphan-connector",
            connector_name="content",
            connector_version="1",
        )
        source_registry = SourceDefinitionRegistry((CONTENT_SOURCE_DEFINITION,))
        observation = project_source_for_transport(
            source_registry,
            await source_registry.resolve(ContentCapture(source_id="remote-turn", content="orphan remote content")),
        )

        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            registered = await runtime.scopes.create(
                ScopeDraft(title="Registered", summary="Registered scope", idempotency_key="registered")
            )

            captured = await runtime.sources.for_scope(registered.scope_id).capture(
                CaptureSource(source_id="registered-turn", content="registered content", metadata={})
            )
            prepared = await runtime.context.for_scope(registered.scope_id).prepare(
                PrepareContextRequest(query="registered")
            )

            assert captured.sequence == 1
            assert prepared.status == "empty"
            with pytest.raises(ScopeNotFoundError):
                await runtime.sources.for_scope(orphan_scope_id).capture(
                    CaptureSource(source_id="orphan-turn", content="orphan content", metadata={})
                )
            with pytest.raises(ScopeNotFoundError):
                await runtime.context.for_scope(orphan_scope_id).prepare(PrepareContextRequest(query="orphan"))
            with pytest.raises(ScopeNotFoundError):
                await runtime.ingestion.checkpoint(binding)
            with pytest.raises(ScopeNotFoundError):
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=orphan_scope_id, observation=observation)
                )
            with pytest.raises(ScopeNotFoundError):
                await runtime.ingestion.commit(
                    CommitConnectorCheckpoint(binding=binding, expected=None, checkpoint={"cursor": "1"})
                )

        async with open_builtin_contexts(config) as contexts:
            orphan = await contexts.get(orphan_scope_id)
            assert await orphan.sources.journal.entries() == ()
            assert (await contexts.connector_checkpoint(binding)).checkpoint is None

    asyncio.run(scenario())


def test_builtin_runtime_uses_sqlite_fts_without_vector_extension(tmp_path, monkeypatch) -> None:
    missing_extension = tmp_path / "missing-sqlite-vec"
    monkeypatch.setattr(
        "powercontext.builtin.persistence.sqlite.profile.sqlite_vec.loadable_path",
        lambda: str(missing_extension),
    )

    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            candidate_pipeline=atomic_memory_pipeline(_ContentCandidatePipeline()),
        ) as runtime:
            assert runtime.scopes is not None
            project = await runtime.scopes.create(
                ScopeDraft(title="Project", summary="Runtime acceptance", idempotency_key="project")
            )
            empty = await runtime.scopes.create(
                ScopeDraft(title="Empty", summary="Empty acceptance", idempotency_key="empty-project")
            )
            captured = await runtime.sources.for_scope(project.scope_id).capture(
                CaptureSource(
                    source_id="turn-1",
                    content="PowerContext composes an atomic SQL provider.",
                    metadata={"origin": "e2e"},
                )
            )
            assert runtime.atomic_memory is not None
            flushed = await runtime.atomic_memory.for_scope(project.scope_id).flush()
            found = await runtime.atomic_memory.for_scope(project.scope_id).search("atomic SQL provider")
            prepared = await runtime.context.for_scope(project.scope_id).prepare(
                PrepareContextRequest(query="atomic SQL provider")
            )
            no_memory = await runtime.context.for_scope(empty.scope_id).prepare(PrepareContextRequest(query="anything"))
            no_match = await runtime.context.for_scope(project.scope_id).prepare(
                PrepareContextRequest(query="unrelated-zebra-phrase")
            )

            assert captured.sequence == 1
            assert flushed.current_cursor == captured.sequence
            assert flushed.memory_ref is None
            assert tuple(hit.text for hit in found.hits) == ("PowerContext composes an atomic SQL provider.",)
            assert prepared.status == "ready"
            assert prepared.content is not None
            item = json.loads(prepared.content.splitlines()[-2])["items"][0]
            assert item["content"] == "PowerContext composes an atomic SQL provider."
            assert item["citation"]["artifact_ref"] == found.hits[0].hit.artifact_ref.model_dump(mode="json")
            assert no_memory.status == "empty"
            assert no_memory.content is None
            assert no_match.status == "empty"
            assert no_match.content is None

    asyncio.run(scenario())


def test_working_note_capture_does_not_promote_into_memory() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            candidate_pipeline=atomic_memory_pipeline(_ContentCandidatePipeline()),
        ) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Project", summary="Working note boundary", idempotency_key="working-note")
            )
            for field, field_value in (
                ("kind", "working_note"),
                ("session_id", "metadata-session"),
                ("note_kind", "validation"),
                ("expires_at", "2000-01-01T00:00:00+00:00"),
            ):
                with pytest.raises(InvalidRuntimeRequestError):
                    await runtime.sources.for_scope(scope.scope_id).capture_working_note(
                        CaptureWorkingNote(
                            source_id=f"reserved-{field}",
                            content="Caller metadata cannot set server-owned Working Note fields.",
                            session_id="session-1",
                            note_kind=WorkingNoteKind.VALIDATION,
                            metadata={field: field_value},
                        )
                    )
            with pytest.raises(InvalidRuntimeRequestError) as naive_expiry:
                await runtime.sources.for_scope(scope.scope_id).capture_working_note(
                    CaptureWorkingNote(
                        source_id="naive-expiry",
                        content="The expiry has no time zone.",
                        session_id="session-1",
                        note_kind=WorkingNoteKind.VALIDATION,
                        expires_at=datetime(2099, 1, 1),  # noqa: DTZ001 - naive on purpose
                    )
                )
            assert naive_expiry.value.code == "working-note-expiry-timezone"
            captured = await runtime.sources.for_scope(scope.scope_id).capture_working_note(
                CaptureWorkingNote(
                    source_id="note-1",
                    content="The rejected approach depended on an unavailable API.",
                    session_id="session-1",
                    note_kind=WorkingNoteKind.ABANDONED_APPROACH,
                    expires_at=datetime(2099, 1, 1, tzinfo=timezone(-timedelta(hours=8))),
                )
            )
            replayed = await runtime.sources.for_scope(scope.scope_id).capture_working_note(
                CaptureWorkingNote(
                    source_id="note-1",
                    content="The rejected approach depended on an unavailable API.",
                    session_id="session-1",
                    note_kind=WorkingNoteKind.ABANDONED_APPROACH,
                    expires_at=datetime(2099, 1, 1, 8, tzinfo=UTC),
                )
            )
            with time_machine.travel(datetime(2100, 1, 1, tzinfo=UTC)):
                expired_replay = await runtime.sources.for_scope(scope.scope_id).capture_working_note(
                    CaptureWorkingNote(
                        source_id="note-1",
                        content="The rejected approach depended on an unavailable API.",
                        session_id="session-1",
                        note_kind=WorkingNoteKind.ABANDONED_APPROACH,
                        expires_at=datetime(2099, 1, 1, 8, tzinfo=UTC),
                    )
                )
                with pytest.raises(SourceConflictError):
                    await runtime.sources.for_scope(scope.scope_id).capture_working_note(
                        CaptureWorkingNote(
                            source_id="note-1",
                            content="Different content cannot reuse the identity.",
                            session_id="session-1",
                            note_kind=WorkingNoteKind.ABANDONED_APPROACH,
                            expires_at=datetime(2099, 1, 1, 8, tzinfo=UTC),
                        )
                    )
                with pytest.raises(InvalidRuntimeRequestError) as past_expiry:
                    await runtime.sources.for_scope(scope.scope_id).capture_working_note(
                        CaptureWorkingNote(
                            source_id="never-accepted",
                            content="A new expired note is invalid.",
                            session_id="session-1",
                            note_kind=WorkingNoteKind.ABANDONED_APPROACH,
                            expires_at=datetime(2099, 1, 1, 8, tzinfo=UTC),
                        )
                    )
                assert past_expiry.value.code == "working-note-expiry-past"

            assert runtime.atomic_memory is not None
            flushed = await runtime.atomic_memory.for_scope(scope.scope_id).flush()

            assert replayed == captured
            assert expired_replay == captured
            assert flushed.current_cursor == captured.sequence
            assert flushed.memory_ref is None

    asyncio.run(scenario())


def test_prepare_context_reads_only_direct_context_references() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            shared = await runtime.scopes.create(
                ScopeDraft(title="Shared", summary="Reusable evidence", idempotency_key="shared")
            )
            middle = await runtime.scopes.create(
                ScopeDraft(
                    title="Middle",
                    summary="Reads shared evidence",
                    context_references=(shared.scope_id,),
                    idempotency_key="middle",
                )
            )
            reader = await runtime.scopes.create(
                ScopeDraft(
                    title="Reader",
                    summary="Reads middle only",
                    context_references=(middle.scope_id,),
                    idempotency_key="reader",
                )
            )
            child = await runtime.scopes.create(
                ScopeDraft(
                    title="Child",
                    summary="Organized under shared",
                    parent_scope_id=shared.scope_id,
                    idempotency_key="child",
                )
            )
            assert runtime.atomic_memory is not None
            shared_memory = await runtime.atomic_memory.for_scope(shared.scope_id).create((
                AtomicMemoryContent(
                    kind="fact", text="Shared direct context evidence.", description="Explicit fixture memory"
                ),
            ))
            await runtime.atomic_memory.for_scope(middle.scope_id).create((
                AtomicMemoryContent(
                    kind="fact", text="Middle reverse-only evidence.", description="Explicit fixture memory"
                ),
            ))

            direct = await runtime.context.for_scope(middle.scope_id).prepare(
                PrepareContextRequest(query="direct context evidence")
            )
            transitive = await runtime.context.for_scope(reader.scope_id).prepare(
                PrepareContextRequest(query="direct context evidence")
            )
            reverse = await runtime.context.for_scope(shared.scope_id).prepare(
                PrepareContextRequest(query="middle reverse-only evidence")
            )
            parent_only = await runtime.context.for_scope(child.scope_id).prepare(
                PrepareContextRequest(query="direct context evidence")
            )

            assert direct.status == "ready"
            assert direct.content is not None
            item = json.loads(direct.content.splitlines()[-2])["items"][0]
            assert item["citation"]["artifact"]["scope_id"] == shared.scope_id
            assert item["citation"]["artifact"]["artifact"] == shared_memory.records[0].ref.model_dump(mode="json")
            assert transitive.status == "empty"
            assert reverse.status == "empty"
            assert parent_only.status == "empty"

            updated = await runtime.scopes.update(
                reader.scope_id,
                ScopeMutation(
                    expected_version=reader.version,
                    title=reader.title,
                    summary=reader.summary,
                    context_references=(shared.scope_id,),
                ),
            )
            assert updated.context_references == (shared.scope_id,)
            now_direct = await runtime.context.for_scope(reader.scope_id).prepare(
                PrepareContextRequest(query="direct context evidence")
            )
            assert now_direct.status == "ready"

    asyncio.run(scenario())


def test_prepare_context_keeps_referenced_scope_eligible_when_local_recall_is_full() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            shared = await runtime.scopes.create(
                ScopeDraft(title="Shared", summary="Reusable evidence", idempotency_key="shared-full-recall")
            )
            reader = await runtime.scopes.create(
                ScopeDraft(
                    title="Reader",
                    summary="Reads shared evidence",
                    context_references=(shared.scope_id,),
                    idempotency_key="reader-full-recall",
                )
            )
            assert runtime.atomic_memory is not None
            await runtime.atomic_memory.for_scope(reader.scope_id).create(
                tuple(
                    AtomicMemoryContent(
                        kind="fact",
                        text=f"Candidate saturation local evidence {index}.",
                        description="Explicit fixture memory",
                    )
                    for index in range(16)
                )
            )
            shared_memory = await runtime.atomic_memory.for_scope(shared.scope_id).create((
                AtomicMemoryContent(
                    kind="fact", text="Candidate saturation shared evidence.", description="Explicit fixture memory"
                ),
            ))

            prepared = await runtime.context.for_scope(reader.scope_id).prepare(
                PrepareContextRequest(query="candidate saturation evidence")
            )

            assert prepared.status == "ready"
            assert prepared.content is not None
            items = json.loads(prepared.content.splitlines()[-2])["items"]
            assert any(
                item["citation"].get("artifact", {}).get("scope_id") == shared.scope_id
                and item["citation"]["artifact"]["artifact"] == shared_memory.records[0].ref.model_dump(mode="json")
                for item in items
            )

    asyncio.run(scenario())


class _ConcurrentReranker:
    policy_id = "test.concurrent-rerank.v1"
    supports_atomic_memory = True

    def __init__(self) -> None:
        self._entered = 0
        self._both_entered = asyncio.Event()

    async def rerank(self, query, candidates, limit, /) -> MemoryRerankDecision:
        self._entered += 1
        if self._entered == 2:
            self._both_entered.set()
        await self._both_entered.wait()
        return MemoryRerankDecision(
            selected_ranks=(1,),
            usage=InferenceUsage(requests=1),
        )


def test_prepare_build_reports_no_recall_effort_while_the_gate_is_disabled() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Gate off", summary="Disabled gate", idempotency_key="gate-off")
            )
            assert runtime.atomic_memory is not None
            await runtime.atomic_memory.for_scope(scope.scope_id).create((
                AtomicMemoryContent(kind="fact", text="Disabled gate evidence.", description="Explicit fixture memory"),
            ))
            request = PrepareContextRequest(query="disabled gate evidence")
            application = runtime.context.for_scope(scope.scope_id)
            async with runtime._scope_operation(scope.scope_id) as descriptor:
                build, effort = await application._prepare_build(request, descriptor)
            prepared = await runtime.context.for_scope(scope.scope_id).prepare(request)

            assert effort is None
            assert build.context.status == "ready"
            assert prepared.content == build.context.content

    asyncio.run(scenario())


def test_same_scope_read_only_searches_do_not_serialize_reranking() -> None:
    async def scenario() -> None:
        reranker = _ConcurrentReranker()
        async with open_builtin_runtime(BuiltinConfig(), memory_reranker=reranker) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Parallel", summary="Concurrent read acceptance", idempotency_key="parallel-search")
            )
            assert runtime.atomic_memory is not None
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            await memory.create((
                AtomicMemoryContent(kind="fact", text="Parallel search fact.", description="Explicit fixture memory"),
            ))

            first = asyncio.create_task(memory.search("parallel", mode="text", limit=1))
            second = asyncio.create_task(memory.search("parallel", mode="text", limit=1))
            pages = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)

            assert all(page.rerank is not None for page in pages)

    asyncio.run(scenario())


def test_atomic_get_inside_in_memory_write_keeps_the_outer_rollback() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            with pytest.raises(RuntimeError, match="abort outer write"):
                async with contexts.database.transaction():
                    created = await contexts.records.create_artifact(
                        "project",
                        "atomic-memory",
                        ArtifactWrite(
                            content={
                                "kind": "fact",
                                "text": "Uncommitted fact",
                                "description": "Explicit fixture memory",
                            }
                        ),
                    )
                    loaded = await contexts.atomic_memory.for_scope("project").get(created.artifact_id)
                    assert loaded.artifact.content.text == "Uncommitted fact"
                    raise RuntimeError("abort outer write")  # noqa: TRY003 - deliberate transaction abort
            assert await contexts.records.logical_artifacts("project") == ()
            assert (await contexts.atomic_memory.for_scope("project").list()).items == ()

    asyncio.run(scenario())
