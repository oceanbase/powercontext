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

"""Explicit Source Definition registration and named projection routing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar, cast

from pydantic import BaseModel, JsonValue, TypeAdapter

from powercontext.errors import (
    InvalidSourceAdapterError,
    InvalidSourceDefinitionError,
    InvalidSourceEntryError,
    InvalidSourceProjectionError,
    InvalidSourceResultError,
    SourceAdapterNotFoundError,
    SourceConflictError,
    SourceDefinitionNotFoundError,
    SourceProjectionNotFoundError,
)
from powercontext.sources.adapters import SourceAdapter
from powercontext.sources.models import Source, SourceProjectionKey

InputT = TypeVar("InputT")
SourceT = TypeVar("SourceT", bound=Source)
ValueT_co = TypeVar("ValueT_co", covariant=True)

_JSON_VALUE = TypeAdapter(JsonValue)
_AnySourceAdapter = SourceAdapter[Any, Any, Any]


class SourceProjection(Protocol[SourceT]):
    """Project one exact Source value through a named, versioned capability."""

    name: str
    version: str
    source_class: type[SourceT]
    output_class: type[BaseModel]

    def project(self, source: SourceT, /) -> object: ...


class SourceDefinition(SourceAdapter[InputT, SourceT, ValueT_co], Protocol[InputT, SourceT, ValueT_co]):
    """Bind one adapter contract to a durable version and optional projections."""

    version: str
    projections: tuple[SourceProjection[SourceT], ...]


@dataclass(frozen=True, slots=True)
class AdapterSourceDefinition(Generic[InputT, SourceT, ValueT_co]):
    """Promote an existing typed Source adapter into an explicit Definition."""

    adapter: SourceAdapter[InputT, SourceT, ValueT_co]
    version: str = "1"
    projections: tuple[SourceProjection[SourceT], ...] = ()

    @property
    def input_class(self) -> type[InputT]:
        return self.adapter.input_class

    @property
    def name(self) -> str:
        return self.adapter.name

    @property
    def source_class(self) -> type[SourceT]:
        return self.adapter.source_class

    async def resolve(self, value: InputT, /) -> SourceT:
        return await self.adapter.resolve(value)

    async def read(self, source: SourceT, /) -> ValueT_co:
        return await self.adapter.read(source)


_AnySourceDefinition = SourceDefinition[Any, Any, Any]


class SourceDefinitionRegistry:
    """Provide one immutable routing view for Source persistence and consumers."""

    def __init__(self, definitions: Iterable[_AnySourceDefinition], /) -> None:
        by_input: dict[type[object], _AnySourceDefinition] = {}
        by_source: dict[type[Source], _AnySourceDefinition] = {}
        by_name: dict[str, _AnySourceDefinition] = {}
        projections: dict[type[Source], Mapping[SourceProjectionKey, SourceProjection[Any]]] = {}
        registered: list[_AnySourceDefinition] = []

        for definition in definitions:
            input_class, source_class = _validate_definition(definition)
            if input_class in by_input:
                raise SourceConflictError("input_class", input_class)
            if source_class in by_source:
                raise SourceConflictError("source_class", source_class)
            if definition.name in by_name:
                raise SourceConflictError("name", definition.name)

            projection_routes: dict[SourceProjectionKey, SourceProjection[Any]] = {}
            for projection in definition.projections:
                key = _validate_projection(definition, projection)
                if key in projection_routes:
                    raise SourceConflictError("projection", (definition.name, key))
                projection_routes[key] = projection

            by_input[input_class] = definition
            by_source[source_class] = definition
            by_name[definition.name] = definition
            projections[source_class] = projection_routes
            registered.append(definition)

        self._definitions = tuple(registered)
        self._by_input = by_input
        self._by_source = by_source
        self._by_name = by_name
        self._projections = projections

    @classmethod
    def from_adapters(cls, adapters: Iterable[_AnySourceAdapter], /) -> SourceDefinitionRegistry:
        """Wrap legacy adapters as version ``1`` Definitions without projections."""

        return cls(AdapterSourceDefinition(adapter) for adapter in adapters)

    @property
    def definitions(self) -> tuple[_AnySourceDefinition, ...]:
        return self._definitions

    def definition_for_name(self, name: str, /) -> _AnySourceDefinition:
        try:
            return self._by_name[name]
        except KeyError:
            raise SourceDefinitionNotFoundError(name) from None

    def definition_for_source(self, source: object, /) -> _AnySourceDefinition:
        if not isinstance(source, Source):
            raise InvalidSourceEntryError(type(source))
        try:
            definition = self._by_source[type(source)]
        except KeyError:
            raise SourceAdapterNotFoundError("source", type(source)) from None
        if source.definition_version != definition.version:
            raise InvalidSourceDefinitionError(
                type(definition),
                "version",
                f"Source declares {source.definition_version!r}, expected {definition.version!r}",
            )
        return definition

    async def resolve(self, value: object, /) -> Source:
        input_class = type(value)
        try:
            definition = self._by_input[input_class]
        except KeyError:
            raise SourceAdapterNotFoundError("input", input_class) from None
        source = await definition.resolve(value)
        if type(source) is not definition.source_class:
            raise InvalidSourceResultError(definition.name, "resolve", definition.source_class, type(source))
        self.definition_for_source(source)
        return cast(Source, source)

    async def read(self, source: Source, /) -> object:
        definition = self.definition_for_source(source)
        return await definition.read(source)

    def projection_keys(self, source: Source, /) -> tuple[SourceProjectionKey, ...]:
        from powercontext.sources.observations import SourceObservation

        if isinstance(source, SourceObservation):
            return tuple(projection.key for projection in source.projections)
        self.definition_for_source(source)
        return tuple(self._projections[type(source)])

    def project(self, source: Source, key: SourceProjectionKey, /) -> JsonValue:
        from powercontext.sources.observations import SourceObservation

        if isinstance(source, SourceObservation):
            return source.projection(key)
        definition = self.definition_for_source(source)
        try:
            projection = self._projections[type(source)][key]
        except KeyError:
            raise SourceProjectionNotFoundError(definition.name, key.name, key.version) from None
        value = projection.project(source)
        try:
            validated = projection.output_class.model_validate(value)
            return _JSON_VALUE.validate_python(validated.model_dump(mode="json"))
        except (TypeError, ValueError) as error:
            raise InvalidSourceProjectionError(key.name, "result", "must match the declared output schema") from error


def _validate_definition(definition: object) -> tuple[type[object], type[Source]]:
    definition_type = type(definition)
    input_class = getattr(definition, "input_class", None)
    if not isinstance(input_class, type):
        raise InvalidSourceAdapterError(definition_type, "input_class", "must be a type")
    name = getattr(definition, "name", None)
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise InvalidSourceDefinitionError(definition_type, "name", "must be a non-empty trimmed string")
    version = getattr(definition, "version", None)
    if not isinstance(version, str) or not version.strip() or version != version.strip():
        raise InvalidSourceDefinitionError(definition_type, "version", "must be a non-empty trimmed string")
    source_class = getattr(definition, "source_class", None)
    if not isinstance(source_class, type) or not issubclass(source_class, Source):
        raise InvalidSourceAdapterError(definition_type, "source_class", "must be a Source subclass")
    projections = getattr(definition, "projections", None)
    if not isinstance(projections, tuple):
        raise InvalidSourceDefinitionError(definition_type, "projections", "must be a tuple")
    for method_name in ("resolve", "read"):
        if not callable(getattr(definition, method_name, None)):
            raise InvalidSourceAdapterError(definition_type, method_name, "must be callable")
    return cast(type[object], input_class), cast(type[Source], source_class)


def _validate_projection(
    definition: _AnySourceDefinition,
    projection: object,
) -> SourceProjectionKey:
    projection_type = type(projection)
    name = getattr(projection, "name", None)
    version = getattr(projection, "version", None)
    if not isinstance(name, str) or not isinstance(version, str):
        raise InvalidSourceProjectionError(str(name), "key", "must contain string name and version")
    try:
        key = SourceProjectionKey(name=name, version=version)
    except (TypeError, ValueError) as error:
        raise InvalidSourceProjectionError(str(name), "key", "must contain valid name and version") from error
    source_class = getattr(projection, "source_class", None)
    if source_class is not definition.source_class:
        raise InvalidSourceProjectionError(
            key.name,
            "source_class",
            f"must be {definition.source_class.__module__}.{definition.source_class.__qualname__}",
        )
    output_class = getattr(projection, "output_class", None)
    if not isinstance(output_class, type) or not issubclass(output_class, BaseModel):
        raise InvalidSourceProjectionError(key.name, "output_class", "must be a BaseModel subclass")
    if not callable(getattr(projection, "project", None)):
        raise InvalidSourceProjectionError(key.name, "project", f"must be callable on {projection_type.__name__}")
    return key
