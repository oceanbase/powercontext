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
from powercontext.artifacts.search import ArtifactSearchContractError, ArtifactSearchExecutionContext
from powercontext.builtin.artifacts.memory import MemoryQueryEmbedding
from powercontext.builtin.artifacts.search import AdmissionFloor, analyze_text
from powercontext.builtin.artifacts.topic_memory import (
    MAX_TOPIC_MEMORY_QUERY_TERMS,
    TopicArtifactSearchRequest,
    TopicMemoryCapabilities,
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

logger = logging.getLogger(__name__)


class TopicMemorySearcher:
    """Own mode selection, embedding reuse, and transient default fallback.

    The calling Runtime facade owns scoped operation and model usage accounting.
    Public execution forwards the Family request to the composed backend. Legacy
    execution preserves its existing callback keywords.
    """

    family = "topic-memory"
    request_type = TopicArtifactSearchRequest

    def __init__(
        self,
        *,
        search: Callable[..., Awaitable[TopicMemorySearchResult]],
        embedding_model: EmbeddingModel | None = None,
        observer: Callable[[str, bool], None] | None = None,
        capabilities: TopicMemoryCapabilities | None = None,
    ) -> None:
        self._search = search
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
        if len(result.matches) != len(result.hits) or len(result.artifacts) != len(result.matches):
            raise ArtifactSearchContractError(self.family, "public Topic search requires complete exact Artifacts")
        self._observe(result, fallback is not None)
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
        self._observe(result, fallback is not None)
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
    ) -> tuple[TopicMemorySearchResult, str | None]:
        embedding = embedding_model if plan is None or plan.mode != "fts" else None
        reusable = (
            embedding is not None
            and query_embedding is not None
            and query_embedding.embedding_profile == embedding.profile
        )
        # Disallowing embedding stops new provider requests; a matching cached vector needs none.
        if not allow_embedding and not reusable:
            embedding = None
        keywords: dict[str, Any] = {"limit": limit}
        if admission is not None:
            keywords["admission"] = admission
        if artifact_request is not None:
            keywords["artifact_request"] = artifact_request
        if execution_context is not None:
            keywords["execution_context"] = execution_context
        if embedding is None:
            result = await self._search(scope_id, query, mode="fts", **keywords)
            return result.model_copy(update={"embedding_calls": 0}), None
        mode = "hybrid" if plan is None else plan.mode
        if query_embedding is not None and reusable:
            result = await self._search(
                scope_id,
                query,
                mode=mode,
                query_vector=query_embedding.query_vector,
                embedding_profile=query_embedding.embedding_profile,
                **keywords,
            )
            return result.model_copy(update={"query_embedding": query_embedding, "embedding_calls": 0}), None
        started = asyncio.get_running_loop().time()
        embedded, fallback = await self._embed_for_search(
            embedding,
            query,
            timeout_seconds=embedding_timeout_seconds,
            allow_fallback=plan is None or plan.fallback_allowed,
        )
        waited = asyncio.get_running_loop().time() - started
        if embedded is None:
            result = await self._search(scope_id, query, mode="fts", **keywords)
            return (
                result.model_copy(
                    update={"embedding_calls": 1, "embedding_fallback": fallback, "embedding_wait_seconds": waited}
                ),
                fallback,
            )
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
                "embedding_wait_seconds": waited,
            }
        ), None

    async def _embed_for_search(
        self,
        embedding: EmbeddingModel,
        query: str,
        *,
        timeout_seconds: float | None,
        allow_fallback: bool,
    ) -> tuple[EmbeddingResult | None, str | None]:
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
            return None, self._fallback_reason(error)
        return embedded, None

    @staticmethod
    def _fallback_reason(error: Exception) -> str:
        """Why an abandoned embedding gave way to FTS.

        The same value is the ``topic_memory.search.embedding_fallback`` ``error_code`` and, when a
        preparation asks for a Receipt, its search ``fallback_reason``. RFC 1435 spells this vocabulary
        ``inference_*`` because the failure belongs to the inference provider, not to the channel it
        was going to feed.
        """

        return (
            "inference_timeout" if isinstance(error, (InferenceTimeoutError, TimeoutError)) else "inference_unavailable"
        )

    def _log_fallback(self, error: Exception) -> None:
        log_safely(
            logger,
            logging.WARNING,
            "Topic Memory search fell back to FTS",
            extra={
                "event": "topic_memory.search.embedding_fallback",
                "outcome": "fallback",
                "mode": "fts",
                # Derived from the public reason so the event and the response cannot disagree
                # about why the vector channel was lost.
                "error_code": self._fallback_reason(error),
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
