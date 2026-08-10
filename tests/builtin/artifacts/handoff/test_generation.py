from __future__ import annotations

import asyncio

import pytest

from powercontext import SourceMaterialization
from powercontext.builtin.artifacts.handoff import (
    HandoffGenerationOutput,
    HandoffGenerationRequest,
    HandoffGenerationStatement,
    HandoffSourceCitation,
    HandoffSourceEvidence,
    LLMHandoffGenerationPipeline,
)
from powercontext.builtin.inference import GenerationResult, InvalidInferenceOutputError
from powercontext.builtin.sources import ContentSource
from powercontext.sources import SourceRef


class RecordingGenerator:
    def __init__(self, output: HandoffGenerationOutput) -> None:
        self.output = output
        self.input = None

    async def generate(self, value, /):
        self.input = value
        return GenerationResult(output=self.output)


def _evidence() -> HandoffSourceEvidence:
    citation = HandoffSourceCitation(
        source_ref=SourceRef(source_type="content", source_id="turn-1"),
    )
    return HandoffSourceEvidence(
        citation=citation,
        source=ContentSource(
            name="turn-1",
            materialization=SourceMaterialization.CAPTURED,
            content="The parser now returns a stable public error.",
        ),
    )


def test_llm_pipeline_maps_only_bounded_evidence_and_preserves_objective() -> None:
    async def scenario() -> None:
        generator = RecordingGenerator(
            HandoffGenerationOutput(
                state=(
                    HandoffGenerationStatement(
                        text="The parser returns a stable public error.",
                        evidence_ids=("source:0",),
                    ),
                ),
                disposition="continuable",
                next_action=HandoffGenerationStatement(
                    text="Run public-interface regression tests.",
                    evidence_ids=("source:0",),
                ),
            )
        )
        pipeline = LLMHandoffGenerationPipeline(generator)
        evidence = _evidence()

        draft = await pipeline.generate(
            HandoffGenerationRequest(
                objective="Complete parser error handling.",
                evidence=(evidence,),
                max_bytes=4096,
            )
        )

        assert draft.objective == "Complete parser error handling."
        assert draft.state[0].citations == (evidence.citation,)
        assert draft.next_action is not None
        assert draft.next_action.citations == (evidence.citation,)
        assert generator.input is not None
        assert generator.input.objective == draft.objective
        assert generator.input.max_bytes == 4096
        assert generator.input.evidence[0].evidence_id == "source:0"

    asyncio.run(scenario())


def test_llm_pipeline_rejects_evidence_outside_the_bounded_request() -> None:
    async def scenario() -> None:
        pipeline = LLMHandoffGenerationPipeline(
            RecordingGenerator(
                HandoffGenerationOutput(
                    state=(
                        HandoffGenerationStatement(
                            text="Unsupported state.",
                            evidence_ids=("source:99",),
                        ),
                    ),
                    disposition="blocked",
                )
            )
        )

        with pytest.raises(InvalidInferenceOutputError, match="outside the request"):
            await pipeline.generate(
                HandoffGenerationRequest(
                    objective="Complete parser error handling.",
                    evidence=(_evidence(),),
                    max_bytes=4096,
                )
            )

    asyncio.run(scenario())


def test_llm_pipeline_rejects_uncited_statements() -> None:
    async def scenario() -> None:
        pipeline = LLMHandoffGenerationPipeline(
            RecordingGenerator(
                HandoffGenerationOutput(
                    state=(
                        HandoffGenerationStatement(
                            text="Unsupported state.",
                            evidence_ids=(),
                        ),
                    ),
                    disposition="blocked",
                )
            )
        )

        with pytest.raises(InvalidInferenceOutputError, match="does not cite evidence"):
            await pipeline.generate(
                HandoffGenerationRequest(
                    objective="Complete parser error handling.",
                    evidence=(_evidence(),),
                    max_bytes=4096,
                )
            )

    asyncio.run(scenario())
