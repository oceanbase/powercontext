"""Ports consumed by the built-in Runtime."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Protocol, TypeVar

from powercontext.builtin.artifacts.handoff import ActivateHandoff, HandoffActivation
from powercontext.builtin.runtime.models import MemoryFlushResult
from powercontext.builtin.sources import SourceCursor
from powercontext.context import PowerContext

SourcesT = TypeVar("SourcesT", covariant=True)
ArtifactsT = TypeVar("ArtifactsT", covariant=True)
TriggersT = TypeVar("TriggersT", covariant=True)
TraceAttribute = str | bool | int | float


class RuntimeSpan(Protocol):
    """Record bounded attributes for one internal Runtime stage."""

    def set_attributes(self, attributes: Mapping[str, TraceAttribute], /) -> None: ...


class RuntimeTracing(Protocol):
    """Create framework-neutral spans for internal Runtime stages."""

    def stage(
        self,
        name: str,
        *,
        attributes: Mapping[str, TraceAttribute],
    ) -> AbstractContextManager[RuntimeSpan]: ...


class PowerContextProvider(Protocol[SourcesT, ArtifactsT, TriggersT]):
    """Resolve an already composed context without transferring lifecycle ownership."""

    async def get(self, scope_id: str, /) -> PowerContext[SourcesT, ArtifactsT, TriggersT]: ...


class BuiltinTriggers(Protocol):
    """Atomically execute the built-in Trigger policies for one scope."""

    async def flush(self, *, limit: int) -> MemoryFlushResult: ...

    async def cursor(self) -> SourceCursor: ...

    async def activate_handoff(self, request: ActivateHandoff, /) -> HandoffActivation: ...
