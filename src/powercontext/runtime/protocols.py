"""Backend-neutral storage contracts for scoped Runtime orchestration."""

from __future__ import annotations

from typing import Protocol

from powercontext.memory import MemoryBackend, MemoryCapabilities, MemoryEvidenceCodec
from powercontext.sources import Source, SourceCatalogBackend, SourceStore
from powercontext.sources.journal import SourceJournal, TriggerCursorStore


class ScopedSourceBackend(
    SourceCatalogBackend,
    SourceStore[Source],
    SourceJournal,
    TriggerCursorStore,
    Protocol,
):
    """Provide the complete Source state required by one Runtime scope."""


class MemoryBindingStore(Protocol):
    """Resolve one stable Memory Artifact identity for an opaque scope."""

    async def memory_artifact_id(self, scope_id: str, /) -> str: ...


class RuntimeScopeStorage(Protocol):
    """Own backend resources and codecs for one Runtime scope."""

    memory_artifact_id: str
    sources: ScopedSourceBackend
    memory: MemoryBackend
    evidence_codec: MemoryEvidenceCodec

    async def close(self) -> None: ...


class RuntimeStorage(Protocol):
    """Create isolated scoped storage without exposing an adapter choice."""

    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    async def memory_capabilities(self) -> MemoryCapabilities: ...

    async def open_scope(self, scope_id: str, /) -> RuntimeScopeStorage: ...

    async def pending_scopes(self, trigger_name: str, /) -> tuple[str, ...]: ...
