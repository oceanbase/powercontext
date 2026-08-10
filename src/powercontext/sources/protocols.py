"""Persistence contract for resolved Sources."""

from typing import Protocol, TypeVar, runtime_checkable

from powercontext.sources.models import Source

SourceT = TypeVar("SourceT", bound=Source)


@runtime_checkable
class SourceStore(Protocol[SourceT]):
    """Persist resolved Sources for later catalog reads and lineage."""

    async def add(self, value: SourceT, /) -> SourceT: ...


@runtime_checkable
class SourceCatalogBackend(Protocol):
    """Provide the Source reads required by SourceCatalog."""

    async def get(self, source: Source, /) -> Source: ...

    async def list(self) -> tuple[Source, ...]:
        """Return the Sources visible in one backend view."""

        ...
