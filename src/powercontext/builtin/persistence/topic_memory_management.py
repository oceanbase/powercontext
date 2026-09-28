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

"""Request-local preparation and atomic manual Topic Memory writes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemory,
    TopicMemoryCapabilityError,
    TopicMemoryContent,
    TopicMemoryDraft,
    TopicMemoryProjection,
    chunk_topic_memory_detail,
    prepare_topic_memory_projection,
)
from powercontext.builtin.inference import EmbeddingModel, InferenceTimeoutError, InvalidInferenceOutputError
from powercontext.builtin.inference.usage import UsageReporter, bind_usage_reporter
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.records import InvalidBaseAccessRequestError
from powercontext.builtin.statistics import ModelUsagePurpose
from powercontext.sources import SourceRef


class _ManualContent(TopicMemoryContent):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TopicMemoryManagementWriter:
    """Keep inference outside the transaction and publish all family state together."""

    family = TopicMemory.family

    def __init__(
        self,
        topics: TopicMemoryRepository,
        embedding_model: EmbeddingModel | None = None,
        *,
        timeout_seconds: float = 30.0,
        max_concurrency: int = 4,
        usage_reporter: Callable[[str], UsageReporter] | None = None,
    ) -> None:
        self.topics = topics
        self._embedding_model = embedding_model
        self._timeout_seconds = timeout_seconds
        self._slots = asyncio.Semaphore(max_concurrency)
        self._usage_reporter = usage_reporter

    def artifact_id_for_create(self, generated: str, /) -> str:
        return generated

    def validate_create(self, content: Mapping[str, JsonValue]) -> BaseModel:
        try:
            return _ManualContent.model_validate(dict(content), strict=True)
        except ValidationError as error:
            raise InvalidBaseAccessRequestError("content", "does not match the topic-memory model") from error

    def validate_replace(self, content: Mapping[str, JsonValue]) -> BaseModel:
        return self.validate_create(content)

    async def prepare(
        self,
        content: BaseModel,
        /,
        *,
        usage_scope_id: str | None = None,
    ) -> TopicMemoryProjection:
        value = TopicMemoryContent.model_validate(content.model_dump())
        capabilities = self.topics.index.capabilities
        if not capabilities.fts:
            raise TopicMemoryCapabilityError("fts")
        if not capabilities.vector:
            return prepare_topic_memory_projection(value)
        model = self._embedding_model
        if model is None or model.profile != capabilities.embedding_profile:
            raise TopicMemoryCapabilityError("embedding-profile")
        chunks = chunk_topic_memory_detail(value.detail)
        texts = (f"{value.title}\n{value.summary}", *(chunk.text for chunk in chunks))
        try:
            with self._embedding_usage(usage_scope_id):
                async with asyncio.timeout(self._timeout_seconds), self._slots:
                    result = await model.embed(texts)
        except TimeoutError as error:
            raise InferenceTimeoutError("topic-memory.index", self._timeout_seconds) from error
        if len(result.vectors) != len(texts):
            raise InvalidInferenceOutputError("topic-memory.index", "requires one vector per input")
        return prepare_topic_memory_projection(
            value,
            topic_embedding=result.vectors[0],
            chunk_embeddings=result.vectors[1:],
            embedding_profile=model.profile,
        )

    def _embedding_usage(self, scope_id: str | None):
        if self._usage_reporter is None or scope_id is None:
            return nullcontext()
        return bind_usage_reporter(
            self._usage_reporter(scope_id),
            embedding_purpose=ModelUsagePurpose.TOPIC_MEMORY_INDEXING,
        )

    async def create(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> TopicMemory:
        if not isinstance(content, TopicMemoryProjection):
            raise TypeError("Topic Memory write requires a prepared projection")  # noqa: TRY003
        published = await self.topics.publish_create(
            connection,
            scope_id,
            artifact_id,
            TopicMemoryDraft(content=content.content, sources=(direct_source,)),
            content,
        )
        return published.topic

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        current: Artifact[Any],
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> TopicMemory:
        if not isinstance(current, TopicMemory) or not isinstance(content, TopicMemoryProjection):
            raise TypeError("Topic Memory replacement requires a topic and prepared projection")  # noqa: TRY003
        published = await self.topics.publish_revision(
            connection,
            scope_id,
            current,
            TopicMemoryDraft(content=content.content, sources=(direct_source,), artifacts=current.lineage.artifacts),
            content,
        )
        return published.topic
