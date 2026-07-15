from __future__ import annotations

import importlib.metadata as importlib_metadata
from collections.abc import Iterable
from types import MappingProxyType
from typing import Any, cast

from powercontext.errors import (
    InvalidSourceAdapterError,
    InvalidSourceEntryError,
    InvalidSourceResultError,
    SourceAdapterNotFoundError,
    SourceConflictError,
    SourceDiscoveryError,
    SourceNotFoundError,
)
from powercontext.sources.adapters import SourceAdapter
from powercontext.sources.models import Source
from powercontext.sources.protocols import SourceCatalogBackend

SOURCE_ADAPTER_ENTRY_POINT_GROUP = "powercontext.source_adapters"

AnySourceAdapter = SourceAdapter[Any, Any, Any]


class SourceCatalog(SourceCatalogBackend):
    """An immutable, read-only catalog of Sources and their adapters."""

    def __init__(
        self,
        *,
        backend: SourceCatalogBackend,
        adapters: Iterable[AnySourceAdapter],
    ) -> None:
        adapters_by_input: dict[type[object], AnySourceAdapter] = {}
        adapters_by_source_type: dict[str, AnySourceAdapter] = {}

        for adapter in adapters:
            input_type, source_type, _ = _validate_adapter(adapter)
            if input_type in adapters_by_input:
                raise SourceConflictError("input_type", input_type)
            if source_type in adapters_by_source_type:
                raise SourceConflictError("source_type", source_type)
            adapters_by_input[input_type] = adapter
            adapters_by_source_type[source_type] = adapter

        self._backend = backend
        self._adapters_by_input = MappingProxyType(adapters_by_input)
        self._adapters_by_source_type = MappingProxyType(adapters_by_source_type)

    @classmethod
    def discover(
        cls,
        *,
        backend: SourceCatalogBackend,
        adapters: Iterable[AnySourceAdapter],
    ) -> SourceCatalog:
        """Build a catalog after explicitly discovering adapter factories."""

        discovered: list[AnySourceAdapter] = list(adapters)
        for entry_point in importlib_metadata.entry_points(group=SOURCE_ADAPTER_ENTRY_POINT_GROUP):
            try:
                factory = entry_point.load()
            except Exception as error:
                raise SourceDiscoveryError(entry_point.name, "factory could not be imported") from error
            if not callable(factory):
                raise SourceDiscoveryError(entry_point.name, "entry point must load a zero-argument factory")
            try:
                adapter = factory()
            except Exception as error:
                raise SourceDiscoveryError(entry_point.name, "zero-argument factory failed") from error
            discovered.append(cast(AnySourceAdapter, adapter))
        return cls(backend=backend, adapters=discovered)

    async def list(self) -> tuple[Source, ...]:
        """Return the Sources visible in the current backend view."""

        sources = await self._backend.list()
        for source in sources:
            _validate_source(source, self._adapters_by_source_type)
        return sources

    async def get(self, source: Source, /) -> Source:
        """Return the canonical catalog entry matching ``source``."""

        stored = await self._backend.get(source)
        _validate_source(stored, self._adapters_by_source_type)
        if type(stored) is not type(source) or stored != source:
            raise SourceNotFoundError(source)
        return stored

    async def resolve(self, value: object, /) -> Source:
        """Resolve an exact adapter input without changing the catalog view."""

        input_type = type(value)
        try:
            adapter = self._adapters_by_input[input_type]
        except KeyError:
            raise SourceAdapterNotFoundError("input", input_type) from None

        source = await adapter.resolve(value)
        expected_type = adapter.source_class
        if type(source) is not expected_type:
            raise InvalidSourceResultError(adapter.source_type, "resolve", expected_type, type(source))
        return cast(Source, source)

    async def read(self, source: Source, /) -> object:
        """Read an adapter-native value without imposing a payload protocol."""

        adapter = _adapter_for_source(source, self._adapters_by_source_type)
        return await adapter.read(source)


def _validate_adapter(adapter: object) -> tuple[type[object], str, type[Source]]:
    adapter_type = type(adapter)
    input_type = getattr(adapter, "input_type", None)
    if not isinstance(input_type, type):
        raise InvalidSourceAdapterError(adapter_type, "input_type", "must be a type")

    source_type = getattr(adapter, "source_type", None)
    if not isinstance(source_type, str) or not source_type.strip():
        raise InvalidSourceAdapterError(adapter_type, "source_type", "must be a non-empty string")

    source_class = getattr(adapter, "source_class", None)
    if not isinstance(source_class, type) or not issubclass(source_class, Source):
        raise InvalidSourceAdapterError(adapter_type, "source_class", "must be a Source subclass")
    if getattr(source_class, "source_type", None) != source_type:
        raise InvalidSourceAdapterError(
            adapter_type,
            "source_class",
            "source_class.source_type must match adapter.source_type",
        )

    for method_name in ("resolve", "read"):
        if not callable(getattr(adapter, method_name, None)):
            raise InvalidSourceAdapterError(adapter_type, method_name, "must be callable")

    return cast(type[object], input_type), source_type, source_class


def _adapter_for_source(
    source: Source,
    adapters_by_source_type: MappingProxyType[str, AnySourceAdapter] | dict[str, AnySourceAdapter],
) -> AnySourceAdapter:
    try:
        adapter = adapters_by_source_type[source.source_type]
    except KeyError:
        raise SourceAdapterNotFoundError("source", type(source)) from None
    if type(source) is not adapter.source_class:
        raise InvalidSourceResultError(source.source_type, "read", adapter.source_class, type(source))
    return adapter


def _validate_source(
    source: object,
    adapters_by_source_type: MappingProxyType[str, AnySourceAdapter] | dict[str, AnySourceAdapter],
) -> None:
    if not isinstance(source, Source):
        raise InvalidSourceEntryError(type(source))
    _adapter_for_source(source, adapters_by_source_type)
