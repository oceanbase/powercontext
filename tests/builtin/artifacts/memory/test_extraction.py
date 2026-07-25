from __future__ import annotations

import asyncio

import pytest

from powercontext import SourceMaterialization
from powercontext.builtin.artifacts.memory import (
    LLMMemoryCandidatePipeline,
    MemoryCandidateRequest,
    MemoryEntryInput,
    MemoryExtractionCandidate,
    MemoryExtractionOutput,
)
from powercontext.builtin.inference import GenerationResult, InvalidInferenceOutputError
from powercontext.builtin.sources import ContentSource


class RecordingGenerator:
    def __init__(self, output: MemoryExtractionOutput) -> None:
        self.output = output

    async def generate(self, value, /):
        return GenerationResult(output=self.output)


def _source() -> ContentSource:
    return ContentSource(
        name="turn-1",
        materialization=SourceMaterialization.CAPTURED,
        content="Use uv for dependency management.",
    )


def test_llm_pipeline_maps_only_bounded_evidence_to_untrusted_candidates() -> None:
    async def scenario() -> None:
        source = _source()
        pipeline = LLMMemoryCandidatePipeline(
            RecordingGenerator(
                MemoryExtractionOutput(
                    candidates=(
                        MemoryExtractionCandidate(
                            intent="add",
                            kind="preference",
                            text="Use uv for dependency management.",
                            evidence_ids=("source:0",),
                        ),
                    )
                )
            )
        )

        candidates = await pipeline.extract(MemoryCandidateRequest(sources=(source,), artifacts=(), current_entries=()))

        assert candidates == (
            MemoryEntryInput(
                kind="preference",
                text="Use uv for dependency management.",
                sources=(source,),
            ),
        )

    asyncio.run(scenario())


def test_llm_pipeline_rejects_evidence_outside_the_bounded_request() -> None:
    async def scenario() -> None:
        pipeline = LLMMemoryCandidatePipeline(
            RecordingGenerator(
                MemoryExtractionOutput(
                    candidates=(
                        MemoryExtractionCandidate(
                            intent="add",
                            kind="fact",
                            text="Unsupported claim.",
                            evidence_ids=("source:99",),
                        ),
                    )
                )
            )
        )
        with pytest.raises(InvalidInferenceOutputError, match="outside the request"):
            await pipeline.extract(MemoryCandidateRequest(sources=(_source(),), artifacts=(), current_entries=()))

    asyncio.run(scenario())
