"""Immutable public values for the Memory Artifact Family."""

from __future__ import annotations

from typing import ClassVar, Literal, TypeAlias

from pydantic import BaseModel, Field

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.sources import Source, SourceRef

MemoryEntryState: TypeAlias = Literal["active", "inactive"]
MemoryChangeOp: TypeAlias = Literal["add", "revise", "deactivate", "reactivate"]
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


class MemoryCapabilities(BaseModel):
    """Backend features available for the configured deployment."""

    fts: bool
    vector: bool = False
    hybrid: bool = False
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
    """Search hits together with the mode actually executed."""

    mode: MemoryUsedSearchMode
    hits: tuple[MemoryHit, ...] = ()
    rerank: MemoryRerankTrace | None = None


class MemoryCitation(BaseModel):
    """A stable Handoff anchor for one exact entry version."""

    memory_ref: ArtifactRef
    entry_id: str
    entry_version_id: str
