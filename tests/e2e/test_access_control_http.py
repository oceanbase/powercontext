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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from powercontext.builtin.artifacts.handoff import HandoffDraft, HandoffGenerationRequest, HandoffStatement
from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.builtin.sources import ContentSource
from powercontext.client import ForbiddenResponseError, PowerContextClient, UnavailableResponseError
from powercontext.http import (
    AccessAction,
    AccessResourceType,
    AcknowledgeHandoffRequest,
    ActivateHandoffRequest,
    CaptureContentSourceRequest,
    CommitHandoffRequest,
    ContinueHandoffRequest,
    CreateAccessBindingRequest,
    CreateArtifactRequest,
    CreateScopeRequest,
    CreateSourceRequest,
    FinalizeHandoffRequest,
    HandoffSelection,
    ListAccessResourcesRequest,
    ListArtifactsRequest,
    ListMemoryEntriesRequest,
    ReplaceArtifactRequest,
    RevokeAccessBindingRequest,
)
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.authz import AccessControlService, MemoryEntrySelector, PrincipalRef, ResourceRef
from powercontext.server.authz.composition import open_builtin_access_control, open_casbin_access_control
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    DashboardConfig,
    McpConfig,
    MetricsConfig,
    ServerSettings,
)

ADMIN = PrincipalRef(type="service", id="admin")
RECEIVER = PrincipalRef(type="user", id="bob")
VIEWER = PrincipalRef(type="user", id="alice")
DEPLOYMENT_ID = "access-control-http-e2e"


class _DeterministicHandoffPipeline:
    async def generate(self, request: HandoffGenerationRequest, /) -> HandoffDraft:
        citations = tuple(item.citation for item in request.evidence)
        return HandoffDraft(
            objective=request.objective,
            state=(HandoffStatement(text="The logical Handoff is ready for its receiver.", citations=citations),),
            disposition="continuable",
            next_action=HandoffStatement(text="Inspect the selected Revision and its evidence.", citations=citations),
        )


class _ContentMemoryPipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="fact", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def test_logical_handoff_grant_and_revoke_cross_the_public_server_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
        async with open_builtin_access_control(
            database,
            bootstrap_administrators=(ADMIN,),
            deployment_id=DEPLOYMENT_ID,
        ) as access_control:
            async with _client(
                _app(database, access_control, ADMIN, "admin-token", tmp_path / "admin-scheduler.db"),
                "admin-token",
            ) as admin:
                scope = await admin.create_scope(
                    CreateScopeRequest(
                        title="Access control E2E",
                        summary="Logical Handoff authorization across the public Server boundary.",
                        idempotency_key="access-control-e2e",
                    )
                )
                scope_id = scope.scope_id
                captured = await admin.capture_content_source(
                    CaptureContentSourceRequest(
                        scope_id=scope_id,
                        source_id="handoff-boundary",
                        content="The receiver may read every Revision of one explicitly shared logical Handoff.",
                    )
                )
                activation = await admin.activate_handoff(
                    ActivateHandoffRequest(
                        scope_id=scope_id,
                        boundary_source=captured.source,
                        objective="Transfer one committed logical Handoff.",
                    )
                )
                assert activation.draft is not None
                prepared = await admin.finalize_handoff(
                    FinalizeHandoffRequest(scope_id=scope_id, draft=activation.draft)
                )
                first_committed = await admin.commit_handoff(CommitHandoffRequest(scope_id=scope_id, handoff=prepared))
                resource = {
                    "type": "artifact",
                    "scope_id": scope_id,
                    "identity": {
                        "family": first_committed.reference.family,
                        "artifact_id": first_committed.reference.artifact_id,
                    },
                    "selector": None,
                }
                binding = await admin.create_access_binding(
                    CreateAccessBindingRequest.model_validate({
                        "subject": {
                            "type": RECEIVER.type,
                            "id": RECEIVER.id,
                        },
                        "resource": resource,
                        "role": "handoff.receiver",
                        "idempotency_key": "share-logical-handoff-with-bob",
                    })
                )
                revised_draft = activation.draft.model_copy(
                    update={"objective": "Transfer the next Revision through the existing logical share."}
                )
                revised_prepared = await admin.finalize_handoff(
                    FinalizeHandoffRequest(scope_id=scope_id, draft=revised_draft)
                )
                committed = await admin.commit_handoff(
                    CommitHandoffRequest(scope_id=scope_id, handoff=revised_prepared)
                )
                assert committed.reference.revision == first_committed.reference.revision + 1

            async with _client(
                _app(database, access_control, RECEIVER, "receiver-token", tmp_path / "receiver-scheduler.db"),
                "receiver-token",
            ) as receiver:
                exact = await receiver.continue_handoff(
                    ContinueHandoffRequest(
                        scope_id=scope_id,
                        selection=HandoffSelection.EXACT,
                        revision=first_committed.reference,
                    )
                )
                assert exact.selected_revision == first_committed.reference
                assert exact.content is not None
                assert exact.content.state[0].citations
                assert exact.evidence_checks
                assert all(check.status == "available" for check in exact.evidence_checks)
                receipt = await receiver.acknowledge_handoff(
                    AcknowledgeHandoffRequest.model_validate({
                        "scope_id": scope_id,
                        "source_id": "receiver-acknowledgement",
                        "receiver": RECEIVER.id,
                        "status": "accepted",
                        "selection": "exact",
                        "receiver_checks": {
                            "live_state": "confirmed",
                            "capability": "confirmed",
                            "authorization": "confirmed",
                        },
                        "revision": first_committed.reference,
                    })
                )
                assert receipt.resolution.selected_revision == first_committed.reference

                latest = await receiver.continue_handoff(
                    ContinueHandoffRequest(scope_id=scope_id, selection=HandoffSelection.LATEST)
                )
                assert latest.selected_revision == committed.reference
                assert latest.evidence_checks
                assert all(check.status == "available" for check in latest.evidence_checks)
                later_exact = await receiver.continue_handoff(
                    ContinueHandoffRequest(
                        scope_id=scope_id,
                        selection=HandoffSelection.EXACT,
                        revision=committed.reference,
                    )
                )
                assert later_exact.selected_revision == committed.reference
                assert all(check.status == "available" for check in later_exact.evidence_checks)
                visible = await receiver.list_access_resources(
                    ListAccessResourcesRequest(
                        action=AccessAction.ARTIFACT_READ,
                        resource_type=AccessResourceType.ARTIFACT,
                        family="handoff",
                    )
                )
                assert visible.total == 1
                assert visible.items[0].model_dump(mode="json") == resource

            async with _client(
                _app(database, access_control, ADMIN, "admin-token", tmp_path / "revoke-scheduler.db"),
                "admin-token",
            ) as admin:
                revoked = await admin.revoke_access_binding(
                    RevokeAccessBindingRequest(
                        binding_id=binding.binding_id,
                        expected_version=binding.version,
                        idempotency_key="revoke-receiver-binding",
                    )
                )
                assert revoked.state == "revoked"

            async with _client(
                _app(database, access_control, RECEIVER, "receiver-token", tmp_path / "denied-scheduler.db"),
                "receiver-token",
            ) as receiver:
                with pytest.raises(ForbiddenResponseError):
                    await receiver.continue_handoff(
                        ContinueHandoffRequest(
                            scope_id=scope_id,
                            selection=HandoffSelection.EXACT,
                            revision=committed.reference,
                        )
                    )
                visible = await receiver.list_access_resources(
                    ListAccessResourcesRequest(
                        action=AccessAction.ARTIFACT_READ,
                        resource_type=AccessResourceType.ARTIFACT,
                        family="handoff",
                    )
                )
                assert visible.total == 0
                assert visible.items == []

    asyncio.run(scenario())


def test_base_source_and_artifact_routes_preserve_access_boundaries(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'base-access-runtime.db'}")
        async with open_builtin_access_control(
            database,
            bootstrap_administrators=(ADMIN,),
            deployment_id=DEPLOYMENT_ID,
        ) as access_control:
            async with _client(
                _app(database, access_control, ADMIN, "admin-token", tmp_path / "base-admin-scheduler.db"),
                "admin-token",
            ) as admin:
                scope = await admin.create_scope(
                    CreateScopeRequest(
                        title="Base Access API",
                        summary="Exercise Source and Artifact routes through the enforced Access boundary.",
                        idempotency_key="base-access-api-scope",
                    )
                )
                await admin.create_access_binding(
                    CreateAccessBindingRequest.model_validate({
                        "subject": {"type": RECEIVER.type, "id": RECEIVER.id},
                        "resource": {"type": "scope", "scope_id": scope.scope_id},
                        "role": "scope.contributor",
                        "idempotency_key": "base-access-bob-contributor",
                    })
                )

            async with _client(
                _app(database, access_control, RECEIVER, "receiver-token", tmp_path / "base-owner-scheduler.db"),
                "receiver-token",
            ) as owner:
                source = await owner.create_source(
                    scope.scope_id,
                    CreateSourceRequest(content={"statement": "An exact viewer cannot enumerate the Scope."}),
                )
                created = await owner.create_artifact(
                    scope.scope_id,
                    CreateArtifactRequest.model_validate({
                        "family": "experience",
                        "content": {
                            "situation": "A new base API was merged into an Access-enabled Server.",
                            "action": "Exercise the API through the public authorization boundary.",
                            "outcome": "The creator retained control of the logical Artifact.",
                            "lesson": "Every new write surface must establish its owner.",
                        },
                    }),
                )
                replaced = await owner.replace_artifact(
                    scope.scope_id,
                    "experience",
                    created.artifact_id,
                    ReplaceArtifactRequest.model_validate({
                        "content": {
                            "situation": "A new base API was merged into an Access-enabled Server.",
                            "action": "Verify the owner can replace the Artifact through the public boundary.",
                            "outcome": "The owner committed revision two.",
                            "lesson": "Creation and later mutation must use the same logical Access identity.",
                        }
                    }),
                    expected_etag='"revision:1"',
                )
                assert replaced.revision == 2
                owner_relation = await access_control.artifact_owner(
                    ResourceRef.artifact(scope.scope_id, family="experience", artifact_id=created.artifact_id)
                )
                assert owner_relation is not None
                assert owner_relation.owner == RECEIVER

                memory = await owner.create_artifact(
                    scope.scope_id,
                    CreateArtifactRequest.model_validate({
                        "family": "memory",
                        "content": {"entries": [{"kind": "preference", "text": "Use concise answers."}]},
                    }),
                )
                memory_head = await owner.get_artifact(scope.scope_id, "memory", memory.artifact_id)
                assert memory_head is not None
                first_entry_id = memory_head.content["manifest"]["entries"][0]["entry_id"]
                revised_memory = await owner.replace_artifact(
                    scope.scope_id,
                    "memory",
                    memory.artifact_id,
                    ReplaceArtifactRequest.model_validate({
                        "content": {
                            "entries": [
                                {
                                    "entry_id": first_entry_id,
                                    "kind": "preference",
                                    "text": "Use concise Chinese answers.",
                                },
                                {"kind": "constraint", "text": "Do not expose credentials."},
                            ]
                        }
                    }),
                    expected_etag='"revision:1"',
                )
                assert revised_memory.revision == 2
                memory_entry_ids = {entry["entry_id"] for entry in revised_memory.content["manifest"]["entries"]}
                assert len(memory_entry_ids) == 2
                for entry_id in memory_entry_ids:
                    relation = await access_control.artifact_owner(
                        ResourceRef.artifact(
                            scope.scope_id,
                            family="memory",
                            artifact_id=memory.artifact_id,
                            selector=MemoryEntrySelector(entry_id=entry_id),
                        )
                    )
                    assert relation is not None
                    assert relation.owner == RECEIVER

            async with _client(
                _app(database, access_control, ADMIN, "admin-token", tmp_path / "base-share-scheduler.db"),
                "admin-token",
            ) as admin:
                await admin.create_access_binding(
                    CreateAccessBindingRequest.model_validate({
                        "subject": {"type": VIEWER.type, "id": VIEWER.id},
                        "resource": {
                            "type": "artifact",
                            "scope_id": scope.scope_id,
                            "identity": {"family": "experience", "artifact_id": created.artifact_id},
                            "selector": None,
                        },
                        "role": "artifact.viewer",
                        "idempotency_key": "base-access-alice-viewer",
                    })
                )

            async with _client(
                _app(database, access_control, VIEWER, "viewer-token", tmp_path / "base-viewer-scheduler.db"),
                "viewer-token",
            ) as viewer:
                head = await viewer.get_artifact(scope.scope_id, "experience", created.artifact_id)
                assert head is not None
                assert head.revision == 2
                assert (
                    await viewer.get_artifact_revision(scope.scope_id, "experience", created.artifact_id, 1)
                ).revision == 1

                with pytest.raises(ForbiddenResponseError):
                    await viewer.list_artifacts(scope.scope_id, "experience", ListArtifactsRequest())
                with pytest.raises(ForbiddenResponseError):
                    await viewer.get_source(scope.scope_id, "content", source.source_id)
                with pytest.raises(ForbiddenResponseError):
                    await viewer.get_artifact(scope.scope_id, "memory", memory.artifact_id)
                with pytest.raises(ForbiddenResponseError):
                    await viewer.replace_artifact(
                        scope.scope_id,
                        "experience",
                        created.artifact_id,
                        ReplaceArtifactRequest.model_validate({
                            "content": {
                                "situation": "An exact viewer attempted a write.",
                                "action": "Reject the replacement before it reaches the Runtime.",
                                "outcome": "The Artifact remained unchanged.",
                                "lesson": "Read grants never imply mutation rights.",
                            }
                        }),
                        expected_etag='"revision:2"',
                    )

    asyncio.run(scenario())


def test_scheduled_memory_processing_uses_the_static_service_principal_as_owner(tmp_path: Path) -> None:
    async def scenario() -> None:
        token = "scheduled-static-token"  # noqa: S105 - test credential.
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'scheduled-runtime.db'}"),
                runtime=RuntimeConfig(schedule_seconds=0.02),
                access=AccessControlConfig(
                    mode="enforced",
                    deployment_id="scheduled-access-e2e",
                ),
                auth=BearerAuthConfig(token=SecretStr(token)),
                dashboard=DashboardConfig(enabled=False),
                metrics=MetricsConfig(enabled=False),
                mcp=McpConfig(enabled=False),
            ),
            scheduler_path=tmp_path / "scheduled-access.db",
            candidate_pipeline=_ContentMemoryPipeline(),
        )
        async with _client(app, token) as client:
            scope = await client.create_scope(
                CreateScopeRequest(
                    title="Scheduled Access",
                    summary="Scheduled Memory ownership under enforced Access control.",
                    idempotency_key="scheduled-access",
                )
            )
            await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope.scope_id,
                    source_id="scheduled-source",
                    content="The scheduled service owns this extracted Memory entry.",
                )
            )
            entries = None
            for _ in range(100):
                try:
                    entries = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope.scope_id))
                except UnavailableResponseError as error:
                    assert error.code == "artifact_owner_pending"
                    await asyncio.sleep(0.02)
                    continue
                if entries.entries:
                    break
                await asyncio.sleep(0.02)
            assert entries is not None
            assert len(entries.entries) == 1

            visible = await client.list_access_resources(
                ListAccessResourcesRequest(
                    action=AccessAction.ARTIFACT_READ,
                    resource_type=AccessResourceType.ARTIFACT,
                    family="memory",
                )
            )
            assert visible.total == 1
            resource = visible.items[0].model_dump(mode="json")
            assert resource["identity"]["family"] == "memory"
            assert resource["selector"]["entry_id"] == entries.entries[0].citation.entry_id

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["builtin", "casbin"])
@pytest.mark.parametrize("family", ["experience", "skill"])
def test_resource_discovery_includes_base_artifacts_under_scope_grants(
    tmp_path: Path, provider: str, family: str
) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'discovery.db'}")
        open_access = open_builtin_access_control if provider == "builtin" else open_casbin_access_control
        async with open_access(
            database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT_ID
        ) as access_control:
            async with _client(
                _app(database, access_control, ADMIN, "admin-token", tmp_path / "admin-scheduler.db"),
                "admin-token",
            ) as admin:
                scopes = [
                    await admin.create_scope(
                        CreateScopeRequest(title=title, summary="Resource discovery", idempotency_key=title)
                    )
                    for title in ("visible", "hidden")
                ]
                content = (
                    {
                        "situation": "An Artifact was created through the base API.",
                        "action": "Discover it through a Scope grant.",
                        "outcome": "The readable Artifact appears in the list.",
                        "lesson": "Discovery includes every committed Artifact.",
                    }
                    if family == "experience"
                    else {
                        "name": "resource-discovery",
                        "description": "Check inherited Artifact access",
                        "instructions": "Compare exact reads with resource discovery.",
                        "validation": ["Authorized Artifacts appear in the list"],
                    }
                )
                artifacts = [
                    await admin.create_artifact(
                        scope.scope_id,
                        CreateArtifactRequest.model_validate({"family": family, "content": content}),
                    )
                    for scope in (scopes[0], scopes[0], scopes[1])
                ]
                await admin.create_access_binding(
                    CreateAccessBindingRequest.model_validate({
                        "subject": {"type": VIEWER.type, "id": VIEWER.id},
                        "resource": {"type": "scope", "scope_id": scopes[0].scope_id},
                        "role": "scope.viewer",
                        "idempotency_key": "visible-scope-viewer",
                    })
                )
                for principal in (VIEWER, RECEIVER):
                    await admin.create_access_binding(
                        CreateAccessBindingRequest.model_validate({
                            "subject": {"type": principal.type, "id": principal.id},
                            "resource": {
                                "type": "artifact",
                                "scope_id": scopes[0].scope_id,
                                "identity": {"family": family, "artifact_id": artifacts[0].artifact_id},
                            },
                            "role": "artifact.viewer",
                            "idempotency_key": f"direct-viewer-{principal.id}",
                        })
                    )

            query = ListAccessResourcesRequest(
                action=AccessAction.ARTIFACT_READ,
                resource_type=AccessResourceType.ARTIFACT,
                family=family,
                limit=1,
            )
            async with _client(
                _app(database, access_control, VIEWER, "viewer-token", tmp_path / "viewer-scheduler.db"),
                "viewer-token",
            ) as viewer:
                assert await viewer.get_artifact(scopes[0].scope_id, family, artifacts[1].artifact_id) is not None
                first = await viewer.list_access_resources(query)
                assert first.total == 2
                assert len(first.items) == 1
                assert first.next_cursor is not None
                second = await viewer.list_access_resources(query.model_copy(update={"cursor": first.next_cursor}))
                assert second.total == 2
                assert len(second.items) == 1
                assert second.next_cursor is None
                assert {
                    item.model_dump(mode="json")["identity"]["artifact_id"] for item in first.items + second.items
                } == {artifact.artifact_id for artifact in artifacts[:2]}
                with pytest.raises(ForbiddenResponseError):
                    await viewer.get_artifact(scopes[1].scope_id, family, artifacts[2].artifact_id)

            async with _client(
                _app(database, access_control, RECEIVER, "receiver-token", tmp_path / "receiver-scheduler.db"),
                "receiver-token",
            ) as receiver:
                direct = await receiver.list_access_resources(query)
                assert direct.total == 1
                assert direct.items[0].model_dump(mode="json")["identity"]["artifact_id"] == artifacts[0].artifact_id
                with pytest.raises(ForbiddenResponseError):
                    await receiver.get_artifact(scopes[0].scope_id, family, artifacts[1].artifact_id)

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["builtin", "casbin"])
def test_server_administrators_discover_managed_resources_without_content_access(tmp_path: Path, provider: str) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'admin-discovery.db'}")
        open_access = open_builtin_access_control if provider == "builtin" else open_casbin_access_control
        async with open_access(
            database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT_ID
        ) as access_control:
            async with _client(
                _app(database, access_control, ADMIN, "admin-token", tmp_path / "admin-scheduler.db"),
                "admin-token",
            ) as admin:
                scope = await admin.create_scope(
                    CreateScopeRequest(title="Managed Scope", summary="Admin discovery", idempotency_key="managed")
                )
                expected_scopes = {item.scope_id for item in (await admin.list_scopes()).items}
                artifact = await admin.create_artifact(
                    scope.scope_id,
                    CreateArtifactRequest.model_validate({
                        "family": "experience",
                        "content": {
                            "situation": "A server administrator needs to manage access.",
                            "action": "List manageable resources.",
                            "outcome": "Resource identities are visible without content access.",
                            "lesson": "Administration does not imply content read.",
                        },
                    }),
                )
                await admin.create_access_binding(
                    CreateAccessBindingRequest.model_validate({
                        "subject": {"type": RECEIVER.type, "id": RECEIVER.id},
                        "resource": {"type": "server", "deployment_id": DEPLOYMENT_ID},
                        "role": "server.admin",
                        "idempotency_key": "bob-server-admin",
                    })
                )
                for principal in (RECEIVER, VIEWER):
                    await admin.create_access_binding(
                        CreateAccessBindingRequest.model_validate({
                            "subject": {"type": principal.type, "id": principal.id},
                            "resource": {"type": "scope", "scope_id": scope.scope_id},
                            "role": "scope.admin",
                            "idempotency_key": f"scope-admin-{principal.id}",
                        })
                    )

            query = ListAccessResourcesRequest(
                action=AccessAction.SCOPE_ADMIN, resource_type=AccessResourceType.SCOPE, limit=1
            )
            async with _client(
                _app(database, access_control, RECEIVER, "receiver-token", tmp_path / "receiver-scheduler.db"),
                "receiver-token",
            ) as receiver:
                discovered: list[str] = []
                while True:
                    page = await receiver.list_access_resources(query)
                    assert page.total == len(expected_scopes)
                    assert len(page.items) == 1
                    discovered.extend(item.model_dump(mode="json")["scope_id"] for item in page.items)
                    if page.next_cursor is None:
                        break
                    query = query.model_copy(update={"cursor": page.next_cursor})
                assert set(discovered) == expected_scopes
                assert len(discovered) == len(expected_scopes)
                manageable = await receiver.list_access_resources(
                    ListAccessResourcesRequest(
                        action=AccessAction.ARTIFACT_SHARE,
                        resource_type=AccessResourceType.ARTIFACT,
                        family="experience",
                    )
                )
                assert manageable.total == 1
                assert manageable.items[0].model_dump(mode="json")["identity"]["artifact_id"] == artifact.artifact_id
                for action, resource_type in (
                    (AccessAction.SCOPE_READ, AccessResourceType.SCOPE),
                    (AccessAction.ARTIFACT_READ, AccessResourceType.ARTIFACT),
                ):
                    readable = await receiver.list_access_resources(
                        ListAccessResourcesRequest(action=action, resource_type=resource_type)
                    )
                    assert readable.total == 0
                    assert readable.items == []
                with pytest.raises(ForbiddenResponseError):
                    await receiver.get_artifact(scope.scope_id, "experience", artifact.artifact_id)

            async with _client(
                _app(database, access_control, VIEWER, "viewer-token", tmp_path / "viewer-scheduler.db"),
                "viewer-token",
            ) as viewer:
                direct = await viewer.list_access_resources(query.model_copy(update={"cursor": None}))
                assert direct.total == 1
                assert direct.items[0].model_dump(mode="json")["scope_id"] == scope.scope_id

    asyncio.run(scenario())


def _app(
    database: SQLiteConfig,
    access_control: AccessControlService,
    principal: PrincipalRef,
    token: str,
    scheduler_path: Path,
):
    authentication = StaticBearerAuthenticationProvider(token, principal)
    return create_server_app(
        settings=ServerSettings(
            database=database,
            access=AccessControlConfig(
                mode="enforced",
                deployment_id=DEPLOYMENT_ID,
            ),
            dashboard=DashboardConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
            mcp=McpConfig(enabled=False),
        ),
        scheduler_path=scheduler_path,
        handoff_pipeline=_DeterministicHandoffPipeline(),
        access_control=access_control,
        authentication_provider=authentication,
    )


@asynccontextmanager
async def _client(app, token: str) -> AsyncIterator[PowerContextClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        PowerContextClient(
            "http://testserver",
            token=token,
            http_client=transport,
            trust_transport_security=True,
        ) as client,
    ):
        yield client
