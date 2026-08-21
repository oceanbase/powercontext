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
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from powercontext.builtin.artifacts.handoff import (
    HandoffDraft as RuntimeHandoffDraft,
)
from powercontext.builtin.artifacts.handoff import (
    HandoffGenerationRequest,
)
from powercontext.builtin.artifacts.handoff import (
    HandoffStatement as RuntimeHandoffStatement,
)
from powercontext.builtin.artifacts.memory import (
    EmbeddingProfile,
    MemoryCandidateRequest,
    MemoryEntryInput,
)
from powercontext.builtin.inference import EmbeddingResult, InferenceConfigurationError
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    HandoffReportConfig,
    InferenceConfig,
    MemorySearchPage,
    ScopedMemoryApplication,
)
from powercontext.builtin.runtime import (
    SearchMemoryRequest as RuntimeSearchMemoryRequest,
)
from powercontext.builtin.sources import ContentSource
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.errors import RevisionConflictError
from powercontext.http import (
    AcknowledgeHandoffRequest,
    ActivateHandoffRequest,
    CaptureContentSourceRequest,
    CommitHandoffRequest,
    ContinueHandoffRequest,
    CreateHandoffReportProjectRequest,
    CreateWorkContractRequest,
    FinalizeHandoffRequest,
    FlushMemoryRequest,
    GetHandoffReportRequest,
    GetMemoryEntryRequest,
    HandoffCurrentWorkRequest,
    HandoffSelection,
    HandoffSourceCitation,
    ListMemoryChangesRequest,
    ListMemoryEntriesRequest,
    PrepareContextRequest,
    ReadinessStatus,
    RecordTaskOutcomeRequest,
    RegisterHandoffReportWorkstreamRequest,
    RememberMemoryRequest,
    ReportFormat,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    SearchMemoryRequest,
    WorkstreamKind,
)
from powercontext.http import MemorySearchMode as HttpMemorySearchMode
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

OCEANBASE_URL = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
EMBEDDING_PROFILE = EmbeddingProfile(
    profile_id="database-e2e-v1",
    model="database-e2e",
    dimension=3,
    distance="l2",
    normalization="unit",
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


class MisconfiguredEmbeddingModel:
    profile = EMBEDDING_PROFILE

    async def embed(self, _texts: tuple[str, ...], /) -> EmbeddingResult:
        raise InferenceConfigurationError("secret provider response")  # noqa: TRY003 - verifies redaction


class DeterministicHandoffPipeline:
    async def generate(self, request: HandoffGenerationRequest, /) -> RuntimeHandoffDraft:
        citations = tuple(item.citation for item in request.evidence)
        return RuntimeHandoffDraft(
            objective=request.objective,
            state=(
                RuntimeHandoffStatement(
                    text="The HTTP and SDK lifecycle is connected.",
                    citations=citations,
                ),
            ),
            disposition="continuable",
            next_action=RuntimeHandoffStatement(
                text="Continue from the inspected temporary Handoff.",
                citations=citations,
            ),
        )


def _server_settings(
    database: Path,
    *,
    generation_model: str | None = None,
    mcp: bool = False,
    handoff_report: bool = False,
) -> ServerSettings:
    return ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        inference=InferenceConfig(generation_model=generation_model),
        handoff_report=HandoffReportConfig(enabled=handoff_report),
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
            prepared = await client.prepare_context(
                PrepareContextRequest(scope_id=scope_id, query="OpenAPI authoritative")
            )
            unrelated = await client.search_memory(
                SearchMemoryRequest(scope_id=scope_id, query="Should we keep blue icons in mobile navigation?")
            )
            entries = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope_id))

        assert readiness.checks == {"runtime": "ready", "database": "ready"}
        assert capabilities.source_types == ["content"]
        assert capabilities.memory_extraction is True
        assert capabilities.search_modes == ["auto", "fts"]
        assert capabilities.context_versions == ["powercontext.prepared-context.v1"]
        assert captured.position == 1
        assert flushed.current_cursor == captured.position
        assert flushed.memory is not None
        assert found.mode == "fts"
        assert [hit.text for hit in found.hits] == ["Keep the OpenAPI contract authoritative."]
        assert prepared.schema_ == "powercontext.prepared-context.v1"
        assert prepared.status == "ready"
        assert prepared.content is not None
        prepared_item = json.loads(prepared.content.splitlines()[-2])["items"][0]
        assert prepared_item["content"] == "Keep the OpenAPI contract authoritative."
        assert prepared_item["citation"] == found.hits[0].citation.model_dump(mode="json", by_alias=True)
        assert unrelated.hits == []
        assert entries.memory == flushed.memory
        assert entries.entries[0].source_refs[0].source_id == "turn-1"

    asyncio.run(scenario())


@pytest.mark.parametrize("database_kind", ["sqlite", "oceanbase"])
def test_server_databases_keep_case_and_accent_variant_identities_distinct(
    database_kind: str,
    tmp_path: Path,
) -> None:
    if database_kind == "oceanbase":
        if OCEANBASE_URL is None:
            pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL to a dedicated OceanBase MySQL-mode test database")
        database = OceanBaseConfig(url=SecretStr(OCEANBASE_URL))
    else:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'identity.db'}")
    marker = uuid4().hex[:12]
    writer_scope = f"PC-{marker}-Alpha"
    reader_scope = writer_scope.lower()
    accent_scope = f"{marker}-café"
    plain_scope = f"{marker}-cafe"
    memory_text = "Rotate the production signing key every ninety days."
    app = create_server_app(
        settings=ServerSettings(database=database, mcp=McpConfig(enabled=False)),
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
            await client.remember_memory(RememberMemoryRequest(scope_id=writer_scope, kind="fact", text=memory_text))
            leaked = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=reader_scope))
            await client.remember_memory(
                RememberMemoryRequest(scope_id=accent_scope, kind="fact", text="Espresso is on the third floor.")
            )
            accent_leaked = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=plain_scope))
            source_scope = f"{marker}-src"
            await client.capture_content_source(
                CaptureContentSourceRequest(scope_id=source_scope, source_id="Turn-1", content="uppercase turn")
            )
            second = await client.capture_content_source(
                CaptureContentSourceRequest(scope_id=source_scope, source_id="turn-1", content="lowercase turn")
            )

        assert not leaked.entries
        assert leaked.memory is None
        assert not accent_leaked.entries
        assert second.position == 2

    asyncio.run(scenario())


def test_inference_failure_degrades_readiness_without_blocking_database_operations(tmp_path: Path) -> None:
    app = create_server_app(
        settings=_server_settings(tmp_path / "degraded.db"),
        embedding_model=MisconfiguredEmbeddingModel(),
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
            captured = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id="degraded-readiness",
                    source_id="turn-1",
                    content="Database-backed capture remains available.",
                )
            )

        assert readiness.status is ReadinessStatus.DEGRADED
        assert readiness.checks == {
            "runtime": "ready",
            "database": "ready",
            "inference.embedding": "misconfigured",
        }
        assert captured.position == 1

    asyncio.run(scenario())


def test_sdk_handoff_lifecycle_reaches_generation_and_persistence(tmp_path: Path) -> None:
    scope_id = "handoff-e2e"
    app = create_server_app(
        settings=_server_settings(tmp_path / "handoff.db"),
        handoff_pipeline=DeterministicHandoffPipeline(),
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
            captured = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="turn-1",
                    content="The end-to-end Handoff lifecycle must remain explicit.",
                )
            )
            activation = await client.activate_handoff(
                ActivateHandoffRequest(
                    scope_id=scope_id,
                    boundary_source=captured.source,
                    objective="Transfer the current implementation state.",
                )
            )
            repeated = await client.activate_handoff(
                ActivateHandoffRequest(
                    scope_id=scope_id,
                    boundary_source=captured.source,
                    objective="Transfer the current implementation state.",
                )
            )
            draft = activation.draft
            assert draft is not None
            inspected = draft.model_copy(
                update={
                    "state": [
                        draft.state[0].model_copy(
                            update={"text": "The full HTTP and SDK lifecycle is connected."},
                        )
                    ]
                }
            )
            prepared = await client.finalize_handoff(FinalizeHandoffRequest(scope_id=scope_id, draft=inspected))
            temporary = await client.continue_handoff(
                ContinueHandoffRequest(
                    scope_id=scope_id,
                    selection=HandoffSelection.PREPARED,
                    prepared=prepared,
                )
            )
            committed = await client.commit_handoff(CommitHandoffRequest(scope_id=scope_id, handoff=prepared))
            exact = await client.continue_handoff(
                ContinueHandoffRequest(
                    scope_id=scope_id,
                    selection=HandoffSelection.EXACT,
                    revision=committed.reference,
                )
            )
            latest = await client.continue_handoff(
                ContinueHandoffRequest(
                    scope_id=scope_id,
                    selection=HandoffSelection.LATEST,
                )
            )

        assert capabilities.artifact_families == ["memory", "experience", "skill", "handoff"]
        assert capabilities.handoff_generation is True
        assert activation.status == "generated"
        assert repeated.status == "ignored"
        assert repeated.draft is None
        assert draft.state[0].text == "The HTTP and SDK lifecycle is connected."
        assert prepared.base is None
        assert temporary.selection == "prepared"
        assert temporary.selected_revision is None
        assert temporary.content is not None
        assert temporary.content.state[0].text == "The full HTTP and SDK lifecycle is connected."
        assert committed.reference.family == "handoff"
        assert committed.source_refs == [captured.source]
        assert exact.selection == "exact"
        assert exact.selected_revision == committed.reference
        assert latest.selection == "latest"
        assert latest.selected_revision == committed.reference

    asyncio.run(scenario())


def test_sdk_closes_the_delegation_handoff_and_outcome_loop(tmp_path: Path) -> None:
    scope_id = "work-continuity-e2e"
    app = create_server_app(settings=_server_settings(tmp_path / "work-continuity.db", handoff_report=True))

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport)
            project = await client.create_handoff_report_project(
                CreateHandoffReportProjectRequest(project_key="work-continuity", title="Work continuity")
            )
            await client.register_handoff_report_workstream(
                RegisterHandoffReportWorkstreamRequest(
                    project_id=project.project_id,
                    scope_id=scope_id,
                    title="Work continuity implementation",
                    kind=WorkstreamKind.FEATURE,
                )
            )
            contract = await client.create_work_contract(
                CreateWorkContractRequest.model_validate({
                    "scope_id": scope_id,
                    "source_id": "contract-1",
                    "contract": {
                        "schema": "powercontext.work-contract.v1",
                        "trust": "untrusted_input",
                        "objective": "Implement and verify the work-continuity loop.",
                        "facts": [
                            {
                                "text": "The repository already has an explicit Handoff lifecycle.",
                                "basis": "declared",
                                "evidence": [],
                            }
                        ],
                        "in_scope": ["Reuse the existing Handoff Artifact."],
                        "exclusions": ["Do not add an Agent scheduler."],
                        "completion_criteria": ["A receiver can acknowledge exact Handoff evidence."],
                        "authorization_notes": ["This record does not grant tool execution authority."],
                        "open_questions": [],
                    },
                })
            )
            prepared = await client.handoff_current_work(
                HandoffCurrentWorkRequest.model_validate({
                    "scope_id": scope_id,
                    "source_id": "boundary-1",
                    "handoff": {
                        "schema": "powercontext.current-work-handoff.v1",
                        "trust": "untrusted_input",
                        "objective": "Implement and verify the work-continuity loop.",
                        "state": [
                            {
                                "text": "The high-level Runtime path is implemented.",
                                "basis": "declared",
                                "evidence": [],
                            }
                        ],
                        "disposition": "continuable",
                        "next_action": {
                            "text": "Run the public Server acceptance test.",
                            "basis": "declared",
                            "evidence": [],
                        },
                        "omissions": ["Live OceanBase validation is not part of this SQLite acceptance test."],
                    },
                })
            )
            temporary_acknowledgement = await client.acknowledge_handoff(
                AcknowledgeHandoffRequest.model_validate({
                    "scope_id": scope_id,
                    "source_id": "receipt-temporary-1",
                    "receiver": "codex-receiver",
                    "status": "accepted",
                    "selection": HandoffSelection.PREPARED,
                    "receiver_checks": {
                        "live_state": "confirmed",
                        "capability": "confirmed",
                        "authorization": "confirmed",
                    },
                    "prepared": prepared.handoff,
                })
            )
            committed = await client.commit_handoff(CommitHandoffRequest(scope_id=scope_id, handoff=prepared.handoff))
            durable_acknowledgement = await client.acknowledge_handoff(
                AcknowledgeHandoffRequest.model_validate({
                    "scope_id": scope_id,
                    "source_id": "receipt-durable-1",
                    "receiver": "human-reviewer",
                    "status": "accepted",
                    "selection": "exact",
                    "receiver_checks": {
                        "live_state": "confirmed",
                        "capability": "confirmed",
                        "authorization": "confirmed",
                    },
                    "revision": committed.reference,
                })
            )
            outcome = await client.record_task_outcome(
                RecordTaskOutcomeRequest.model_validate({
                    "scope_id": scope_id,
                    "source_id": "outcome-1",
                    "outcome": {
                        "schema": "powercontext.task-outcome.v1",
                        "trust": "untrusted_observation",
                        "objective": "Implement and verify the work-continuity loop.",
                        "status": "succeeded",
                        "summary": "The public SQLite Server journey completed.",
                        "handoff_receipt_ref": durable_acknowledgement.receipt.source.model_dump(),
                        "observations": [
                            {
                                "text": "Both temporary and committed Handoffs were acknowledged.",
                                "basis": "declared",
                                "evidence": [],
                            }
                        ],
                        "checks": [
                            {
                                "name": "SQLite Server acceptance",
                                "status": "passed",
                                "basis": "declared",
                                "evidence": [],
                            }
                        ],
                        "produced_artifacts": [],
                        "remaining_work": [],
                    },
                })
            )
            report = await client.get_handoff_report(
                GetHandoffReportRequest(
                    project_id=project.project_id,
                    include_evidence_checks=False,
                    format=ReportFormat.JSON,
                )
            )

        assert contract.kind == "work-contract"
        assert contract.position == 1
        assert prepared.boundary.kind == "handoff-boundary"
        assert prepared.boundary.position == 2
        citation = prepared.handoff.content.state[0].citations[0].root
        assert isinstance(citation, HandoffSourceCitation)
        assert citation.source_ref == prepared.boundary.source
        assert temporary_acknowledgement.resolution.selection == "prepared"
        assert temporary_acknowledgement.receipt.kind == "handoff-receipt"
        assert temporary_acknowledgement.receipt.position == 3
        assert committed.source_refs == [prepared.boundary.source]
        assert durable_acknowledgement.resolution.selection == "exact"
        assert durable_acknowledgement.resolution.selected_revision == committed.reference
        assert durable_acknowledgement.receipt.position == 4
        assert outcome.kind == "task-outcome"
        assert outcome.position == 5
        assert not isinstance(report, str)
        assert report.report is not None
        continuity = report.report["workstreams"][0]["continuity"]
        assert continuity["coverage"]["transfer_state"] == "accepted"
        assert continuity["coverage"]["outcome_state"] == "covered"
        assert continuity["coverage"]["handoff_result_covered"] is True
        assert [event["kind"] for event in continuity["events"]] == [
            "work-contract",
            "handoff-boundary",
            "handoff-receipt",
            "handoff-receipt",
            "task-outcome",
        ]

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
            await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="beta-source",
                    content="Beta semantic record.",
                )
            )
            flushed = await client.flush_memory(FlushMemoryRequest(scope_id=scope_id))
            vector = await client.search_memory(
                SearchMemoryRequest(
                    scope_id=scope_id,
                    query="alpha",
                    mode=HttpMemorySearchMode.VECTOR,
                )
            )
            hybrid = await client.search_memory(
                SearchMemoryRequest(
                    scope_id=scope_id,
                    query="alpha",
                    mode=HttpMemorySearchMode.HYBRID,
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
            current = await client.list_memory_entries(
                ListMemoryEntriesRequest(scope_id="project:powercontext"),
            )
            audited = await client.list_memory_entries(
                ListMemoryEntriesRequest(
                    scope_id="project:powercontext",
                    include_inactive=True,
                ),
            )
            retired_search = await client.search_memory(
                SearchMemoryRequest(
                    scope_id="project:powercontext",
                    query="strict Pydantic transport models",
                ),
            )
            retired_exact = await client.get_memory_entry(
                GetMemoryEntryRequest(
                    scope_id="project:powercontext",
                    citation=retired.entry.citation,
                ),
            )
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
        assert current.memory == retired.memory
        assert current.entries == []
        assert audited.memory == retired.memory
        assert audited.entries == [retired.entry]
        assert retired_search.memory == retired.memory
        assert retired_search.hits == []
        assert retired_exact == retired.entry
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
            json={"scope_id": "project", "source_id": "turn-1", "content": "changed"},
        )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert len(conflict.headers["X-PowerContext-Request-ID"]) == 16
    assert int(conflict.headers["X-PowerContext-Request-ID"], 16) > 0
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
        assert caught.value.request_id is not None

    asyncio.run(stale_revision())


def test_memory_search_returns_revision_conflict_as_http_409(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def conflicting_search(
        _self: ScopedMemoryApplication,
        _request: RuntimeSearchMemoryRequest,
        /,
    ) -> MemorySearchPage:
        raise RevisionConflictError("stale", "current")

    monkeypatch.setattr(ScopedMemoryApplication, "search", conflicting_search)
    app = create_server_app(settings=_server_settings(tmp_path / "runtime.db"))

    with TestClient(app) as transport:
        response = transport.post(
            "/v1/memory/search",
            json={"scope_id": "project", "query": "stable searchable"},
        )

    assert response.status_code == 409
    assert len(response.headers["X-PowerContext-Request-ID"]) == 16
    assert int(response.headers["X-PowerContext-Request-ID"], 16) > 0
    assert response.json()["error"] == {
        "code": "revision_conflict",
        "message": "The Memory Revision is stale.",
        "details": None,
    }


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
