from __future__ import annotations

import asyncio

import pytest

from powercontext.builtin.artifacts.experience import (
    MAX_EXPERIENCE_INCUBATION_SOURCE_CHARS,
    ExperienceContent,
    ExperienceIncubationCandidate,
    ExperienceIncubationInput,
    ExperienceIncubationOutput,
    LLMExperienceCandidatePipeline,
)
from powercontext.builtin.inference import GenerationResult, InvalidInferenceOutputError
from powercontext.builtin.sources import ContentSource
from powercontext.sources import SourceMaterialization, SourceRef


class _Generator:
    def __init__(self, output: ExperienceIncubationOutput) -> None:
        self.output = output
        self.inputs: list[ExperienceIncubationInput] = []

    async def generate(self, value: ExperienceIncubationInput, /) -> GenerationResult[ExperienceIncubationOutput]:
        self.inputs.append(value)
        return GenerationResult(output=self.output)


def _source(
    source_id: str,
    *,
    content: str = "The verified repair passed.",
    kind: str = "task-outcome",
) -> ContentSource:
    return ContentSource(
        name=source_id,
        materialization=SourceMaterialization.CAPTURED,
        content=content,
        metadata={"kind": kind},
    )


def _proposal() -> ExperienceContent:
    return ExperienceContent(
        situation="A strict configuration fixture failed.",
        action="Set the mode to strict and ran the fixture.",
        outcome="The independently observed check passed.",
        lesson="Run the strict fixture after changing configuration.",
    )


def test_pipeline_maps_only_bounded_task_outcomes_to_exact_source_refs() -> None:
    async def scenario() -> None:
        generator = _Generator(
            ExperienceIncubationOutput(
                candidates=(
                    ExperienceIncubationCandidate(
                        proposal=_proposal(),
                        evidence_ids=(
                            "source:content/task-1",
                            "source:content/task-1",
                        ),
                    ),
                )
            )
        )
        pipeline = LLMExperienceCandidatePipeline(generator)

        candidates = await pipeline.incubate((
            _source("ordinary", kind="prompt"),
            _source("task-1", content="x" * (MAX_EXPERIENCE_INCUBATION_SOURCE_CHARS + 10)),
        ))

        assert len(generator.inputs) == 1
        assert generator.inputs[0].evidence[0].evidence_id == "source:content/task-1"
        assert len(generator.inputs[0].evidence[0].content) == MAX_EXPERIENCE_INCUBATION_SOURCE_CHARS
        assert candidates[0].proposal == _proposal()
        assert candidates[0].sources == (SourceRef(source_type="content", source_id="task-1"),)

    asyncio.run(scenario())


def test_pipeline_skips_generation_when_the_window_has_no_task_outcomes() -> None:
    async def scenario() -> None:
        generator = _Generator(ExperienceIncubationOutput())
        pipeline = LLMExperienceCandidatePipeline(generator)

        candidates = await pipeline.incubate((_source("ordinary", kind="prompt"),))

        assert candidates == ()
        assert generator.inputs == []

    asyncio.run(scenario())


def test_pipeline_rejects_evidence_outside_the_current_window() -> None:
    async def scenario() -> None:
        generator = _Generator(
            ExperienceIncubationOutput(
                candidates=(
                    ExperienceIncubationCandidate(
                        proposal=_proposal(),
                        evidence_ids=("source:content/not-in-window",),
                    ),
                )
            )
        )
        pipeline = LLMExperienceCandidatePipeline(generator)

        with pytest.raises(InvalidInferenceOutputError):
            await pipeline.incubate((_source("task-1"),))

    asyncio.run(scenario())
