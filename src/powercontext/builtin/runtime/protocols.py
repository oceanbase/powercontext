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
    """Record bounded attributes and a deferred outcome for one internal Runtime stage."""

    def set_attributes(self, attributes: Mapping[str, TraceAttribute], /) -> None: ...

    def set_outcome(self, outcome: str, /) -> None: ...


class RuntimeTracing(Protocol):
    """Create framework-neutral spans for internal Runtime stages."""

    def stage(
        self,
        name: str,
        *,
        attributes: Mapping[str, TraceAttribute],
    ) -> AbstractContextManager[RuntimeSpan]: ...

    def background(
        self,
        name: str,
        *,
        operation: str,
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
