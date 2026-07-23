from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from pydantic import JsonValue, ValidationError

from powercontext import ArtifactNotFoundError
from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.inference import GenerationResult, InferenceUnavailableError, InvalidInferenceOutputError
from powercontext.memory import (
    InvalidMemoryEvidenceError,
    LLMMemoryCandidatePipeline,
    MemoryCandidateRequest,
    MemoryEntryInput,
    MemoryEntryVersion,
    MemoryEvidenceCodec,
    MemoryExtractionCandidate,
    MemoryExtractionInput,
    MemoryExtractionOutput,
    MemoryService,
)
from powercontext.memory.backends import SQLiteMemoryBackend
from powercontext.sources import Source, SourceMaterialization
from tests.memory.backends.contract import ContractIds


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationSource(Source):
    messages: tuple[str, ...]


class RecordingGenerator:
    def __init__(self, output: MemoryExtractionOutput) -> None:
        self.output = output
        self.inputs: list[MemoryExtractionInput] = []

    async def generate(self, value: MemoryExtractionInput, /) -> GenerationResult[MemoryExtractionOutput]:
        self.inputs.append(value)
        return GenerationResult(output=self.output)


class RevisingGenerator:
    async def generate(self, value: MemoryExtractionInput, /) -> GenerationResult[MemoryExtractionOutput]:
        target = value.current_entries[0]
        return GenerationResult(
            output=MemoryExtractionOutput(
                candidates=(
                    MemoryExtractionCandidate(
                        intent="revise",
                        entry_id=target.entry_id,
                        kind="constraint",
                        text="Run make check and make test after dependency changes.",
                        evidence_ids=("source:0",),
                        reason="user_correction",
                    ),
                )
            )
        )


class FailingGenerator:
    async def generate(self, value: MemoryExtractionInput, /) -> GenerationResult[MemoryExtractionOutput]:
        del value
        raise InferenceUnavailableError("generate")


class SourceResolver:
    def __init__(self, source: Source) -> None:
        self._source = source

    async def get(self, source: Source, /) -> Source:
        assert source == self._source
        return self._source


class ExtractionEvidenceProjector:
    def project_source(self, source: Source, /) -> JsonValue:
        assert isinstance(source, ConversationSource)
        return {
            "name": source.name,
            "materialization": source.materialization.value,
            "messages": list(source.messages),
        }

    def project_artifact(self, artifact: Artifact[object], /) -> JsonValue:
        return {
            "artifact_id": artifact.artifact_id,
            "revision": artifact.revision,
            "content": artifact.content,
        }


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
        assert isinstance(revision, int)
        return ArtifactRef(artifact_id, revision)


def conversation_source() -> ConversationSource:
    return ConversationSource(
        name="turn-42",
        materialization=SourceMaterialization.CAPTURED,
        messages=("Use uv add for dependencies.",),
    )


def current_entry() -> MemoryEntryVersion:
    return MemoryEntryVersion(
        memory_artifact_id="memory-1",
        entry_id="entry-verify",
        entry_version_id="version-verify-1",
        version=1,
        previous_version_id=None,
        kind="constraint",
        text="Run make check before committing.",
        entry_content_hash="a" * 64,
        created_in_revision=1,
    )


def test_llm_pipeline_maps_add_and_revise_to_exact_bounded_evidence() -> None:
    async def scenario() -> None:
        source = conversation_source()
        artifact = Artifact(artifact_id="task-result", revision=3, content={"status": "tests passed"})
        entry = current_entry()
        generator = RecordingGenerator(
            MemoryExtractionOutput(
                candidates=(
                    MemoryExtractionCandidate(
                        intent="add",
                        kind="preference",
                        text="Use uv add for dependencies.",
                        evidence_ids=("source:0",),
                    ),
                    MemoryExtractionCandidate(
                        intent="revise",
                        entry_id=entry.entry_id,
                        kind="constraint",
                        text="Run make check and make test before committing.",
                        evidence_ids=("artifact:0",),
                    ),
                )
            )
        )
        pipeline = LLMMemoryCandidatePipeline(generator, evidence_projector=ExtractionEvidenceProjector())

        candidates = await pipeline.extract(
            MemoryCandidateRequest(sources=(source,), artifacts=(artifact,), current_entries=(entry,))
        )

        assert candidates == (
            MemoryEntryInput(
                kind="preference",
                text="Use uv add for dependencies.",
                sources=(source,),
            ),
            MemoryEntryInput(
                entry=entry,
                kind="constraint",
                text="Run make check and make test before committing.",
                artifacts=(artifact,),
            ),
        )
        bounded = generator.inputs[0]
        assert tuple(item.evidence_id for item in bounded.evidence) == ("source:0", "artifact:0")
        assert bounded.evidence[0].content == {
            "name": "turn-42",
            "materialization": "captured",
            "messages": ["Use uv add for dependencies."],
        }
        assert bounded.evidence[1].content == {
            "artifact_id": "task-result",
            "revision": 3,
            "content": {"status": "tests passed"},
        }
        assert bounded.current_entries[0].entry_id == entry.entry_id
        assert "entry_version_id" not in repr(bounded.current_entries[0])

    asyncio.run(scenario())


def test_empty_extraction_output_is_a_no_op() -> None:
    async def scenario() -> None:
        pipeline = LLMMemoryCandidatePipeline(RecordingGenerator(MemoryExtractionOutput()))

        candidates = await pipeline.extract(
            MemoryCandidateRequest(sources=(conversation_source(),), artifacts=(), current_entries=())
        )

        assert candidates == ()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        (
            MemoryExtractionCandidate(
                intent="add",
                kind="fact",
                text="Unsupported claim.",
                evidence_ids=(),
            ),
            "does not cite evidence",
        ),
        (
            MemoryExtractionCandidate(
                intent="add",
                kind="fact",
                text="Unsupported claim.",
                evidence_ids=("source:99",),
            ),
            "outside the request",
        ),
        (
            MemoryExtractionCandidate(
                intent="revise",
                entry_id="entry-missing",
                kind="constraint",
                text="Unsupported revision.",
                evidence_ids=("source:0",),
            ),
            "active entry",
        ),
    ],
)
def test_llm_pipeline_rejects_claims_outside_bounded_inputs(
    candidate: MemoryExtractionCandidate,
    message: str,
) -> None:
    async def scenario() -> None:
        pipeline = LLMMemoryCandidatePipeline(RecordingGenerator(MemoryExtractionOutput(candidates=(candidate,))))
        with pytest.raises(InvalidInferenceOutputError, match=message):
            await pipeline.extract(
                MemoryCandidateRequest(
                    sources=(conversation_source(),),
                    artifacts=(),
                    current_entries=(current_entry(),),
                )
            )

    asyncio.run(scenario())


def test_extraction_schema_cannot_propose_authoritative_lifecycle_fields() -> None:
    assert set(MemoryExtractionCandidate.model_fields) == {
        "intent",
        "kind",
        "text",
        "evidence_ids",
        "entry_id",
        "reason",
    }


def test_extraction_schema_validates_the_complete_output_tree() -> None:
    with pytest.raises(ValidationError):
        MemoryExtractionOutput.model_validate({
            "candidates": [
                {
                    "intent": "delete",
                    "kind": "constraint",
                    "text": "Unsupported lifecycle operation.",
                    "evidence_ids": ["source:0"],
                }
            ]
        })


def test_default_evidence_projection_fails_closed_for_unsupported_json() -> None:
    async def scenario() -> None:
        pipeline = LLMMemoryCandidatePipeline(RecordingGenerator(MemoryExtractionOutput()))
        with pytest.raises(InvalidMemoryEvidenceError, match="projection"):
            await pipeline.extract(
                MemoryCandidateRequest(
                    sources=(),
                    artifacts=(Artifact(artifact_id="unsupported", revision=1, content=object()),),
                    current_entries=(),
                )
            )

    asyncio.run(scenario())


def test_memory_service_commits_model_revision_through_existing_validation(tmp_path) -> None:
    async def scenario() -> None:
        source = conversation_source()
        codec = EvidenceCodec(source)
        backend = SQLiteMemoryBackend(tmp_path / "memory.db", evidence_codec=codec)
        await backend.initialize()
        try:
            ids = ContractIds()
            initial_service = MemoryService(backend=backend, id_factory=ids)
            initial = await initial_service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="constraint", text="Run make check before committing."),),
                mode="append",
            )
            assert initial is not None
            service = MemoryService(
                backend=backend,
                candidate_pipeline=LLMMemoryCandidatePipeline(RevisingGenerator()),
                evidence_codec=codec,
                source_resolver=SourceResolver(source),
                id_factory=ids,
            )

            revised = await service.remember(memory=initial, sources=(source,), mode="extract")

            assert revised is not None
            assert revised.revision == 2
            assert revised.lineage.sources == (source,)
            entry = (await service.entries(revised))[0]
            assert entry.version == 2
            assert entry.text == "Run make check and make test after dependency changes."
            assert entry.sources == (source,)
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_duplicate_model_additions_create_only_one_entry(tmp_path) -> None:
    async def scenario() -> None:
        source = conversation_source()
        codec = EvidenceCodec(source)
        candidate = MemoryExtractionCandidate(
            intent="add",
            kind="preference",
            text="Use uv add for dependencies.",
            evidence_ids=("source:0",),
        )
        pipeline = LLMMemoryCandidatePipeline(
            RecordingGenerator(MemoryExtractionOutput(candidates=(candidate, candidate)))
        )
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
            assert len(await service.entries(memory)) == 1
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_generation_failure_does_not_commit_a_partial_revision(tmp_path) -> None:
    async def scenario() -> None:
        source = conversation_source()
        codec = EvidenceCodec(source)
        backend = SQLiteMemoryBackend(tmp_path / "memory.db", evidence_codec=codec)
        await backend.initialize()
        try:
            service = MemoryService(
                backend=backend,
                candidate_pipeline=LLMMemoryCandidatePipeline(FailingGenerator()),
                evidence_codec=codec,
                source_resolver=SourceResolver(source),
                id_factory=ContractIds(),
            )

            with pytest.raises(InferenceUnavailableError):
                await service.remember(memory=None, sources=(source,), mode="extract")
            with pytest.raises(ArtifactNotFoundError):
                await backend.latest("contract-memory-1")
        finally:
            await backend.close()

    asyncio.run(scenario())
