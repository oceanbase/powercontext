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

"""Shared Topic search orchestration for public Artifact and legacy entry points."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from powercontext._logging import log_safely
from powercontext.artifacts import ArtifactRef
from powercontext.artifacts.search import ArtifactSearchContractError, ArtifactSearchExecutionContext
from powercontext.builtin.artifacts.memory import MemoryQueryEmbedding
from powercontext.builtin.artifacts.search import AdmissionFloor, analyze_text
from powercontext.builtin.artifacts.topic_memory import (
    MAX_TOPIC_MEMORY_QUERY_TERMS,
    PublishedTopicMemory,
    TopicArtifactSearchRequest,
    TopicMemoryCapabilities,
    TopicMemoryCurrentItem,
    TopicMemorySearchResult,
)
from powercontext.builtin.artifacts.topic_memory.search import TopicSearchPlan, plan_topic_search, topic_search_error
from powercontext.builtin.inference import (
    EmbeddingModel,
    EmbeddingResult,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.inference.protocols import embed_query
from powercontext.builtin.persistence.errors import RepositoryNotFoundError

logger = logging.getLogger(__name__)


class TopicMemorySearcher:
    """Own mode selection, embedding reuse, and transient default fallback.

    The calling Runtime facade owns scoped operation and model usage accounting.
    Public execution forwards the Family request to the composed backend. Legacy
    execution preserves its existing callback keywords and complete-read cost.
    """

    family = "topic-memory"
    request_type = TopicArtifactSearchRequest

    def __init__(
        self,
        *,
        search: Callable[..., Awaitable[TopicMemorySearchResult]],
        get: Callable[[str, ArtifactRef], Awaitable[PublishedTopicMemory]] | None = None,
        browse: Callable[..., Awaitable[tuple[TopicMemoryCurrentItem, ...]]] | None = None,
        embedding_model: EmbeddingModel | None = None,
        observer: Callable[[str, bool], None] | None = None,
        capabilities: TopicMemoryCapabilities | None = None,
    ) -> None:
        self._search = search
        self._get = get
        self._browse = browse
        self._embedding = embedding_model
        self._observer = observer
        self._capabilities = capabilities

    async def search(
        self,
        scope_id: str,
        request: TopicArtifactSearchRequest,
        /,
        *,
        execution_context: ArtifactSearchExecutionContext | None = None,
    ) -> TopicMemorySearchResult:
        capabilities = self._capabilities
        if capabilities is None:
            raise topic_search_error(("mode",), "Topic search capabilities are not configured")
        embedding = self._embedding
        plan = plan_topic_search(request, capabilities, embedding_available=embedding is not None)
        if (
            plan.mode in {"vector", "hybrid"}
            and embedding is not None
            and capabilities.embedding_profile != embedding.profile
        ):
            raise topic_search_error(
                ("mode",), "vector and hybrid searches require an embedding model matching the index profile"
            )
        if (
            plan.mode in {"fts", "hybrid"}
            and len(set(analyze_text(request.query).split())) > MAX_TOPIC_MEMORY_QUERY_TERMS
        ):
            raise topic_search_error(
                ("query",), f"text search accepts at most {MAX_TOPIC_MEMORY_QUERY_TERMS} analyzed terms"
            )
        result, fallback = await self._execute(
            scope_id,
            request.query,
            limit=request.limit,
            admission=request.admission.as_floor(),
            artifact_request=request,
            plan=plan,
            embedding_model=embedding,
            execution_context=execution_context,
        )
        if result.artifacts is None:
            if self._get is None:
                raise ArtifactSearchContractError(
                    self.family, "public Topic search did not materialize exact Artifacts"
                )
            if len(result.matches) != len(result.hits):
                raise ArtifactSearchContractError(self.family, "public Topic hits and matches must have the same count")
            artifacts = []
            for match in result.matches:
                try:
                    publication = await self._get(scope_id, match.artifact_ref)
                except RepositoryNotFoundError as error:
                    raise ArtifactSearchContractError(self.family, "selected exact Artifact is missing") from error
                artifacts.append(publication.topic)
            result = result.model_copy(update={"artifacts": tuple(artifacts)})
        self._observe(result, fallback)
        return result

    async def search_legacy(
        self,
        scope_id: str,
        query: str,
        /,
        *,
        limit: int,
        admission: AdmissionFloor | None = None,
        query_embedding: MemoryQueryEmbedding | None = None,
        embedding_timeout_seconds: float | None = None,
        allow_embedding: bool = True,
        execution_context: ArtifactSearchExecutionContext | None = None,
    ) -> TopicMemorySearchResult:
        result, fallback = await self._execute(
            scope_id,
            query,
            limit=limit,
            admission=admission,
            query_embedding=query_embedding,
            embedding_timeout_seconds=embedding_timeout_seconds,
            allow_embedding=allow_embedding,
            embedding_model=self._embedding,
            execution_context=execution_context,
        )
        self._observe(result, fallback)
        return result

    async def _execute(
        self,
        scope_id: str,
        query: str,
        *,
        limit: int,
        admission: AdmissionFloor | None,
        embedding_model: EmbeddingModel | None,
        query_embedding: MemoryQueryEmbedding | None = None,
        embedding_timeout_seconds: float | None = None,
        allow_embedding: bool = True,
        artifact_request: TopicArtifactSearchRequest | None = None,
        plan: TopicSearchPlan | None = None,
        execution_context: ArtifactSearchExecutionContext | None = None,
    ) -> tuple[TopicMemorySearchResult, bool]:
        embedding = embedding_model if allow_embedding and (plan is None or plan.mode != "fts") else None
        keywords: dict[str, Any] = {"limit": limit}
        if admission is not None:
            keywords["admission"] = admission
        if artifact_request is not None:
            keywords["artifact_request"] = artifact_request
        if execution_context is not None:
            keywords["execution_context"] = execution_context
        if (
            embedding is not None
            and self._browse is not None
            and (execution_context is None or (execution_context.access is None and execution_context.trusted_local))
            and not await self._browse(scope_id, limit=1, after=None)
        ):
            if artifact_request is not None and plan is not None and not plan.fallback_allowed:
                return TopicMemorySearchResult(mode=plan.mode, artifacts=()), False
            embedding = None
        if embedding is None:
            result = await self._search(scope_id, query, mode="fts", **keywords)
            return result.model_copy(update={"embedding_calls": 0}), False
        mode = "hybrid" if plan is None else plan.mode
        if query_embedding is not None and query_embedding.embedding_profile == embedding.profile:
            result = await self._search(
                scope_id,
                query,
                mode=mode,
                query_vector=query_embedding.query_vector,
                embedding_profile=query_embedding.embedding_profile,
                **keywords,
            )
            return result.model_copy(update={"query_embedding": query_embedding, "embedding_calls": 0}), False
        embedded = await self._embed_for_search(
            embedding,
            query,
            timeout_seconds=embedding_timeout_seconds,
            allow_fallback=plan is None or plan.fallback_allowed,
        )
        if embedded is None:
            result = await self._search(scope_id, query, mode="fts", **keywords)
            return result.model_copy(update={"embedding_calls": 1}), True
        result = await self._search(
            scope_id,
            query,
            mode=mode,
            query_vector=embedded.vectors[0],
            embedding_profile=embedding.profile,
            **keywords,
        )
        return result.model_copy(
            update={
                "query_embedding": MemoryQueryEmbedding(
                    query_vector=tuple(embedded.vectors[0]), embedding_profile=embedding.profile
                ),
                "embedding_calls": 1,
            }
        ), False

    async def _embed_for_search(
        self,
        embedding: EmbeddingModel,
        query: str,
        *,
        timeout_seconds: float | None,
        allow_fallback: bool,
    ) -> EmbeddingResult | None:
        try:
            async with asyncio.timeout(timeout_seconds):
                embedded = await embed_query(embedding, (query,))
            if len(embedded.vectors) != 1:
                raise InvalidInferenceOutputError("embed", "provider returned the wrong vector count")
        except (InferenceUnavailableError, InferenceTimeoutError, TimeoutError) as error:
            if not allow_fallback:
                if isinstance(error, TimeoutError) and not isinstance(error, InferenceTimeoutError):
                    raise InferenceUnavailableError("embed", "embedding request timed out") from None
                raise
            self._log_fallback(error)
            return None
        return embedded

    @staticmethod
    def _log_fallback(error: Exception) -> None:
        log_safely(
            logger,
            logging.WARNING,
            "Topic Memory search fell back to FTS",
            extra={
                "event": "topic_memory.search.embedding_fallback",
                "outcome": "fallback",
                "mode": "fts",
                "error_code": "inference_timeout"
                if isinstance(error, (InferenceTimeoutError, TimeoutError))
                else "inference_unavailable",
                "unit": "topic-memory",
            },
        )

    def _observe(self, result: TopicMemorySearchResult, fallback: bool) -> None:
        if self._observer is not None:
            try:
                self._observer(result.mode, fallback)
            except Exception as error:
                log_safely(
                    logger,
                    logging.ERROR,
                    "Topic Memory search observation failed",
                    exc_info=error,
                    extra={
                        "event": "topic_memory.search.observation_failed",
                        "outcome": "failure",
                        "unit": "topic-memory",
                    },
                )


__all__ = ["TopicMemorySearcher"]
