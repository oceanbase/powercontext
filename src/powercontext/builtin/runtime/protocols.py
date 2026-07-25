"""Ports consumed by the built-in Runtime."""

from __future__ import annotations

from typing import Protocol, TypeVar

from powercontext.context import PowerContext

SourcesT = TypeVar("SourcesT", covariant=True)
ArtifactsT = TypeVar("ArtifactsT", covariant=True)
TriggersT = TypeVar("TriggersT", covariant=True)


class PowerContextProvider(Protocol[SourcesT, ArtifactsT, TriggersT]):
    """Resolve an already composed context without transferring lifecycle ownership."""

    async def get(self, scope_id: str, /) -> PowerContext[SourcesT, ArtifactsT, TriggersT]: ...
