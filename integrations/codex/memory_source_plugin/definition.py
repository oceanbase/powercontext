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

"""Immutable, provenance-preserving snapshots of already-derived Codex memory files."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal, cast

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    MemoryEvidenceDeclaration,
    Source,
    SourceMaterialization,
    SourceProjection,
    TextEvidence,
)


class MemoryCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(min_length=1, max_length=2048)
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str = Field(min_length=1)
    redaction_applied: bool
    origin: Literal["codex-memory"] = "codex-memory"
    evidence_kind: Literal["derived_memory"] = "derived_memory"

    @field_validator("namespace", "content")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Memory namespace and content must not be blank")  # noqa: TRY003
        return value

    def snapshot_id(self) -> str:
        return "codex-memory:" + sha256(rfc8785.dumps(self.model_dump(mode="json"))).hexdigest()


class MemorySource(Source):
    capture: MemoryCapture


class MemoryText:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = MemorySource
    output_class = TextEvidence

    def project(self, source: MemorySource, /) -> TextEvidence:
        value = source.capture
        return TextEvidence(
            source_type="codex-memory-snapshot",
            source_id=source.name,
            content=value.content,
            metadata={
                "origin": value.origin,
                "evidence_kind": value.evidence_kind,
                "namespace": value.namespace,
                "relative_path": value.relative_path,
                "original_sha256": value.original_sha256,
                "redaction_applied": value.redaction_applied,
            },
        )


class MemoryDefinition:
    memory_evidence: MemoryEvidenceDeclaration | None = None
    name = "codex-memory-snapshot"
    version = "1"
    input_class = MemoryCapture
    source_class = MemorySource
    projections: tuple[SourceProjection[MemorySource], ...] = (cast(SourceProjection[MemorySource], MemoryText()),)

    async def resolve(self, value: MemoryCapture, /) -> MemorySource:
        return MemorySource(name=value.snapshot_id(), materialization=SourceMaterialization.CAPTURED, capture=value)

    async def read(self, source: MemorySource, /) -> MemoryCapture:
        return source.capture


MEMORY_DEFINITION = MemoryDefinition()
