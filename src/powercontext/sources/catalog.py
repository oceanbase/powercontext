from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
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
from powercontext.sources.models import Source
from powercontext.sources.protocols import SourceCatalogBackend

_AnySourceAdapter = SourceAdapter[Any, Any, Any]


class SourceCatalog(SourceCatalogBackend):
    """An immutable, read-only catalog of Sources and their adapters."""

    def __init__(
        self,
        *,
        backend: SourceCatalogBackend,
        adapters: Iterable[_AnySourceAdapter],
    ) -> None:
        adapters_by_input_class: dict[type[object], _AnySourceAdapter] = {}
        adapters_by_source_class: dict[type[Source], _AnySourceAdapter] = {}
        adapters_by_name: dict[str, _AnySourceAdapter] = {}

        for adapter in adapters:
            input_class, name, source_class = _validate_adapter(adapter)
            if input_class in adapters_by_input_class:
                raise SourceConflictError("input_class", input_class)
            if source_class in adapters_by_source_class:
                raise SourceConflictError("source_class", source_class)
            if name in adapters_by_name:
                raise SourceConflictError("name", name)
            adapters_by_input_class[input_class] = adapter
            adapters_by_source_class[source_class] = adapter
            adapters_by_name[name] = adapter

        self._backend = backend
        self._adapters_by_input_class = MappingProxyType(adapters_by_input_class)
        self._adapters_by_source_class = MappingProxyType(adapters_by_source_class)

    async def list(self) -> tuple[Source, ...]:
        """Return the Sources visible in the current backend view."""

        sources = await self._backend.list()
        for source in sources:
            _validate_source(source, self._adapters_by_source_class)
        return sources

    async def get(self, source: Source, /) -> Source:
        """Return the canonical catalog entry matching ``source``."""

        stored = await self._backend.get(source)
        _validate_source(stored, self._adapters_by_source_class)
        if type(stored) is not type(source) or stored != source:
            raise SourceNotFoundError(source)
        return stored

    async def resolve(self, value: object, /) -> Source:
        """Resolve an exact adapter input without changing the catalog view."""

        input_class = type(value)
        try:
            adapter = self._adapters_by_input_class[input_class]
        except KeyError:
            raise SourceAdapterNotFoundError("input", input_class) from None

        source = await adapter.resolve(value)
        expected_type = adapter.source_class
        if type(source) is not expected_type:
            raise InvalidSourceResultError(adapter.name, "resolve", expected_type, type(source))
        return cast(Source, source)

    async def read(self, source: Source, /) -> object:
        """Read an adapter-native value without imposing a payload protocol."""

        adapter = _adapter_for_source(source, self._adapters_by_source_class)
        return await adapter.read(source)


def _validate_adapter(adapter: object) -> tuple[type[object], str, type[Source]]:
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

    return cast(type[object], input_class), name, source_class


def _adapter_for_source(
    source: Source,
    adapters_by_source_class: Mapping[type[Source], _AnySourceAdapter],
) -> _AnySourceAdapter:
    try:
        adapter = adapters_by_source_class[type(source)]
    except KeyError:
        raise SourceAdapterNotFoundError("source", type(source)) from None
    return adapter


def _validate_source(
    source: object,
    adapters_by_source_class: Mapping[type[Source], _AnySourceAdapter],
) -> None:
    if not isinstance(source, Source):
        raise InvalidSourceEntryError(type(source))
    _adapter_for_source(source, adapters_by_source_class)
