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

"""Declarations and generation contracts for Worker-loaded Artifact plugins."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.artifacts.indexes import ArtifactSearchDeclaration, FragmentProjection, TextIndex, VectorIndex
from powercontext.artifacts.search import ArtifactSearchOutcome
from powercontext.sources import SourceProjectionKey, SourceRef

ResourceT = TypeVar("ResourceT")


@dataclass(frozen=True)
class ArtifactFamily:
    family: str
    schema_version: str
    content_model: type[BaseModel]
    search: ArtifactSearchDeclaration | None = None


@dataclass(frozen=True)
class InputContract:
    projections: dict[str, SourceProjectionKey]
    artifact_families: tuple[str, ...] = ()


class GenerationSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: SourceRef
    projection: SourceProjectionKey
    value: JsonValue


@dataclass(frozen=True)
class GenerationBatch:
    sources: tuple[GenerationSource, ...]


class ArtifactDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    family: str
    schema_version: str
    artifact_id: str = Field(min_length=1, max_length=128, pattern=r"^[\x21-\x7E]+$")
    expected_revision: int = Field(default=0, ge=0)
    content: dict[str, JsonValue]
    source_refs: tuple[SourceRef, ...]
    artifact_refs: tuple[ArtifactRef, ...] = ()


class ArtifactSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: ArtifactRef
    lineage: ArtifactLineage
    schema_version: str | None
    content: dict[str, JsonValue]
    source_refs: tuple[SourceRef, ...] = ()
    artifact_refs: tuple[ArtifactRef, ...] = ()


class ArtifactReferencePage(BaseModel):
    items: tuple[ArtifactRef, ...]
    next_cursor: str | None = None


class GenerationContext:
    """Scope-bound reads whose exact evidence can be used by generated drafts."""

    def __init__(
        self,
        scope_id: str,
        resources: AsyncExitStack,
        read_artifact: Callable[[str, str, int | None], Awaitable[ArtifactSnapshot | None]],
        *,
        list_artifacts: Callable[..., Awaitable[ArtifactReferencePage]] | None = None,
        search_artifacts: Callable[..., Awaitable[ArtifactSearchOutcome]] | None = None,
    ) -> None:
        self.scope_id = scope_id
        self._resources = resources
        self._read_artifact = read_artifact
        self._list_artifacts = list_artifacts
        self._search_artifacts = search_artifacts

    async def enter_async_context(self, resource: AbstractAsyncContextManager[ResourceT]) -> ResourceT:
        return await self._resources.enter_async_context(resource)

    async def read_latest(self, family: str, artifact_id: str) -> ArtifactSnapshot | None:
        """Read one current Artifact; separate reads do not establish a cross-family snapshot."""

        return await self._read_artifact(family, artifact_id, None)

    async def read_revision(self, ref: ArtifactRef) -> ArtifactSnapshot:
        """Read and register an exact revision, including a superseded historical revision."""

        result = await self._read_artifact(ref.family, ref.artifact_id, ref.revision)
        if result is None:
            raise LookupError(ref)
        return result

    async def list_artifacts(self, family: str, *, limit: int = 50, cursor: str | None = None) -> ArtifactReferencePage:
        """Page visible current references; use a fixed manifest for historical enumeration."""
        if self._list_artifacts is None:
            raise NotImplementedError("Artifact listing is unavailable")
        return await self._list_artifacts(family, limit=limit, cursor=cursor)

    async def search_artifacts(self, family: str, query: str | None = None, **parameters) -> ArtifactSearchOutcome:
        """Retrieve complete exact Artifacts through the shared search contract."""
        if self._search_artifacts is None:
            raise NotImplementedError("Artifact search is unavailable")
        return await self._search_artifacts(family, query, **parameters)


__all__ = [
    "ArtifactDraft",
    "ArtifactFamily",
    "ArtifactReferencePage",
    "ArtifactSearchDeclaration",
    "ArtifactSnapshot",
    "FragmentProjection",
    "GenerationBatch",
    "GenerationContext",
    "GenerationSource",
    "InputContract",
    "TextIndex",
    "VectorIndex",
]
