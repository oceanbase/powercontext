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

"""End-to-end acceptance for curated lifecycle bootstrap context."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import CONTEXT_BOOTSTRAP_RECEIPTS_TABLE
from powercontext.builtin.runtime.application import ScopedBootstrapApplication
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.builtin.runtime.recall_sufficiency import RecallSufficiencyGate
from powercontext.client import PowerContextClient
from powercontext.http import (
    BootstrapContextRequest,
    ContextReference,
    CreateArtifactRequest,
    CreateScopeRequest,
    CreateSourceRequest,
    PrepareContextRequest,
    RecordBootstrapDeliveryRequest,
    RememberMemoryRequest,
    ReplaceArtifactRequest,
    ReplaceArtifactTagsRequest,
)
from powercontext.http._generated.models import PreparedContext
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, MetricsConfig, ServerSettings


async def _tag_for_bootstrap(
    client: PowerContextClient,
    scope_id: str,
    artifact_id: str,
) -> None:
    tags = await client.get_artifact_tags(scope_id, "atomic-memory", artifact_id)
    assert tags is not None
    await client.replace_artifact_tags(
        scope_id,
        "atomic-memory",
        artifact_id,
        ReplaceArtifactTagsRequest.model_validate({"tags": ["bootstrap-context"]}),
        expected_etag=tags.etag,
    )


async def _replace_atomic_memory(
    client: PowerContextClient,
    transport: httpx.AsyncClient,
    scope_id: str,
    artifact_id: str,
    *,
    kind: str,
    text: str,
):
    current = await transport.get(f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}")
    assert current.status_code == 200, current.text
    return await client.replace_artifact(
        scope_id,
        "atomic-memory",
        artifact_id,
        ReplaceArtifactRequest.model_validate({"content": {"kind": kind, "text": text}}),
        expected_etag=current.headers["ETag"],
    )


def test_bootstrap_is_opt_in_exact_bounded_idempotent_and_keeps_partial_memory_recallable(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'bootstrap.db'}"),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope = await client.create_scope(
                CreateScopeRequest(title="Bootstrap", summary="Lifecycle context", idempotency_key="bootstrap")
            )
            remembered = await client.remember_memory(
                RememberMemoryRequest(
                    scope_id=scope.scope_id,
                    kind="decision",
                    text="BOOTSTRAP-ONLY: regenerate the OpenAPI bindings before contract tests. "
                    + "中文上下文。" * 60
                    + " OMITTED-TAIL: restore the lunar archive after validation.",
                )
            )
            assert len(remembered.records) == 1
            entry = remembered.records[0]
            await _tag_for_bootstrap(client, scope.scope_id, entry.artifact.artifact_id)
            secret = await client.remember_memory(
                RememberMemoryRequest(scope_id=scope.scope_id, kind="access-key", text="do-not-inject-secret")
            )
            assert len(secret.records) == 1
            await _tag_for_bootstrap(client, scope.scope_id, secret.records[0].artifact.artifact_id)

            disabled = await client.prepare_bootstrap_context(
                BootstrapContextRequest.model_validate({
                    "scope_id": scope.scope_id,
                    "lifecycle": "startup",
                    "integration": "acceptance",
                })
            )
            assert disabled.status == "skipped"
            assert disabled.reason == "disabled"
            assert disabled.receipt.state == "skipped"
            database = app.state.application._bootstrap_receipts._database
            async with database.transaction() as connection:
                assert (await connection.execute(select(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE))).all() == []

            request = BootstrapContextRequest.model_validate({
                "scope_id": scope.scope_id,
                "enabled": True,
                "lifecycle": "startup",
                "integration": "acceptance",
                "event_id": "stable-event-1",
                "max_bytes": 1024,
            })
            prepared = await client.prepare_bootstrap_context(request)
            assert prepared.status == "ready"
            assert prepared.content is not None
            assert "BOOTSTRAP-ONLY" in prepared.content
            assert "OMITTED-TAIL" not in prepared.content
            assert "do-not-inject-secret" not in prepared.content
            assert prepared.content_bytes == len(prepared.content.encode("utf-8")) <= 1024
            assert prepared.truncated is True
            assert len(prepared.items) == 1
            assert prepared.items[0].artifact == entry.artifact
            assert prepared.items[0].truncated is True

            retried = await client.prepare_bootstrap_context(request)
            assert retried.content == prepared.content
            assert retried.receipt == prepared.receipt
            mismatched_retry = await transport.post(
                "/v1/context/bootstrap",
                json={
                    "scope_id": scope.scope_id,
                    "enabled": True,
                    "lifecycle": "startup",
                    "integration": "acceptance",
                    "event_id": "stable-event-1",
                    "max_bytes": 512,
                },
            )
            assert mismatched_retry.status_code == 422
            recorded = await client.record_bootstrap_delivery(
                RecordBootstrapDeliveryRequest.model_validate({
                    "scope_id": scope.scope_id,
                    "receipt_id": prepared.receipt.receipt_id,
                    "outcome": "injected",
                })
            )
            assert recorded.state == "injected"
            duplicate_claim = await transport.post(
                "/v1/context/bootstrap/receipts",
                json={"scope_id": scope.scope_id, "receipt_id": prepared.receipt.receipt_id, "outcome": "injected"},
            )
            assert duplicate_claim.status_code == 422
            repeated_record = await client.record_bootstrap_delivery(
                RecordBootstrapDeliveryRequest.model_validate({
                    "scope_id": scope.scope_id,
                    "receipt_id": prepared.receipt.receipt_id,
                    "outcome": "failed",
                })
            )
            assert repeated_record.state == "injected"
            terminal_retry = await client.prepare_bootstrap_context(request)
            assert terminal_retry.status == "skipped"
            assert terminal_retry.reason == "already_delivered"
            assert terminal_retry.receipt.state == "injected"

            ordinary = await client.prepare_context(
                PrepareContextRequest(scope_id=scope.scope_id, query="restore lunar archive validation")
            )
            assert isinstance(ordinary, PreparedContext)
            assert ordinary.status == "ready"
            assert ordinary.content is not None and "OMITTED-TAIL" in ordinary.content
            with_receipt = await client.prepare_context(
                PrepareContextRequest(
                    scope_id=scope.scope_id,
                    query="restore lunar archive validation",
                    bootstrap_receipt_id=prepared.receipt.receipt_id,
                )
            )
            assert isinstance(with_receipt, PreparedContext)
            assert with_receipt.status == "ready"
            assert with_receipt.content is not None and "OMITTED-TAIL" in with_receipt.content

            revised = await _replace_atomic_memory(
                client,
                transport,
                scope.scope_id,
                entry.artifact.artifact_id,
                kind="decision",
                text="BOOTSTRAP-ONLY: regenerate both Python and JavaScript bindings.",
            )
            after_revision = await client.prepare_context(
                PrepareContextRequest(
                    scope_id=scope.scope_id,
                    query="regenerate Python JavaScript bindings",
                    bootstrap_receipt_id=prepared.receipt.receipt_id,
                )
            )
            assert isinstance(after_revision, PreparedContext)
            assert after_revision.status == "ready"
            assert after_revision.content is not None and "both Python and JavaScript" in after_revision.content

            pending_retry_request = BootstrapContextRequest.model_validate({
                "scope_id": scope.scope_id,
                "enabled": True,
                "lifecycle": "resume",
                "integration": "acceptance",
                "event_id": "stable-event-2",
                "max_bytes": 1024,
            })
            pending_before_revision = await client.prepare_bootstrap_context(pending_retry_request)
            assert pending_before_revision.status == "ready"
            changed_again = await _replace_atomic_memory(
                client,
                transport,
                scope.scope_id,
                entry.artifact.artifact_id,
                kind="decision",
                text="SECOND-REVISION: this must not change a pending bootstrap retry.",
            )
            assert changed_again.revision == revised.revision + 1
            stable_retry = await client.prepare_bootstrap_context(pending_retry_request)
            assert stable_retry.receipt == pending_before_revision.receipt
            assert stable_retry.content == pending_before_revision.content
            assert stable_retry.items == pending_before_revision.items
            assert stable_retry.content is not None and "SECOND-REVISION" not in stable_retry.content

            disabled_retry = await client.prepare_bootstrap_context(
                pending_retry_request.model_copy(update={"enabled": False})
            )
            assert disabled_retry.status == "skipped"
            assert disabled_retry.reason == "disabled"
            assert disabled_retry.content is None
            assert disabled_retry.receipt.state == "skipped"
            assert disabled_retry.receipt.receipt_id == pending_before_revision.receipt.receipt_id
            reenabled_same_event = await client.prepare_bootstrap_context(pending_retry_request)
            assert reenabled_same_event.status == "skipped"
            assert reenabled_same_event.reason == "already_delivered"

            async with database.transaction() as connection:
                row = (
                    (
                        await connection.execute(
                            select(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE).where(
                                CONTEXT_BOOTSTRAP_RECEIPTS_TABLE.c.receipt_id == prepared.receipt.receipt_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            serialized = repr(dict(row))
            assert "BOOTSTRAP-ONLY" not in serialized
            assert "regenerate OpenAPI" not in serialized
            assert "stable-event-1" not in serialized

    asyncio.run(scenario())


def test_bootstrap_deduplicates_complete_atomic_revision_before_recall_sufficiency_gate(
    tmp_path,
    monkeypatch,
) -> None:
    assessed_memory_counts: list[int] = []
    original_assess = RecallSufficiencyGate.assess

    def observe_assess(self, candidates, *args, **kwargs):
        assessed_memory_counts.append(sum(candidate.family == "memory" for candidate in candidates))
        return original_assess(self, candidates, *args, **kwargs)

    monkeypatch.setattr(RecallSufficiencyGate, "assess", observe_assess)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'deduplicate-atomic-revision.db'}"),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
            runtime=RuntimeConfig(
                recall_gate_enabled=True,
                recall_gate_min_top_score=0.0,
                recall_gate_min_top_gap=0.0,
                recall_gate_min_lexical_overlap=0.0,
            ),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope = await client.create_scope(
                CreateScopeRequest(
                    title="Atomic revision deduplication",
                    summary="Ignore unrelated Atomic Memory writes",
                    idempotency_key="atomic-revision-deduplication",
                )
            )
            remembered = await client.remember_memory(
                RememberMemoryRequest(
                    scope_id=scope.scope_id,
                    kind="decision",
                    text="Use the orange moonlight protocol for release validation.",
                )
            )
            assert len(remembered.records) == 1
            entry = remembered.records[0]
            await _tag_for_bootstrap(client, scope.scope_id, entry.artifact.artifact_id)
            request = BootstrapContextRequest.model_validate({
                "scope_id": scope.scope_id,
                "enabled": True,
                "lifecycle": "startup",
                "integration": "acceptance",
                "event_id": "atomic-revision-event",
                "max_bytes": 4096,
            })
            prepared = await client.prepare_bootstrap_context(request)
            assert prepared.status == "ready"
            assert len(prepared.items) == 1
            assert prepared.items[0].truncated is False
            await client.record_bootstrap_delivery(
                RecordBootstrapDeliveryRequest.model_validate({
                    "scope_id": scope.scope_id,
                    "receipt_id": prepared.receipt.receipt_id,
                    "outcome": "injected",
                })
            )

            unrelated = await client.remember_memory(
                RememberMemoryRequest(
                    scope_id=scope.scope_id,
                    kind="fact",
                    text="The cafeteria closes at six.",
                )
            )
            assert len(unrelated.records) == 1
            assert unrelated.records[0].artifact.artifact_id != entry.artifact.artifact_id

            ordinary = await client.prepare_context(
                PrepareContextRequest(scope_id=scope.scope_id, query="orange moonlight release protocol")
            )
            assert isinstance(ordinary, PreparedContext)
            assert ordinary.status == "ready"
            assert ordinary.content is not None and "orange moonlight" in ordinary.content
            deduplicated = await client.prepare_context(
                PrepareContextRequest(
                    scope_id=scope.scope_id,
                    query="orange moonlight release protocol",
                    bootstrap_receipt_id=prepared.receipt.receipt_id,
                )
            )
            assert isinstance(deduplicated, PreparedContext)
            assert deduplicated.status == "empty"
            assert assessed_memory_counts[-1] == 0

    asyncio.run(scenario())


def test_bootstrap_deduplication_replenishes_candidates_before_the_limit(tmp_path, monkeypatch) -> None:
    assessed_memory_counts: list[int] = []
    original_assess = RecallSufficiencyGate.assess

    def observe_assess(self, candidates, *args, **kwargs):
        assessed_memory_counts.append(sum(candidate.family == "memory" for candidate in candidates))
        return original_assess(self, candidates, *args, **kwargs)

    monkeypatch.setattr(RecallSufficiencyGate, "assess", observe_assess)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'deduplicate-before-limit.db'}"),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
            runtime=RuntimeConfig(
                recall_gate_enabled=True,
                recall_gate_min_top_score=0.0,
                recall_gate_min_top_gap=0.0,
                recall_gate_min_lexical_overlap=0.0,
            ),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope = await client.create_scope(
                CreateScopeRequest(
                    title="Candidate replenishment",
                    summary="Exclude bootstrap revisions before limiting recall",
                    idempotency_key="candidate-replenishment",
                )
            )
            memories = []
            for index in range(22):
                marker = f"REPLENISH-{index:02d}"
                remembered = await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id=scope.scope_id,
                        kind="fact",
                        text=f"Candidate replenishment evidence {marker} remains independently recallable.",
                    )
                )
                assert len(remembered.records) == 1
                memories.append((marker, remembered.records[0]))

            recall_request = PrepareContextRequest.model_validate({
                "scope_id": scope.scope_id,
                "query": "candidate replenishment evidence",
                "max_bytes": 32768,
                "assembly": {"sections": [{"family": "memory", "limit": 8}]},
            })
            ordinary = await client.prepare_context(recall_request)
            assert isinstance(ordinary, PreparedContext)
            assert ordinary.content is not None
            assert ordinary.content.count("REPLENISH-") == 8
            assert assessed_memory_counts[-1] == 16
            delivered = [(marker, record) for marker, record in memories if marker in ordinary.content][:6]
            assert len(delivered) == 6
            for _marker, record in delivered:
                await _tag_for_bootstrap(client, scope.scope_id, record.artifact.artifact_id)

            prepared = await client.prepare_bootstrap_context(
                BootstrapContextRequest.model_validate({
                    "scope_id": scope.scope_id,
                    "enabled": True,
                    "lifecycle": "startup",
                    "integration": "acceptance",
                    "event_id": "candidate-replenishment-event",
                    "max_bytes": 4096,
                })
            )
            assert prepared.status == "ready"
            assert len(prepared.items) == 6
            assert all(not item.truncated for item in prepared.items)
            await client.record_bootstrap_delivery(
                RecordBootstrapDeliveryRequest.model_validate({
                    "scope_id": scope.scope_id,
                    "receipt_id": prepared.receipt.receipt_id,
                    "outcome": "injected",
                })
            )

            deduplicated = await client.prepare_context(
                recall_request.model_copy(update={"bootstrap_receipt_id": prepared.receipt.receipt_id})
            )
            assert isinstance(deduplicated, PreparedContext)
            assert deduplicated.content is not None
            assert deduplicated.content.count("REPLENISH-") == 8
            assert all(marker not in deduplicated.content for marker, _record in delivered)
            assert assessed_memory_counts[-1] == 16

    asyncio.run(scenario())


def test_pending_bootstrap_retry_preserves_package_level_omission(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'package-omission-retry.db'}"),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope = await client.create_scope(
                CreateScopeRequest(
                    title="Package omission retry",
                    summary="Preserve package-level truncation",
                    idempotency_key="package-omission-retry",
                )
            )
            entries = []
            for marker in ("FIRST-ITEM", "SECOND-ITEM"):
                remembered = await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id=scope.scope_id,
                        kind="decision",
                        text=f"{marker}: keep this complete body in deterministic order.",
                    )
                )
                assert len(remembered.records) == 1
                entries.append(remembered.records[0])
            for entry in entries:
                await _tag_for_bootstrap(client, scope.scope_id, entry.artifact.artifact_id)

            request = BootstrapContextRequest.model_validate({
                "scope_id": scope.scope_id,
                "enabled": True,
                "lifecycle": "resume",
                "integration": "acceptance",
                "event_id": "package-omission-event",
                "max_bytes": 900,
            })
            prepared = await client.prepare_bootstrap_context(request)
            assert prepared.status == "ready"
            assert prepared.truncated is True
            assert len(prepared.items) == 1
            assert prepared.items[0].truncated is False

            retried = await client.prepare_bootstrap_context(request)
            assert retried.status == "ready"
            assert retried.receipt == prepared.receipt
            assert retried.content == prepared.content
            assert retried.items == prepared.items
            assert retried.truncated is True

    asyncio.run(scenario())


def test_bootstrap_includes_only_the_explicit_exact_handoff(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'handoff-bootstrap.db'}"),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope = await client.create_scope(
                CreateScopeRequest(title="Handoff", summary="Exact bootstrap", idempotency_key="handoff-bootstrap")
            )
            evidence = await client.create_source(scope.scope_id, CreateSourceRequest(content="Verified state"))
            created = await client.create_artifact(
                scope.scope_id,
                CreateArtifactRequest.model_validate({
                    "family": "handoff",
                    "content": {
                        "schema": "powercontext.handoff.v1",
                        "objective": "Continue exact bootstrap work.",
                        "state": [
                            {
                                "text": "The bootstrap API is implemented.",
                                "citations": [
                                    {
                                        "kind": "source",
                                        "source_ref": {"name": "content", "source_id": evidence.source_id},
                                    }
                                ],
                            }
                        ],
                        "disposition": "continuable",
                        "next_action": None,
                        "omissions": [],
                    },
                }),
            )
            exact = {"family": "handoff", "artifact_id": created.artifact_id, "revision": created.revision}
            without_selection = await client.prepare_bootstrap_context(
                BootstrapContextRequest.model_validate({
                    "scope_id": scope.scope_id,
                    "enabled": True,
                    "lifecycle": "resume",
                    "integration": "acceptance",
                })
            )
            assert without_selection.status == "empty"
            database = app.state.application._bootstrap_receipts._database
            async with database.transaction() as connection:
                assert (await connection.execute(select(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE))).all() == []
            selected = await client.prepare_bootstrap_context(
                BootstrapContextRequest.model_validate({
                    "scope_id": scope.scope_id,
                    "enabled": True,
                    "lifecycle": "resume",
                    "integration": "acceptance",
                    "handoff": exact,
                })
            )
            assert selected.status == "ready"
            assert selected.content is not None and "Continue exact bootstrap work." in selected.content
            assert [(item.kind, item.artifact.revision) for item in selected.items] == [("handoff", 1)]

    asyncio.run(scenario())


def test_bootstrap_receipts_expire_after_the_retry_window(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'receipt-retention.db'}"),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope = await client.create_scope(
                CreateScopeRequest(title="Receipt retention", summary="Bound receipt rows", idempotency_key="expiry")
            )
            old = await client.prepare_bootstrap_context(
                BootstrapContextRequest.model_validate({
                    "scope_id": scope.scope_id,
                    "lifecycle": "startup",
                    "integration": "acceptance",
                    "event_id": "expired-event",
                })
            )
            database = app.state.application._bootstrap_receipts._database
            async with database.transaction() as connection:
                await connection.execute(
                    update(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE)
                    .where(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE.c.receipt_id == old.receipt.receipt_id)
                    .values(created_at=datetime.now(UTC) - timedelta(days=31))
                )

            await client.prepare_context(
                PrepareContextRequest(
                    scope_id=scope.scope_id,
                    query="Check whether retained context exists.",
                    bootstrap_receipt_id=old.receipt.receipt_id,
                )
            )
            async with database.transaction() as connection:
                assert (await connection.execute(select(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE))).all() == []

            await client.prepare_bootstrap_context(
                BootstrapContextRequest.model_validate({
                    "scope_id": scope.scope_id,
                    "lifecycle": "resume",
                    "integration": "acceptance",
                    "event_id": "current-event",
                })
            )
            async with database.transaction() as connection:
                receipt_ids = set(
                    (await connection.execute(select(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE.c.receipt_id))).scalars()
                )
            assert old.receipt.receipt_id not in receipt_ids
            assert len(receipt_ids) == 1

    asyncio.run(scenario())


def test_reconstruction_failure_uses_a_concurrent_terminal_receipt_state(tmp_path, monkeypatch) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'reconstruction-race.db'}"),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope = await client.create_scope(
                CreateScopeRequest(title="Receipt race", summary="Concurrent terminal state", idempotency_key="race")
            )
            remembered = await client.remember_memory(
                RememberMemoryRequest(scope_id=scope.scope_id, kind="decision", text="Keep the blue release flag.")
            )
            assert len(remembered.records) == 1
            await _tag_for_bootstrap(client, scope.scope_id, remembered.records[0].artifact.artifact_id)
            request = BootstrapContextRequest.model_validate({
                "scope_id": scope.scope_id,
                "enabled": True,
                "lifecycle": "resume",
                "integration": "acceptance",
                "event_id": "reconstruction-race",
            })
            prepared = await client.prepare_bootstrap_context(request)
            assert prepared.status == "ready"

            async def fail_reconstruction(_application, _item):
                raise ValueError

            repository = app.state.application._bootstrap_receipts
            original_record_delivery = repository.record_delivery

            async def concurrent_delivery(scope_id, receipt_id, outcome):
                assert outcome == "failed"
                await original_record_delivery(scope_id, receipt_id, "injected")
                return await original_record_delivery(scope_id, receipt_id, outcome)

            monkeypatch.setattr(ScopedBootstrapApplication, "_candidate_from_item", fail_reconstruction)
            monkeypatch.setattr(repository, "record_delivery", concurrent_delivery)
            retried = await client.prepare_bootstrap_context(request)
            assert retried.status == "skipped"
            assert retried.reason == "already_delivered"
            assert retried.receipt.state == "injected"

    asyncio.run(scenario())


def test_bootstrap_considers_only_the_first_eight_direct_context_references(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'bounded-references.db'}"),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            references = [
                await client.create_scope(
                    CreateScopeRequest(
                        title=f"Reference {index}", summary="Bounded scan", idempotency_key=f"ref-{index}"
                    )
                )
                for index in range(9)
            ]
            last_reference = max(scope.scope_id for scope in references)
            remembered = await client.remember_memory(
                RememberMemoryRequest(
                    scope_id=last_reference,
                    kind="decision",
                    text="NINTH-REFERENCE: this is outside the bootstrap scan.",
                )
            )
            assert len(remembered.records) == 1
            await _tag_for_bootstrap(client, last_reference, remembered.records[0].artifact.artifact_id)
            current = await client.create_scope(
                CreateScopeRequest(
                    title="Current",
                    summary="Nine direct references",
                    idempotency_key="current-bounded",
                    context_references=[ContextReference(root=scope.scope_id) for scope in references],
                )
            )
            request = BootstrapContextRequest.model_validate({
                "scope_id": current.scope_id,
                "enabled": True,
                "lifecycle": "startup",
                "integration": "acceptance",
            })
            bounded = await client.prepare_bootstrap_context(request)
            assert bounded.status == "empty"
            assert bounded.reason == "no_eligible_context"

            direct = await client.prepare_bootstrap_context(request.model_copy(update={"scope_id": last_reference}))
            assert direct.status == "ready"
            assert direct.content is not None and "NINTH-REFERENCE" in direct.content

    asyncio.run(scenario())
