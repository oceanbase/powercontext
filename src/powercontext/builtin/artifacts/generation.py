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
