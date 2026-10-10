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
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
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
)
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCandidateError
from powercontext.builtin.inference import EmbeddingResult, GenerationResult, InferenceConfigurationError
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    HandoffReportConfig,
    InferenceConfig,
    ScopedAtomicMemoryApplication,
)
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.errors import RevisionConflictError
from powercontext.http import (
    AcknowledgeHandoffRequest,
    ActivateHandoffRequest,
    ArtifactAddress,
    AtomicMemoryLifecycleRequest,
    CaptureContentSourceRequest,
    CommitHandoffRequest,
    ContinueHandoffRequest,
    CreateScopeRequest,
    CreateWorkContractRequest,
    ExactScopeSelection,
    FinalizeHandoffRequest,
    FlushMemoryRequest,
    GetHandoffReportRequest,
    HandoffCurrentWorkRequest,
    HandoffSelection,
    HandoffSourceCitation,
    ListMemoryChangesRequest,
    ListMemoryEntriesRequest,
    PrepareContextRequest,
    PublishArtifactRequest,
    ReadinessStatus,
    RecordTaskOutcomeRequest,
    RememberMemoryRequest,
    ReplaceArtifactRequest,
    ReportFormat,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    ScopeSelection,
    SearchMemoryRequest,
)
from powercontext.http import MemorySearchMode as HttpMemorySearchMode
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from tests.e2e.dream_support import atomic_memory_pipeline, memory_source_text

_ACCESS_READINESS_CHECKS = {
    "access_mode": "disabled",
    "authentication_provider": "disabled",
    "access_provider": "disabled",
    "access_resource_kinds": "server,scope,artifact",
    "access_artifact_families": (
        "atomic-memory:enabled,experience:enabled,handoff:enabled,memory:enabled,profile:enabled,prompt:enabled,skill:enabled"
    ),
}
EMBEDDING_PROFILE = EmbeddingProfile(
    profile_id="database-e2e-v1",
    model="database-e2e",
    dimension=3,
    distance="l2",
    normalization="unit",
)


@pytest.fixture
def database(database_kind: str, tmp_path: Path) -> Iterator[SQLiteConfig | OceanBaseConfig]:
    if database_kind == "sqlite":
        yield SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
        return
    configured_url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
    if not configured_url:
        pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL with test database creation and deletion privileges")
    configured = OceanBaseConfig(url=SecretStr(configured_url))
    name = f"pc_runtime_{uuid4().hex}"

    async def execute(statement: str) -> None:
        async with (
            OceanBaseProfile.open(configured, tables=()) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.exec_driver_sql(statement)

    asyncio.run(execute(f"CREATE DATABASE `{name}`"))
    try:
        url = make_url(configured_url).set(database=name).render_as_string(hide_password=False)
        yield OceanBaseConfig(url=SecretStr(url))
    finally:
        asyncio.run(execute(f"DROP DATABASE `{name}`"))


class ContentCandidatePipeline:
    async def generate(self, request: AtomicMemoryExtractionInput, /) -> GenerationResult[AtomicMemoryExtractionOutput]:
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(kind="decision", text=text, evidence_ids=(evidence.evidence_id,))
                    for evidence in request.evidence
                    if (text := memory_source_text(evidence)) is not None
                )
            )
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
    database: SQLiteConfig | OceanBaseConfig,
) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=database,
            mcp=McpConfig(enabled=False),
        ),
        candidate_pipeline=atomic_memory_pipeline(ContentCandidatePipeline()),
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            readiness = await client.get_readiness()
            capabilities = await client.get_capabilities()
            scope = await client.create_scope(
                CreateScopeRequest(
                    title="Database acceptance",
                    summary="Isolated source-to-memory search acceptance.",
                    idempotency_key=f"database-e2e-{uuid4()}",
                )
            )
            scope_id = scope.scope_id
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
            ref = found.hits[0].memory.artifact
            exact = await client.get_artifact_revision(scope_id, ref.family, ref.artifact_id, ref.revision)

        assert readiness.checks == {
            "runtime": "ready",
            "database": "ready",
            "artifact_processing_supervisor": "disabled",
            **_ACCESS_READINESS_CHECKS,
        }
        assert capabilities.source_types == ["content"]
        assert ref.family == "atomic-memory"
        assert ref.family in capabilities.artifact_families
        assert "memory" in capabilities.artifact_families
        assert capabilities.memory_extraction is True
        assert capabilities.search_modes == ["auto", "fts"]
        assert capabilities.context_versions == ["powercontext.prepared-context.v1"]
        assert captured.position == 1
        assert flushed.current_cursor == captured.position
        assert flushed.memory is None
        assert found.mode == "text"
        assert [hit.memory.text for hit in found.hits] == ["Keep the OpenAPI contract authoritative."]
        assert prepared.schema_ == "powercontext.prepared-context.v1"
        assert prepared.status == "ready"
        assert prepared.content is not None
        prepared_item = json.loads(prepared.content.splitlines()[-2])["items"][0]
        assert prepared_item["content"] == "Keep the OpenAPI contract authoritative."
        assert prepared_item["citation"]["artifact_ref"] == ref.model_dump(mode="json", by_alias=True)
        assert unrelated.hits == []
        assert entries.entries[0].artifact == ref
        assert exact.sources[0].source_id == "turn-1"

    asyncio.run(scenario())


@pytest.mark.parametrize("database_kind", ["sqlite", "oceanbase"])
def test_server_databases_keep_case_and_accent_variant_identities_distinct(
    database: SQLiteConfig | OceanBaseConfig,
) -> None:
    marker = uuid4().hex[:12]
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
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            writer_scope = await client.create_scope(
                CreateScopeRequest(
                    title=f"PC {marker} Alpha",
                    summary="Writer Scope for case-sensitive isolation acceptance.",
                    idempotency_key=f"PC-{marker}-Alpha",
                )
            )
            reader_scope = await client.create_scope(
                CreateScopeRequest(
                    title=f"pc {marker} alpha",
                    summary="Reader Scope for case-sensitive isolation acceptance.",
                    idempotency_key=f"pc-{marker}-alpha",
                )
            )
            accent_scope = await client.create_scope(
                CreateScopeRequest(
                    title=f"{marker} café",
                    summary="Writer Scope for accent-sensitive isolation acceptance.",
                    idempotency_key=f"{marker}-café",
                )
            )
            plain_scope = await client.create_scope(
                CreateScopeRequest(
                    title=f"{marker} cafe",
                    summary="Reader Scope for accent-sensitive isolation acceptance.",
                    idempotency_key=f"{marker}-cafe",
                )
            )
            source_scope = await client.create_scope(
                CreateScopeRequest(
                    title=f"{marker} source identity",
                    summary="Source identity case-sensitivity acceptance.",
                    idempotency_key=f"{marker}-src",
                )
            )
            scope_ids = {
                writer_scope.scope_id,
                reader_scope.scope_id,
                accent_scope.scope_id,
                plain_scope.scope_id,
                source_scope.scope_id,
            }
            assert len(scope_ids) == 5
            await client.remember_memory(
                RememberMemoryRequest(scope_id=writer_scope.scope_id, kind="fact", text=memory_text)
            )
            leaked = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=reader_scope.scope_id))
            await client.remember_memory(
                RememberMemoryRequest(
                    scope_id=accent_scope.scope_id,
                    kind="fact",
                    text="Espresso is on the third floor.",
                )
            )
            accent_leaked = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=plain_scope.scope_id))
            await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=source_scope.scope_id,
                    source_id="Turn-1",
                    content="uppercase turn",
                )
            )
            second = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=source_scope.scope_id,
                    source_id="turn-1",
                    content="lowercase turn",
                )
            )

        assert not leaked.entries
        assert leaked.next_cursor is None
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
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            readiness = await client.get_readiness()
            scope_id = (await client.get_default_scope()).scope_id
            captured = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="turn-1",
                    content="Database-backed capture remains available.",
                )
            )

        assert readiness.status is ReadinessStatus.DEGRADED
        assert readiness.checks == {
            "runtime": "ready",
            "database": "ready",
            "inference.embedding": "misconfigured",
            "artifact_processing_supervisor": "disabled",
            **_ACCESS_READINESS_CHECKS,
        }
        assert captured.position == 1

    asyncio.run(scenario())


def test_sdk_handoff_lifecycle_reaches_generation_and_persistence(tmp_path: Path) -> None:
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
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            source_scope = await client.create_scope(
                CreateScopeRequest(
                    title="Source work",
                    summary="Work prepared for an exact Handoff publication.",
                    idempotency_key="handoff-e2e-source",
                )
            )
            target_scope = await client.create_scope(
                CreateScopeRequest(
                    title="Target work",
                    summary="Work continued from an exact Handoff publication.",
                    idempotency_key="handoff-e2e-target",
                )
            )
            scope_id = source_scope.scope_id
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
            publication = await client.publish_artifact(
                PublishArtifactRequest(
                    source=ArtifactAddress(scope_id=scope_id, artifact=committed.reference),
                    target_scope_id=target_scope.scope_id,
                    idempotency_key="handoff-e2e-publication",
                )
            )
            published = await client.continue_handoff(
                ContinueHandoffRequest(
                    scope_id=target_scope.scope_id,
                    selection=HandoffSelection.EXACT,
                    revision=publication.target.artifact,
                )
            )

        assert capabilities.artifact_families == [
            "memory",
            "atomic-memory",
            "topic-memory",
            "experience",
            "skill",
            "handoff",
            "profile",
            "prompt",
        ]
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
        assert published.selection == "exact"
        assert published.selected_revision == publication.target.artifact
        assert published.current_revision == publication.target.artifact
        assert published.content == committed.content

    asyncio.run(scenario())


def test_sdk_closes_the_delegation_handoff_and_outcome_loop(tmp_path: Path) -> None:
    app = create_server_app(settings=_server_settings(tmp_path / "work-continuity.db", handoff_report=True))

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope_response = await transport.post(
                "/v1/scopes",
                json={
                    "title": "Work continuity",
                    "summary": "Delegation Handoff and outcome loop",
                    "idempotency_key": "work-continuity-e2e",
                },
            )
            scope_response.raise_for_status()
            scope_id = scope_response.json()["scope_id"]
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
                    selection=ScopeSelection(root=ExactScopeSelection(mode="exact", scope_ids=[scope_id])),
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
        assert report.report["scope_ids"] == [scope_id]
        assert report.report["scopes"][0]["handoff"] == {
            "scope_id": scope_id,
            "artifact": committed.reference.model_dump(mode="json"),
        }
        assert report.report["scopes"][0]["content"]["objective"] == ("Implement and verify the work-continuity loop.")

    asyncio.run(scenario())


@pytest.mark.parametrize("database_kind", ["sqlite", "oceanbase"])
def test_server_databases_share_vector_and_hybrid_search_behavior(
    database: SQLiteConfig | OceanBaseConfig,
) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=database,
            mcp=McpConfig(enabled=False),
        ),
        candidate_pipeline=atomic_memory_pipeline(ContentCandidatePipeline()),
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
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            capabilities = await client.get_capabilities()
            scope = await client.create_scope(
                CreateScopeRequest(
                    title="Vector search acceptance",
                    summary="Isolated vector and hybrid search acceptance.",
                    idempotency_key=f"vector-e2e-{uuid4()}",
                )
            )
            scope_id = scope.scope_id
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

        assert flushed.memory is None
        assert capabilities.search_modes == ["auto", "fts", "vector", "hybrid"]
        assert [hit.memory.text for hit in vector.hits] == ["Alpha semantic record."]
        assert vector.hits[0].matched_by == ["vector"]
        assert [hit.memory.text for hit in hybrid.hits] == ["Alpha semantic record."]
        assert hybrid.hits[0].matched_by == ["text", "vector"]

    asyncio.run(scenario())


def test_sdk_memory_lifecycle_reaches_one_composed_runtime(tmp_path: Path) -> None:
    app = create_server_app(settings=_server_settings(tmp_path / "runtime.db"))

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope_id = (await client.get_default_scope()).scope_id
            remembered = await client.remember_memory(
                RememberMemoryRequest(scope_id=scope_id, kind="decision", text="Use strict transport models.")
            )
            assert len(remembered.records) == 1
            original = remembered.records[0]
            ref = original.artifact
            exact = await client.get_artifact_revision(scope_id, ref.family, ref.artifact_id, ref.revision)
            path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{ref.artifact_id}"
            head = await transport.get(path)
            revised = await client.replace_artifact(
                scope_id,
                ref.family,
                ref.artifact_id,
                ReplaceArtifactRequest.model_validate({
                    "content": {"kind": "decision", "text": "Keep strict Pydantic transport models."}
                }),
                expected_etag=head.headers["ETag"],
            )
            state = await client.get_atomic_memory_state(scope_id, ref.artifact_id)
            forgotten = await client.change_atomic_memory_lifecycle(
                AtomicMemoryLifecycleRequest.model_validate({
                    "scope_id": scope_id,
                    "target": {
                        "artifact": state.artifact.model_dump(mode="json"),
                        "state_version": state.state_version,
                    },
                    "state": "forgotten",
                })
            )
            current = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope_id))
            audited = await client.list_memory_entries(
                ListMemoryEntriesRequest(scope_id=scope_id, include_inactive=True)
            )
            forgotten_search = await client.search_memory(
                SearchMemoryRequest(scope_id=scope_id, query="strict Pydantic transport models")
            )
            forgotten_exact = await client.get_artifact_revision(
                scope_id, ref.family, ref.artifact_id, revised.revision
            )
            legacy = {
                "memory_ref": {"family": "memory", "artifact_id": "legacy-collection", "revision": 1},
                "entry_id": "legacy-entry",
                "entry_version_id": "legacy-version",
            }
            with pytest.raises(ServerResponseError) as inactive:
                await client.revise_memory_entry(
                    ReviseMemoryEntryRequest(
                        scope_id=scope_id, citation=legacy, kind="decision", text="Rejected legacy revision."
                    )
                )
            with pytest.raises(ServerResponseError) as retired:
                await client.retire_memory_entry(RetireMemoryEntryRequest(scope_id=scope_id, citation=legacy))
            with pytest.raises(ServerResponseError) as changes:
                await client.list_memory_changes(ListMemoryChangesRequest(scope_id=scope_id, since_revision=1))
            with pytest.raises(ServerResponseError) as missing:
                await client.get_artifact_revision(scope_id, ref.family, "missing-memory", 1)
            assert (
                await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope_id, include_inactive=True))
                == audited
            )

        assert exact.content["text"] == "Use strict transport models."
        assert revised.content["text"] == "Keep strict Pydantic transport models."
        assert revised.revision == ref.revision + 1
        assert forgotten.records[0].state == "forgotten"
        assert current.entries == []
        assert audited.entries == forgotten.records
        assert forgotten_search.hits == []
        assert forgotten_exact.content == revised.content
        for error in (inactive.value, retired.value, changes.value):
            assert (error.status_code, error.code) == (422, "legacy_memory_operation_unsupported")
        assert missing.value.status_code == 404

    asyncio.run(scenario())


def test_runtime_conflicts_keep_http_and_sdk_error_context(tmp_path: Path) -> None:
    app = create_server_app(settings=_server_settings(tmp_path / "runtime.db"))

    with TestClient(app) as transport:
        default_scope = transport.get("/v1/scopes/default")
        default_scope.raise_for_status()
        scope_id = default_scope.json()["scope_id"]
        first = transport.post(
            "/v1/sources/content",
            json={"scope_id": scope_id, "source_id": "turn-1", "content": "first"},
        )
        conflict = transport.post(
            "/v1/sources/content",
            json={"scope_id": scope_id, "source_id": "turn-1", "content": "changed"},
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
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            remembered = await client.remember_memory(
                RememberMemoryRequest(scope_id=scope_id, kind="decision", text="first")
            )
            with pytest.raises(ServerResponseError) as caught:
                await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id=scope_id,
                        kind="decision",
                        text="stale",
                        expected_revision=remembered.records[0].artifact.revision + 1,
                    )
                )
        assert caught.value.status_code == 422
        assert caught.value.code == "legacy_memory_operation_unsupported"
        assert caught.value.request_id is not None

    asyncio.run(stale_revision())


def test_memory_search_returns_revision_conflict_as_http_409(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def conflicting_search(_self: ScopedAtomicMemoryApplication, _query: str, **_kwargs: object) -> None:
        raise RevisionConflictError("stale", "current")

    monkeypatch.setattr(ScopedAtomicMemoryApplication, "search", conflicting_search)
    app = create_server_app(settings=_server_settings(tmp_path / "runtime.db"))

    with TestClient(app) as transport:
        default_scope = transport.get("/v1/scopes/default")
        default_scope.raise_for_status()
        scope_id = default_scope.json()["scope_id"]
        response = transport.post(
            "/v1/memory/search",
            json={"scope_id": scope_id, "query": "stable searchable"},
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
        default_scope = transport.get("/v1/scopes/default")
        default_scope.raise_for_status()
        scope_id = default_scope.json()["scope_id"]
        responses = [
            transport.post(
                "/v1/memory/search",
                json={"scope_id": scope_id, "query": "query", "limit": True},
            ),
            transport.post(
                "/v1/memory/search",
                json={"scope_id": " ", "query": "query"},
            ),
            transport.post(
                "/v1/memory/remember",
                json={"scope_id": scope_id, "kind": "decision", "text": "🧠" * 3_000},
            ),
        ]

    assert [response.status_code for response in responses] == [422, 422, 422]
    assert {response.json()["error"]["code"] for response in responses} == {"invalid_request"}


@pytest.mark.parametrize("text", ["a" * 8_193, "界" * 2_731, "🧠" * 2_049], ids=["ascii", "chinese", "emoji"])
def test_runtime_server_returns_canonical_memory_error_details(tmp_path: Path, text: str) -> None:
    app = create_server_app(settings=_server_settings(tmp_path / "runtime.db"))

    with TestClient(app) as transport:
        default_scope = transport.get("/v1/scopes/default")
        default_scope.raise_for_status()
        scope_id = default_scope.json()["scope_id"]
        remembered = transport.post(
            "/v1/memory/remember",
            json={"scope_id": scope_id, "kind": "decision", "text": "Keep canonical errors actionable."},
        )
        remembered.raise_for_status()
        before = transport.post("/v1/memory/entries/list", json={"scope_id": scope_id})
        before.raise_for_status()
        responses = [
            transport.post(
                "/v1/memory/remember",
                json={"scope_id": scope_id, "kind": "decision", "text": text},
            ),
            transport.post(
                "/v1/memory/entries/revise",
                json={
                    "scope_id": scope_id,
                    "citation": {
                        "memory_ref": {"family": "memory", "artifact_id": "legacy-collection", "revision": 1},
                        "entry_id": "legacy-entry",
                        "entry_version_id": "legacy-version",
                    },
                    "kind": "decision",
                    "text": text,
                },
            ),
        ]
        after = transport.post("/v1/memory/entries/list", json={"scope_id": scope_id})
        after.raise_for_status()
        assert after.json() == before.json()

    expected_error = {
        "code": "invalid_request",
        "message": "The request violates the API contract.",
        "details": {
            "errors": [
                {
                    "type": "value_error",
                    "loc": ["text"],
                    "msg": "Value error, memory entry text must not exceed 8192 UTF-8 bytes",
                }
            ],
        },
    }
    assert [response.status_code for response in responses] == [422, 422]
    assert responses[0].json()["error"] == expected_error
    assert responses[1].json()["error"]["code"] == "legacy_memory_operation_unsupported"


@pytest.mark.parametrize(
    ("text", "normalized"),
    [
        ("a" * 8_192, "a" * 8_192),
        ("🧠" * 2_048, "🧠" * 2_048),
        (" " + "a" * 8_192 + " ", "a" * 8_192),
        ("e\u0301" * 4_096, "é" * 4_096),
    ],
    ids=["ascii-limit", "emoji-limit", "trimmed-limit", "nfc-limit"],
)
def test_runtime_server_accepts_normalized_memory_byte_limit(tmp_path: Path, text: str, normalized: str) -> None:
    app = create_server_app(settings=_server_settings(tmp_path / "runtime.db"))

    with TestClient(app) as transport:
        scope = transport.get("/v1/scopes/default")
        scope.raise_for_status()
        payload = {"scope_id": scope.json()["scope_id"], "kind": "decision", "text": text}
        remembered = transport.post("/v1/memory/remember", json=payload)
        remembered.raise_for_status()
        assert remembered.json()["records"][0]["text"] == normalized
        revised = transport.post(
            "/v1/memory/entries/revise",
            json={
                **payload,
                "citation": {
                    "memory_ref": {"family": "memory", "artifact_id": "legacy-collection", "revision": 1},
                    "entry_id": "legacy-entry",
                    "entry_version_id": "legacy-version",
                },
            },
        )
        assert revised.status_code == 422
        assert revised.json()["error"]["code"] == "legacy_memory_operation_unsupported"
        listed = transport.post("/v1/memory/entries/list", json={"scope_id": payload["scope_id"]})
        assert listed.json()["entries"] == remembered.json()["records"]


def test_runtime_server_rejects_legacy_memory_tag_targets(tmp_path: Path) -> None:
    app = create_server_app(settings=_server_settings(tmp_path / "runtime.db"))

    with TestClient(app) as transport:
        scope = transport.get("/v1/scopes/default")
        scope.raise_for_status()
        scope_id = scope.json()["scope_id"]
        remembered = transport.post(
            "/v1/memory/remember", json={"scope_id": scope_id, "kind": "fact", "text": "Tagged migrations."}
        )
        remembered.raise_for_status()
        artifact_id = remembered.json()["records"][0]["artifact"]["artifact_id"]
        empty = transport.get(f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}/tags")
        empty.raise_for_status()
        tagged = transport.put(
            f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}/tags",
            json={"tags": ["project"]},
            headers={"If-Match": empty.headers["ETag"]},
        )
        tagged.raise_for_status()
        collection = f"/v1/scopes/{scope_id}/artifacts/memory/legacy-collection/tags"
        responses = [
            transport.get(collection),
            transport.put(collection, json={"tags": ["project"]}, headers={"If-Match": '"tags:legacy"'}),
            transport.post(
                f"/v1/scopes/{scope_id}/artifact-tags/query", json={"tags": ["project"], "families": ["memory"]}
            ),
            transport.post(
                f"/v1/scopes/{scope_id}/artifact-tags/query",
                json={"tags": ["project"], "target_types": ["memory_entry"]},
            ),
        ]
        for response in responses:
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "legacy_memory_operation_unsupported"
        current = transport.post(f"/v1/scopes/{scope_id}/artifact-tags/query", json={"tags": ["project"]})
        current.raise_for_status()
        assert [item["target"] for item in current.json()["items"]] == [
            {"type": "artifact", "family": "atomic-memory", "artifact_id": artifact_id}
        ]


def test_runtime_server_keeps_unstructured_memory_errors_private(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def invalid_create(_self: ScopedAtomicMemoryApplication, _contents: object, /, *, context=None) -> None:
        raise InvalidMemoryCandidateError("canonical", "private implementation detail")

    monkeypatch.setattr(ScopedAtomicMemoryApplication, "create", invalid_create)
    app = create_server_app(settings=_server_settings(tmp_path / "runtime.db"))

    with TestClient(app) as transport:
        default_scope = transport.get("/v1/scopes/default")
        default_scope.raise_for_status()
        response = transport.post(
            "/v1/memory/remember",
            json={
                "scope_id": default_scope.json()["scope_id"],
                "kind": "decision",
                "text": "valid text",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "invalid_request",
        "message": "The request is invalid.",
        "details": None,
    }
