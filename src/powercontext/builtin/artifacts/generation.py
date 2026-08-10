"""Shared bounded input contract for reviewed Artifact generation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

MAX_GENERATION_EVIDENCE = 32
MAX_GENERATION_EVIDENCE_CHARS = 64_000


class GenerationEvidenceKind(StrEnum):
    """Exact evidence kind exposed to a generation model."""

    SOURCE = "source"
    ARTIFACT = "artifact"


class GenerationEvidence(BaseModel):
    """One exact bounded evidence projection."""

    evidence_id: str
    kind: GenerationEvidenceKind
    content: str = Field(min_length=1, max_length=MAX_GENERATION_EVIDENCE_CHARS)
    truncated: bool = False


class ArtifactGenerationInput(BaseModel):
    """A bounded, caller-selected evidence set with an optional exact target."""

    evidence: tuple[GenerationEvidence, ...] = Field(min_length=1, max_length=MAX_GENERATION_EVIDENCE)
    target_evidence_id: str | None = None


__all__ = [
    "MAX_GENERATION_EVIDENCE",
    "MAX_GENERATION_EVIDENCE_CHARS",
    "ArtifactGenerationInput",
    "GenerationEvidence",
    "GenerationEvidenceKind",
]
