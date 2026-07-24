from pathlib import Path
from types import TracebackType
from typing import Self, cast

import apsw
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

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
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.errors import MemoryBackendConfigurationError
from powercontext.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.server.runtime import create_runtime_app
from powercontext.server.settings import (
    InferenceSettings,
    ServerSettings,
    SQLiteStorageSettings,
)
from powercontext.sources import ContentSource


class ContentCandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        source = cast(ContentSource, request.sources[0])
        return (
            MemoryEntryInput(
                kind="decision",
                text=source.content,
                sources=(source,),
                reason="captured",
            ),
        )


class LifecycleTestModel(TestModel):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    async def __aenter__(self) -> Self:
        self.events.append("enter")
        return await super().__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        try:
            return await super().__aexit__(exc_type, exc_val, exc_tb)
        finally:
            self.events.append("exit")


def _server_settings(
    database: Path,
    *,
    generation_model: str | None = None,
) -> ServerSettings:
    return ServerSettings(
        storage=SQLiteStorageSettings(path=database),
        inference=InferenceSettings(generation_model=generation_model),
    )


def test_server_owns_configured_generation_model_for_its_lifespan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    model = LifecycleTestModel(events)
    monkeypatch.setattr("pydantic_ai.models.infer_model", lambda _: model)
    app = create_runtime_app(
        settings=_server_settings(tmp_path / "runtime.db", generation_model="test"),
    )

    with TestClient(app) as transport:
        assert transport.get("/health/ready").status_code == 200
        assert events == ["enter"]

    assert events == ["enter", "exit"]


def test_sdk_reaches_the_runtime_source_to_memory_chain(tmp_path: Path) -> None:
    app = create_runtime_app(
        settings=_server_settings(tmp_path / "runtime.db"),
        candidate_pipeline=ContentCandidatePipeline(),
    )

    with TestClient(app) as transport:
        client = PowerContextClient("http://testserver", http_client=transport)

        readiness = client.get_readiness()
        capabilities = client.get_capabilities()
        captured = client.capture_content_source(
            CaptureContentSourceRequest(
                scope_id="project:powercontext",
                source_id="turn-1",
                content="Keep the OpenAPI contract authoritative.",
            )
        )
        flushed = client.flush_memory(FlushMemoryRequest(scope_id="project:powercontext"))
        found = client.search_memory(
            SearchMemoryRequest(scope_id="project:powercontext", query="OpenAPI authoritative")
        )
        entries = client.list_memory_entries(ListMemoryEntriesRequest(scope_id="project:powercontext"))

    assert readiness.checks == {"runtime": "ready"}
    assert capabilities.source_types == ["content"]
    assert capabilities.memory_extraction is True
    assert captured.position == 1
    assert flushed.current_cursor == captured.position
    assert flushed.memory is not None
    assert [hit.text for hit in found.hits] == ["Keep the OpenAPI contract authoritative."]
    assert entries.memory == flushed.memory
    assert entries.entries[0].source_refs[0].name == "content"
    assert entries.entries[0].source_refs[0].source_id == "turn-1"


def test_runtime_domain_errors_keep_their_status_and_request_id(tmp_path: Path) -> None:
    app = create_runtime_app(
        settings=_server_settings(tmp_path / "runtime.db"),
        candidate_pipeline=ContentCandidatePipeline(),
    )

    with TestClient(app) as transport:
        first = transport.post(
            "/v1/sources/content",
            json={
                "scope_id": "project:powercontext",
                "source_id": "turn-1",
                "content": "first",
            },
        )
        conflict = transport.post(
            "/v1/sources/content",
            headers={"X-Request-ID": "request-123"},
            json={
                "scope_id": "project:powercontext",
                "source_id": "turn-1",
                "content": "changed",
            },
        )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.headers["X-Request-ID"] == "request-123"
    assert conflict.json()["error"]["code"] == "source_conflict"


def test_runtime_revision_conflicts_reach_the_sdk(tmp_path: Path) -> None:
    app = create_runtime_app(
        settings=_server_settings(tmp_path / "runtime.db"),
    )

    with TestClient(app) as transport:
        client = PowerContextClient("http://testserver", http_client=transport)
        first = client.remember_memory(
            RememberMemoryRequest(
                scope_id="project:powercontext",
                kind="decision",
                text="Use one Runtime composition.",
            )
        )

        with pytest.raises(ServerResponseError) as caught:
            client.remember_memory(
                RememberMemoryRequest(
                    scope_id="project:powercontext",
                    kind="decision",
                    text="Use a stale Revision.",
                    expected_revision=first.memory.revision + 1,
                )
            )

    assert caught.value.status_code == 409
    assert caught.value.code == "revision_conflict"
    assert caught.value.request_id is not None


def test_sdk_memory_lifecycle_reaches_one_runtime(tmp_path: Path) -> None:
    app = create_runtime_app(
        settings=_server_settings(tmp_path / "runtime.db"),
    )

    with TestClient(app) as transport:
        client = PowerContextClient("http://testserver", http_client=transport)
        remembered = client.remember_memory(
            RememberMemoryRequest(
                scope_id="project:powercontext",
                kind="decision",
                text="Use strict transport models.",
            )
        )
        assert remembered.entry is not None
        entry = remembered.entry

        exact = client.get_memory_entry(
            GetMemoryEntryRequest(
                scope_id="project:powercontext",
                citation=entry.citation,
            )
        )
        revised = client.revise_memory_entry(
            ReviseMemoryEntryRequest(
                scope_id="project:powercontext",
                citation=entry.citation,
                kind="decision",
                text="Keep strict Pydantic transport models.",
            )
        )
        assert revised.entry is not None
        revised_entry = revised.entry

        changes = client.list_memory_changes(
            ListMemoryChangesRequest(
                scope_id="project:powercontext",
                since_revision=remembered.memory.revision,
            )
        )
        full_changes = client.list_memory_changes(
            ListMemoryChangesRequest(
                scope_id="project:powercontext",
                since_revision=0,
            )
        )
        retired = client.retire_memory_entry(
            RetireMemoryEntryRequest(
                scope_id="project:powercontext",
                citation=revised_entry.citation,
                reason="superseded",
            )
        )
        current = client.list_memory_entries(ListMemoryEntriesRequest(scope_id="project:powercontext"))
        assert retired.entry is not None
        with pytest.raises(ServerResponseError) as inactive:
            client.revise_memory_entry(
                ReviseMemoryEntryRequest(
                    scope_id="project:powercontext",
                    citation=retired.entry.citation,
                    kind="decision",
                    text="An inactive entry cannot be revised.",
                )
            )

    assert exact.text == "Use strict transport models."
    assert revised_entry.text == "Keep strict Pydantic transport models."
    assert changes.memory == revised.memory
    assert [revision.memory_ref.revision for revision in changes.revisions] == [revised.memory.revision]
    assert [revision.memory_ref.revision for revision in full_changes.revisions] == [
        remembered.memory.revision,
        revised.memory.revision,
    ]
    assert retired.entry.state == "inactive"
    assert current.memory == retired.memory
    assert current.entries == [retired.entry]
    assert inactive.value.status_code == 409
    assert inactive.value.code == "memory_entry_inactive"


def test_invalid_runtime_state_and_missing_citation_are_not_internal_errors(tmp_path: Path) -> None:
    app = create_runtime_app(
        settings=_server_settings(tmp_path / "runtime.db"),
    )

    with TestClient(app) as transport:
        remembered = transport.post(
            "/v1/memory/remember",
            json={
                "scope_id": "project:powercontext",
                "kind": "decision",
                "text": "Preserve domain error semantics.",
            },
        ).json()
        future_changes = transport.post(
            "/v1/memory/changes",
            headers={"X-Request-ID": "future-revision"},
            json={
                "scope_id": "project:powercontext",
                "since_revision": remembered["memory"]["revision"] + 1,
            },
        )
        missing_entry = transport.post(
            "/v1/memory/entries/get",
            headers={"X-Request-ID": "missing-entry"},
            json={
                "scope_id": "project:powercontext",
                "citation": {
                    **remembered["entry"]["citation"],
                    "entry_id": "00000000-0000-0000-0000-000000000000",
                },
            },
        )
        wrong_memory_revise = transport.post(
            "/v1/memory/entries/revise",
            json={
                "scope_id": "project:powercontext",
                "citation": {
                    **remembered["entry"]["citation"],
                    "memory_ref": {
                        "artifact_id": "memory-other",
                        "revision": remembered["memory"]["revision"],
                    },
                },
                "kind": "decision",
                "text": "Do not confuse identity with revision.",
            },
        )
        wrong_memory_retire = transport.post(
            "/v1/memory/entries/retire",
            json={
                "scope_id": "project:powercontext",
                "citation": {
                    **remembered["entry"]["citation"],
                    "memory_ref": {
                        "artifact_id": "memory-other",
                        "revision": remembered["memory"]["revision"],
                    },
                },
            },
        )

    assert future_changes.status_code == 422
    assert future_changes.headers["X-Request-ID"] == "future-revision"
    assert future_changes.json()["error"]["code"] == "invalid_request"
    assert missing_entry.status_code == 404
    assert missing_entry.headers["X-Request-ID"] == "missing-entry"
    assert missing_entry.json()["error"]["code"] == "memory_not_found"
    assert [wrong_memory_revise.status_code, wrong_memory_retire.status_code] == [404, 404]
    assert {
        wrong_memory_revise.json()["error"]["code"],
        wrong_memory_retire.json()["error"]["code"],
    } == {"memory_not_found"}


def test_runtime_startup_rejects_an_unusable_memory_schema(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    connection = apsw.Connection(str(database))
    connection.execute("CREATE TABLE powercontext_schema (version INTEGER NOT NULL PRIMARY KEY)")
    connection.execute("INSERT INTO powercontext_schema (version) VALUES (2)")
    connection.close()
    app = create_runtime_app(settings=_server_settings(database))

    with pytest.raises(MemoryBackendConfigurationError, match="schema version"), TestClient(app):
        pass


def test_runtime_without_generation_keeps_capture_durable_and_reports_flush_capability(tmp_path: Path) -> None:
    app = create_runtime_app(
        settings=_server_settings(tmp_path / "runtime.db"),
    )

    with TestClient(app) as transport:
        capabilities = transport.get("/v1/capabilities")
        captured = transport.post(
            "/v1/sources/content",
            json={
                "scope_id": "project:powercontext",
                "source_id": "turn-1",
                "content": "Captured without extraction.",
            },
        )
        flushed = transport.post(
            "/v1/memory/flush",
            json={"scope_id": "project:powercontext"},
        )

    assert captured.status_code == 202
    assert capabilities.json()["memory_extraction"] is False
    assert captured.json()["position"] == 1
    assert flushed.status_code == 422
    assert flushed.json()["error"]["code"] == "capability_not_supported"


def test_configured_generation_pipeline_accepts_content_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pydantic_ai.models.infer_model",
        lambda _: TestModel(custom_output_text='{"candidates":[]}'),
    )
    app = create_runtime_app(
        settings=_server_settings(tmp_path / "runtime.db", generation_model="test"),
    )

    with TestClient(app) as transport:
        captured = transport.post(
            "/v1/sources/content",
            json={
                "scope_id": "project:powercontext",
                "source_id": "turn-1",
                "content": "Exercise the configured extraction pipeline.",
                "metadata": {"channel": "http", "nested": {"values": [1, True, None]}},
            },
        )
        flushed = transport.post(
            "/v1/memory/flush",
            json={"scope_id": "project:powercontext"},
        )

    assert captured.status_code == 202
    assert flushed.status_code == 200
    assert flushed.json()["current_cursor"] == captured.json()["position"]
    assert flushed.json()["processed_source_count"] == 1


def test_runtime_server_rejects_non_strict_transport_values(tmp_path: Path) -> None:
    app = create_runtime_app(
        settings=_server_settings(tmp_path / "runtime.db"),
    )

    with TestClient(app) as transport:
        responses = [
            transport.post(
                "/v1/memory/search",
                json={
                    "scope_id": "project:powercontext",
                    "query": "query",
                    "limit": True,
                },
            ),
            transport.post(
                "/v1/memory/search",
                json={
                    "scope_id": " ",
                    "query": "query",
                },
            ),
            transport.post(
                "/v1/memory/remember",
                json={
                    "scope_id": "project:powercontext",
                    "kind": "decision",
                    "text": "🧠" * 3_000,
                },
            ),
            transport.post(
                "/v1/memory/entries/get",
                json={
                    "scope_id": "project:powercontext",
                    "citation": {
                        "memory_ref": {"artifact_id": " ", "revision": 1},
                        "entry_id": "entry",
                        "entry_version_id": "version",
                    },
                },
            ),
        ]

    assert [response.status_code for response in responses] == [422, 422, 422, 422]
    assert {response.json()["error"]["code"] for response in responses} == {"invalid_request"}
