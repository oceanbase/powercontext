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

"""Immutable public values for the Memory Artifact Family."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, TypeAlias

from pydantic import BaseModel, Field, model_validator

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.artifacts import MemoryCitation as MemoryCitation
from powercontext.builtin.artifacts.search import AdmissionCounts
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.sources import Source, SourceRef

MemoryEntryState: TypeAlias = Literal["active", "inactive"]
MemoryChangeOp: TypeAlias = Literal["add", "revise", "deactivate", "reactivate", "compact"]
MemoryCapacityDimension: TypeAlias = Literal["active_entries", "manifest_entries", "manifest_bytes"]
MemorySearchMode: TypeAlias = Literal["fts", "vector", "hybrid", "auto"]
MemoryUsedSearchMode: TypeAlias = Literal["fts", "vector", "hybrid"]
MemoryMatchedBy: TypeAlias = Literal["fts", "vector"]


class EmbeddingProfile(BaseModel):
    """The Memory embedding index contract for one deployment."""

    profile_id: str
    model: str
    dimension: int
    distance: Literal["l2"] = "l2"
    normalization: Literal["none", "unit"] = "unit"


@dataclass(frozen=True)
class MemoryQueryEmbedding:
    """One already-computed query vector together with the profile it was computed under.

    Carried back out of :meth:`MemoryService.search` so a later recall round can reuse the
    round-0 vector instead of paying for a second embedding call. The profile is part of the
    value because a vector is only meaningful against the profile that produced it: reusing a
    vector across profiles would silently compare incompatible spaces, so a mismatch is
    treated as "reuse unavailable" and the round pays for a fresh embedding.
    """

    query_vector: tuple[float, ...]
    embedding_profile: EmbeddingProfile


class MemoryCapabilities(BaseModel):
    """Backend features available for the configured deployment."""

    fts: bool
    vector: bool = False
    hybrid: bool = False
    tag_filter: bool = False
    embedding_profile: EmbeddingProfile | None = None


class MemoryManifestEntry(BaseModel):
    """One logical entry pointer and state in an immutable Revision."""

    entry_id: str
    entry_version_id: str
    entry_content_hash: str
    state: MemoryEntryState


class MemoryManifest(BaseModel):
    """The authoritative directory for one Memory Revision."""

    entries: tuple[MemoryManifestEntry, ...] = ()
    format: Literal["flat-v1"] = "flat-v1"


class MemoryChange(BaseModel):
    """A compact entry change recorded by one Memory Revision."""

    op: MemoryChangeOp
    entry_id: str
    from_entry_version_id: str | None
    to_entry_version_id: str | None
    reason: str | None = None


class MemoryContent(BaseModel):
    """The complete canonical content of one Memory Artifact Revision."""

    manifest: MemoryManifest
    changes: tuple[MemoryChange, ...] = ()
    schema_version: Literal["powercontext.memory.v1"] = Field(
        default="powercontext.memory.v1",
        alias="schema",
    )


class Memory(Artifact[MemoryContent]):
    """An immutable snapshot in a Memory lifecycle."""

    family: ClassVar[str] = "memory"


class MemoryCapacityBudget(BaseModel):
    """The capacity ceiling applied to one Memory Artifact."""

    max_active_entries: int = Field(default=5_000, ge=1)
    max_manifest_entries: int = Field(default=10_000, ge=1)
    max_manifest_bytes: int = Field(default=4_194_304, ge=1_024)

    @model_validator(mode="after")
    def validate_entry_ceiling_order(self) -> MemoryCapacityBudget:
        if self.max_active_entries > self.max_manifest_entries:
            raise ValueError("max_active_entries cannot exceed max_manifest_entries")  # noqa: TRY003
        return self


class MemoryCapacity(BaseModel):
    """Observed capacity of one exact Memory Revision against its budget."""

    memory_ref: ArtifactRef
    active_entry_count: int = Field(ge=0)
    manifest_entry_count: int = Field(ge=0)
    manifest_bytes: int = Field(ge=0)
    compactable_entry_count: int = Field(ge=0)
    budget: MemoryCapacityBudget
    exceeded: tuple[MemoryCapacityDimension, ...] = ()


class MemoryCompactionPolicy(BaseModel):
    """Opt-in removal of inactive manifest pointers after a recovery window."""

    enabled: bool = False
    min_tombstone_revisions: int = Field(
        default=10, ge=0, description="Completed Revision advances since deactivation; zero permits immediate removal."
    )


class MemoryCompactionResult(BaseModel):
    """A compaction preview or committed Revision, retaining all historical bodies."""

    memory: Memory
    entry_ids: tuple[str, ...] = ()
    reclaimed_bytes: int = Field(
        default=0,
        description="Signed decrease in complete canonical content bytes, including compaction audit records.",
    )
    dry_run: bool


class MemoryEntryInput(BaseModel):
    """An untrusted proposed entry addition or content revision."""

    kind: str
    text: str
    entry: MemoryEntryVersion | None = None
    sources: tuple[Source, ...] = ()
    artifacts: tuple[Artifact[object], ...] = ()
    reason: str | None = None


class MemoryEntryVersion(BaseModel):
    """One immutable version of a logical Memory entry."""

    memory_artifact_id: str
    entry_id: str
    entry_version_id: str
    version: int
    previous_version_id: str | None
    kind: str
    text: str
    entry_content_hash: str
    created_in_revision: int
    sources: tuple[SourceRef, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()


class MemoryRevisionChanges(BaseModel):
    """The compact changes stored by one exact Memory Revision."""

    memory_ref: ArtifactRef
    changes: tuple[MemoryChange, ...]


class MemoryChannelHit(BaseModel):
    """An identity-preserving candidate returned by one search channel."""

    memory_ref: ArtifactRef
    entry_id: str
    entry_version_id: str
    text: str
    distance: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)


class MemoryHit(BaseModel):
    """A fused retrieval result anchored to exact Memory content."""

    memory_ref: ArtifactRef
    entry_id: str
    entry_version_id: str
    text: str
    score: float
    matched_by: tuple[MemoryMatchedBy, ...]


class MemoryRerankTrace(BaseModel):
    """Observable listwise selection over one coarse retrieval pool."""

    policy_id: str = Field(min_length=1)
    candidate_hits: tuple[MemoryHit, ...]
    selected_ranks: tuple[int, ...]
    discarded_rank_count: int = 0
    used_fallback: bool = False
    latency_ms: float = Field(ge=0.0, allow_inf_nan=False)
    usage: InferenceUsage


class MemorySearchResult(BaseModel):
    """Search hits together with the mode actually executed.

    The last four fields are **in-process only**: they exist so the Runtime's recall gate can
    account for what one search cost and admitted. They are ``exclude=True`` as
    defence-in-depth so a future ``model_dump`` cannot leak a gate artefact into a response;
    the HTTP projection is independently safe because ``search_response`` enumerates
    ``MemorySearchPage``'s fields explicitly.
    """

    mode: MemoryUsedSearchMode
    hits: tuple[MemoryHit, ...] = ()
    rerank: MemoryRerankTrace | None = None
    admission: AdmissionCounts | None = Field(default=None, exclude=True)
    embedding_calls: int = Field(default=0, exclude=True)
    generation_calls: int = Field(default=0, exclude=True)
    query_embedding: MemoryQueryEmbedding | None = Field(default=None, exclude=True)
