from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from powercontext.errors import (
    InvalidSourceAdapterError,
    InvalidSourceEntryError,
    InvalidSourceResultError,
    SourceAdapterNotFoundError,
    SourceConflictError,
    SourceNotFoundError,
)
from powercontext.sources.adapters import SourceAdapter
from powercontext.sources.models import Source, SourceRef
from powercontext.sources.protocols import SourceCatalogBackend

_AnySourceAdapter = SourceAdapter[Any, Any, Any]


class SourceCatalog:
    """A read-only Source catalog routed by actual adapters."""

    def __init__(
        self,
        *,
        backend: SourceCatalogBackend,
        adapters: Iterable[_AnySourceAdapter],
    ) -> None:
        by_input: dict[type[object], _AnySourceAdapter] = {}
        by_source: dict[type[Source], _AnySourceAdapter] = {}
        for adapter in adapters:
            input_class, source_class = _validate_adapter(adapter)
            if input_class in by_input:
                raise SourceConflictError("input_class", input_class)
            if source_class in by_source:
                raise SourceConflictError("source_class", source_class)
            by_input[input_class] = adapter
            by_source[source_class] = adapter
        self._backend = backend
        self._by_input = by_input
        self._by_source = by_source

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
        adapter = _adapter_for_source(source, self._by_source)
        return SourceRef(source_type=adapter.name, source_id=source.name)

    async def resolve(self, value: object, /) -> Source:
        input_class = type(value)
        try:
            adapter = self._by_input[input_class]
        except KeyError:
            raise SourceAdapterNotFoundError("input", input_class) from None
        source = await adapter.resolve(value)
        if type(source) is not adapter.source_class:
            raise InvalidSourceResultError(adapter.name, "resolve", adapter.source_class, type(source))
        self.as_ref(source)
        return cast(Source, source)

    async def read(self, source: Source, /) -> object:
        adapter = _adapter_for_source(source, self._by_source)
        return await adapter.read(source)


def _validate_adapter(adapter: object) -> tuple[type[object], type[Source]]:
    adapter_type = type(adapter)
    input_class = getattr(adapter, "input_class", None)
    if not isinstance(input_class, type):
        raise InvalidSourceAdapterError(adapter_type, "input_class", "must be a type")
    name = getattr(adapter, "name", None)
    if not isinstance(name, str) or not name.strip():
        raise InvalidSourceAdapterError(adapter_type, "name", "must be a non-empty string")
    source_class = getattr(adapter, "source_class", None)
    if not isinstance(source_class, type) or not issubclass(source_class, Source):
        raise InvalidSourceAdapterError(adapter_type, "source_class", "must be a Source subclass")
    for method_name in ("resolve", "read"):
        if not callable(getattr(adapter, method_name, None)):
            raise InvalidSourceAdapterError(adapter_type, method_name, "must be callable")
    return cast(type[object], input_class), cast(type[Source], source_class)


def _adapter_for_source(
    source: object,
    adapters: Mapping[type[Source], _AnySourceAdapter],
) -> _AnySourceAdapter:
    if not isinstance(source, Source):
        raise InvalidSourceEntryError(type(source))
    try:
        return adapters[type(source)]
    except KeyError:
        raise SourceAdapterNotFoundError("source", type(source)) from None
