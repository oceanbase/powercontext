from __future__ import annotations

import asyncio

import apsw
import pytest

from powercontext import (
    ArtifactNotFoundError,
    CapabilityNotSupportedError,
    MemoryCitation,
    MemoryEntryInput,
    MemoryEntryNotFoundError,
    RevisionConflictError,
    SourceConflictError,
)
from powercontext.errors import MemoryBackendConfigurationError
from powercontext.memory import MemoryCandidateRequest
from powercontext.runtime import (
    GetMemoryEntryRequest,
    InvalidRuntimeRequestError,
    PowerContextRuntime,
    RememberMemoryRequest,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    SearchMemoryRequest,
)
from powercontext.sources import ContentCapture, ContentSource


class ContentCandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="working_note",
                text=source.content,
                sources=(source,),
            )
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def test_runtime_rejects_configuration_without_creating_storage(tmp_path) -> None:
    async def scenario() -> None:
        with pytest.raises(ValueError, match="source_window_limit"):
            await PowerContextRuntime.open(tmp_path / "window.db", source_window_limit=0)
        with pytest.raises(ValueError, match="schedule_seconds"):
            await PowerContextRuntime.open(
                tmp_path / "schedule.db",
                candidate_pipeline=ContentCandidatePipeline(),
                schedule_seconds=0,
            )
        with pytest.raises(ValueError, match="candidate pipeline"):
            await PowerContextRuntime.open(tmp_path / "pipeline.db", schedule_seconds=30)
        with pytest.raises(MemoryBackendConfigurationError, match="profile and extension"):
            await PowerContextRuntime.open(tmp_path / "vector.db", vec1_extension="vec1")

        assert list(tmp_path.iterdir()) == []

    asyncio.run(scenario())


def test_runtime_without_candidate_pipeline_keeps_deterministic_memory_available(tmp_path) -> None:
    async def scenario() -> None:
        runtime = await PowerContextRuntime.open(tmp_path / "runtime.db")
        try:
            memory = runtime.memory.for_scope("scope:direct")
            created = await memory.remember(
                RememberMemoryRequest(
                    entries=(MemoryEntryInput(kind="decision", text="Keep deterministic Memory available."),)
                )
            )
            result = await memory.search(SearchMemoryRequest("deterministic"))

            assert created.memory_ref.revision == 1
            assert [hit.text for hit in result.hits] == ["Keep deterministic Memory available."]

            await runtime.sources.for_scope("scope:direct").capture(
                ContentCapture(source_id="task-1", content="This Source requires extraction.")
            )
            with pytest.raises(CapabilityNotSupportedError) as unavailable:
                await memory.flush()
            assert unavailable.value.capability == "extract"
            assert (await memory.cursor()).sequence == 0
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_runtime_open_probes_memory_storage_before_serving_scopes(tmp_path) -> None:
    database = tmp_path / "runtime.db"
    connection = apsw.Connection(str(database))
    connection.execute("CREATE TABLE powercontext_schema (version INTEGER NOT NULL PRIMARY KEY)")
    connection.execute("INSERT INTO powercontext_schema (version) VALUES (2)")
    connection.close()

    async def scenario() -> None:
        with pytest.raises(MemoryBackendConfigurationError, match="schema version"):
            await PowerContextRuntime.open(database)

    asyncio.run(scenario())


def test_runtime_keeps_explicit_memory_separate_from_captured_sources(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "runtime.db"
        runtime = await PowerContextRuntime.open(database, candidate_pipeline=ContentCandidatePipeline())
        try:
            project = runtime.memory.for_scope("project:powercontext")
            assert (await project.search(SearchMemoryRequest("SQLite"))).memory_ref is None

            direct = await project.remember(
                RememberMemoryRequest(
                    entries=(MemoryEntryInput(kind="decision", text="Use SQLite for the local runtime."),),
                )
            )
            assert direct.previous_revision is None
            assert direct.memory_ref.revision == 1
            unchanged = await project.remember(
                RememberMemoryRequest(
                    entries=(MemoryEntryInput(kind="decision", text="Use SQLite for the local runtime."),),
                    expected_revision=1,
                )
            )
            assert unchanged.previous_revision == 1
            assert unchanged.memory_ref.revision == 1
            assert unchanged.entry is None
            assert (await project.cursor()).sequence == 0

            first = await runtime.sources.for_scope("project:powercontext").capture(
                ContentCapture(source_id="task-1", content="Run make test before publishing.")
            )
            replay = await runtime.sources.for_scope("project:powercontext").capture(
                ContentCapture(source_id="task-1", content="Run make test before publishing.")
            )
            assert first.sequence == replay.sequence == 1
            assert not (await project.search(SearchMemoryRequest("publishing"))).hits

            flushed = await project.flush()
            assert flushed.previous_cursor == 0
            assert flushed.high_watermark == 1
            assert flushed.current_cursor == 1
            assert flushed.source_count == 1
            result = await project.search(SearchMemoryRequest("publishing"))
            assert len(result.hits) == 1
            exact = await project.get(
                GetMemoryEntryRequest(
                    citation=MemoryCitation(
                        memory_ref=result.hits[0].memory_ref,
                        entry_id=result.hits[0].entry_id,
                        entry_version_id=result.hits[0].entry_version_id,
                    )
                )
            )
            assert exact.entry.sources == (first.source,)
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_runtime_rejects_changed_content_for_an_existing_source_identity(tmp_path) -> None:
    async def scenario() -> None:
        runtime = await PowerContextRuntime.open(tmp_path / "runtime.db", candidate_pipeline=ContentCandidatePipeline())
        try:
            sources = runtime.sources.for_scope("scope:identity")
            await sources.capture(ContentCapture(source_id="task-1", content="Original content."))

            with pytest.raises(SourceConflictError):
                await sources.capture(ContentCapture(source_id="task-1", content="Changed content."))
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_runtime_isolates_scopes_and_restores_cursor_and_lineage(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "runtime.db"
        runtime = await PowerContextRuntime.open(database, candidate_pipeline=ContentCandidatePipeline())
        try:
            await runtime.sources.for_scope("scope:a").capture(
                ContentCapture(source_id="same-id", content="Alpha architecture decision.")
            )
            await runtime.sources.for_scope("scope:b").capture(
                ContentCapture(source_id="same-id", content="Beta deployment decision.")
            )
            await runtime.memory.for_scope("scope:a").flush()
            original = await runtime.memory.for_scope("scope:a").search(SearchMemoryRequest("Alpha"))
            assert original.hits
            assert original.memory_ref is not None
            assert not (await runtime.memory.for_scope("scope:b").search(SearchMemoryRequest("Alpha"))).hits
        finally:
            await runtime.close()

        restored = await PowerContextRuntime.open(database, candidate_pipeline=ContentCandidatePipeline())
        try:
            scope_a = restored.memory.for_scope("scope:a")
            assert (await scope_a.cursor()).sequence == 1
            recovered = await scope_a.search(SearchMemoryRequest("Alpha"))
            assert recovered.hits
            assert recovered.memory_ref == original.memory_ref
            assert (await restored.memory.for_scope("scope:b").cursor()).sequence == 0
        finally:
            await restored.close()

    asyncio.run(scenario())


def test_runtime_rejects_a_citation_bound_to_another_scope(tmp_path) -> None:
    async def scenario() -> None:
        runtime = await PowerContextRuntime.open(tmp_path / "runtime.db", candidate_pipeline=ContentCandidatePipeline())
        try:
            created = await runtime.memory.for_scope("scope:a").remember(
                RememberMemoryRequest(
                    entries=(MemoryEntryInput(kind="decision", text="Keep scope identity explicit."),)
                )
            )
            assert created.entry is not None
            other = await runtime.memory.for_scope("scope:b").remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="decision", text="Keep scopes isolated."),))
            )
            assert other.entry is not None

            with pytest.raises(ArtifactNotFoundError):
                await runtime.memory.for_scope("scope:b").get(GetMemoryEntryRequest(citation=created.entry.citation))
            with pytest.raises(ArtifactNotFoundError):
                await runtime.memory.for_scope("scope:b").revise(
                    ReviseMemoryEntryRequest(
                        citation=created.entry.citation,
                        kind="decision",
                        text="Do not revise another scope.",
                    )
                )
            with pytest.raises(ArtifactNotFoundError):
                await runtime.memory.for_scope("scope:b").retire(
                    RetireMemoryEntryRequest(citation=created.entry.citation)
                )
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_runtime_revises_and_retires_exact_current_entries(tmp_path) -> None:
    async def scenario() -> None:
        runtime = await PowerContextRuntime.open(tmp_path / "runtime.db", candidate_pipeline=ContentCandidatePipeline())
        try:
            memory = runtime.memory.for_scope("scope:memory")
            created = await memory.remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="decision", text="Use an HTTP facade."),))
            )
            assert created.entry is not None
            original = created.entry.citation

            revised = await memory.revise(
                ReviseMemoryEntryRequest(
                    citation=original,
                    kind="decision",
                    text="Use HTTP with an explicit MCP projection.",
                )
            )
            assert revised.entry is not None
            assert revised.previous_revision == 1
            assert revised.memory_ref.revision == 2

            missing = MemoryCitation(
                memory_ref=revised.entry.memory_ref,
                entry_id="missing-entry",
                entry_version_id=revised.entry.entry.entry_version_id,
            )
            with pytest.raises(MemoryEntryNotFoundError):
                await memory.get(GetMemoryEntryRequest(citation=missing))
            with pytest.raises(InvalidRuntimeRequestError, match="newer"):
                await memory.changes(since_revision=revised.memory_ref.revision + 1)
            with pytest.raises(RevisionConflictError):
                await memory.retire(RetireMemoryEntryRequest(citation=original))

            retired = await memory.retire(RetireMemoryEntryRequest(citation=revised.entry.citation))
            assert retired.entry is not None
            assert retired.previous_revision == 2
            assert retired.entry.state == "inactive"
            assert retired.memory_ref.revision == 3
        finally:
            await runtime.close()

    asyncio.run(scenario())
