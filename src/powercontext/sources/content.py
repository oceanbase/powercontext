"""A neutral captured-text Source for runtime integrations."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Annotated

from pydantic import JsonValue, PlainSerializer, TypeAdapter

from powercontext.sources.models import Source, SourceMaterialization

CONTENT_SOURCE_NAME = "content"
_METADATA_ADAPTER = TypeAdapter(dict[str, JsonValue])


def _serialize_metadata(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return dict(value)


_JsonMetadata = Annotated[
    Mapping[str, JsonValue],
    PlainSerializer(_serialize_metadata, return_type=dict[str, JsonValue]),
]


class _FrozenJsonObject(Mapping[str, JsonValue]):
    """Keep one private JSON snapshot and return copies of nested values."""

    def __init__(self, value: Mapping[str, JsonValue]) -> None:
        self._value = deepcopy(dict(value))

    def __getitem__(self, key: str) -> JsonValue:
        return deepcopy(self._value[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._value)

    def __len__(self) -> int:
        return len(self._value)


def _freeze_metadata(value: object) -> _FrozenJsonObject:
    return _FrozenJsonObject(_METADATA_ADAPTER.validate_python(value))


class _InvalidContentCaptureError(ValueError):
    def __init__(self, field_name: str) -> None:
        super().__init__(f"content {field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class ContentCapture:
    """Text supplied by an integration with a caller-stable identity."""

    source_id: str
    content: str
    metadata: _JsonMetadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise _InvalidContentCaptureError("source_id")
        if not self.content.strip():
            raise _InvalidContentCaptureError("body")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True, kw_only=True)
class ContentSource(Source):
    """Captured text that can be used as Artifact evidence."""

    content: str
    metadata: _JsonMetadata = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


class ContentSourceAdapter:
    """Resolve and read the runtime's built-in captured-text Source."""

    input_class = ContentCapture
    name = CONTENT_SOURCE_NAME
    source_class = ContentSource

    async def resolve(self, value: ContentCapture, /) -> ContentSource:
        return ContentSource(
            name=value.source_id,
            materialization=SourceMaterialization.CAPTURED,
            content=value.content,
            metadata=value.metadata,
        )

    async def read(self, source: ContentSource, /) -> ContentCapture:
        return ContentCapture(
            source_id=source.name,
            content=source.content,
            metadata=source.metadata,
        )
