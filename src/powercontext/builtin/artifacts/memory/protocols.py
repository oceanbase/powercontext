"""Family-level ports used by the Memory service and SQL adapters."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from pydantic import BaseModel

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.builtin.artifacts.memory.models import (
    EmbeddingProfile,
    Memory,
    MemoryCapabilities,
    MemoryChannelHit,
    MemoryEntryInput,
    MemoryEntryVersion,
    MemoryHit,
    MemoryRevisionChanges,
    MemoryUsedSearchMode,
)
from powercontext.builtin.inference import EmbeddingModel, EmbeddingVector
from powercontext.sources import Source


class MemoryCandidateRequest(BaseModel):
    """Canonical evidence and bounded current entries offered to a pipeline."""

    sources: tuple[Source, ...]
    artifacts: tuple[Artifact[object], ...]
    current_entries: tuple[MemoryEntryVersion, ...]


class MemoryProjection(BaseModel):
    """A rebuildable active-head projection prepared outside a transaction."""

    entry_version: MemoryEntryVersion
    searchable_text: str
    embedding: EmbeddingVector | None = None
    embedding_content_hash: str | None = None


class MemoryCommit(BaseModel):
    """A complete Memory Revision and every row changed atomically with it."""

    base: Memory | None
    memory: Memory
    content_hash: str
    entry_versions: tuple[MemoryEntryVersion, ...]
    projections: tuple[MemoryProjection, ...]


class MemoryWritePlan(BaseModel):
    """A side-effect-free result that can be committed in an outer transaction."""

    result: Memory | None
    commit: MemoryCommit | None


class MemorySearchRequest(BaseModel):
    """A fully validated backend search request for explicit current heads."""

    query: str
    analyzed_query: str
    memories: tuple[ArtifactRef, ...]
    candidate_limit: int
    mode: MemoryUsedSearchMode
    query_vector: EmbeddingVector | None = None
    embedding_profile: EmbeddingProfile | None = None


class MemorySearchChannels(BaseModel):
    """Backend-internal channel rankings before shared fusion."""

    fts: tuple[MemoryChannelHit, ...] = ()
    vector: tuple[MemoryChannelHit, ...] = ()


class CandidatePipeline(Protocol):
    """Produce untrusted Memory candidates from canonical bounded evidence."""

    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        """Return candidates that still require all service validations."""

        ...


class MemoryUnitOfWork(Protocol):
    """Commit authoritative and projection rows in one backend transaction."""

    async def commit(self, value: MemoryCommit, /) -> Memory:
        """Validate and atomically commit one complete Revision."""

        ...


class MemoryBackend(Protocol):
    """Storage and retrieval capabilities required by the Memory Family."""

    async def capabilities(self) -> MemoryCapabilities:
        """Return probed deployment capabilities."""

        ...

    async def get(self, memory: ArtifactRef, /) -> Memory:
        """Load one exact Memory Revision."""

        ...

    async def latest(self, artifact_id: str, /) -> Memory:
        """Load the current head of one Memory identity."""

        ...

    async def entries(self, memory: ArtifactRef, /) -> tuple[MemoryEntryVersion, ...]:
        """Load the entry versions referenced by an exact manifest."""

        ...

    async def projections(self, memory: ArtifactRef, /) -> tuple[MemoryProjection, ...]:
        """Load rebuildable active-head projections for an exact Memory Revision."""

        ...

    async def rebuild_projections(self, embedding_model: EmbeddingModel | None = None, /) -> None:
        """Rebuild active-head and search projections from authoritative revisions."""

        ...

    def begin(self) -> AbstractAsyncContextManager[MemoryUnitOfWork]:
        """Open the adapter-specific atomic write boundary."""

        ...

    async def changes(
        self,
        memory: ArtifactRef,
        since_revision: int | None,
        /,
    ) -> tuple[MemoryRevisionChanges, ...]:
        """Read compact Revision changes without entry bodies."""

        ...

    async def vector_complete(
        self,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        """Derive fixed-profile vector completeness for selected heads."""

        ...

    async def search(self, request: MemorySearchRequest, /) -> MemorySearchChannels:
        """Return backend-ordered FTS/vector channels after manifest checks."""

        ...

    async def expand(self, hits: tuple[MemoryHit, ...], /) -> tuple[MemoryEntryVersion, ...]:
        """Load and validate exact versions anchored by hits."""

        ...
