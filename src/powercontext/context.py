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

"""Explicit public composition root for PowerContext components."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from powercontext.artifacts import Artifact, ArtifactCatalog, ArtifactDraft, ArtifactStore
from powercontext.errors import ArtifactFamilyMismatchError
from powercontext.sources import Source, SourceCatalog, SourceStore

SourcesT = TypeVar("SourcesT")
ArtifactsT = TypeVar("ArtifactsT")
TriggersT = TypeVar("TriggersT")


class PowerContext(BaseModel, Generic[SourcesT, ArtifactsT, TriggersT]):
    """Bind three explicitly selected component groups without owning their lifecycle."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    sources: SourcesT
    artifacts: ArtifactsT
    triggers: TriggersT


class Sources(BaseModel):
    """Compose Source acquisition, persistence, and read operations."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    catalog: SourceCatalog
    store: SourceStore[Source]

    async def resolve(self, value: object, /) -> Source:
        """Resolve an adapter-native input without persisting it."""

        return await self.catalog.resolve(value)

    async def add(self, source: Source, /) -> Source:
        """Persist a resolved Source without deriving Artifacts."""

        self.catalog.as_ref(source)
        stored = await self.store.add(source)
        self.catalog.as_ref(stored)
        return stored

    async def get(self, source: Source, /) -> Source:
        """Return the canonical persisted Source matching ``source``."""

        return await self.catalog.get(source)

    async def list(self) -> tuple[Source, ...]:
        """Return the Sources visible in the current catalog view."""

        return await self.catalog.list()

    async def read(self, source: Source, /) -> object:
        """Read the adapter-native value described by ``source``."""

        return await self.catalog.read(source)


class Artifacts(BaseModel):
    """Compose shared Artifact persistence and read operations."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

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
