"""Immutable public values for the Memory Artifact Family."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal, TypeAlias

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.sources import Source

MemoryEntryState: TypeAlias = Literal["active", "inactive"]
MemoryChangeOp: TypeAlias = Literal["add", "revise", "deactivate", "reactivate"]
MemorySearchMode: TypeAlias = Literal["fts", "vector", "hybrid", "auto"]
MemoryUsedSearchMode: TypeAlias = Literal["fts", "vector", "hybrid"]
MemoryMatchedBy: TypeAlias = Literal["fts", "vector"]


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    """The Memory embedding index contract for one deployment."""

    profile_id: str
    model: str
    dimension: int
    distance: Literal["l2"] = "l2"
    normalization: str = "none"


@dataclass(frozen=True, slots=True)
class MemoryCapabilities:
    """Backend features available for the configured deployment."""

    fts: bool
    vector: bool = False
    hybrid: bool = False
    embedding_profile: EmbeddingProfile | None = None


@dataclass(frozen=True, slots=True)
class MemoryManifestEntry:
    """One logical entry pointer and state in an immutable Revision."""

    entry_id: str
    entry_version_id: str
    entry_content_hash: str
    state: MemoryEntryState


@dataclass(frozen=True, slots=True)
class MemoryManifest:
    """The authoritative directory for one Memory Revision."""

    entries: tuple[MemoryManifestEntry, ...] = ()
    format: Literal["flat-v1"] = field(default="flat-v1", init=False)


@dataclass(frozen=True, slots=True)
class MemoryChange:
    """A compact entry change recorded by one Memory Revision."""

    op: MemoryChangeOp
    entry_id: str
    from_entry_version_id: str | None
    to_entry_version_id: str | None
    reason: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryContent:
    """The complete canonical content of one Memory Artifact Revision."""

    manifest: MemoryManifest
    changes: tuple[MemoryChange, ...] = ()
    schema: Literal["powercontext.memory.v1"] = field(default="powercontext.memory.v1", init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class Memory(Artifact[MemoryContent]):
    """An immutable snapshot in a Memory lifecycle."""

    family: ClassVar[str] = "memory"


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryEntryInput:
    """An untrusted proposed entry addition or content revision."""

    kind: str
    text: str
    entry: MemoryEntryVersion | None = None
    sources: tuple[Source, ...] = ()
    artifacts: tuple[Artifact[object], ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryEntryVersion:
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
    sources: tuple[Source, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryRevisionChanges:
    """The compact changes stored by one exact Memory Revision."""

    memory_ref: ArtifactRef
    changes: tuple[MemoryChange, ...]


@dataclass(frozen=True, slots=True)
class MemoryChannelHit:
    """An identity-preserving candidate returned by one search channel."""

    memory_ref: ArtifactRef
    entry_id: str
    entry_version_id: str
    text: str


@dataclass(frozen=True, slots=True)
class MemoryHit:
    """A fused retrieval result anchored to exact Memory content."""

    memory_ref: ArtifactRef
    entry_id: str
    entry_version_id: str
    text: str
    score: float
    matched_by: tuple[MemoryMatchedBy, ...]


@dataclass(frozen=True, slots=True)
class MemorySearchResult:
    """Search hits together with the mode actually executed."""

    mode: MemoryUsedSearchMode
    hits: tuple[MemoryHit, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryCitation:
    """A stable Handoff anchor for one exact entry version."""

    memory_ref: ArtifactRef
    entry_id: str
    entry_version_id: str
