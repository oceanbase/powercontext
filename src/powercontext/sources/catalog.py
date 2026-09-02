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

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import JsonValue

from powercontext.errors import (
    SourceNotFoundError,
)
from powercontext.sources.adapters import SourceAdapter
from powercontext.sources.definitions import SourceDefinitionRegistry
from powercontext.sources.models import Source, SourceProjectionKey, SourceRef
from powercontext.sources.observations import SourceObservation
from powercontext.sources.protocols import SourceCatalogBackend

_AnySourceAdapter = SourceAdapter[Any, Any, Any]


class SourceCatalog:
    """A read-only Source catalog routed by actual adapters."""

    def __init__(
        self,
        *,
        backend: SourceCatalogBackend,
        adapters: Iterable[_AnySourceAdapter] = (),
        registry: SourceDefinitionRegistry | None = None,
    ) -> None:
        adapter_values = tuple(adapters)
        if registry is not None and adapter_values:
            raise TypeError("SourceCatalog accepts either registry or adapters, not both")  # noqa: TRY003
        self._backend = backend
        self._registry = registry or SourceDefinitionRegistry.from_adapters(adapter_values)

    async def list(self) -> tuple[Source, ...]:
        sources = await self._backend.list()
        for source in sources:
            self.as_ref(source)
        return sources

    async def get(self, source: Source, /) -> Source:
        self.as_ref(source)
        stored = await self._backend.get(source)
        self.as_ref(stored)
        if type(stored) is not type(source) or stored != source:
            raise SourceNotFoundError(source)
        return stored

    def as_ref(self, source: Source, /) -> SourceRef:
        if isinstance(source, SourceObservation):
            return SourceRef(source_type=source.source_type, source_id=source.name)
        definition = self._registry.definition_for_source(source)
        return SourceRef(source_type=definition.name, source_id=source.name)

    async def resolve(self, value: object, /) -> Source:
        return await self._registry.resolve(value)

    async def read(self, source: Source, /) -> object:
        if isinstance(source, SourceObservation):
            return source.payload
        return await self._registry.read(source)

    def projection_keys(self, source: Source, /) -> tuple[SourceProjectionKey, ...]:
        """Return the exact named projection capabilities advertised for ``source``."""

        return self._registry.projection_keys(source)

    def project(self, source: Source, key: SourceProjectionKey, /) -> JsonValue:
        """Evaluate one named projection against an exact Source value."""

        return self._registry.project(source, key)
