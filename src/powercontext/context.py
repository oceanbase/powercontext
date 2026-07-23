"""Explicit public composition root for PowerContext components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from powercontext.artifacts import Artifact, ArtifactCatalog, ArtifactDraft, ArtifactStore
from powercontext.errors import ArtifactFamilyMismatchError
from powercontext.sources import Source, SourceCatalog, SourceCatalogBackend, SourceStore

TriggersT = TypeVar("TriggersT")
ArtifactsT = TypeVar("ArtifactsT")


@dataclass(frozen=True, slots=True, kw_only=True)
class Sources(SourceCatalogBackend, SourceStore[Source]):
    """Compose Source acquisition, persistence, and read operations."""

    catalog: SourceCatalog
    store: SourceStore[Source]

    async def resolve(self, value: object, /) -> Source:
        """Resolve an adapter-native input without persisting it."""

        return await self.catalog.resolve(value)

    async def add(self, source: Source, /) -> Source:
        """Persist a resolved Source without deriving Artifacts."""

        return await self.store.add(source)

    async def get(self, source: Source, /) -> Source:
        """Return the canonical persisted Source matching ``source``."""

        return await self.catalog.get(source)

    async def list(self) -> tuple[Source, ...]:
        """Return the Sources visible in the current catalog view."""

        return await self.catalog.list()

    async def read(self, source: Source, /) -> object:
        """Read the adapter-native value described by ``source``."""

        return await self.catalog.read(source)


@dataclass(frozen=True, slots=True, kw_only=True)
class Artifacts(
    ArtifactCatalog[Artifact[object]],
    ArtifactStore[ArtifactDraft[object], Artifact[object]],
):
    """Compose shared Artifact persistence and read operations."""

    catalog: ArtifactCatalog[Artifact[object]]
    store: ArtifactStore[ArtifactDraft[object], Artifact[object]]

    async def add(self, draft: ArtifactDraft[object], /) -> Artifact[object]:
        """Commit an initial Artifact Revision without semantic generation."""

        return await self.store.add(draft)

    async def revise(
        self,
        artifact: Artifact[object],
        draft: ArtifactDraft[object],
        /,
    ) -> Artifact[object]:
        """Commit a Revision using ``artifact`` as the optimistic base."""

        if artifact.family != draft.family:
            raise ArtifactFamilyMismatchError(artifact, draft)
        return await self.store.revise(artifact, draft)

    async def get(self, artifact: Artifact[object], /) -> Artifact[object]:
        """Return the canonical exact Revision matching ``artifact``."""

        return await self.catalog.get(artifact)

    async def latest(self, artifact: Artifact[object], /) -> Artifact[object]:
        """Return the latest visible Revision of ``artifact``."""

        return await self.catalog.latest(artifact)

    async def revisions(self, artifact: Artifact[object], /) -> tuple[Artifact[object], ...]:
        """Return the visible history of ``artifact``."""

        return await self.catalog.revisions(artifact)


@dataclass(frozen=True, slots=True, kw_only=True)
class PowerContext(Generic[TriggersT, ArtifactsT]):
    """Bind explicitly configured domain components without owning their lifecycle."""

    sources: Sources
    artifacts: ArtifactsT
    triggers: TriggersT
