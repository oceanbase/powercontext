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

"""Explicit Experience merge uses the runtime's shared service and real indexes."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from powercontext.artifacts import ArtifactAddress, ArtifactLineage, ArtifactRef, ArtifactSearchExecutionContext
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.experience import ExperienceContent, FailureRecord
from powercontext.builtin.artifacts.merge_models import ArtifactMergeRelationError
from powercontext.builtin.persistence.artifact_governance import InvalidArtifactLifecycleError
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.experience_index import ensure_artifact_head_searchable_text
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.records import ArtifactWrite, BaseOperationNotSupportedError
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest,
    BuiltinConfig,
    GetArtifactCandidateRequest,
    ProposeExperienceRequest,
    RuntimeConfig,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.tags import ArtifactTagTarget
from powercontext.server.authz import (
    AccessAuditContext,
    AccessBinding,
    AccessBindingState,
    AccessControlService,
    AccessDeniedError,
    AccessRole,
    BuiltinAuthorizationProvider,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.repository import RelationalAccessRepository
from powercontext.sources import SourceRef

OWNER = PrincipalRef(type="service", id="experience-owner")


def _execution_context(runtime, principal: PrincipalRef = OWNER) -> ArtifactSearchExecutionContext:
    contexts = cast(RelationalContexts, runtime._provider)
    repository = RelationalAccessRepository(contexts.database)
    access = AccessControlService(
        BuiltinAuthorizationProvider(repository),
        relationships=repository,
        audit=repository,
        static_scope_principal=OWNER,
    )
    return ArtifactSearchExecutionContext(
        principal=principal, access=access, audit=AccessAuditContext(transport="test", operation="experience-merge")
    )


@pytest.fixture(params=("sqlite", "oceanbase"))
def database(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[SQLiteConfig | OceanBaseConfig]:
    if request.param == "sqlite":
        yield SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'artifact-merge.db'}")
        return
    configured_url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
    if configured_url is None:
        pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL with test database creation and deletion privileges")
    configured = OceanBaseConfig(url=SecretStr(configured_url))
    database_name = "pc_artifact_merge_test_" + uuid4().hex[:16]

    async def manage(statement: str) -> None:
        async with (
            OceanBaseProfile.open(configured, tables=()) as admin,
            admin.database.transaction() as connection,
        ):
            await connection.exec_driver_sql(statement)

    asyncio.run(manage(f"CREATE DATABASE `{database_name}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"))
    try:
        url = make_url(configured_url).set(database=database_name)
        yield OceanBaseConfig(url=SecretStr(url.render_as_string(hide_password=False)))
    finally:
        asyncio.run(manage(f"DROP DATABASE `{database_name}`"))


def _content(name: str, *, failure: FailureRecord | None = None) -> ExperienceContent:
    return ExperienceContent(
        situation=f"The rollout needs {name} checks.",
        action=f"Perform the {name} rollout check.",
        outcome=f"The {name} rollout check passed.",
        lesson=f"Keep the {name} rollout evidence.",
        failure=failure,
    )


def _refs(refs: Iterable[ArtifactRef]) -> set[tuple[str, str, int]]:
    return {(ref.family, ref.artifact_id, ref.revision) for ref in refs}


async def _scope(runtime) -> str:
    scope = await runtime.scopes.create(
        ScopeDraft(title="Merge acceptance", summary="Experience merge", idempotency_key=uuid4().hex)
    )
    assert getattr(runtime, "artifact_merge", None) is not None, "Runtime must expose registered shared Artifact merge"
    return scope.scope_id


async def _create(runtime, scope: str, name: str):
    created = await runtime.records.for_scope(scope).create_artifact(
        "experience", ArtifactWrite(content=_content(name).model_dump(mode="json"))
    )
    contexts = cast(RelationalContexts, runtime._provider)
    resource = ResourceRef.artifact(scope, family="experience", artifact_id=created.artifact_id)
    context = _execution_context(runtime)
    # Ordinary local management writes leave ownership to their host's authorization boundary.
    async with contexts.database.transaction() as connection:
        await context.access.with_connection(connection).establish_artifact_owner(
            resource, OWNER, idempotency_key="test-owner:" + created.artifact_id, context=context.audit
        )
    return await runtime.artifact_merge.for_scope(scope, "experience").get(created.artifact_id)


async def _search(runtime, scope: str, query: str):
    contexts = cast(RelationalContexts, runtime._provider)
    return (await contexts.search_experience_outcome(scope, query, 20)).hits


def test_experience_management_merge_freezes_inputs_preserves_history_and_candidate_rollback(
    database, tmp_path
) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=database), scheduler_path=tmp_path / "scheduler.db"
        ) as runtime:
            scope = await _scope(runtime)
            assert runtime.artifact_merge is not None
            records = runtime.records.for_scope(scope)
            merge = runtime.artifact_merge.for_scope(scope, "experience")
            contexts = cast(RelationalContexts, runtime._provider)
            context = _execution_context(runtime)
            a, b = await _create(runtime, scope, "canary"), await _create(runtime, scope, "pause")
            await records.replace_artifact(
                "experience",
                a.ref.artifact_id,
                '"revision:1"',
                ArtifactWrite(content=_content("revised").model_dump(mode="json")),
            )
            a = await merge.get(a.ref.artifact_id)
            assert a.ref.revision == 2
            assert len((await records.query_artifacts("experience", limit=20, cursor=None)).items) == 2
            assert _refs(hit.artifact_ref for hit in await _search(runtime, scope, "rollout")) == _refs((a.ref, b.ref))
            for original, labels in ((a, ("Rollout", "canary")), (b, ("rollout", "pause"))):
                target = ArtifactTagTarget(family="experience", artifact_id=original.ref.artifact_id)
                tags = await records.get_tags(target)
                await records.replace_tags(target, labels, expected_etag=tags.etag)
            source = await records.create_source("content", "The rollout pause check passed.")
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=_content("candidate"),
                    target=a.ref,
                    artifacts=(a.ref,),
                    sources=(SourceRef(source_type="content", source_id=source.source_id),),
                )
            )
            merged_content = _content(
                "combined",
                failure=FailureRecord.model_validate({
                    "signature": {"recall_cue": "Rollout check failed"},
                    "repair_surface": "acceptance_check",
                    "verification": {"condition": "Rollout check passed", "check_subject": "rollout-check"},
                }),
            )
            result = await merge.merge((a.as_read(), b.as_read()), merged_content, context=context)
            merged = result.primary
            assert merged.ref.revision == 1
            assert merged.artifact.content == merged_content
            assert merged.artifact.lineage.artifacts == (a.ref, b.ref)
            tags = await records.get_tags(ArtifactTagTarget(family="experience", artifact_id=merged.ref.artifact_id))
            assert {tag.casefold() for tag in tags.tags} == {"rollout", "canary", "pause"}
            async with contexts.database.transaction() as connection:
                access = RelationalAccessRepository(contexts.database, connection=connection)
                owner = await access.get_artifact_owner(
                    ResourceRef.artifact(scope, family="experience", artifact_id=merged.ref.artifact_id)
                )
                assert owner is not None and owner.owner == OWNER
                assert (
                    await access.get_artifact_owner(
                        ResourceRef.artifact(scope, family="atomic-memory", artifact_id=merged.ref.artifact_id)
                    )
                    is None
                )
            for original in (a, b):
                frozen = await merge.get(original.ref.artifact_id)
                assert frozen.artifact == original.artifact
                assert frozen.state.lifecycle_state == "deprecated"
                assert frozen.state.merged_into_id == merged.ref.artifact_id
                assert frozen.as_read().state_version == original.as_read().state_version + 1
                historical = await records.get_artifact_revision(
                    "experience", original.ref.artifact_id, original.ref.revision
                )
                assert historical.content == original.artifact.content.model_dump(mode="json")
                with pytest.raises(InvalidArtifactLifecycleError):
                    await records.replace_artifact(
                        "experience",
                        original.ref.artifact_id,
                        f'"revision:{original.ref.revision}"',
                        ArtifactWrite(content=_content("illegal").model_dump(mode="json")),
                    )
            with pytest.raises(InvalidArtifactLifecycleError):
                await runtime.review.for_scope(scope).approve(
                    ApproveArtifactCandidateRequest(
                        candidate_id=candidate.candidate_id, expected_version=candidate.version
                    )
                )
            pending = await runtime.review.for_scope(scope).get(
                GetArtifactCandidateRequest(candidate_id=candidate.candidate_id)
            )
            assert pending.status == "pending" and pending.version == candidate.version
            assert (await merge.get(a.ref.artifact_id)).artifact == a.artifact
            assert tuple(
                item.artifact_id for item in (await records.query_artifacts("experience", limit=1, cursor=None)).items
            ) == (merged.ref.artifact_id,)
            assert tuple(hit.artifact_ref for hit in await _search(runtime, scope, "rollout")) == (merged.ref,)
            assert {item.artifact_id for item in await records.logical_artifacts()} >= {
                a.ref.artifact_id,
                b.ref.artifact_id,
                merged.ref.artifact_id,
            }
            with pytest.raises(BaseOperationNotSupportedError):
                runtime.artifact_merge.for_scope(scope, "prompt")

    asyncio.run(scenario())


def test_registered_families_keep_same_result_identity_and_projections_separate(database, tmp_path) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=database), scheduler_path=tmp_path / "scheduler.db"
        ) as runtime:
            scope = await _scope(runtime)
            assert runtime.artifact_merge is not None
            assert runtime.atomic_memory is not None
            records = runtime.records.for_scope(scope)
            merged_api = runtime.artifact_merge.for_scope(scope, "atomic-memory")
            originals = []
            for name in ("canary", "pause"):
                created = await records.create_artifact(
                    "atomic-memory", ArtifactWrite(content={"kind": "rule", "text": f"Keep the rollout {name} check."})
                )
                originals.append(await merged_api.get(created.artifact_id))
                target = ArtifactTagTarget(family="atomic-memory", artifact_id=created.artifact_id)
                tags = await records.get_tags(target)
                await records.replace_tags(target, ("Atomic",), expected_etag=tags.etag)
            result = await merged_api.merge(
                tuple(item.as_read() for item in originals),
                AtomicMemoryContent(kind="rule", text="Keep both rollout checks."),
            )
            atomic = await runtime.atomic_memory.for_scope(scope).get(result.primary.ref.artifact_id)
            assert atomic.artifact == result.primary.artifact
            assert atomic.artifact.content.creation is not None
            assert atomic.artifact.content.creation.input_artifact_ids == tuple(
                item.ref.artifact_id for item in originals
            )
            experience = runtime.artifact_merge.for_scope(scope, "experience")
            inputs = (await _create(runtime, scope, "canary"), await _create(runtime, scope, "pause"))
            for original in inputs:
                target = ArtifactTagTarget(family="experience", artifact_id=original.ref.artifact_id)
                tags = await records.get_tags(target)
                await records.replace_tags(target, ("Experience",), expected_etag=tags.etag)
            merged_experience = (
                await experience.merge(
                    tuple(item.as_read() for item in inputs),
                    _content("combined"),
                    artifact_id=result.primary.ref.artifact_id,
                )
            ).primary
            assert merged_experience.ref.artifact_id == atomic.ref.artifact_id
            for family, label in (("atomic-memory", "Atomic"), ("experience", "Experience")):
                tags = await records.get_tags(ArtifactTagTarget(family=family, artifact_id=atomic.ref.artifact_id))
                assert tags.tags == (label,)
            await experience.restore(merged_experience.ref.artifact_id, operation="undo_merge")
            assert (await merged_api.get(atomic.ref.artifact_id)).artifact == atomic.artifact
            assert (await merged_api.get(atomic.ref.artifact_id)).state.lifecycle_state == "active"
            assert tuple(
                hit.hit.artifact_ref for hit in (await runtime.atomic_memory.for_scope(scope).search("rollout")).hits
            ) == (atomic.ref,)
            assert _refs(hit.artifact_ref for hit in await _search(runtime, scope, "rollout")) == _refs(
                item.ref.model_copy(update={"revision": item.ref.revision + 1}) for item in inputs
            )
            restored = await merged_api.restore(result.primary.ref.artifact_id, operation="undo_merge")
            assert _refs(restored.restored) == _refs(
                item.ref.model_copy(update={"revision": item.ref.revision + 1}) for item in originals
            )
            assert (
                await runtime.atomic_memory.for_scope(scope).get(result.primary.ref.artifact_id)
            ).state.state == "retired"

    asyncio.run(scenario())


@pytest.mark.parametrize("target,historical", (("endpoint", False), ("input", False), ("input", True)))
def test_experience_nested_restore_republishes_the_selected_group(database, tmp_path, target, historical) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=database,
            runtime=RuntimeConfig(atomic_memory_preview_signing_secret=SecretStr("x" * 32)),
        )
        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            scope = await _scope(runtime)
            assert runtime.artifact_merge is not None
            merge = runtime.artifact_merge.for_scope(scope, "experience")
            records = runtime.records.for_scope(scope)
            a, b, d = (
                await _create(runtime, scope, "canary"),
                await _create(runtime, scope, "pause"),
                await _create(runtime, scope, "health"),
            )
            await records.replace_artifact(
                "experience",
                b.ref.artifact_id,
                '"revision:1"',
                ArtifactWrite(content=_content("newpause").model_dump(mode="json")),
            )
            b_current = await merge.get(b.ref.artifact_id)
            first = (await merge.merge((a.as_read(), b_current.as_read()), _content("combined"))).primary
            await records.replace_artifact(
                "experience",
                first.ref.artifact_id,
                '"revision:1"',
                ArtifactWrite(content=_content("updatedcombined").model_dump(mode="json")),
            )
            first = await merge.get(first.ref.artifact_id)
            last = (await merge.merge((first.as_read(), d.as_read()), _content("complete"))).primary
            identity = last.ref.artifact_id if target == "endpoint" else b.ref.artifact_id
            kwargs = {"operation": "undo_merge"} if target == "endpoint" else {"revision": 1} if historical else {}
            preview = await merge.preview_restoration(identity, **kwargs)
            assert preview.endpoint.ref == last.ref
            result = await merge.restore(identity, preview_token=preview.preview_token, **kwargs)
            outcome = await merge.restoration_outcome(identity, revision=result.primary.ref.revision)
            assert outcome is not None and outcome.operation == kwargs.get("operation", "restore")
            assert outcome.undo_merge_results == ((last.ref,) if target == "endpoint" else (last.ref, first.ref))
            assert _refs(item.after_ref for item in outcome.writes) == _refs((*result.restored, *result.retired))
            source = next(item.content_from_ref for item in outcome.writes if item.after_ref.artifact_id == identity)
            assert source == (last.ref if target == "endpoint" else b.ref if historical else b_current.ref)
            assert len(result.primary.artifact.lineage.sources) == 1
            assert all(not item.artifact.lineage.sources for item in result.records if item.ref.artifact_id != identity)
            expected_refs = (
                (first.ref.model_copy(update={"revision": 3}), d.ref.model_copy(update={"revision": 2}))
                if target == "endpoint"
                else (
                    a.ref.model_copy(update={"revision": 2}),
                    d.ref.model_copy(update={"revision": 2}),
                    ArtifactRef(family="experience", artifact_id=b.ref.artifact_id, revision=3),
                )
            )
            assert _refs(result.restored) == _refs(expected_refs)
            retired = await merge.get(last.ref.artifact_id)
            assert retired.state.lifecycle_state == "retired"
            assert retired.ref.revision == 2
            assert retired.ref in result.retired
            assert all(item.creates_revision for item in preview.restore)
            assert result.undo_merge_results == (
                (last.ref.artifact_id,) if target == "endpoint" else (last.ref.artifact_id, first.ref.artifact_id)
            )
            assert _refs(hit.artifact_ref for hit in await _search(runtime, scope, "rollout")) == _refs(expected_refs)
            assert {
                item.artifact_id for item in (await records.query_artifacts("experience", limit=20, cursor=None)).items
            } == {ref.artifact_id for ref in expected_refs}
            if target == "endpoint":
                assert (await merge.get(b.ref.artifact_id)).state.merged_into_id == first.ref.artifact_id
                restored_first = await merge.get(first.ref.artifact_id)
                assert restored_first.ref.revision == 3
                assert restored_first.artifact.content == first.artifact.content
                assert restored_first.artifact.lineage.artifacts == (first.ref,)
                assert (await merge.get(first.ref.artifact_id, revision=2)).artifact == first.artifact
            else:
                assert (await merge.get(first.ref.artifact_id)).state.lifecycle_state == "retired"
                restored = await merge.get(b.ref.artifact_id)
                assert restored.artifact.content == (b.artifact.content if historical else b_current.artifact.content)
                assert (await merge.get(b.ref.artifact_id, revision=1)).artifact == b.artifact
                if historical:
                    assert b.ref in restored.artifact.lineage.artifacts
                    assert b_current.ref in restored.artifact.lineage.artifacts

    asyncio.run(scenario())


def test_experience_merge_preserves_explicit_caller_identity_and_denies_foreign_inputs(database, tmp_path) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=database), scheduler_path=tmp_path / "scheduler.db"
        ) as runtime:
            scope = await _scope(runtime)
            assert runtime.artifact_merge is not None
            merge = runtime.artifact_merge.for_scope(scope, "experience")
            a, b = await _create(runtime, scope, "canary"), await _create(runtime, scope, "pause")
            foreign = _execution_context(runtime, PrincipalRef(type="user", id="foreign"))
            contexts = cast(RelationalContexts, runtime._provider)
            async with contexts.database.transaction() as connection:
                await RelationalAccessRepository(contexts.database, connection=connection).create_binding(
                    AccessBinding(
                        binding_id="foreign-contributor",
                        subject=foreign.principal,
                        resource=ResourceRef.scope(scope),
                        role=AccessRole.SCOPE_CONTRIBUTOR,
                        granted_by=OWNER,
                        reason=None,
                        created_at=datetime.now(UTC),
                        expires_at=None,
                        state=AccessBindingState.ACTIVE,
                        version=1,
                        policy_revision="pending",
                        idempotency_key="foreign-contributor",
                    )
                )
            assert await merge.get(a.ref.artifact_id, context=foreign) == a
            with pytest.raises(AccessDeniedError):
                await merge.merge(
                    (a.as_read(), b.as_read()), _content("combined"), artifact_id="denied-result", context=foreign
                )
            assert await merge.get(a.ref.artifact_id) == a
            assert await merge.get(b.ref.artifact_id) == b
            with pytest.raises(RepositoryNotFoundError):
                await merge.get("denied-result")
            assert (
                len((await runtime.records.for_scope(scope).query_artifacts("experience", limit=20, cursor=None)).items)
                == 2
            )
            owner_context = _execution_context(runtime)
            merged = (
                await merge.merge((a.as_read(), b.as_read()), _content("combined"), context=owner_context)
            ).primary
            owner = await owner_context.access.artifact_owner(
                ResourceRef.artifact(scope, family="experience", artifact_id=merged.ref.artifact_id)
            )
            assert owner is not None and owner.owner == OWNER

    asyncio.run(scenario())


def test_restoration_outcome_survives_reopen_and_requires_group_read_access(database, tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=database)
        scheduler_path = tmp_path / "scheduler.db"
        async with open_builtin_runtime(config, scheduler_path=scheduler_path) as runtime:
            scope = await _scope(runtime)
            assert runtime.artifact_merge is not None
            merge = runtime.artifact_merge.for_scope(scope, "experience")
            owner_context = _execution_context(runtime)
            a, b = await _create(runtime, scope, "canary"), await _create(runtime, scope, "pause")
            merged = (
                await merge.merge((a.as_read(), b.as_read()), _content("combined"), context=owner_context)
            ).primary
            assert await merge.restoration_outcome(a.ref.artifact_id, revision=1) is None
            restored = await merge.restore(a.ref.artifact_id, context=owner_context)
            outcome = await merge.restoration_outcome(a.ref.artifact_id, revision=2)
            assert outcome is not None
            assert outcome.operation == "restore" and outcome.target == a.ref
            assert outcome.undo_merge_results == (merged.ref,)
            assert _refs(item.before_ref for item in outcome.writes) == _refs((a.ref, b.ref, merged.ref))
            assert _refs(item.after_ref for item in outcome.writes) == _refs((*restored.restored, *restored.retired))
            assert all(item.content_from_ref == item.before_ref for item in outcome.writes)
            assert {item.after_ref.artifact_id: item.lifecycle_state for item in outcome.writes} == {
                a.ref.artifact_id: "active",
                b.ref.artifact_id: "active",
                merged.ref.artifact_id: "retired",
            }
            frozen_outcome = outcome.model_dump(mode="json")
            await runtime.records.for_scope(scope).replace_artifact(
                "experience", a.ref.artifact_id, '"revision:2"', ArtifactWrite(content=_content("latest").model_dump())
            )
            again = await merge.merge(
                ((await merge.get(a.ref.artifact_id)).as_read(), (await merge.get(b.ref.artifact_id)).as_read()),
                _content("again"),
                context=owner_context,
            )
            reader = PrincipalRef(type="user", id="artifact-only-reader")
            contexts = cast(RelationalContexts, runtime._provider)
            async with contexts.database.transaction() as connection:
                await RelationalAccessRepository(contexts.database, connection=connection).create_binding(
                    AccessBinding(
                        binding_id="primary-only",
                        subject=reader,
                        resource=ResourceRef.artifact(scope, family="experience", artifact_id=a.ref.artifact_id),
                        role=AccessRole.ARTIFACT_VIEWER,
                        granted_by=OWNER,
                        reason=None,
                        created_at=datetime.now(UTC),
                        expires_at=None,
                        state=AccessBindingState.ACTIVE,
                        version=1,
                        policy_revision="pending",
                        idempotency_key="primary-only",
                    )
                )
            context = _execution_context(runtime, reader)
            assert (await merge.get(a.ref.artifact_id, revision=2, context=context)).ref == restored.primary.ref
            with pytest.raises(AccessDeniedError):
                await merge.restoration_outcome(a.ref.artifact_id, revision=2, context=context)
        async with open_builtin_runtime(config, scheduler_path=scheduler_path) as reopened:
            assert reopened.artifact_merge is not None
            merge = reopened.artifact_merge.for_scope(scope, "experience")
            recovered = await merge.restoration_outcome(a.ref.artifact_id, revision=2)
            assert recovered is not None and recovered.model_dump(mode="json") == frozen_outcome
            assert (await merge.get(a.ref.artifact_id)).ref.revision == 3
            assert (await merge.get(a.ref.artifact_id)).state.merged_into_id == again.primary.ref.artifact_id

    asyncio.run(scenario())


def test_existing_shared_tables_upgrade_preserves_exact_rows_and_defaults(database) -> None:
    async def scenario() -> None:
        profile_factory = SQLiteProfile if isinstance(database, SQLiteConfig) else OceanBaseProfile
        async with profile_factory.open(database, tables=()) as profile, profile.database.transaction() as connection:
            identity = (
                "VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
                if connection.dialect.name == "mysql"
                else "VARCHAR(128)"
            )
            table_default = (
                " DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
                if connection.dialect.name == "mysql"
                else ""
            )
            await connection.exec_driver_sql(
                "CREATE TABLE pc_artifact_heads ("
                f"scope_id {identity} NOT NULL, family {identity} NOT NULL, artifact_id {identity} NOT NULL, "
                "revision BIGINT NOT NULL, searchable_text TEXT NULL, lifecycle_state VARCHAR(16) NOT NULL DEFAULT 'active', "
                "replacement_artifact_id VARCHAR(128) NULL, governance_generation BIGINT NOT NULL DEFAULT 0, "
                f"PRIMARY KEY (scope_id, family, artifact_id)){table_default}"
            )
            await connection.exec_driver_sql(
                "CREATE TABLE pc_artifact_lineage_artifacts ("
                "scope_id VARCHAR(128) NOT NULL, family VARCHAR(128) NOT NULL, artifact_id VARCHAR(128) NOT NULL, "
                "revision BIGINT NOT NULL, ordinal BIGINT NOT NULL, upstream_family VARCHAR(128) NOT NULL, "
                "upstream_artifact_id VARCHAR(128) NOT NULL, upstream_revision BIGINT NOT NULL, "
                "PRIMARY KEY (scope_id, family, artifact_id, revision, ordinal))"
            )
            await connection.exec_driver_sql(
                "INSERT INTO pc_artifact_heads (scope_id, family, artifact_id, revision, searchable_text, governance_generation) "
                "VALUES ('historical', 'experience', 'existing', 3, 'existing searchable evidence', 7)"
            )
            await connection.exec_driver_sql(
                "INSERT INTO pc_artifact_lineage_artifacts "
                "VALUES ('historical', 'experience', 'existing', 3, 0, 'experience', 'upstream', 2)"
            )
            await ensure_artifact_head_searchable_text(connection)
            head = (
                await connection.exec_driver_sql(
                    "SELECT revision, searchable_text, governance_generation, merged_into_id FROM pc_artifact_heads"
                )
            ).one()
            assert tuple(head) == (3, "existing searchable evidence", 7, None)
            lineage = (
                await connection.exec_driver_sql(
                    "SELECT revision, ordinal, upstream_family, upstream_artifact_id, upstream_revision, is_merge_input "
                    "FROM pc_artifact_lineage_artifacts"
                )
            ).one()
            assert tuple(lineage) == (3, 0, "experience", "upstream", 2, 0)
            await ensure_artifact_head_searchable_text(connection)
            await connection.exec_driver_sql(
                "UPDATE pc_artifact_heads SET lifecycle_state = 'deprecated', merged_into_id = 'ResultA'"
            )
            assert (
                await connection.exec_driver_sql(
                    "SELECT artifact_id FROM pc_artifact_heads WHERE merged_into_id = 'resulta'"
                )
            ).all() == []
            assert (
                await connection.exec_driver_sql(
                    "SELECT artifact_id FROM pc_artifact_heads WHERE merged_into_id = 'ResultA'"
                )
            ).scalar_one() == "existing"

    asyncio.run(scenario())


def test_experience_merge_rejects_unsupported_lineage_before_writing(database, tmp_path) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=database), scheduler_path=tmp_path / "scheduler.db"
        ) as runtime:
            scope = await _scope(runtime)
            assert runtime.artifact_merge is not None
            merge = runtime.artifact_merge.for_scope(scope, "experience")
            a, b = await _create(runtime, scope, "canary"), await _create(runtime, scope, "pause")
            with pytest.raises(ArtifactMergeRelationError, match="direct Sources and exact in-Scope Artifacts"):
                await merge.merge(
                    (a.as_read(), b.as_read()),
                    _content("combined"),
                    lineage=ArtifactLineage(
                        artifacts=(a.ref, b.ref),
                        publication_source=ArtifactAddress(scope_id=scope, artifact=a.ref),
                        publication_digest="a" * 64,
                    ),
                )
            assert await merge.get(a.ref.artifact_id) == a
            assert await merge.get(b.ref.artifact_id) == b
            assert (
                len((await runtime.records.for_scope(scope).query_artifacts("experience", limit=20, cursor=None)).items)
                == 2
            )

    asyncio.run(scenario())
