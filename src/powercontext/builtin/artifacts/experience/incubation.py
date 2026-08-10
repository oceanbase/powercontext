"""Model-backed incubation of reviewed Experience candidates."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field, ValidationError

from powercontext.builtin.artifacts.experience.models import ExperienceContent
from powercontext.builtin.inference import GenerationResult, InvalidInferenceOutputError, StructuredGenerator
from powercontext.builtin.sources import CONTENT_SOURCE_NAME, ContentSource
from powercontext.sources import Source, SourceRef

TASK_OUTCOME_SOURCE_KIND = "task-outcome"
EXPERIENCE_INCUBATION_CURSOR_NAME = "experience-incubation"
EXPERIENCE_INCUBATION_WINDOW_LIMIT = 32
MAX_EXPERIENCE_INCUBATION_SOURCES = 100
MAX_EXPERIENCE_INCUBATION_SOURCE_CHARS = 64_000
MAX_EXPERIENCE_CANDIDATE_EVIDENCE = 32
EXPERIENCE_INCUBATION_REASON = "Incubated from bounded task-outcome evidence by the configured Experience pipeline."


class ExperienceIncubationEvidence(BaseModel):
    """One bounded Task Outcome exposed to the Experience generator."""

    evidence_id: str
    content: str = Field(min_length=1, max_length=MAX_EXPERIENCE_INCUBATION_SOURCE_CHARS)


class ExperienceIncubationInput(BaseModel):
    """A bounded Source window containing only eligible Task Outcomes."""

    evidence: tuple[ExperienceIncubationEvidence, ...] = Field(
        min_length=1,
        max_length=MAX_EXPERIENCE_INCUBATION_SOURCES,
    )


class ExperienceIncubationCandidate(BaseModel):
    """One schema-valid Experience proposal citing operation-local evidence."""

    proposal: ExperienceContent
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=MAX_EXPERIENCE_CANDIDATE_EVIDENCE)


class ExperienceIncubationOutput(BaseModel):
    """Schema-bound generator output; an empty tuple is a valid no-op."""

    candidates: tuple[ExperienceIncubationCandidate, ...] = ()


class ExperienceCandidateInput(BaseModel):
    """One validated Candidate write ready for the Review Inbox."""

    proposal: ExperienceContent
    sources: tuple[SourceRef, ...] = Field(min_length=1, max_length=MAX_EXPERIENCE_CANDIDATE_EVIDENCE)
    reason: str = EXPERIENCE_INCUBATION_REASON


class ExperienceCandidatePipeline(Protocol):
    """Turn a bounded Source window into zero or more reviewed writes."""

    async def incubate(self, sources: tuple[Source, ...], /) -> tuple[ExperienceCandidateInput, ...]:
        """Return validated writes without allocating Candidate or Artifact identity."""

        ...


class LLMExperienceCandidatePipeline:
    """Map schema-valid model proposals back to exact Task Outcome Sources."""

    def __init__(
        self,
        generator: StructuredGenerator[ExperienceIncubationInput, ExperienceIncubationOutput],
    ) -> None:
        self._generator = generator

    async def incubate(self, sources: tuple[Source, ...], /) -> tuple[ExperienceCandidateInput, ...]:
        incubation_input, evidence = _incubation_input(sources)
        if incubation_input is None:
            return ()
        result = await self._generator.generate(incubation_input)
        output = _validated_output(result)
        candidates: list[ExperienceCandidateInput] = []
        seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        for candidate in output.candidates:
            selected = _selected_sources(candidate.evidence_ids, evidence)
            key = (
                candidate.proposal.model_dump_json(),
                tuple((source.source_type, source.source_id) for source in selected),
            )
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                ExperienceCandidateInput(
                    proposal=candidate.proposal,
                    sources=selected,
                )
            )
        return tuple(candidates)


def _incubation_input(
    sources: tuple[Source, ...],
) -> tuple[ExperienceIncubationInput | None, dict[str, SourceRef]]:
    projected: list[ExperienceIncubationEvidence] = []
    evidence: dict[str, SourceRef] = {}
    for source in sources:
        if not isinstance(source, ContentSource) or source.metadata.get("kind") != TASK_OUTCOME_SOURCE_KIND:
            continue
        evidence_id = f"source:{CONTENT_SOURCE_NAME}/{source.name}"
        projected.append(
            ExperienceIncubationEvidence(
                evidence_id=evidence_id,
                content=source.content[:MAX_EXPERIENCE_INCUBATION_SOURCE_CHARS],
            )
        )
        evidence[evidence_id] = SourceRef(source_type=CONTENT_SOURCE_NAME, source_id=source.name)
    if not projected:
        return None, {}
    try:
        return ExperienceIncubationInput(evidence=tuple(projected)), evidence
    except ValidationError as error:
        raise InvalidInferenceOutputError(
            "experience-incubate",
            "eligible Task Outcome evidence exceeded the bounded input contract",
        ) from error


def _validated_output(result: GenerationResult[ExperienceIncubationOutput]) -> ExperienceIncubationOutput:
    if not isinstance(result, GenerationResult):
        raise InvalidInferenceOutputError("experience-incubate", "generator returned the wrong output type")
    try:
        return ExperienceIncubationOutput.model_validate(result.output)
    except ValidationError as error:
        raise InvalidInferenceOutputError(
            "experience-incubate",
            "generator returned an invalid output tree",
        ) from error


def _selected_sources(values: tuple[str, ...], evidence: dict[str, SourceRef]) -> tuple[SourceRef, ...]:
    selected: list[SourceRef] = []
    seen: set[tuple[str, str]] = set()
    for evidence_id in values:
        try:
            source = evidence[evidence_id]
        except KeyError:
            raise InvalidInferenceOutputError(
                "experience-incubate",
                "candidate cited evidence outside the current Task Outcome window",
            ) from None
        key = (source.source_type, source.source_id)
        if key not in seen:
            seen.add(key)
            selected.append(source)
    return tuple(selected)


__all__ = [
    "EXPERIENCE_INCUBATION_CURSOR_NAME",
    "EXPERIENCE_INCUBATION_REASON",
    "EXPERIENCE_INCUBATION_WINDOW_LIMIT",
    "MAX_EXPERIENCE_CANDIDATE_EVIDENCE",
    "MAX_EXPERIENCE_INCUBATION_SOURCES",
    "MAX_EXPERIENCE_INCUBATION_SOURCE_CHARS",
    "TASK_OUTCOME_SOURCE_KIND",
    "ExperienceCandidateInput",
    "ExperienceCandidatePipeline",
    "ExperienceIncubationCandidate",
    "ExperienceIncubationEvidence",
    "ExperienceIncubationInput",
    "ExperienceIncubationOutput",
    "LLMExperienceCandidatePipeline",
]
