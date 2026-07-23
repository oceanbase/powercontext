"""Model-backed extraction of untrusted Memory candidates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from powercontext.artifacts import Artifact
from powercontext.errors import InvalidMemoryEvidenceError
from powercontext.inference import GenerationResult, InvalidInferenceOutputError, StructuredGenerator
from powercontext.memory.models import MemoryEntryInput, MemoryEntryVersion
from powercontext.memory.protocols import MemoryCandidateRequest
from powercontext.sources import Source

MemoryExtractionIntent: TypeAlias = Literal["add", "revise"]
KnownMemoryEntryKind: TypeAlias = Literal["fact", "preference", "decision", "constraint", "working_note"]
MemoryEvidenceType: TypeAlias = Literal["source", "artifact"]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
_JSON_VALUE = TypeAdapter(JsonValue)


class MemoryExtractionEvidence(BaseModel):
    """One operation-local evidence value visible to the generator."""

    model_config = _MODEL_CONFIG

    evidence_id: str
    evidence_type: MemoryEvidenceType
    content: JsonValue


class MemoryExtractionCurrentEntry(BaseModel):
    """One active entry from the selected Memory head."""

    model_config = _MODEL_CONFIG

    entry_id: str
    kind: str
    text: str


class MemoryExtractionInput(BaseModel):
    """Only bounded operation evidence and active current entries."""

    model_config = _MODEL_CONFIG

    evidence: tuple[MemoryExtractionEvidence, ...]
    current_entries: tuple[MemoryExtractionCurrentEntry, ...]


class MemoryExtractionCandidate(BaseModel):
    """One model-proposed addition or revision with operation-local citations."""

    model_config = _MODEL_CONFIG

    intent: MemoryExtractionIntent
    kind: KnownMemoryEntryKind
    text: str
    evidence_ids: tuple[str, ...]
    entry_id: str | None = None
    reason: str | None = None


class MemoryExtractionOutput(BaseModel):
    """Schema-bound Memory extraction output; an empty tuple is a no-op."""

    model_config = _MODEL_CONFIG

    candidates: tuple[MemoryExtractionCandidate, ...] = ()


class MemoryEvidenceProjector(Protocol):
    """Project adapter-owned evidence values into explicit JSON-visible content."""

    def project_source(self, source: Source, /) -> JsonValue:
        """Return the stable JSON projection exposed to the model."""

        ...

    def project_artifact(self, artifact: Artifact[object], /) -> JsonValue:
        """Return the stable JSON projection exposed to the model."""

        ...


class DefaultMemoryEvidenceProjector:
    """Expose only stable public evidence metadata unless a caller opts in to more."""

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


class LLMMemoryCandidatePipeline:
    """Map schema-valid model proposals back to exact bounded domain values."""

    def __init__(
        self,
        generator: StructuredGenerator[MemoryExtractionInput, MemoryExtractionOutput],
        *,
        evidence_projector: MemoryEvidenceProjector | None = None,
    ) -> None:
        self._generator = generator
        self._evidence_projector = (
            DefaultMemoryEvidenceProjector() if evidence_projector is None else evidence_projector
        )

    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        """Generate and map candidates that still require MemoryService validation."""

        extraction_input, evidence = _extraction_input(request, self._evidence_projector)
        result = await self._generator.generate(extraction_input)
        output = _validated_output(result)
        current_entries = {entry.entry_id: entry for entry in request.current_entries}
        revised_entries: set[str] = set()
        candidates: list[MemoryEntryInput] = []
        for proposal in output.candidates:
            selected_sources, selected_artifacts = _selected_evidence(proposal, evidence)
            entry = _revision_target(proposal, current_entries, revised_entries)
            candidates.append(
                MemoryEntryInput(
                    entry=entry,
                    kind=proposal.kind,
                    text=proposal.text,
                    sources=selected_sources,
                    artifacts=selected_artifacts,
                    reason=proposal.reason,
                )
            )
        return tuple(candidates)


def _validated_output(result: GenerationResult[MemoryExtractionOutput]) -> MemoryExtractionOutput:
    if not isinstance(result, GenerationResult):
        raise InvalidInferenceOutputError("memory-extract", "generator returned the wrong output type")
    try:
        return MemoryExtractionOutput.model_validate(result.output)
    except ValidationError as error:
        raise InvalidInferenceOutputError("memory-extract", "generator returned an invalid output tree") from error


def _extraction_input(
    request: MemoryCandidateRequest,
    projector: MemoryEvidenceProjector,
) -> tuple[MemoryExtractionInput, dict[str, Source | Artifact[object]]]:
    evidence_values: list[MemoryExtractionEvidence] = []
    evidence: dict[str, Source | Artifact[object]] = {}
    for index, source in enumerate(request.sources):
        evidence_id = f"source:{index}"
        evidence[evidence_id] = source
        evidence_values.append(
            MemoryExtractionEvidence(
                evidence_id=evidence_id,
                evidence_type="source",
                content=_validated_json(projector.project_source(source)),
            )
        )
    for index, artifact in enumerate(request.artifacts):
        evidence_id = f"artifact:{index}"
        evidence[evidence_id] = artifact
        evidence_values.append(
            MemoryExtractionEvidence(
                evidence_id=evidence_id,
                evidence_type="artifact",
                content=_validated_json(projector.project_artifact(artifact)),
            )
        )
    current_entries = tuple(
        MemoryExtractionCurrentEntry(entry_id=entry.entry_id, kind=entry.kind, text=entry.text)
        for entry in request.current_entries
    )
    return MemoryExtractionInput(evidence=tuple(evidence_values), current_entries=current_entries), evidence


def _selected_evidence(
    proposal: MemoryExtractionCandidate,
    evidence: Mapping[str, Source | Artifact[object]],
) -> tuple[tuple[Source, ...], tuple[Artifact[object], ...]]:
    if not proposal.evidence_ids:
        raise InvalidInferenceOutputError("memory-extract", "candidate does not cite evidence")
    selected_sources: list[Source] = []
    selected_artifacts: list[Artifact[object]] = []
    for evidence_id in dict.fromkeys(proposal.evidence_ids):
        try:
            value = evidence[evidence_id]
        except KeyError:
            raise InvalidInferenceOutputError(
                "memory-extract", "candidate cites evidence outside the request"
            ) from None
        if isinstance(value, Source):
            selected_sources.append(value)
        else:
            selected_artifacts.append(value)
    return tuple(selected_sources), tuple(selected_artifacts)


def _revision_target(
    proposal: MemoryExtractionCandidate,
    current_entries: Mapping[str, MemoryEntryVersion],
    revised_entries: set[str],
) -> MemoryEntryVersion | None:
    if proposal.intent == "add":
        if proposal.entry_id is not None:
            raise InvalidInferenceOutputError("memory-extract", "add candidate must not identify an existing entry")
        return None
    if proposal.intent != "revise":
        raise InvalidInferenceOutputError("memory-extract", "candidate has an unsupported intent")
    if proposal.entry_id is None:
        raise InvalidInferenceOutputError("memory-extract", "revise candidate must identify an active entry")
    try:
        entry = current_entries[proposal.entry_id]
    except KeyError:
        raise InvalidInferenceOutputError(
            "memory-extract", "revise candidate does not identify an active entry"
        ) from None
    if proposal.entry_id in revised_entries:
        raise InvalidInferenceOutputError("memory-extract", "an active entry can only be revised once per extraction")
    revised_entries.add(proposal.entry_id)
    return entry


def _validated_json(value: object) -> JsonValue:
    try:
        return _JSON_VALUE.validate_python(value)
    except ValidationError as error:
        raise InvalidMemoryEvidenceError("projection") from error


__all__ = [
    "DefaultMemoryEvidenceProjector",
    "KnownMemoryEntryKind",
    "LLMMemoryCandidatePipeline",
    "MemoryEvidenceProjector",
    "MemoryExtractionCandidate",
    "MemoryExtractionCurrentEntry",
    "MemoryExtractionEvidence",
    "MemoryExtractionInput",
    "MemoryExtractionIntent",
    "MemoryExtractionOutput",
]
