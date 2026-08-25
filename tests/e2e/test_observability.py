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

import asyncio
import json
import logging
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from pydantic_ai import Embedder
from pydantic_ai.embeddings import TestEmbeddingModel
from pydantic_ai.models import Model
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.models.test import TestModel
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import (
    EmbeddingProfile,
    MemoryCapabilities,
    MemoryEntryInput,
    MemoryProjection,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.inference.pydantic_ai import PydanticAIEmbeddingModel
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RememberMemoryRequest, open_builtin_runtime
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.errors import RevisionConflictError
from powercontext.server.factory import create_server_app
from powercontext.server.logging import OperationalContextFilter
from powercontext.server.settings import McpConfig, ServerSettings
from powercontext.server.tracing import ServerTracing

_STAGE_ATTRIBUTE_KEYS = {
    "scope.context": {
        "powercontext.operation.name",
        "powercontext.operation.unit",
        "powercontext.operation.outcome",
    },
    "scope.lock": {
        "powercontext.operation.name",
        "powercontext.operation.unit",
        "powercontext.operation.outcome",
        "powercontext.scope.lock.contended",
    },
    "memory.search": {
        "powercontext.operation.name",
        "powercontext.operation.unit",
        "powercontext.operation.outcome",
        "powercontext.memory.search.requested_mode",
        "powercontext.memory.search.limit",
        "powercontext.memory.search.memory_present",
        "powercontext.memory.search.mode",
        "powercontext.memory.search.result_count",
    },
    "memory.rerank": {
        "powercontext.operation.name",
        "powercontext.operation.unit",
        "powercontext.operation.outcome",
        "powercontext.memory.rerank.candidate_count",
        "powercontext.memory.rerank.limit",
        "powercontext.memory.rerank.selected_count",
        "powercontext.memory.rerank.discarded_rank_count",
        "powercontext.memory.rerank.used_fallback",
    },
    "experience.search": {
        "powercontext.operation.name",
        "powercontext.operation.unit",
        "powercontext.operation.outcome",
        "powercontext.experience.search.configured",
        "powercontext.experience.search.limit",
        "powercontext.experience.search.result_count",
    },
    "context.build": {
        "powercontext.operation.name",
        "powercontext.operation.unit",
        "powercontext.operation.outcome",
        "powercontext.context.build.memory_candidate_count",
        "powercontext.context.build.experience_candidate_count",
        "powercontext.context.build.selected_count",
        "powercontext.context.build.status",
        "powercontext.context.build.content_bytes",
    },
}

_VECTOR_PROFILE = EmbeddingProfile(
    profile_id="trace-test-v1",
    model="test",
    dimension=3,
    distance="l2",
    normalization="unit",
)


class _StageTeardownError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("stage teardown failed")


class _StageTeardown:
    def __init__(self, *, failing: bool) -> None:
        self._failing = failing

    def __enter__(self) -> _StageTeardown:
        return self

    def set_attributes(self, _attributes: object, /) -> None:
        pass

    def __exit__(self, *_: object) -> None:
        if self._failing:
            raise _StageTeardownError


class _ScopeLockTeardownFailingTracing:
    """Fail while closing `scope.lock`, the way a faulty injected tracing adapter would."""

    def stage(self, name: str, **_: object) -> _StageTeardown:
        return _StageTeardown(failing=name == "scope.lock")


class _VectorMemoryIndex:
    """Expose deterministic vector capability without a platform extension."""

    capabilities = MemoryCapabilities(
        fts=False,
        vector=True,
        embedding_profile=_VECTOR_PROFILE,
    )
    tables = ()

    async def initialize(self, _connection: AsyncConnection, /) -> None:
        pass

    async def replace(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _memory_ref: ArtifactRef,
        _projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        pass

    async def search(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        assert request.mode == "vector"
        assert request.query_vector is not None
        return MemorySearchChannels()

    async def vector_complete(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        return profile == _VECTOR_PROFILE

    async def hydrate(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]:
        return projections


def test_observability_signals_correlate_without_counting_the_mcp_bridge(caplog, tmp_path) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing = ServerTracing(provider)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
        ),
        tracing=tracing,
    )
    scope_id = "project:sensitive-evaluation"

    def create_http_client(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **_: object,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=True,
        )

    async def scenario() -> str:
        transport = StreamableHttpTransport(
            "http://testserver/mcp/",
            httpx_client_factory=create_http_client,
        )
        async with (
            app.router.lifespan_context(app),
            create_http_client() as http_client,
            Client(transport) as mcp_client,
        ):
            direct = await http_client.get("/v1/capabilities")
            assert direct.status_code == 200
            await mcp_client.call_tool("list_memory_entries", {"scope_id": scope_id})
            return (await http_client.get("/metrics")).text

    correlation_filter = OperationalContextFilter()
    caplog.handler.addFilter(correlation_filter)
    try:
        with caplog.at_level(logging.INFO):
            metrics = asyncio.run(scenario())
    finally:
        caplog.handler.removeFilter(correlation_filter)

    access_records = [
        record for record in caplog.records if getattr(record, "event", None) == "transport.request.completed"
    ]
    http_record = next(record for record in access_records if record.operation == "get_capabilities")
    mcp_record = next(record for record in access_records if record.operation == "mcp.tools.call")
    http_span = next(span for span in exporter.get_finished_spans() if span.name == "HTTP get_capabilities")
    assert http_record.request_id == format(http_span.context.span_id, "016x")
    assert http_record.trace_id
    assert mcp_record.trace_id

    assert (
        'powercontext_server_transport_requests_total{operation="mcp.tools.call",outcome="success",transport="mcp"} 1.0'
        in metrics
    )
    assert (
        'powercontext_server_application_operations_total{operation="list_memory_entries",outcome="success"} 1.0'
        in metrics
    )
    assert 'operation="list_memory_entries",outcome="success",transport="http"' not in metrics

    spans = exporter.get_finished_spans()
    mcp_span = next(span for span in spans if span.name == "MCP mcp.tools.call")
    application_span = next(span for span in spans if span.name == "powercontext list_memory_entries")
    assert mcp_record.request_id == format(mcp_span.context.span_id, "016x")
    assert application_span.parent is not None
    assert application_span.parent.span_id == mcp_span.context.span_id
    assert not any(span.name == "HTTP list_memory_entries" for span in spans)

    signal_payload = json.dumps(
        {
            "logs": [vars(record) for record in access_records],
            "metrics": metrics,
            "spans": [dict(span.attributes or {}) for span in spans],
        },
        default=str,
    )
    assert scope_id not in signal_payload


def test_database_failure_log_does_not_include_memory_content(caplog, tmp_path) -> None:
    database_path = tmp_path / "failure-log.db"
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
            mcp=McpConfig(enabled=False),
        )
    )
    memory_content = "PROBE-SENSITIVE-MEMORY-1318"

    with TestClient(app, raise_server_exceptions=False) as client:
        with sqlite3.connect(database_path) as connection:
            connection.executescript("""
                CREATE TRIGGER reject_memory_insert
                BEFORE INSERT ON pc_memory_entry_versions
                BEGIN
                    SELECT RAISE(ABORT, 'forced persistence failure');
                END;
            """)
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="powercontext.server.app"):
            response = client.post(
                "/v1/memory/remember",
                json={"scope_id": "project:failure-log", "kind": "fact", "text": memory_content},
            )

    records = [
        record for record in caplog.records if getattr(record, "event", None) == "application.operation.completed"
    ]
    assert response.status_code == 500
    assert len(records) == 1
    record = records[0]
    assert record.operation == "remember_memory"
    assert record.outcome == "failure"
    assert record.error_code == "internal_error"
    assert record.exc_info is not None

    formatter = logging.Formatter()
    rendered_records = tuple(formatter.format(record) for record in caplog.records)
    assert "forced persistence failure" in formatter.format(record)
    assert all(memory_content not in rendered for rendered in rendered_records)


def test_inference_spans_join_the_operation_trace_only_when_instrumented(monkeypatch, tmp_path) -> None:
    # Pydantic AI also resolves already-constructed models through `infer_model`, so pass those through.
    monkeypatch.setattr(
        "pydantic_ai.models.infer_model",
        lambda model: model if isinstance(model, Model) else TestModel(custom_output_text='{"candidates":[]}'),
    )

    instrumented = _flush_memory_spans(tmp_path / "instrumented.db", instrumented=True)
    uninstrumented = _flush_memory_spans(tmp_path / "uninstrumented.db", instrumented=False)

    transport = next(span for span in instrumented if span.name == "HTTP flush_memory")
    application = next(span for span in instrumented if span.name == "powercontext flush_memory")
    invoke_agent = next(span for span in instrumented if span.name == "invoke_agent memory_extraction")
    chat = next(span for span in instrumented if span.name.startswith("chat "))

    assert application.parent is not None
    assert application.parent.span_id == transport.context.span_id
    assert invoke_agent.parent is not None
    assert invoke_agent.parent.span_id == application.context.span_id
    assert chat.parent is not None
    assert chat.parent.span_id == invoke_agent.context.span_id
    assert {span.context.trace_id for span in (transport, application, invoke_agent, chat)} == {
        transport.context.trace_id
    }
    assert not any(_is_inference_span(span) for span in uninstrumented)


def test_memory_read_stage_spans_are_bounded_and_nested(monkeypatch, tmp_path) -> None:
    # Resolve the configured test model without consulting the environment or a real provider.
    monkeypatch.setattr(
        "pydantic_ai.models.infer_model",
        lambda model: model if isinstance(model, Model) else TestModel(custom_output_text='{"selected_ranks":[99,1]}'),
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory-read-tracing.db'}"),
            runtime=RuntimeConfig(memory_rerank_enabled=True),
            inference=InferenceConfig(generation_model="test"),
            mcp=McpConfig(enabled=False),
        ),
        tracing=ServerTracing(provider, instrumented=True),
    )
    scope_id = "project:private-trace-scope"
    memory_content = "Private trace sentinel evidence."
    query = "private trace sentinel"
    no_match_query = "unmatched giraffe phrase"

    with TestClient(app) as client:
        remembered = client.post(
            "/v1/memory/remember",
            json={"scope_id": scope_id, "kind": "fact", "text": memory_content},
        )
        searched = client.post(
            "/v1/memory/search",
            json={"scope_id": scope_id, "query": query, "limit": 1, "mode": "fts"},
        )
        no_match = client.post(
            "/v1/memory/search",
            json={"scope_id": scope_id, "query": no_match_query, "limit": 1, "mode": "fts"},
        )
        no_memory = client.post(
            "/v1/memory/search",
            json={
                "scope_id": "project:private-empty-search-scope",
                "query": query,
                "limit": 1,
                "mode": "fts",
            },
        )
        prepared = client.post(
            "/v1/context/prepare",
            json={"scope_id": scope_id, "query": query},
        )
        empty = client.post(
            "/v1/context/prepare",
            json={"scope_id": "project:private-empty-scope", "query": query},
        )

    assert remembered.status_code == 200
    assert searched.status_code == 200
    assert searched.json()["hits"]
    assert no_match.status_code == 200
    assert no_match.json()["hits"] == []
    assert no_memory.status_code == 200
    assert no_memory.json()["hits"] == []
    assert prepared.status_code == 200
    assert prepared.json()["status"] == "ready"
    assert empty.status_code == 200
    assert empty.json()["status"] == "empty"

    spans = list(exporter.get_finished_spans())
    search_applications = [span for span in spans if span.name == "powercontext search_memory"]
    prepare_applications = [span for span in spans if span.name == "powercontext prepare_context"]
    assert len(search_applications) == 3
    assert len(prepare_applications) == 2

    search_applications_by_result = {
        (
            bool(
                (_only_child(spans, application, "memory.search").attributes or {}).get(
                    "powercontext.memory.search.memory_present"
                )
            ),
            (_only_child(spans, application, "memory.search").attributes or {})[
                "powercontext.memory.search.result_count"
            ],
        ): application
        for application in search_applications
    }
    search_application = search_applications_by_result[(True, 1)]
    assert dict(_only_child(spans, search_application, "scope.context").attributes or {}) == {
        "powercontext.operation.name": "scope.context",
        "powercontext.operation.unit": "stage",
        "powercontext.operation.outcome": "success",
    }
    # Read-only searches never serialize on the scope write lock, so they emit no wait span.
    assert not _children(spans, search_application, "scope.lock")
    search = _only_child(spans, search_application, "memory.search")
    search_attributes = dict(search.attributes or {})
    assert search_attributes == {
        "powercontext.operation.name": "memory.search",
        "powercontext.operation.unit": "stage",
        "powercontext.memory.search.requested_mode": "fts",
        "powercontext.memory.search.limit": 1,
        "powercontext.memory.search.memory_present": True,
        "powercontext.memory.search.mode": "fts",
        "powercontext.memory.search.result_count": 1,
        "powercontext.operation.outcome": "success",
    }
    rerank = _only_child(spans, search, "memory.rerank")
    assert dict(rerank.attributes or {}) == {
        "powercontext.operation.name": "memory.rerank",
        "powercontext.operation.unit": "stage",
        "powercontext.memory.rerank.candidate_count": 1,
        "powercontext.memory.rerank.limit": 1,
        "powercontext.memory.rerank.selected_count": 1,
        "powercontext.memory.rerank.discarded_rank_count": 1,
        "powercontext.memory.rerank.used_fallback": False,
        "powercontext.operation.outcome": "success",
    }
    invoke_agent = _only_child(spans, rerank, "invoke_agent memory_rerank")
    chat = _only_child_with_prefix(spans, invoke_agent, "chat ")
    assert {span.context.trace_id for span in (search_application, search, rerank, invoke_agent, chat)} == {
        search_application.context.trace_id
    }

    no_match_application = search_applications_by_result[(True, 0)]
    no_match_search = _only_child(spans, no_match_application, "memory.search")
    assert (no_match_search.attributes or {})["powercontext.memory.search.mode"] == "fts"
    assert not _children(spans, no_match_search, "memory.rerank")
    no_memory_application = search_applications_by_result[(False, 0)]
    no_memory_search = _only_child(spans, no_memory_application, "memory.search")
    assert "powercontext.memory.search.mode" not in (no_memory_search.attributes or {})
    assert not _children(spans, no_memory_search, "memory.rerank")

    prepared_by_memory_presence = {
        bool(
            (_only_child(spans, application, "memory.search").attributes or {}).get(
                "powercontext.memory.search.memory_present"
            )
        ): application
        for application in prepare_applications
    }
    ready_application = prepared_by_memory_presence[True]
    empty_application = prepared_by_memory_presence[False]

    # Both setup spans stay siblings of the recall stages: neither one covers the operation body.
    for application in (ready_application, empty_application):
        assert dict(_only_child(spans, application, "scope.context").attributes or {}) == {
            "powercontext.operation.name": "scope.context",
            "powercontext.operation.unit": "stage",
            "powercontext.operation.outcome": "success",
        }
        assert dict(_only_child(spans, application, "scope.lock").attributes or {}) == {
            "powercontext.operation.name": "scope.lock",
            "powercontext.operation.unit": "stage",
            "powercontext.scope.lock.contended": False,
            "powercontext.operation.outcome": "success",
        }

    ready_memory = _only_child(spans, ready_application, "memory.search")
    assert (ready_memory.attributes or {})["powercontext.memory.search.result_count"] == 1
    assert _only_child(spans, ready_memory, "memory.rerank")
    ready_experience = _only_child(spans, ready_application, "experience.search")
    assert (ready_experience.attributes or {})["powercontext.experience.search.configured"] is True
    assert (ready_experience.attributes or {})["powercontext.experience.search.result_count"] == 0
    ready_context = _only_child(spans, ready_application, "context.build")
    assert (ready_context.attributes or {})["powercontext.context.build.memory_candidate_count"] == 1
    assert (ready_context.attributes or {})["powercontext.context.build.experience_candidate_count"] == 0
    assert (ready_context.attributes or {})["powercontext.context.build.selected_count"] == 1
    assert (ready_context.attributes or {})["powercontext.context.build.status"] == "ready"
    ready_content_bytes = (ready_context.attributes or {})["powercontext.context.build.content_bytes"]
    assert isinstance(ready_content_bytes, int)
    assert ready_content_bytes > 0

    empty_memory = _only_child(spans, empty_application, "memory.search")
    empty_memory_attributes = dict(empty_memory.attributes or {})
    assert empty_memory_attributes["powercontext.memory.search.memory_present"] is False
    assert empty_memory_attributes["powercontext.memory.search.result_count"] == 0
    assert "powercontext.memory.search.mode" not in empty_memory_attributes
    assert not _children(spans, empty_memory, "memory.rerank")
    empty_experience = _only_child(spans, empty_application, "experience.search")
    assert (empty_experience.attributes or {})["powercontext.experience.search.result_count"] == 0
    empty_context = _only_child(spans, empty_application, "context.build")
    assert (empty_context.attributes or {})["powercontext.context.build.selected_count"] == 0
    assert (empty_context.attributes or {})["powercontext.context.build.status"] == "empty"
    assert (empty_context.attributes or {})["powercontext.context.build.content_bytes"] == 0

    for span in spans:
        allowed_keys = _STAGE_ATTRIBUTE_KEYS.get(span.name)
        if allowed_keys is None:
            continue
        attributes = dict(span.attributes or {})
        assert attributes.keys() <= allowed_keys
        assert all(isinstance(value, str | bool | int | float) for value in attributes.values())

    exported = _exported_span_data(spans)
    assert scope_id not in exported
    assert "project:private-empty-scope" not in exported
    assert "project:private-empty-search-scope" not in exported
    assert memory_content not in exported
    assert query not in exported
    assert no_match_query not in exported


def test_scope_lock_stage_span_reports_contention_and_closes_at_acquisition(tmp_path) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_ON, shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    scope_id = "project:private-lock-scope"
    memory_content = "Private lock sentinel evidence."

    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'scope-lock.db'}")),
            tracing=ServerTracing(provider),
        ) as runtime:
            memory = runtime.memory.for_scope(scope_id)
            first = await memory.remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text=memory_content),))
            )

            # Holding the scope lock outside the Runtime makes the next write observe real contention.
            lock = runtime._locks[scope_id]
            await lock.acquire()
            contending = asyncio.create_task(
                memory.remember(RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Second fact."),)))
            )
            await asyncio.sleep(0.05)
            assert not contending.done()
            lock.release()
            await contending

            # A failure inside the critical section must still release the lock for later writes.
            with pytest.raises(RevisionConflictError):
                await memory.remember(
                    RememberMemoryRequest(
                        entries=(MemoryEntryInput(kind="fact", text="Conflicting fact."),),
                        expected_revision=first.memory_ref.revision,
                    )
                )
            assert not lock.locked()
            await memory.remember(RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Third fact."),)))

    asyncio.run(scenario())

    spans = list(exporter.get_finished_spans())
    assert [
        (span.attributes or {})["powercontext.scope.lock.contended"] for span in spans if span.name == "scope.lock"
    ] == [False, True, False, False]
    # Every wait span succeeds, including the conflicting write's: the span closes before the critical section runs.
    for span in spans:
        assert (span.attributes or {}).get("powercontext.operation.outcome") == "success"
        allowed_keys = _STAGE_ATTRIBUTE_KEYS.get(span.name)
        assert allowed_keys is None or (span.attributes or {}).keys() <= allowed_keys
    exported = _exported_span_data(spans)
    assert scope_id not in exported
    assert memory_content not in exported


def test_scope_lock_is_released_when_stage_teardown_fails(tmp_path) -> None:
    scope_id = "project:private-broken-tracing"

    async def scenario() -> bool:
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'broken-tracing.db'}")),
            tracing=_ScopeLockTeardownFailingTracing(),
        ) as runtime:
            with pytest.raises(_StageTeardownError):
                await runtime.memory.for_scope(scope_id).remember(
                    RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Guarded fact."),))
                )
            return runtime._locks[scope_id].locked()

    assert asyncio.run(scenario()) is False


def test_vector_search_exports_embedding_under_memory_search_without_recording_text(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "pydantic_ai.embeddings.infer_embedding_model",
        lambda _model, **_kwargs: TestEmbeddingModel(dimensions=3),
    )
    monkeypatch.setattr(
        "powercontext.builtin.runtime.composition.SQLiteMemoryVectorIndex",
        lambda _profile: _VectorMemoryIndex(),
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'vector-tracing.db'}"),
            inference=InferenceConfig(
                embedding_model="test",
                embedding_profile_id=_VECTOR_PROFILE.profile_id,
                embedding_dimension=_VECTOR_PROFILE.dimension,
            ),
            mcp=McpConfig(enabled=False),
        ),
        tracing=ServerTracing(provider, instrumented=True),
    )
    scope_id = "project:private-vector-scope"
    memory_content = "Private vector memory sentinel."
    private_text = "private embedding sentinel"

    with TestClient(app) as client:
        remembered = client.post(
            "/v1/memory/remember",
            json={"scope_id": scope_id, "kind": "fact", "text": memory_content},
        )
        searched = client.post(
            "/v1/memory/search",
            json={"scope_id": scope_id, "query": private_text, "limit": 1, "mode": "vector"},
        )

    assert remembered.status_code == 200
    assert searched.status_code == 200
    assert searched.json()["mode"] == "vector"
    assert searched.json()["hits"] == []

    spans = list(exporter.get_finished_spans())
    applications = [span for span in spans if span.name == "powercontext search_memory"]
    assert len(applications) == 1
    application = applications[0]
    search = _only_child(spans, application, "memory.search")
    embedding = _only_child_with_prefix(spans, search, "embeddings ")
    assert dict(search.attributes or {}) == {
        "powercontext.operation.name": "memory.search",
        "powercontext.operation.unit": "stage",
        "powercontext.memory.search.requested_mode": "vector",
        "powercontext.memory.search.limit": 1,
        "powercontext.memory.search.memory_present": True,
        "powercontext.memory.search.mode": "vector",
        "powercontext.memory.search.result_count": 0,
        "powercontext.operation.outcome": "success",
    }
    assert embedding.name == "embeddings test"
    assert embedding.context.trace_id == application.context.trace_id
    assert not any(_is_inference_span(span) and span.parent is None for span in spans)
    exported = _exported_span_data(spans)
    assert scope_id not in exported
    assert memory_content not in exported
    assert private_text not in exported


def test_injected_always_on_embedding_skips_readiness_but_traces_vector_search(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "powercontext.builtin.runtime.composition.SQLiteMemoryFTSIndex",
        _VectorMemoryIndex,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_ON, shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing = ServerTracing(provider, instrumented=True)
    embedding_model = PydanticAIEmbeddingModel(
        embedder=Embedder(
            TestEmbeddingModel(dimensions=3),
            instrument=InstrumentationSettings(tracer_provider=provider),
        ),
        profile=_VECTOR_PROFILE,
    )

    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'readiness-tracing.db'}"),
            mcp=McpConfig(enabled=False),
        ),
        embedding_model=embedding_model,
        tracing=tracing,
    )
    scope_id = "project:injected-always-on"

    with TestClient(app) as client:
        readiness = client.get("/health/ready")
        readiness_spans = list(exporter.get_finished_spans())
        assert not [span for span in readiness_spans if span.parent is None]
        assert not any(_is_inference_span(span) for span in readiness_spans)

        remembered = client.post(
            "/v1/memory/remember",
            json={"scope_id": scope_id, "kind": "fact", "text": "Private injected vector memory."},
        )
        exporter.clear()
        searched = client.post(
            "/v1/memory/search",
            json={"scope_id": scope_id, "query": "private injected query", "limit": 1, "mode": "vector"},
        )

    assert readiness.status_code == 200
    assert readiness.json()["checks"]["inference.embedding"] == "ready"
    assert remembered.status_code == 200
    assert searched.status_code == 200
    assert searched.json()["mode"] == "vector"
    spans = list(exporter.get_finished_spans())
    application = next(span for span in spans if span.name == "powercontext search_memory")
    search = _only_child(spans, application, "memory.search")
    embedding = _only_child_with_prefix(spans, search, "embeddings ")
    assert embedding.name == "embeddings test"
    assert [span for span in spans if _is_inference_span(span)] == [embedding]


def _flush_memory_spans(database_path: Path, *, instrumented: bool) -> list[ReadableSpan]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
            inference=InferenceConfig(generation_model="test"),
            mcp=McpConfig(enabled=False),
        ),
        tracing=ServerTracing(provider, instrumented=instrumented),
    )
    scope_id = "project:inference-tracing"

    with TestClient(app) as client:
        captured = client.post(
            "/v1/sources/content",
            json={"scope_id": scope_id, "source_id": "task-1", "content": "bounded evidence"},
        )
        assert captured.status_code == 202
        flushed = client.post("/v1/memory/flush", json={"scope_id": scope_id})
        assert flushed.status_code == 200

    return list(exporter.get_finished_spans())


def _is_inference_span(span: ReadableSpan) -> bool:
    return span.instrumentation_scope is not None and span.instrumentation_scope.name == "pydantic-ai"


def _children(spans: list[ReadableSpan], parent: ReadableSpan, name: str) -> list[ReadableSpan]:
    return [
        span
        for span in spans
        if span.name == name and span.parent is not None and span.parent.span_id == parent.context.span_id
    ]


def _only_child(spans: list[ReadableSpan], parent: ReadableSpan, name: str) -> ReadableSpan:
    children = _children(spans, parent, name)
    assert len(children) == 1
    return children[0]


def _only_child_with_prefix(spans: list[ReadableSpan], parent: ReadableSpan, prefix: str) -> ReadableSpan:
    children = [
        span
        for span in spans
        if span.name.startswith(prefix) and span.parent is not None and span.parent.span_id == parent.context.span_id
    ]
    assert len(children) == 1
    return children[0]


def _exported_span_data(spans: list[ReadableSpan]) -> str:
    return json.dumps(
        [
            {
                "name": span.name,
                "attributes": dict(span.attributes or {}),
                "events": [{"name": event.name, "attributes": dict(event.attributes or {})} for event in span.events],
                "status": {
                    "code": str(span.status.status_code),
                    "description": span.status.description,
                },
            }
            for span in spans
        ],
        default=str,
    )
