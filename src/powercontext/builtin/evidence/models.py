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

"""Content-free evidence manifests and transient model projections."""

from __future__ import annotations

import hashlib
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.errors import PowerContextError
from powercontext.sources import SourceRef

EVIDENCE_TRANSFORM_VERSION = "powercontext.dream.evidence.v1"


class EvidenceResolutionError(PowerContextError, ValueError):
    """A stable admission failure with no evidence content in the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EvidenceLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_items: int = Field(default=32, ge=1, le=32)
    max_bytes: int = Field(default=65_536, ge=1, le=65_536)
    max_nodes: int = Field(default=128, ge=1, le=128)
    max_edges: int = Field(default=256, ge=1, le=256)
    max_depth: int = Field(default=8, ge=1, le=8)


class EvidenceNode(BaseModel):
    """An immutable content digest and its exact provenance, never its body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    kind: Literal["source", "experience", "memory", "unresolved"]
    digest: str
    source: SourceRef | None = None
    artifact: ArtifactRef | None = None
    memory_citations: tuple[MemoryCitation, ...] = ()
    role: Literal["root", "derived", "lineage_only", "unresolved"]
    historical: bool = False
    current_entry_version_id: str | None = None


class EvidenceEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    derived_id: str
    upstream_id: str


class RootEvidenceGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    group_id: str
    sources: tuple[SourceRef, ...]
    independence: Literal["attested", "unknown"] = "unknown"


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    transform_version: str = EVIDENCE_TRANSFORM_VERSION
    artifacts: tuple[ArtifactRef, ...] = ()
    memory_citations: tuple[MemoryCitation, ...] = ()
    sources: tuple[SourceRef, ...] = ()
    nodes: tuple[EvidenceNode, ...] = ()
    edges: tuple[EvidenceEdge, ...] = ()
    root_groups: tuple[RootEvidenceGroup, ...] = ()
    projection_digest: str
    projection_bytes: int
    incomplete: bool = False


class ProjectedEvidence(BaseModel):
    """Untrusted content visible to the model for this operation only."""

    evidence_id: str
    kind: Literal["source", "experience", "memory"]
    text: str
    historical: bool = False
    root_group_ids: tuple[str, ...] = ()


class EvidenceProjection(BaseModel):
    evidence: tuple[ProjectedEvidence, ...]
    root_groups: tuple[RootEvidenceGroup, ...]
    incomplete: bool = False


class ResolvedEvidence(BaseModel):
    manifest: EvidenceManifest
    projection: EvidenceProjection


def content_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def reference_key(value: ArtifactRef | MemoryCitation | SourceRef) -> str:
    return value.model_dump_json()


T = TypeVar("T")


def unique_references(values: tuple[T, ...]) -> tuple[T, ...]:
    """Keep stable reference order without requiring hashable domain models."""

    # Function-scoped identity preserves the caller's precise reference type.
    selected: list[T] = []
    for value in values:
        if value not in selected:
            selected.append(value)
    return tuple(selected)
