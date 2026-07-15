"""Read contracts for artifact lifecycles."""

from __future__ import annotations

from typing import Protocol, TypeVar

from powercontext.artifacts.models import Artifact, ArtifactDraft
from powercontext.catalogs import Catalog, CatalogStore

ArtifactT = TypeVar("ArtifactT", bound=Artifact[object])
DraftT_contra = TypeVar("DraftT_contra", bound=ArtifactDraft[object], contravariant=True)


class ArtifactCatalog(Catalog[ArtifactT], Protocol[ArtifactT]):
    """Read artifact revisions without owning their writes."""

    async def latest(self, artifact: ArtifactT, /) -> ArtifactT:
        """Return the latest visible revision of ``artifact``."""

        ...

    async def revisions(self, artifact: ArtifactT, /) -> tuple[ArtifactT, ...]:
        """Return the visible history of ``artifact`` in ascending revision order."""

        ...


class ArtifactStore(CatalogStore[DraftT_contra, ArtifactT], Protocol[DraftT_contra, ArtifactT]):
    """Commit new Artifact revisions from complete domain objects."""

    async def revise(self, artifact: ArtifactT, draft: DraftT_contra, /) -> ArtifactT:
        """Commit ``draft`` only if ``artifact`` remains the latest revision."""

        ...
