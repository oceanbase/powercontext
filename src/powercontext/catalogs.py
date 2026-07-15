"""Shared read and write semantics for catalogs of domain objects."""

from __future__ import annotations

from typing import Protocol, TypeVar

EntryT = TypeVar("EntryT")
InputT_contra = TypeVar("InputT_contra", contravariant=True)
StoredT_co = TypeVar("StoredT_co", covariant=True)


class Catalog(Protocol[EntryT]):
    """Read canonical domain objects without exposing backend keys."""

    async def get(self, entry: EntryT, /) -> EntryT:
        """Return the canonical stored object corresponding to ``entry``."""

        ...


class CatalogStore(Protocol[InputT_contra, StoredT_co]):
    """Persist an input object without implying downstream semantic work."""

    async def add(self, value: InputT_contra, /) -> StoredT_co:
        """Persist ``value`` and return its canonical stored object."""

        ...
