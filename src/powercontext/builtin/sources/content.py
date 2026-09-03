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

"""A neutral captured-text Source for runtime integrations."""

from __future__ import annotations

from typing import Annotated, Literal

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


class ContentSourceTarget(BaseModel):
    """Exact Artifact revision to which one system Source is bound."""

    scope_id: str
    family: Literal["memory", "experience", "skill", "handoff"]
    artifact_id: str
    revision: Literal[1] = 1


class ContentSourceInternal(BaseModel):
    """Server-owned Source purpose data never exposed by the base REST API."""

    role: Literal["lineage_only"]
    operation: Literal["artifact_create"]
    target: ContentSourceTarget


class ContentSource(Source):
    """Captured content that can be used as Artifact evidence."""

    content: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    wire_content: JsonValue | None = None
    internal: ContentSourceInternal | None = None


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
        if not isinstance(source.content, str):
            raise TypeError("system Content Sources cannot be read as integration captures")  # noqa: TRY003
        return ContentCapture(
            source_id=source.name,
            content=source.content,
            metadata=source.metadata,
        )


CONTENT_SOURCE_ADAPTER = ContentSourceAdapter()
