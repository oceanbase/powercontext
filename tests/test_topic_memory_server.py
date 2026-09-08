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

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from fastapi.testclient import TestClient
from pydantic_ai import Embedder
from pydantic_ai.embeddings import EmbeddingModel as PydanticAIEmbeddingModelBase
from pydantic_ai.exceptions import ModelHTTPError

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.topic_memory import (
    PublishedTopicMemory,
    TopicMemory,
    TopicMemoryContent,
    TopicMemorySearchHit,
    TopicMemorySearchResult,
)
from powercontext.builtin.inference.pydantic_ai import PydanticAIEmbeddingModel
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinRuntime,
    RuntimeCapabilities,
    TopicMemoryFlushResult,
    TopicMemoryProcessingUnavailableError,
)
from powercontext.errors import ArtifactNotFoundError
from powercontext.server.app import create_app
from powercontext.server.factory import create_server_app
from powercontext.server.logging import JsonFormatter
from powercontext.server.settings import McpConfig, ServerSettings
from powercontext.sources import SourceRef

REFERENCE = ArtifactRef(family="topic-memory", artifact_id="supervisor", revision=3)


class _UnusedProvider:
    async def get(self, scope_id: str, /) -> Any:
        raise AssertionError(scope_id)


class _RegisteredScopes:
    async def get(self, scope_id: str, /) -> Any:
        return SimpleNamespace(scope_id=scope_id)


class _FailingProviderEmbeddingModel(PydanticAIEmbeddingModelBase):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    @property
    def model_name(self) -> str:
        return "failing-model"

    @property
    def system(self) -> str:
        return "test"

    async def embed(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self._error


class _RecordingMetrics:
    def __init__(self) -> None:
        self.applications: list[tuple[str, str]] = []

    def observe_application(self, operation: str, outcome: str, _started_at: float) -> None:
        self.applications.append((operation, outcome))


class _TopicMemoryApplication:
    def for_scope(self, scope_id: str, /) -> _TopicMemoryApplication:
        assert scope_id == "scope-a"
        return self

    async def search(self, request):
        assert request.query == "supervisor recovery"
        assert request.limit == 4
        return TopicMemorySearchResult(
            mode="hybrid",
            hits=(
                TopicMemorySearchHit(
                    artifact_ref=REFERENCE,
                    title="Supervisor",
                    summary="Durable recovery",
                    snippet="Pending waves recover after restart.",
                    score=0.75,
                    matched_by=("topic_fts", "detail_vector"),
                ),
            ),
        )

    async def get(self, request):
        if request.artifact.artifact_id == "missing":
            raise ArtifactNotFoundError(request.artifact)
        assert request.artifact == REFERENCE
        return PublishedTopicMemory(
            topic=TopicMemory(
                artifact_id="supervisor",
                revision=3,
                content=TopicMemoryContent(
                    title="Supervisor",
                    summary="Durable recovery",
                    detail="The database is authoritative.",
                ),
                lineage=ArtifactLineage(sources=(SourceRef(source_type="content", source_id="source-1"),)),
            ),
            published_at=datetime.now(UTC),
            is_current=True,
            current_artifact=REFERENCE,
        )

    async def flush(self):
        return TopicMemoryFlushResult(status="accepted")


def _client(application: object | None = None) -> TestClient:
    topic_memory = _TopicMemoryApplication() if application is None else application
    return TestClient(create_app(application=SimpleNamespace(topic_memory=topic_memory)))


def test_topic_memory_http_operations_return_public_progressive_disclosure_shapes() -> None:
    with _client() as client:
        search = client.post(
            "/v1/topic-memory/search",
            json={"scope_id": "scope-a", "query": "supervisor recovery", "limit": 4},
        )
        exact = client.post(
            "/v1/topic-memory/get",
            json={"scope_id": "scope-a", "artifact": REFERENCE.model_dump(mode="json")},
        )
        flush = client.post("/v1/topic-memory/flush", json={"scope_id": "scope-a"})

    assert search.status_code == 200
    assert search.json() == {
        "mode": "hybrid",
        "hits": [
            {
                "artifact": REFERENCE.model_dump(mode="json"),
                "title": "Supervisor",
                "summary": "Durable recovery",
                "snippet": "Pending waves recover after restart.",
                "score": 0.75,
                "matched_by": ["topic_fts", "detail_vector"],
            }
        ],
    }
    assert exact.status_code == 200
    assert exact.json() == {
        "artifact": REFERENCE.model_dump(mode="json"),
        "title": "Supervisor",
        "summary": "Durable recovery",
        "detail": "The database is authoritative.",
        "source_refs": [{"name": "content", "source_id": "source-1"}],
    }
    assert flush.status_code == 200
    assert flush.json() == {"status": "accepted"}


def test_topic_memory_search_rejects_caller_selected_mode() -> None:
    response = _client().post(
        "/v1/topic-memory/search",
        json={"scope_id": "scope-a", "query": "supervisor recovery", "mode": "fts"},
    )

    assert response.status_code == 422


def test_topic_memory_get_maps_missing_or_cross_scope_values_to_not_found() -> None:
    response = _client().post(
        "/v1/topic-memory/get",
        json={
            "scope_id": "scope-a",
            "artifact": {"family": "topic-memory", "artifact_id": "missing", "revision": 1},
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "artifact_not_found"


def test_topic_memory_flush_maps_known_missing_processing_capability_to_503() -> None:
    class _Unavailable(_TopicMemoryApplication):
        async def flush(self):
            raise TopicMemoryProcessingUnavailableError

    response = _client(_Unavailable()).post("/v1/topic-memory/flush", json={"scope_id": "scope-a"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "topic_memory_processing_unavailable"


def test_topic_memory_fallback_failure_redacts_production_provider_context(
    caplog,
) -> None:
    query = "supervisor recovery"
    provider_sentinel = "provider-secret-sentinel"
    fts_sentinel = "fts-error-sentinel"
    calls: list[str] = []
    active_exceptions: list[BaseException | None] = []
    fts_failure = RuntimeError(fts_sentinel)

    async def search(_scope: str, _query: str, **kwargs: Any) -> TopicMemorySearchResult:
        calls.append(kwargs["mode"])
        active_exceptions.append(sys.exception())
        raise fts_failure

    provider_error = ModelHTTPError(
        503,
        "failing-model",
        {"secret": provider_sentinel, "echo": query},
    )
    embedding = PydanticAIEmbeddingModel(
        embedder=Embedder(_FailingProviderEmbeddingModel(provider_error)),
        profile=EmbeddingProfile(profile_id="topic-v1", model="test", dimension=2),
    )
    runtime = BuiltinRuntime(
        provider=_UnusedProvider(),
        capabilities=RuntimeCapabilities(memory_extraction=False, memory_search_modes=()),
        topic_memory_search=search,
        topic_memory_embedding_model=embedding,
        scope_application=cast(Any, _RegisteredScopes()),
    )
    metrics = _RecordingMetrics()
    app = create_app(
        application=SimpleNamespace(topic_memory=runtime.topic_memory),
        metrics=cast(Any, metrics),
    )

    with caplog.at_level(logging.WARNING), TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/topic-memory/search",
            json={"scope_id": "scope-a", "query": query},
        )

    fallback = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "topic_memory.search.embedding_fallback"
    )
    server_failure = next(
        record for record in caplog.records if getattr(record, "event", None) == "application.operation.completed"
    )
    rendered = "\n".join(JsonFormatter().format(record) for record in caplog.records)

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "internal_error",
        "message": "The Server failed.",
        "details": None,
    }
    assert calls == ["fts"]
    assert active_exceptions == [None]
    assert fallback.error_code == "inference_unavailable"
    assert fallback.mode == "fts"
    assert fallback.exc_info is None
    assert server_failure.operation == "search_topic_memory"
    assert server_failure.error_code == "internal_error"
    assert server_failure.exc_info is None
    assert metrics.applications == [("search_topic_memory", "failure")]
    assert provider_sentinel not in rendered
    assert fts_sentinel not in rendered
    assert query not in rendered


def test_composed_fts_runtime_fails_closed_only_for_missing_topic_processing(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        scope_id = client.get("/v1/scopes/default").json()["scope_id"]
        capabilities = client.get("/v1/capabilities")
        search = client.post(
            "/v1/topic-memory/search",
            json={"scope_id": scope_id, "query": "supervisor recovery"},
        )
        missing = client.post(
            "/v1/topic-memory/get",
            json={"scope_id": scope_id, "artifact": REFERENCE.model_dump(mode="json")},
        )
        wrong_family = client.post(
            "/v1/topic-memory/get",
            json={
                "scope_id": scope_id,
                "artifact": {"family": "memory", "artifact_id": "memory", "revision": 1},
            },
        )
        flush = client.post("/v1/topic-memory/flush", json={"scope_id": scope_id})

    assert "topic-memory" in capabilities.json()["artifact_families"]
    assert search.status_code == 200
    assert search.json() == {"mode": "fts", "hits": []}
    assert missing.status_code == 404
    assert wrong_family.status_code == 422
    assert flush.status_code == 503


def test_prepared_context_focuses_65_term_topic_recall_without_losing_memory(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )
    query = " ".join(f"term{index:02d}" for index in range(65))

    with TestClient(app) as client:
        scope_id = client.get("/v1/scopes/default").json()["scope_id"]
        remembered = client.post(
            "/v1/memory/remember",
            json={"scope_id": scope_id, "kind": "fact", "text": query},
        )
        prepared = client.post(
            "/v1/context/prepare",
            json={"scope_id": scope_id, "query": query},
        )
        public_topic_search = client.post(
            "/v1/topic-memory/search",
            json={"scope_id": scope_id, "query": query},
        )

    assert remembered.status_code == 200
    assert prepared.status_code == 200
    assert prepared.json()["status"] == "ready"
    assert query in prepared.json()["content"]
    assert public_topic_search.status_code == 422
    assert public_topic_search.json()["error"]["code"] == "invalid_request"
