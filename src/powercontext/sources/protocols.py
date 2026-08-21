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

"""Persistence contract for resolved Sources."""

from typing import Protocol, TypeVar, runtime_checkable

from powercontext.sources.models import Source

SourceT = TypeVar("SourceT", bound=Source)


@runtime_checkable
class SourceStore(Protocol[SourceT]):
    """Persist resolved Sources for later catalog reads and lineage."""

    async def add(self, value: SourceT, /) -> SourceT: ...


@runtime_checkable
class SourceCatalogBackend(Protocol):
    """Provide the Source reads required by SourceCatalog."""

    async def get(self, source: Source, /) -> Source: ...

    async def list(self) -> tuple[Source, ...]:
        """Return the Sources visible in one backend view."""

        ...
