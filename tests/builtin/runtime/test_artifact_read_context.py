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

"""The shared read context preserves scope, family and caller permissions."""

import asyncio
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from functools import partial
from typing import cast

import pytest
from sqlalchemy import insert, select

from powercontext.artifact_plugins import ArtifactDraft, GenerationContext
from powercontext.artifacts import ArtifactRef
from powercontext.artifacts.search import ArtifactSearchUnsupported
from powercontext.builtin.artifacts.profile.models import Profile, ProfileContent, ProfileGeneration
from powercontext.builtin.artifacts.topic_memory.models import TopicMemory, TopicMemoryContent
from powercontext.builtin.persistence.artifact_indexes import ArtifactIndexBuilder
from powercontext.builtin.persistence.artifacts import ArtifactRepository, RepositoryArtifactDraft
from powercontext.builtin.persistence.custom_artifacts import (
    StoredArtifactContent,
    custom_artifact_repository,
)
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.experience_index import SQLiteExperienceFTSIndex
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import ARTIFACT_FAMILY_SCHEMAS_TABLE, BUILTIN_TABLES, SOURCE_CURSORS_TABLE
from powercontext.builtin.records import BaseValueNotFoundError, InvalidBaseAccessRequestError
from powercontext.builtin.runtime.artifact_plugins import ArtifactPluginWorkerSpec, _commit_window
from powercontext.builtin.runtime.artifact_reads import ArtifactReadSession
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkAssignment
from powercontext.builtin.runtime.processing_execution import ScopeInvocation
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.server.authz import (
    AccessAuditContext,
    AccessControlService,
    AccessDecision,
    AccessDeniedError,
    AccessRole,
    BuiltinAuthorizationProvider,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.authz.profiles import register_custom_artifact_access_family
from powercontext.server.authz.repository import RelationalAccessRepository
from powercontext.server.processing_security import WorkerSecurity


class FilteredProvider(BuiltinAuthorizationProvider):
    async def check_batch(self, requests, /):
        decisions = await super().check_batch(requests)
        return tuple(
            AccessDecision(False, "restricted-artifact", decision.policy_revision)
            if request.resource.artifact_id == "a-hidden"
            else decision
            for request, decision in zip(requests, decisions, strict=True)
        )


def test_generation_reads_share_permission_filtered_candidates_and_exact_evidence(tmp_path):
    async def scenario():
        admin = PrincipalRef(type="service", id="admin")
        alice, bob = PrincipalRef(type="user", id="alice"), PrincipalRef(type="user", id="bob")
        audit = AccessAuditContext(transport="test", operation="artifact-reads")
        family = "context-items"
        register_custom_artifact_access_family(family)
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'reads.db'}")
        async with AsyncExitStack() as resources:
            profile = await resources.enter_async_context(SQLiteProfile.open(config, tables=BUILTIN_TABLES))
            access = await resources.enter_async_context(
                open_builtin_access_control(config, bootstrap_administrators=(admin,))
            )
            await access.create_binding(
                admin,
                CreateBinding(
                    subject=alice,
                    resource=ResourceRef.scope("scope"),
                    role=AccessRole.SCOPE_VIEWER,
                    idempotency_key="scope-viewer",
                ),
                context=audit,
            )
            repository = custom_artifact_repository(family)
            async with profile.database.transaction() as connection:
                await SQLiteExperienceFTSIndex().initialize(connection)
                topic = await ArtifactRepository((TopicMemory,)).create(
                    connection,
                    "scope",
                    "topic",
                    RepositoryArtifactDraft(
                        family="topic-memory",
                        content=TopicMemoryContent(title="Topic", summary="Summary", detail="Evidence"),
                    ),
                )
                await connection.execute(
                    insert(ARTIFACT_FAMILY_SCHEMAS_TABLE).values(
                        family=family,
                        schema_version="1",
                        declaration={"search_fields": ["text"], "schema_version": "1"},
                    )
                )
                for scope, identity in (("scope", "a-hidden"), ("scope", "b-visible"), ("other", "foreign")):
                    await repository.create(
                        connection,
                        scope,
                        identity,
                        RepositoryArtifactDraft(
                            family=family,
                            content=StoredArtifactContent(schema_version="1", content={"text": "数据库 TIMEOUT"}),
                        ),
                    )
            builder = ArtifactIndexBuilder(profile.database)
            await builder.refresh("scope", family)
            assert await builder.build("scope", family)
            for identity, owner in (("a-hidden", bob), ("b-visible", alice)):
                await access.establish_artifact_owner(
                    ResourceRef.artifact("scope", family=family, artifact_id=identity),
                    owner,
                    idempotency_key=identity,
                    context=audit,
                )
            builtin_reads = ArtifactReadSession(
                profile.database, "scope", (family,), access=access, principal=alice, audit=audit
            )
            builtin_page = await builtin_reads.list(family)
            assert {ref.artifact_id for ref in builtin_page.items} == {"a-hidden", "b-visible"}
            assert (await builtin_reads.read(family, "a-hidden", 1)).content["text"] == "数据库 TIMEOUT"
            builtin_matches = await builtin_reads.search(family, "数据库", limit=2)
            assert {hit.artifact_ref.artifact_id for hit in builtin_matches.matches} == {"a-hidden", "b-visible"}
            # An injected policy may narrow object visibility; it is not the Worker provider.
            access = AccessControlService(
                FilteredProvider(cast(RelationalAccessRepository, access.relationships)),
                relationships=access.relationships,
                audit=access.audit,
            )
            reads = ArtifactReadSession(
                profile.database, "scope", (family, "topic-memory"), access=access, principal=alice, audit=audit
            )
            context = GenerationContext(
                "scope", resources, reads.read, list_artifacts=reads.list, search_artifacts=reads.search
            )
            page = await context.list_artifacts(family, limit=1)
            assert [item.artifact_id for item in page.items] == ["b-visible"]
            assert page.next_cursor is None
            matches = await context.search_artifacts(family, "数据库", limit=1)
            assert [hit.artifact_ref.artifact_id for hit in matches.matches] == ["b-visible"]
            assert matches.artifacts is not None
            assert matches.artifacts[0].content.model_dump() == {"text": "数据库 TIMEOUT"}
            assert not reads.refs
            snapshot = await context.read_revision(matches.matches[0].artifact_ref)
            assert snapshot.content["text"] == "数据库 TIMEOUT"
            assert reads.refs == {(family, "b-visible", 1)}
            with pytest.raises(AccessDeniedError):
                await context.read_latest(family, "a-hidden")
            with pytest.raises(InvalidBaseAccessRequestError):
                await context.read_latest("undeclared", "any")
            with pytest.raises(InvalidBaseAccessRequestError):
                await context.list_artifacts("undeclared")
            with pytest.raises(InvalidBaseAccessRequestError):
                await context.search_artifacts("undeclared", "数据库")
            assert await context.read_latest(family, "foreign") is None
            denied = ArtifactReadSession(
                profile.database, "other", (family,), access=access, principal=alice, audit=audit
            )
            foreign_context = GenerationContext(
                "other", resources, denied.read, list_artifacts=denied.list, search_artifacts=denied.search
            )
            with pytest.raises(AccessDeniedError):
                await foreign_context.read_latest(family, "foreign")
            with pytest.raises(AccessDeniedError):
                await foreign_context.list_artifacts(family)
            with pytest.raises(AccessDeniedError):
                await foreign_context.search_artifacts(family, "数据库")
            topic_page = await context.list_artifacts("topic-memory")
            assert topic_page.items == (topic.as_ref(),)
            assert (await context.read_revision(topic.as_ref())).content["detail"] == "Evidence"
            assert ("topic-memory", "topic", 1) in reads.refs
            unauthorized = ArtifactReadSession(
                profile.database, "scope", ("topic-memory",), access=access, principal=bob, audit=audit
            )
            with pytest.raises(AccessDeniedError):
                await unauthorized.list("topic-memory")
            with pytest.raises(AccessDeniedError):
                await unauthorized.read("topic-memory", "topic", 1)

    asyncio.run(scenario())


def test_generation_context_reads_fixed_history_across_families_and_pages_current_references(tmp_path):
    async def scenario():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
        async with AsyncExitStack() as resources:
            profile = await resources.enter_async_context(SQLiteProfile.open(config, tables=BUILTIN_TABLES))
            items = custom_artifact_repository("context-items")
            roots = custom_artifact_repository("context-roots")
            async with profile.database.transaction() as connection:
                await SQLiteExperienceFTSIndex().initialize(connection)
                for family in ("context-items", "context-roots"):
                    await connection.execute(
                        insert(ARTIFACT_FAMILY_SCHEMAS_TABLE).values(
                            family=family,
                            schema_version="1",
                            declaration={
                                "schema_version": "1",
                                "search_fields": ["text"] if family == "context-items" else [],
                            },
                        )
                    )
                first = await items.create(
                    connection,
                    "scope",
                    "a",
                    RepositoryArtifactDraft(
                        family="context-items",
                        content=StoredArtifactContent(schema_version="1", content={"text": "旧证据"}),
                    ),
                )
                await roots.create(
                    connection,
                    "scope",
                    "root",
                    RepositoryArtifactDraft(
                        family="context-roots",
                        content=StoredArtifactContent(
                            schema_version="1", content={"ref": first.as_ref().model_dump(mode="json")}
                        ),
                    ),
                )
                for identity in ("b", "c"):
                    await items.create(
                        connection,
                        "scope",
                        identity,
                        RepositoryArtifactDraft(
                            family="context-items",
                            content=StoredArtifactContent(schema_version="1", content={"text": "其他"}),
                        ),
                    )
                builtin = await ArtifactRepository((Profile,)).create(
                    connection,
                    "scope",
                    "profile",
                    RepositoryArtifactDraft(
                        family="profile",
                        content=ProfileContent(
                            content="使用中文",
                            generation=ProfileGeneration(mode="manual_create", created_at=datetime.now(UTC)),
                        ),
                    ),
                )
                await connection.execute(
                    insert(SOURCE_CURSORS_TABLE).values(
                        scope_id="scope", binding_name="reports", cursor=b'{"through":7}', generation=3
                    )
                )
            reads = ArtifactReadSession(profile.database, "scope", ("context-items", "context-roots", "profile"))
            context = GenerationContext(
                "scope", resources, reads.read, list_artifacts=reads.list, search_artifacts=reads.search
            )
            root = await context.read_latest("context-roots", "root")
            assert root is not None
            fixed_ref = ArtifactRef.model_validate(root.content["ref"])
            async with profile.database.transaction() as connection:
                await items.revise(
                    connection,
                    "scope",
                    first,
                    RepositoryArtifactDraft(
                        family="context-items",
                        content=StoredArtifactContent(schema_version="1", content={"text": "数据库 NEW"}),
                    ),
                )
            builder = ArtifactIndexBuilder(profile.database)
            await builder.refresh("scope", "context-items")
            assert await builder.build("scope", "context-items")
            first_page = await context.list_artifacts("context-items", limit=2)
            assert [(ref.artifact_id, ref.revision) for ref in first_page.items] == [("a", 2), ("b", 1)]
            assert first_page.next_cursor is not None
            last_page = await context.list_artifacts("context-items", limit=2, cursor=first_page.next_cursor)
            assert [(ref.artifact_id, ref.revision) for ref in last_page.items] == [("c", 1)]
            assert last_page.next_cursor is None
            assert not (await context.search_artifacts("context-items", "旧证据")).matches
            current = await context.search_artifacts("context-items", "数据库")
            assert current.matches[0].artifact_ref.revision == 2
            assert (await context.read_revision(current.matches[0].artifact_ref)).content["text"] == "数据库 NEW"
            assert (await context.read_revision(fixed_ref)).content["text"] == "旧证据"
            snapshot = await context.read_revision(builtin.as_ref())
            assert snapshot.schema_version is None
            assert snapshot.content == builtin.content.model_dump(mode="json", by_alias=True)
            with pytest.raises(BaseValueNotFoundError):
                await context.read_revision(ArtifactRef(family="context-items", artifact_id="a", revision=99))
            with pytest.raises(ArtifactSearchUnsupported):
                await context.search_artifacts("context-items", "数据库", mode="hybrid")
            async with profile.database.transaction() as connection:
                cursor = (await connection.execute(select(SOURCE_CURSORS_TABLE))).mappings().one()
                assert cursor["cursor"] == b'{"through":7}' and cursor["generation"] == 3

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "evidence_family,revoked,update_foreign",
    [
        ("topic-memory", False, False),
        ("topic-memory", True, False),
        ("profile", False, False),
        ("profile", True, False),
        ("topic-memory", False, True),
    ],
)
def test_plugin_commit_rechecks_evidence_permissions(tmp_path, evidence_family, revoked, update_foreign):
    async def scenario():
        admin = PrincipalRef(type="service", id="admin")
        alice = PrincipalRef(type="user", id="alice")
        audit = AccessAuditContext(transport="test", operation="artifact-reads")
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'commit.db'}")
        output_family = "context-reports"
        register_custom_artifact_access_family(output_family)
        async with AsyncExitStack() as resources:
            profile = await resources.enter_async_context(SQLiteProfile.open(config, tables=BUILTIN_TABLES))
            access = await resources.enter_async_context(
                open_builtin_access_control(config, bootstrap_administrators=(admin,))
            )
            binding = await access.create_binding(
                admin,
                CreateBinding(
                    subject=alice,
                    resource=ResourceRef.scope("scope"),
                    role=AccessRole.SCOPE_CONTRIBUTOR,
                    idempotency_key="scope-viewer",
                ),
                context=audit,
            )
            async with profile.database.transaction() as connection:
                if evidence_family == "topic-memory":
                    artifact_type = TopicMemory
                    content = TopicMemoryContent(title="Topic", summary="Summary", detail="Evidence")
                else:
                    artifact_type = Profile
                    content = ProfileContent(
                        content="Evidence",
                        generation=ProfileGeneration(mode="manual_create", created_at=datetime.now(UTC)),
                    )
                evidence = await ArtifactRepository((artifact_type,)).create(
                    connection,
                    "scope",
                    "a-hidden",
                    RepositoryArtifactDraft(family=evidence_family, content=content),
                )
                await connection.execute(
                    insert(ARTIFACT_FAMILY_SCHEMAS_TABLE).values(
                        family=output_family,
                        schema_version="1",
                        declaration={"schema_version": "1"},
                    )
                )
                lease = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "test-owner")
                intent = await ArtifactProcessingIntentRepository().request(connection, "scope", "reports")
                assignment = ArtifactProcessingWorkAssignment(
                    binding_name="reports",
                    scope_id="scope",
                    artifact_family=output_family,
                    claimed_request_generation=intent.requested_generation,
                    fence=lease.fence("single-process"),
                    worker_id="test-worker",
                )
                security = WorkerSecurity(access, alice)
                invocation = ScopeInvocation(
                    assignment, authorize_transaction=partial(security.authorize_transaction, scope_id="scope")
                )
                await invocation.start(connection)
            if evidence_family == "profile":
                await access.establish_artifact_owner(
                    ResourceRef.artifact("scope", family=evidence_family, artifact_id="a-hidden"),
                    alice,
                    idempotency_key="evidence-owner",
                    context=audit,
                )
            reads = ArtifactReadSession(
                profile.database,
                "scope",
                (evidence_family, output_family),
                access=access,
                principal=alice,
                audit=audit,
                guard=invocation.guard,
            )
            snapshot = await reads.read(evidence_family, "a-hidden", 1)
            assert snapshot.ref == evidence.as_ref()
            if revoked:
                await access.revoke_binding(
                    admin, binding.binding_id, expected_version=1, idempotency_key="revoke-scope", context=audit
                )
                with pytest.raises(AccessDeniedError):
                    await reads.read(evidence_family, "a-hidden", 1)
                with pytest.raises(AccessDeniedError):
                    await reads.list(evidence_family)
            if update_foreign:
                async with profile.database.transaction() as connection:
                    await custom_artifact_repository(output_family).create(
                        connection,
                        "scope",
                        "report",
                        RepositoryArtifactDraft(
                            family=output_family,
                            content=StoredArtifactContent(schema_version="1", content={"text": "Original"}),
                        ),
                    )
                await access.establish_artifact_owner(
                    ResourceRef.artifact("scope", family=output_family, artifact_id="report"),
                    PrincipalRef(type="user", id="bob"),
                    idempotency_key="output-owner",
                    context=audit,
                )
                previous = await reads.read(output_family, "report", 1)
                assert previous.content == {"text": "Original"}
            draft = ArtifactDraft(
                family=output_family,
                schema_version="1",
                artifact_id="report",
                expected_revision=1 if update_foreign else 0,
                content={"text": "Result"},
                source_refs=(),
                artifact_refs=(snapshot.ref,),
            )

            async def commit():
                await _commit_window(
                    profile.database,
                    ArtifactPluginWorkerSpec(config, "reports", 1, {}),
                    assignment,
                    {"family": output_family, "schema_version": "1", "content_schema": {"type": "object"}},
                    invocation,
                    SourceRepository(BUILTIN_SOURCE_REGISTRY),
                    None,
                    0,
                    frozenset(),
                    reads.refs,
                    [draft],
                    security,
                )

            if revoked or update_foreign:
                with pytest.raises(AccessDeniedError):
                    await commit()
            else:
                await commit()
            async with profile.database.transaction() as connection:
                repository = custom_artifact_repository(output_family)
                if revoked:
                    with pytest.raises(RepositoryNotFoundError):
                        await repository.latest(connection, "scope", output_family, "report")
                else:
                    result = await repository.latest(connection, "scope", output_family, "report")
                    if update_foreign:
                        assert result.revision == 1
                        assert result.content.content == {"text": "Original"}
                    else:
                        assert result.lineage.artifacts == (snapshot.ref,)
                current = await ArtifactProcessingIntentRepository().load(connection, "scope", "reports")
                assert current is not None
                assert current.handled_generation == (0 if revoked or update_foreign else intent.requested_generation)

    asyncio.run(scenario())
