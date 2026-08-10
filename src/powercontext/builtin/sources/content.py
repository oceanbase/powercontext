"""A neutral captured-text Source for runtime integrations."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, JsonValue, field_validator

from powercontext.sources.models import Source, SourceMaterialization

CONTENT_SOURCE_NAME = "content"
NonEmptyText = Annotated[str, Field(min_length=1)]


class ContentCapture(BaseModel):
    """Text supplied by an integration with a caller-stable identity."""

    source_id: NonEmptyText
    content: NonEmptyText
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("source_id", "content")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content text must not be blank")  # noqa: TRY003
        return value


class ContentSource(Source):
    """Captured text that can be used as Artifact evidence."""

    content: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


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


CONTENT_SOURCE_ADAPTER = ContentSourceAdapter()
