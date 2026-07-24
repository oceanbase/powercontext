from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

import pytest

from powercontext import ArtifactNotFoundError
from powercontext.artifacts import ArtifactRef
from powercontext.memory import (
    CapabilityNotSupportedError,
    InvalidMemoryEvidenceError,
    MemoryCandidateRequest,
    MemoryEntryInput,
    MemoryEvidenceCodec,
    MemoryService,
)
from powercontext.memory.backends import SQLiteMemoryBackend
from powercontext.sources import Source, SourceMaterialization
from tests.memory.backends.contract import ContractIds
from tests.task_outcomes import TaskOutcomeReport, TaskOutcomeSource, WorkingNoteCandidatePipeline


class SourceResolver:
    def __init__(self, source: Source) -> None:
        self._source = source

    async def get(self, source: Source, /) -> Source:
        assert source == self._source
        return self._source


class EvidenceCodec(MemoryEvidenceCodec):
    def __init__(self, source: Source) -> None:
        self._source = source

    def encode_source(self, source: Source, /) -> object:
        assert source == self._source
        return {"name": source.name}

    def decode_source(self, value: object, /) -> Source:
        assert value == {"name": self._source.name}
        return self._source

    def encode_artifact(self, artifact: ArtifactRef, /) -> object:
        return {"artifact_id": artifact.artifact_id, "revision": artifact.revision}

    def decode_artifact(self, value: object, /) -> ArtifactRef:
        assert isinstance(value, dict)
        artifact_id = value.get("artifact_id")
        revision = value.get("revision")
        assert isinstance(artifact_id, str)
        assert isinstance(revision, int) and not isinstance(revision, bool)
        return ArtifactRef(artifact_id, revision)


class StaleEvidenceCodec(EvidenceCodec):
    def decode_source(self, value: object, /) -> Source:
        source = super().decode_source(value)
        return replace(source, name=f"{source.name}-changed")


class RecordingWorkingNotePipeline:
    def __init__(self) -> None:
        self._pipeline = WorkingNoteCandidatePipeline()
        self.requests: list[MemoryCandidateRequest] = []

    async def extract(self, request: MemoryCandidateRequest, /):
        self.requests.append(request)
        return await self._pipeline.extract(request)


@dataclass(frozen=True, slots=True, kw_only=True)
class UnrelatedSource(Source):
    value: str


def task_outcome_source() -> TaskOutcomeSource:
    return TaskOutcomeSource(
        name="task-outcome-42",
        materialization=SourceMaterialization.CAPTURED,
        report=TaskOutcomeReport(
            final_report="Implemented the explicit Memory facade.",
            changed_paths=("src/powercontext/context.py", "tests/memory/test_service.py"),
            git_head="abc123",
            verification=("make test: passed", "make check: passed"),
        ),
    )


def test_working_note_pipeline_emits_a_canonical_working_note(tmp_path) -> None:
    async def scenario() -> None:
        source = task_outcome_source()
        codec = EvidenceCodec(source)
        pipeline = RecordingWorkingNotePipeline()
        backend = SQLiteMemoryBackend(tmp_path / "memory.db", evidence_codec=codec)
        await backend.initialize()
        try:
            service = MemoryService(
                backend=backend,
                candidate_pipeline=pipeline,
                evidence_codec=codec,
                source_resolver=SourceResolver(source),
                id_factory=ContractIds(),
            )
            memory = await service.remember(memory=None, sources=(source,), mode="extract")
            assert memory is not None
            version = (await backend.entries(memory.ref))[0]
            assert version.kind == "working_note"
            assert version.sources == (source,)
            assert version.text == (
                "Implemented the explicit Memory facade.\n"
                "Changed paths: src/powercontext/context.py, tests/memory/test_service.py\n"
                "Git head: abc123\n"
                "Verification: make test: passed; make check: passed"
            )
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_sqlite_commit_re_resolves_evidence_and_rolls_back_when_it_changed(tmp_path) -> None:
    async def scenario() -> None:
        source = task_outcome_source()
        codec = StaleEvidenceCodec(source)
        backend = SQLiteMemoryBackend(tmp_path / "memory.db", evidence_codec=codec)
        await backend.initialize()
        try:
            service = MemoryService(
                backend=backend,
                evidence_codec=codec,
                source_resolver=SourceResolver(source),
                id_factory=ContractIds(),
            )
            with pytest.raises(InvalidMemoryEvidenceError, match="changed"):
                await service.remember(
                    memory=None,
                    sources=(source,),
                    entries=(MemoryEntryInput(kind="working_note", text="Pending handoff.", sources=(source,)),),
                    mode="append",
                )
            with pytest.raises(ArtifactNotFoundError):
                await backend.latest("contract-memory-1")
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_working_note_pipeline_ignores_unrelated_or_empty_task_outcomes(tmp_path) -> None:
    async def scenario() -> None:
        unrelated = UnrelatedSource(
            name="unrelated",
            materialization=SourceMaterialization.CAPTURED,
            value="raw transcript",
        )
        codec = EvidenceCodec(unrelated)
        backend = SQLiteMemoryBackend(tmp_path / "memory.db", evidence_codec=codec)
        await backend.initialize()
        try:
            service = MemoryService(
                backend=backend,
                candidate_pipeline=WorkingNoteCandidatePipeline(),
                evidence_codec=codec,
                source_resolver=SourceResolver(unrelated),
            )
            assert await service.remember(memory=None, sources=(unrelated,), mode="extract") is None
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_candidate_pipeline_sees_only_active_current_entries(tmp_path) -> None:
    async def scenario() -> None:
        source = task_outcome_source()
        codec = EvidenceCodec(source)
        pipeline = RecordingWorkingNotePipeline()
        backend = SQLiteMemoryBackend(tmp_path / "memory.db", evidence_codec=codec)
        await backend.initialize()
        try:
            service = MemoryService(
                backend=backend,
                candidate_pipeline=pipeline,
                evidence_codec=codec,
                source_resolver=SourceResolver(source),
                id_factory=ContractIds(),
            )
            first = await service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Active entry."),
                    MemoryEntryInput(kind="fact", text="Inactive entry."),
                ),
                mode="append",
            )
            assert first is not None
            inactive_entry = (await service.entries(first))[1]
            forgotten = await service.forget(first, entries=(inactive_entry,))
            updated = await service.remember(memory=forgotten, sources=(source,), mode="extract")
            assert updated is not None
            assert tuple(entry.text for entry in pipeline.requests[0].current_entries) == ("Active entry.",)
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_explicit_extract_without_a_candidate_provider_is_typed_error(tmp_path) -> None:
    async def scenario() -> None:
        source = task_outcome_source()
        codec = EvidenceCodec(source)
        backend = SQLiteMemoryBackend(tmp_path / "memory.db", evidence_codec=codec)
        await backend.initialize()
        try:
            service = MemoryService(
                backend=backend,
                evidence_codec=codec,
                source_resolver=SourceResolver(source),
            )
            with pytest.raises(CapabilityNotSupportedError, match="extract"):
                await service.remember(memory=None, sources=(source,), mode="extract")
        finally:
            await backend.close()

    asyncio.run(scenario())
