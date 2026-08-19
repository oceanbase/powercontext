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

"""Model-backed generation of untrusted Handoff Drafts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol, TypeAlias

from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from powercontext.artifacts import Artifact
from powercontext.builtin.artifacts.handoff.models import (
    HandoffArtifactEvidence,
    HandoffCitation,
    HandoffDisposition,
    HandoffDraft,
    HandoffGenerationEvidence,
    HandoffGenerationRequest,
    HandoffMemoryEvidence,
    HandoffOmission,
    HandoffSourceEvidence,
    HandoffStatement,
)
from powercontext.builtin.artifacts.memory import MemoryEntryVersion
from powercontext.builtin.inference import GenerationResult, InvalidInferenceOutputError, StructuredGenerator
from powercontext.sources import Source

HandoffGenerationEvidenceType: TypeAlias = Literal["source", "artifact", "memory"]

_JSON_VALUE = TypeAdapter(JsonValue)


class HandoffGenerationEvidenceInput(BaseModel):
    """One operation-local evidence value visible to the generator."""

    evidence_id: str
    evidence_type: HandoffGenerationEvidenceType
    content: JsonValue


class HandoffGenerationInput(BaseModel):
    """Caller-owned objective, bounded evidence, and output budget."""

    objective: str
    evidence: tuple[HandoffGenerationEvidenceInput, ...]
    max_bytes: int


class HandoffGenerationStatement(BaseModel):
    """One generated statement with operation-local citations."""

    text: str
    evidence_ids: tuple[str, ...]


class HandoffGenerationOmission(BaseModel):
    """One generated omission with optional operation-local evidence."""

    text: str
    evidence_id: str | None = None


class HandoffGenerationOutput(BaseModel):
    """Schema-bound Handoff generation output."""

    state: tuple[HandoffGenerationStatement, ...]
    disposition: HandoffDisposition
    next_action: HandoffGenerationStatement | None = None
    omissions: tuple[HandoffGenerationOmission, ...] = ()


class HandoffEvidenceProjector(Protocol):
    """Project canonical Handoff evidence into explicit JSON-visible content."""

    def project_source(self, source: Source, /) -> JsonValue: ...

    def project_artifact(self, artifact: Artifact[object], /) -> JsonValue: ...

    def project_memory_entry(self, entry: MemoryEntryVersion, /) -> JsonValue: ...


class DefaultHandoffEvidenceProjector:
    """Expose stable public evidence fields unless a caller opts in to more."""

    def project_source(self, source: Source, /) -> JsonValue:
        return _validated_json({
            "name": source.name,
            "materialization": source.materialization.value,
            "description": source.description,
        })

    def project_artifact(self, artifact: Artifact[object], /) -> JsonValue:
        return _validated_json({
            "artifact_id": artifact.artifact_id,
            "revision": artifact.revision,
            "family": artifact.family,
            "content": artifact.content,
        })

    def project_memory_entry(self, entry: MemoryEntryVersion, /) -> JsonValue:
        return _validated_json({
            "entry_id": entry.entry_id,
            "entry_version_id": entry.entry_version_id,
            "kind": entry.kind,
            "text": entry.text,
        })


class LLMHandoffGenerationPipeline:
    """Map schema-valid model output back to exact bounded citations."""

    def __init__(
        self,
        generator: StructuredGenerator[HandoffGenerationInput, HandoffGenerationOutput],
        *,
        evidence_projector: HandoffEvidenceProjector | None = None,
    ) -> None:
        self._generator = generator
        self._evidence_projector = (
            DefaultHandoffEvidenceProjector() if evidence_projector is None else evidence_projector
        )

    async def generate(self, request: HandoffGenerationRequest, /) -> HandoffDraft:
        generation_input, citations = _generation_input(request, self._evidence_projector)
        output = _validated_output(await self._generator.generate(generation_input))
        try:
            return HandoffDraft(
                objective=request.objective,
                state=tuple(_statement(value, citations) for value in output.state),
                disposition=output.disposition,
                next_action=None if output.next_action is None else _statement(output.next_action, citations),
                omissions=tuple(_omission(value, citations) for value in output.omissions),
            )
        except ValidationError as error:
            raise InvalidInferenceOutputError(
                "handoff-generate",
                "generated content violates the Handoff Draft contract",
            ) from error


def _validated_output(result: GenerationResult[HandoffGenerationOutput]) -> HandoffGenerationOutput:
    if not isinstance(result, GenerationResult):
        raise InvalidInferenceOutputError("handoff-generate", "generator returned the wrong output type")
    try:
        return HandoffGenerationOutput.model_validate(result.output)
    except ValidationError as error:
        raise InvalidInferenceOutputError("handoff-generate", "generator returned an invalid output tree") from error


def _generation_input(
    request: HandoffGenerationRequest,
    projector: HandoffEvidenceProjector,
) -> tuple[HandoffGenerationInput, dict[str, HandoffCitation]]:
    values: list[HandoffGenerationEvidenceInput] = []
    citations: dict[str, HandoffCitation] = {}
    for index, evidence in enumerate(request.evidence):
        evidence_id = f"{evidence.citation.kind}:{index}"
        citations[evidence_id] = evidence.citation
        values.append(
            HandoffGenerationEvidenceInput(
                evidence_id=evidence_id,
                evidence_type=evidence.citation.kind,
                content=_project_evidence(evidence, projector),
            )
        )
    return (
        HandoffGenerationInput(
            objective=request.objective,
            evidence=tuple(values),
            max_bytes=request.max_bytes,
        ),
        citations,
    )


def _project_evidence(
    evidence: HandoffGenerationEvidence,
    projector: HandoffEvidenceProjector,
) -> JsonValue:
    if isinstance(evidence, HandoffSourceEvidence):
        return _validated_json(projector.project_source(evidence.source))
    if isinstance(evidence, HandoffArtifactEvidence):
        return _validated_json(projector.project_artifact(evidence.artifact))
    if isinstance(evidence, HandoffMemoryEvidence):
        return _validated_json(projector.project_memory_entry(evidence.entry))
    raise TypeError(f"unsupported Handoff evidence: {type(evidence).__name__}")  # noqa: TRY003


def _statement(
    value: HandoffGenerationStatement,
    citations: Mapping[str, HandoffCitation],
) -> HandoffStatement:
    if not value.evidence_ids:
        raise InvalidInferenceOutputError("handoff-generate", "statement does not cite evidence")
    return HandoffStatement(
        text=value.text,
        citations=tuple(_citation(evidence_id, citations) for evidence_id in dict.fromkeys(value.evidence_ids)),
    )


def _omission(
    value: HandoffGenerationOmission,
    citations: Mapping[str, HandoffCitation],
) -> HandoffOmission:
    return HandoffOmission(
        text=value.text,
        citation=None if value.evidence_id is None else _citation(value.evidence_id, citations),
    )


def _citation(evidence_id: str, citations: Mapping[str, HandoffCitation]) -> HandoffCitation:
    try:
        return citations[evidence_id]
    except KeyError:
        raise InvalidInferenceOutputError(
            "handoff-generate",
            "generated content cites evidence outside the request",
        ) from None


def _validated_json(value: object) -> JsonValue:
    try:
        return _JSON_VALUE.validate_python(value)
    except ValidationError as error:
        raise InvalidInferenceOutputError(
            "handoff-generate",
            "Handoff evidence projection is not JSON-compatible",
        ) from error


__all__ = [
    "DefaultHandoffEvidenceProjector",
    "HandoffEvidenceProjector",
    "HandoffGenerationEvidenceInput",
    "HandoffGenerationInput",
    "HandoffGenerationOmission",
    "HandoffGenerationOutput",
    "HandoffGenerationStatement",
    "LLMHandoffGenerationPipeline",
]
