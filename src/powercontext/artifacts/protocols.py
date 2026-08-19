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

"""Read contracts for artifact lifecycles."""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from powercontext.artifacts.models import Artifact, ArtifactDraft

ArtifactT = TypeVar("ArtifactT", bound=Artifact[object])
DraftT_contra = TypeVar("DraftT_contra", bound=ArtifactDraft[object], contravariant=True)


@runtime_checkable
class ArtifactCatalog(Protocol[ArtifactT]):
    """Read artifact revisions without owning their writes."""

    async def get(self, artifact: ArtifactT, /) -> ArtifactT:
        """Return the canonical exact revision matching ``artifact``."""

        ...

    async def latest(self, artifact: ArtifactT, /) -> ArtifactT:
        """Return the latest visible revision of ``artifact``."""

        ...

    async def revisions(self, artifact: ArtifactT, /) -> tuple[ArtifactT, ...]:
        """Return the visible history of ``artifact`` in ascending revision order."""

        ...


@runtime_checkable
class ArtifactStore(Protocol[DraftT_contra, ArtifactT]):
    """Commit new Artifact revisions from complete domain objects."""

    async def add(self, draft: DraftT_contra, /) -> ArtifactT:
        """Commit the first revision represented by ``draft``."""

        ...

    async def revise(self, artifact: ArtifactT, draft: DraftT_contra, /) -> ArtifactT:
        """Commit ``draft`` only if ``artifact`` remains the latest revision."""

        ...
