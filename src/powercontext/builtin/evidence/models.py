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

from pydantic import BaseModel, ConfigDict, Field, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.errors import PowerContextError
from powercontext.sources import SourceRef

# v1 records only unqualified references; v2 records each node's owning Scope. Both
# stay readable, and a stored v1 payload is never reinterpreted with v2 identity.
EVIDENCE_TRANSFORM_VERSION = "powercontext.dream.evidence.v1"
QUALIFIED_EVIDENCE_TRANSFORM_VERSION = "powercontext.dream.evidence.v2"


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


EvidenceKind = Literal[
    "source",
    "experience",
    "memory",
    "profile",
    "topic_memory",
    "handoff",
    "skill",
    "prompt",
    "catalog_target",
    "unresolved",
]
EvidenceRole = Literal["root", "derived", "target", "lineage_only", "unresolved"]


class EvidenceNode(BaseModel):
    """An immutable content digest and its exact provenance, never its body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    kind: Literal[
        "source",
        "experience",
        "memory",
        "profile",
        "topic_memory",
        "handoff",
        "skill",
        "prompt",
        "catalog_target",
        "unresolved",
    ]
    digest: str
    source: SourceRef | None = None
    artifact: ArtifactRef | None = None
    # The Scope that owns this node. Required by a v2 manifest and absent in v1, so an
    # existing payload keeps decoding without gaining a field it never wrote.
    origin_scope_id: str | None = Field(default=None, exclude_if=lambda value: value is None)

    role: Literal["root", "derived", "target", "lineage_only", "unresolved"]
    historical: bool = False

    @property
    def qualified(self) -> bool:
        return self.origin_scope_id is not None


class EvidenceEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    derived_id: str
    upstream_id: str


class RootEvidenceGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    group_id: str
    sources: tuple[SourceRef, ...]
    scope_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    independence: Literal["attested", "unknown"] = "unknown"


class QualifiedOrigin(BaseModel):
    """One exact input this run may use, with the Scope that owns it.

    The reference stays unqualified because the owning Scope is the separate field, so
    equal local identities in two Scopes are two origins. ``digest`` is the value read at
    that origin, never a reconstruction from a bare reference.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope_id: str
    source: SourceRef | None = None
    artifact: ArtifactRef | None = None
    digest: str

    @model_validator(mode="after")
    def require_one_reference(self):
        if (self.source is None) == (self.artifact is None):
            raise ValueError("a qualified origin must name exactly one Source or Artifact")  # noqa: TRY003
        return self

    @property
    def reference(self) -> SourceRef | ArtifactRef:
        reference = self.source if self.artifact is None else self.artifact
        if reference is None:
            raise ValueError("qualified origin has no reference")  # noqa: TRY003
        return reference


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    transform_version: str = EVIDENCE_TRANSFORM_VERSION
    artifacts: tuple[ArtifactRef, ...] = ()
    sources: tuple[SourceRef, ...] = ()
    nodes: tuple[EvidenceNode, ...] = ()
    edges: tuple[EvidenceEdge, ...] = ()
    root_groups: tuple[RootEvidenceGroup, ...] = ()
    projection_digest: str
    projection_bytes: int
    incomplete: bool = False

    @model_validator(mode="after")
    def require_version_complete_identities(self):
        """A v2 manifest must qualify every node; a v1 manifest must not.

        This is what lets a stored v1 payload decode unchanged while a new record is
        forced to carry complete identities, without a second node type.
        """

        version = self.transform_version
        if version == EVIDENCE_TRANSFORM_VERSION:
            qualified = any(node.origin_scope_id is not None for node in self.nodes)
            if qualified or any(group.scope_id is not None for group in self.root_groups):
                raise ValueError("v1 evidence must not carry qualified identities")  # noqa: TRY003
            return self
        if version != QUALIFIED_EVIDENCE_TRANSFORM_VERSION:
            raise ValueError("unsupported evidence transform version")  # noqa: TRY003
        if any(node.origin_scope_id is None for node in self.nodes):
            raise ValueError("v2 evidence must qualify every node")  # noqa: TRY003
        return self


class ProjectedEvidence(BaseModel):
    """Untrusted content visible to the model for this operation only."""

    evidence_id: str
    kind: Literal[
        "source", "experience", "memory", "profile", "topic_memory", "handoff", "skill", "prompt", "catalog_target"
    ]
    text: str
    historical: bool = False
    root_group_ids: tuple[str, ...] = ()


class EvidenceProjection(BaseModel):
    evidence: tuple[ProjectedEvidence, ...]
    root_groups: tuple[RootEvidenceGroup, ...]
    incomplete: bool = False
    # What the model actually cited, resolved to exact origins. Distinct from the
    # manifest, which records every admitted input.
    adopted: tuple[QualifiedOrigin, ...] = ()


class ResolvedEvidence(BaseModel):
    manifest: EvidenceManifest
    projection: EvidenceProjection


def content_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def reference_key(value: ArtifactRef | SourceRef) -> str:
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
