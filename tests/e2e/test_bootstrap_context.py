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

import httpx
from sqlalchemy import select

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import CONTEXT_BOOTSTRAP_RECEIPTS_TABLE
from powercontext.client import PowerContextClient
from powercontext.http import (
    BootstrapContextRequest,
    ContextReference,
    CreateArtifactRequest,
    CreateScopeRequest,
    CreateSourceRequest,
    ListMemoryEntriesRequest,
    PrepareContextRequest,
    RecordBootstrapDeliveryRequest,
    RememberMemoryRequest,
    ReplaceArtifactTagsRequest,
    ReviseMemoryEntryRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, MetricsConfig, ServerSettings


def test_bootstrap_is_opt_in_exact_bounded_idempotent_and_deduplicates_first_query(tmp_path) -> None:
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
                    + "中文上下文。" * 120,
                )
            )
            assert remembered.entry is not None
            entry = remembered.entry
            tags = await client.get_memory_entry_tags(
                scope.scope_id,
                entry.citation.memory_ref.artifact_id,
                entry.citation.entry_id,
            )
            assert tags is not None
            await client.replace_memory_entry_tags(
                scope.scope_id,
                entry.citation.memory_ref.artifact_id,
                entry.citation.entry_id,
                ReplaceArtifactTagsRequest.model_validate({"tags": ["bootstrap-context"]}),
                expected_etag=tags.etag,
            )
            secret = await client.remember_memory(
                RememberMemoryRequest(scope_id=scope.scope_id, kind="credential", text="do-not-inject-secret")
            )
            assert secret.entry is not None
            secret_tags = await client.get_memory_entry_tags(
                scope.scope_id,
                secret.entry.citation.memory_ref.artifact_id,
                secret.entry.citation.entry_id,
            )
            assert secret_tags is not None
            await client.replace_memory_entry_tags(
                scope.scope_id,
                secret.entry.citation.memory_ref.artifact_id,
                secret.entry.citation.entry_id,
                ReplaceArtifactTagsRequest.model_validate({"tags": ["bootstrap-context"]}),
                expected_etag=secret_tags.etag,
            )

            disabled = await client.prepare_bootstrap_context(
                BootstrapContextRequest.model_validate({
                    "scope_id": scope.scope_id,
                    "lifecycle": "startup",
                    "integration": "acceptance",
                    "event_id": "disabled-event",
                })
            )
            assert disabled.status == "skipped"
            assert disabled.reason == "disabled"
            assert disabled.receipt.state == "skipped"

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
            assert "do-not-inject-secret" not in prepared.content
            assert prepared.content_bytes == len(prepared.content.encode("utf-8")) <= 1024
            assert prepared.truncated is True
            assert len(prepared.items) == 1
            assert prepared.items[0].entry_version_id == entry.citation.entry_version_id
            assert prepared.items[0].truncated is True

            retried = await client.prepare_bootstrap_context(request)
            assert retried.content == prepared.content
            assert retried.receipt == prepared.receipt
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
                PrepareContextRequest(scope_id=scope.scope_id, query="regenerate OpenAPI bindings contract tests")
            )
            assert ordinary.status == "ready"
            deduplicated = await client.prepare_context(
                PrepareContextRequest(
                    scope_id=scope.scope_id,
                    query="regenerate OpenAPI bindings contract tests",
                    bootstrap_receipt_id=prepared.receipt.receipt_id,
                )
            )
            assert deduplicated.status == "empty"

            current_entries = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope.scope_id))
            current_citation = next(
                item.citation for item in current_entries.entries if item.citation.entry_id == entry.citation.entry_id
            )
            revised = await client.revise_memory_entry(
                ReviseMemoryEntryRequest(
                    scope_id=scope.scope_id,
                    citation=current_citation,
                    kind="decision",
                    text="BOOTSTRAP-ONLY: regenerate both Python and JavaScript bindings.",
                )
            )
            assert revised.entry is not None
            after_revision = await client.prepare_context(
                PrepareContextRequest(
                    scope_id=scope.scope_id,
                    query="regenerate Python JavaScript bindings",
                    bootstrap_receipt_id=prepared.receipt.receipt_id,
                )
            )
            assert after_revision.status == "ready"
            assert (
                after_revision.content is not None and revised.entry.citation.entry_version_id in after_revision.content
            )

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
            changed_again = await client.revise_memory_entry(
                ReviseMemoryEntryRequest(
                    scope_id=scope.scope_id,
                    citation=revised.entry.citation,
                    kind="decision",
                    text="SECOND-REVISION: this must not change a pending bootstrap retry.",
                )
            )
            assert changed_again.entry is not None
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

            database = app.state.application._bootstrap_receipts._database
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
            assert remembered.entry is not None
            citation = remembered.entry.citation
            tags = await client.get_memory_entry_tags(
                last_reference, citation.memory_ref.artifact_id, citation.entry_id
            )
            assert tags is not None
            await client.replace_memory_entry_tags(
                last_reference,
                citation.memory_ref.artifact_id,
                citation.entry_id,
                ReplaceArtifactTagsRequest.model_validate({"tags": ["bootstrap-context"]}),
                expected_etag=tags.etag,
            )
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
