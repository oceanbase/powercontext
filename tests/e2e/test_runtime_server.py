from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from powercontext.api import (
    CaptureContentSourceRequest,
    FlushMemoryRequest,
    GetMemoryEntryRequest,
    ListMemoryChangesRequest,
    ListMemoryEntriesRequest,
    RememberMemoryRequest,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    SearchMemoryRequest,
)
from powercontext.api import MemorySearchMode as ApiMemorySearchMode
from powercontext.builtin.artifacts.memory import (
    EmbeddingProfile,
    MemoryCandidateRequest,
    MemoryEntryInput,
)
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.builtin.sources import ContentSource
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

OCEANBASE_URL = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
EMBEDDING_PROFILE = EmbeddingProfile(
    profile_id="database-e2e-v1",
    model="database-e2e",
    dimension=3,
    distance="l2",
    normalization="none",
)


class ContentCandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="decision",
                text=source.content,
                sources=(source,),
                reason="captured",
            )
            for source in request.sources
            if isinstance(source, ContentSource)
        )


class KeywordEmbeddingModel:
    profile = EMBEDDING_PROFILE

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=tuple((1.0, 0.0, 0.0) if "alpha" in text.casefold() else (0.0, 1.0, 0.0) for text in texts)
        )


def _server_settings(
    database: Path,
    *,
    generation_model: str | None = None,
    mcp: bool = False,
) -> ServerSettings:
    return ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        inference=InferenceConfig(generation_model=generation_model),
        mcp=McpConfig(enabled=mcp),
    )


@pytest.mark.parametrize("database_kind", ["sqlite", "oceanbase"])
def test_server_databases_share_source_to_memory_search_behavior(
    database_kind: str,
    tmp_path: Path,
) -> None:
    if database_kind == "oceanbase":
        if OCEANBASE_URL is None:
            pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL to a dedicated OceanBase MySQL-mode test database")
        database = OceanBaseConfig(url=SecretStr(OCEANBASE_URL))
    else:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    scope_id = f"database-e2e-{uuid4()}"
    app = create_server_app(
        settings=ServerSettings(
            database=database,
            mcp=McpConfig(enabled=False),
        ),
        candidate_pipeline=ContentCandidatePipeline(),
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport)
            readiness = await client.get_readiness()
            capabilities = await client.get_capabilities()
            captured = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="turn-1",
                    content="Keep the OpenAPI contract authoritative.",
                    metadata={"channel": "e2e"},
                )
            )
            flushed = await client.flush_memory(FlushMemoryRequest(scope_id=scope_id))
            found = await client.search_memory(SearchMemoryRequest(scope_id=scope_id, query="OpenAPI authoritative"))
            entries = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope_id))

        assert readiness.checks == {"runtime": "ready"}
        assert capabilities.source_types == ["content"]
        assert capabilities.memory_extraction is True
        assert capabilities.search_modes == ["auto", "fts"]
        assert captured.position == 1
        assert flushed.current_cursor == captured.position
        assert flushed.memory is not None
        assert found.mode == "fts"
        assert [hit.text for hit in found.hits] == ["Keep the OpenAPI contract authoritative."]
        assert entries.memory == flushed.memory
        assert entries.entries[0].source_refs[0].source_id == "turn-1"

    asyncio.run(scenario())


@pytest.mark.parametrize("database_kind", ["sqlite", "oceanbase"])
def test_server_databases_share_vector_and_hybrid_search_behavior(
    database_kind: str,
    tmp_path: Path,
) -> None:
    if database_kind == "oceanbase":
        if OCEANBASE_URL is None:
            pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL to a dedicated OceanBase MySQL-mode test database")
        database = OceanBaseConfig(url=SecretStr(OCEANBASE_URL))
    else:
        configured = os.environ.get("POWERCONTEXT_VEC1_EXTENSION")
        if configured is None or not Path(configured).is_file():
            pytest.skip("set POWERCONTEXT_VEC1_EXTENSION to a Vec1 extension file")
        database = SQLiteConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'vector-runtime.db'}",
            vec1_extension=Path(configured),
        )
    scope_id = f"vector-e2e-{uuid4()}"
    app = create_server_app(
        settings=ServerSettings(
            database=database,
            mcp=McpConfig(enabled=False),
        ),
        candidate_pipeline=ContentCandidatePipeline(),
        embedding_model=KeywordEmbeddingModel(),
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport)
            capabilities = await client.get_capabilities()
            await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="alpha-source",
                    content="Alpha semantic record.",
                )
            )
            flushed = await client.flush_memory(FlushMemoryRequest(scope_id=scope_id))
            vector = await client.search_memory(
                SearchMemoryRequest(
                    scope_id=scope_id,
                    query="alpha",
                    mode=ApiMemorySearchMode.VECTOR,
                )
            )
            hybrid = await client.search_memory(
                SearchMemoryRequest(
                    scope_id=scope_id,
                    query="alpha",
                    mode=ApiMemorySearchMode.HYBRID,
                )
            )

        assert flushed.memory is not None
        assert capabilities.search_modes == ["auto", "fts", "vector", "hybrid"]
        assert [hit.text for hit in vector.hits] == ["Alpha semantic record."]
        assert vector.hits[0].matched_by == ["vector"]
        assert [hit.text for hit in hybrid.hits] == ["Alpha semantic record."]
        assert hybrid.hits[0].matched_by == ["fts", "vector"]

    asyncio.run(scenario())


def test_sdk_memory_lifecycle_reaches_one_composed_runtime(tmp_path: Path) -> None:
    app = create_server_app(settings=_server_settings(tmp_path / "runtime.db"))

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport)
            remembered = await client.remember_memory(
                RememberMemoryRequest(
                    scope_id="project:powercontext",
                    kind="decision",
                    text="Use strict transport models.",
                )
            )
            assert remembered.entry is not None
            exact = await client.get_memory_entry(
                GetMemoryEntryRequest(
                    scope_id="project:powercontext",
                    citation=remembered.entry.citation,
                )
            )
            revised = await client.revise_memory_entry(
                ReviseMemoryEntryRequest(
                    scope_id="project:powercontext",
                    citation=remembered.entry.citation,
                    kind="decision",
                    text="Keep strict Pydantic transport models.",
                )
            )
            assert revised.entry is not None
            changes = await client.list_memory_changes(
                ListMemoryChangesRequest(
                    scope_id="project:powercontext",
                    since_revision=remembered.memory.revision,
                )
            )
            retired = await client.retire_memory_entry(
                RetireMemoryEntryRequest(
                    scope_id="project:powercontext",
                    citation=revised.entry.citation,
                    reason="superseded",
                )
            )
            assert retired.entry is not None
            current = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id="project:powercontext"))
            with pytest.raises(ServerResponseError) as inactive:
                await client.revise_memory_entry(
                    ReviseMemoryEntryRequest(
                        scope_id="project:powercontext",
                        citation=retired.entry.citation,
                        kind="decision",
                        text="Inactive entries cannot be revised.",
                    )
                )
            with pytest.raises(ServerResponseError) as missing:
                await client.get_memory_entry(
                    GetMemoryEntryRequest(
                        scope_id="project:powercontext",
                        citation=retired.entry.citation.model_copy(update={"entry_id": "missing-entry"}),
                    )
                )

        assert exact.text == "Use strict transport models."
        assert revised.entry.text == "Keep strict Pydantic transport models."
        assert [revision.memory_ref.revision for revision in changes.revisions] == [revised.memory.revision]
        assert retired.entry.state == "inactive"
        assert current.entries == [retired.entry]
        assert (inactive.value.status_code, inactive.value.code) == (409, "memory_entry_inactive")
        assert (missing.value.status_code, missing.value.code) == (404, "memory_not_found")

    asyncio.run(scenario())


def test_runtime_conflicts_keep_http_and_sdk_error_context(tmp_path: Path) -> None:
    app = create_server_app(settings=_server_settings(tmp_path / "runtime.db"))

    with TestClient(app) as transport:
        first = transport.post(
            "/v1/sources/content",
            json={"scope_id": "project", "source_id": "turn-1", "content": "first"},
        )
        conflict = transport.post(
            "/v1/sources/content",
            headers={"X-Request-ID": "request-123"},
            json={"scope_id": "project", "source_id": "turn-1", "content": "changed"},
        )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.headers["X-Request-ID"] == "request-123"
    assert conflict.json()["error"]["code"] == "source_conflict"

    async def stale_revision() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport)
            remembered = await client.remember_memory(
                RememberMemoryRequest(scope_id="project", kind="decision", text="first")
            )
            with pytest.raises(ServerResponseError) as caught:
                await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id="project",
                        kind="decision",
                        text="stale",
                        expected_revision=remembered.memory.revision + 1,
                    )
                )
        assert caught.value.status_code == 409
        assert caught.value.code == "revision_conflict"

    asyncio.run(stale_revision())


def test_runtime_server_rejects_non_strict_transport_values(tmp_path: Path) -> None:
    app = create_server_app(settings=_server_settings(tmp_path / "runtime.db"))

    with TestClient(app) as transport:
        responses = [
            transport.post(
                "/v1/memory/search",
                json={"scope_id": "project", "query": "query", "limit": True},
            ),
            transport.post(
                "/v1/memory/search",
                json={"scope_id": " ", "query": "query"},
            ),
            transport.post(
                "/v1/memory/remember",
                json={"scope_id": "project", "kind": "decision", "text": "🧠" * 3_000},
            ),
        ]

    assert [response.status_code for response in responses] == [422, 422, 422]
    assert {response.json()["error"]["code"] for response in responses} == {"invalid_request"}
