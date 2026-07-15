"""Persistence contract for resolved Sources."""

from typing import Protocol, TypeVar

from powercontext.catalogs import Catalog, CatalogStore
from powercontext.sources.models import Source

SourceT = TypeVar("SourceT", bound=Source)


class SourceStore(CatalogStore[SourceT, SourceT], Protocol[SourceT]):
    """Persist resolved Sources for later catalog reads and lineage."""


class SourceCatalogBackend(Catalog[Source], Protocol):
    """Provide the Source-specific read operations used by SourceCatalog."""

    async def list(self) -> tuple[Source, ...]:
        """Return the Sources visible in one backend view."""

        ...
