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

"""Typed content for the built-in Topic Memory Artifact family."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, ClassVar, Literal, TypeAlias

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from powercontext.artifacts import Artifact, ArtifactDraft, ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingVector

MAX_TOPIC_MEMORY_TITLE_LENGTH = 512
MAX_TOPIC_MEMORY_SUMMARY_LENGTH = 8_000
MAX_TOPIC_MEMORY_DETAIL_LENGTH = 125_000

TopicMemoryTitle = Annotated[str, Field(min_length=1, max_length=MAX_TOPIC_MEMORY_TITLE_LENGTH)]
TopicMemorySummary = Annotated[str, Field(min_length=1, max_length=MAX_TOPIC_MEMORY_SUMMARY_LENGTH)]
TopicMemoryDetail = Annotated[str, Field(min_length=1, max_length=MAX_TOPIC_MEMORY_DETAIL_LENGTH)]


class TopicMemoryContent(BaseModel):
    """Progressively disclosed content for one durable topic."""

    title: TopicMemoryTitle
    summary: TopicMemorySummary
    detail: TopicMemoryDetail

    @field_validator("title", "summary", "detail")
    @classmethod
    def reject_blank_sections(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Topic Memory sections must not be blank")  # noqa: TRY003
        return value


class TopicMemory(Artifact[TopicMemoryContent]):
    """An immutable Topic Memory revision."""

    family: ClassVar[str] = "topic-memory"


class TopicMemoryDraft(ArtifactDraft[TopicMemoryContent]):
    """Complete Topic Memory content and evidence ready for commit."""

    family: ClassVar[str] = "topic-memory"


TopicMemorySearchMode: TypeAlias = Literal["fts", "vector", "hybrid", "auto"]
TopicMemoryUsedSearchMode: TypeAlias = Literal["fts", "vector", "hybrid"]
TopicMemoryMatchedBy: TypeAlias = Literal["topic_fts", "topic_vector", "detail_fts", "detail_vector"]


class TopicMemoryChunk(BaseModel):
    """One rebuildable Markdown-aware search chunk without public identity."""

    ordinal: StrictInt = Field(ge=0)
    start_offset: StrictInt = Field(ge=0)
    end_offset: StrictInt = Field(gt=0)
    text: str = Field(min_length=1)
    policy_version: Literal["markdown-v1"] = "markdown-v1"

    @model_validator(mode="after")
    def validate_offsets(self):
        if self.end_offset <= self.start_offset:
            raise ValueError("Topic Memory chunk end_offset must follow start_offset")  # noqa: TRY003
        if not self.text.strip():
            raise ValueError("Topic Memory chunks must not be blank")  # noqa: TRY003
        return self


class TopicMemoryProjection(BaseModel):
    """A complete active projection prepared before the publication transaction."""

    content: TopicMemoryContent
    chunks: tuple[TopicMemoryChunk, ...]
    topic_searchable_text: str = Field(min_length=1)
    topic_embedding: EmbeddingVector | None = None
    chunk_embeddings: tuple[EmbeddingVector, ...] = ()
    embedding_profile: EmbeddingProfile | None = None

    @model_validator(mode="after")
    def validate_projection(self):
        if not self.chunks:
            raise ValueError("Topic Memory projection requires at least one detail chunk")  # noqa: TRY003
        for ordinal, chunk in enumerate(self.chunks):
            if chunk.ordinal != ordinal:
                raise ValueError("Topic Memory chunk ordinals must be contiguous")  # noqa: TRY003
            if chunk.end_offset > len(self.content.detail):
                raise ValueError("Topic Memory chunk exceeds detail bounds")  # noqa: TRY003
            if self.content.detail[chunk.start_offset : chunk.end_offset] != chunk.text:
                raise ValueError("Topic Memory chunk text must match its detail offsets")  # noqa: TRY003
        if self.chunk_embeddings and len(self.chunk_embeddings) != len(self.chunks):
            raise ValueError("Topic Memory chunk embeddings must cover every chunk")  # noqa: TRY003
        if (self.topic_embedding is None) != (self.embedding_profile is None):
            raise ValueError("Topic Memory topic embedding requires its embedding profile")  # noqa: TRY003
        if self.chunk_embeddings and self.embedding_profile is None:
            raise ValueError("Topic Memory chunk embeddings require an embedding profile")  # noqa: TRY003
        return self


class TopicMemoryCapabilities(BaseModel):
    """Search channels available for the fixed deployment shape."""

    fts: bool
    vector: bool = False
    hybrid: bool = False
    embedding_profile: EmbeddingProfile | None = None


class TopicMemorySearchRequest(BaseModel):
    """A bounded, validated request over one scope's active projections."""

    query: str = Field(min_length=1)
    analyzed_query: str = ""
    candidate_limit: StrictInt = Field(ge=1, le=100)
    mode: TopicMemoryUsedSearchMode
    query_vector: EmbeddingVector | None = None
    embedding_profile: EmbeddingProfile | None = None


class TopicMemoryChannelHit(BaseModel):
    """One exact active Topic hit from a single ordered search channel."""

    artifact_ref: ArtifactRef
    title: str
    summary: str
    channel: TopicMemoryMatchedBy
    chunk_ordinal: StrictInt | None = Field(default=None, ge=0)
    chunk_start: StrictInt | None = Field(default=None, ge=0)
    chunk_text: str | None = None
    distance: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)


class TopicMemorySearchChannels(BaseModel):
    """Backend-ordered rankings before Topic collapse and RRF."""

    topic_fts: tuple[TopicMemoryChannelHit, ...] = ()
    topic_vector: tuple[TopicMemoryChannelHit, ...] = ()
    detail_fts: tuple[TopicMemoryChannelHit, ...] = ()
    detail_vector: tuple[TopicMemoryChannelHit, ...] = ()


class TopicMemorySearchHit(BaseModel):
    """A fused, progressively disclosed hit anchored to an exact Revision."""

    artifact_ref: ArtifactRef
    title: str
    summary: str
    snippet: str | None = None
    score: float = Field(gt=0.0, allow_inf_nan=False)
    matched_by: tuple[TopicMemoryMatchedBy, ...]


class TopicMemorySearchResult(BaseModel):
    """Fused Topic hits and the deployment mode actually used."""

    mode: TopicMemoryUsedSearchMode
    hits: tuple[TopicMemorySearchHit, ...] = ()


class PublishedTopicMemory(BaseModel):
    """One exact immutable Topic Revision with authoritative publication time."""

    topic: TopicMemory
    published_at: datetime
    is_current: bool
    current_artifact: ArtifactRef

    @field_validator("published_at")
    @classmethod
    def require_utc_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Topic Memory publication time must include a UTC offset")  # noqa: TRY003
        return value.astimezone(UTC)


class TopicMemoryCurrentItem(BaseModel):
    """A compact current-head row for bounded management browsing."""

    artifact_ref: ArtifactRef
    title: str
    summary: str
    published_at: datetime
    source_count: StrictInt = Field(ge=0)

    @field_validator("published_at")
    @classmethod
    def normalize_utc_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Topic Memory publication time must include a UTC offset")  # noqa: TRY003
        return value.astimezone(UTC)


class TopicMemoryBrowseCursor(BaseModel):
    """Exclusive keyset boundary for stable current-head browsing."""

    published_at: datetime
    artifact_id: str
    revision: StrictInt = Field(ge=1)

    @field_validator("published_at")
    @classmethod
    def normalize_cursor_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Topic Memory browse cursor time must include a UTC offset")  # noqa: TRY003
        return value.astimezone(UTC)


__all__ = [
    "MAX_TOPIC_MEMORY_DETAIL_LENGTH",
    "MAX_TOPIC_MEMORY_SUMMARY_LENGTH",
    "MAX_TOPIC_MEMORY_TITLE_LENGTH",
    "PublishedTopicMemory",
    "TopicMemory",
    "TopicMemoryBrowseCursor",
    "TopicMemoryCapabilities",
    "TopicMemoryChannelHit",
    "TopicMemoryChunk",
    "TopicMemoryContent",
    "TopicMemoryCurrentItem",
    "TopicMemoryDraft",
    "TopicMemoryMatchedBy",
    "TopicMemoryProjection",
    "TopicMemorySearchChannels",
    "TopicMemorySearchHit",
    "TopicMemorySearchMode",
    "TopicMemorySearchRequest",
    "TopicMemorySearchResult",
    "TopicMemoryUsedSearchMode",
]
