# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Ledger publication authorization, ownership, and the membership claim gate."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.experience import (
    EXPERIENCE_INCUBATION_CURSOR_NAME,
    ExperienceCandidateInput,
    ExperienceContent,
)
from powercontext.builtin.artifacts.profile.service import (
    PreparedProfileFlush,
    ProfileFlushResult,
    RelationalProfileService,
)
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES, CANDIDATE_VERSIONS_TABLE
from powercontext.builtin.persistence.work import WorkClaim, WorkRepository, WorkStatus
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.composition import _work_authorizer
from powercontext.builtin.runtime.config import CoordinationConfig, DeploymentConfig, WorkerConfig
from powercontext.builtin.runtime.membership import RuntimeMembership
from powercontext.builtin.runtime.work_handlers import (
    ExperienceWorkHandler,
    ProfileWorkHandler,
    WorkExecutionError,
    experience_work_spec,
)
from powercontext.builtin.runtime.worker import DurableWorker
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentCapture
from powercontext.server.authz import (
    AccessAuditContext,
    AccessRole,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.authz.repository import ACCESS_TABLES, RelationalAccessRepository
from powercontext.server.processing_security import WorkerSecuritySpec, open_worker_security
from powercontext.sources import SourceRef

ADMIN = PrincipalRef(type="service", id="admin")
BACKGROUND = PrincipalRef(type="service", id="background-worker")
AUDIT = AccessAuditContext(transport="http", operation="publication-security")


class _RevokingExperiencePipeline:
    """Generate one Candidate per Source, optionally revoking the grant mid-generation."""

    def __init__(self, revoke=None) -> None:
        self.revoke = revoke

    async def incubate(self, sources: tuple[Any, ...], /) -> tuple[ExperienceCandidateInput, ...]:
        if self.revoke is not None:
            await self.revoke()
        return tuple(
            ExperienceCandidateInput(
                proposal=ExperienceContent(
                    situation="A background worker processed one bounded Source window.",
                    action="Incubate the captured fact into a reviewed proposal.",
                    outcome=source.content,
                    lesson="Publication must recheck the grant it was granted under.",
                ),
                sources=(SourceRef(source_type="content", source_id=source.name),),
            )
            for source in sources
            if getattr(source, "content", None) is not None
        )


async def _open_security_contexts(tmp_path, pipeline: _RevokingExperiencePipeline):
    """Create one SQLite file carrying both the builtin and the access tables."""

    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'publication-security.db'}")
    async with SQLiteProfile.open(config, tables=(*BUILTIN_TABLES, *ACCESS_TABLES)):
        pass
    contexts_cm = open_builtin_contexts(BuiltinConfig(database=config), experience_pipeline=pipeline)
    contexts = await contexts_cm.__aenter__()
    return config, contexts, contexts_cm


async def _experience_candidate_count(contexts, scope_id: str) -> int:
    async with contexts.database.transaction() as connection:
        return (
            await connection.execute(
                select(func.count())
                .select_from(CANDIDATE_VERSIONS_TABLE)
                .where(
                    CANDIDATE_VERSIONS_TABLE.c.scope_id == scope_id,
                    CANDIDATE_VERSIONS_TABLE.c.family == "experience",
                )
            )
        ).scalar()


def test_experience_publication_rechecks_contribution_and_attests_owners(tmp_path) -> None:
    """A grant revoked during generation fails the fenced publication, not the retry."""

    async def scenario() -> None:
        pipeline = _RevokingExperiencePipeline()
        config, contexts, contexts_cm = await _open_security_contexts(tmp_path, pipeline)
        try:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Publication security", summary="Revoke during generation", idempotency_key="pub")
            )
            scope_id = scope.scope_id
            context = await contexts.get(scope_id)
            await context.sources.capture(ContentCapture(source_id="one", content="First durable fact."))
            await context.sources.capture(ContentCapture(source_id="two", content="Second durable fact."))
            spec_dict = WorkerSecuritySpec(principal=BACKGROUND, deployment_id="test").model_dump(mode="json")

            async with (
                open_builtin_access_control(config, bootstrap_administrators=(ADMIN,)) as admin,
                open_worker_security(spec_dict, contexts.database) as security,
            ):
                grant = await admin.create_binding(
                    ADMIN,
                    CreateBinding(
                        subject=BACKGROUND,
                        resource=ResourceRef.scope(scope_id),
                        role=AccessRole.SCOPE_CONTRIBUTOR,
                        idempotency_key="grant-contribute",
                    ),
                    context=AUDIT,
                )

                async def revoke() -> None:
                    await admin.revoke_binding(
                        ADMIN,
                        grant.binding_id,
                        expected_version=1,
                        idempotency_key="revoke-contribute",
                        context=AUDIT,
                    )

                pipeline.revoke = revoke
                spec = await experience_work_spec(contexts, scope_id, limit=10, max_attempts=2, payload_version=1)
                assert spec is not None
                repository = WorkRepository()
                async with contexts.database.transaction() as connection:
                    enqueued = await repository.enqueue(connection, spec)
                assert enqueued.created is True
                worker = DurableWorker(
                    database=contexts.database,
                    worker_id="worker-a",
                    handlers=(ExperienceWorkHandler(contexts, security=security),),
                    config=WorkerConfig(concurrency=1),
                    authorizer=_work_authorizer(security),
                )
                # Entry authorization passed; the grant was revoked during
                # generation; the fenced publication must deny, not publish.
                assert await worker.run_once() == 1
                async with contexts.database.transaction() as connection:
                    stored = await repository.get(connection, enqueued.work.work_id)
                    cursor_state = await SourceCursorRepository().load(
                        connection, scope_id, EXPERIENCE_INCUBATION_CURSOR_NAME
                    )
                assert stored.status is WorkStatus.FAILED
                assert stored.error_category == "authorization"
                assert stored.error_code == "experience_publication_denied"
                assert cursor_state is None
                assert await _experience_candidate_count(contexts, scope_id) == 0

                # A fresh grant and an operator retry let the same Work row
                # publish, and every Candidate carries the background
                # principal as its owner.
                await admin.create_binding(
                    ADMIN,
                    CreateBinding(
                        subject=BACKGROUND,
                        resource=ResourceRef.scope(scope_id),
                        role=AccessRole.SCOPE_CONTRIBUTOR,
                        idempotency_key="grant-contribute-again",
                    ),
                    context=AUDIT,
                )
                pipeline.revoke = None
                async with contexts.database.transaction() as connection:
                    retried = await repository.retry(
                        connection, enqueued.work.work_id, expected_version=stored.state_version
                    )
                assert retried.status is WorkStatus.QUEUED
                assert await worker.run_once() == 1
                async with contexts.database.transaction() as connection:
                    stored = await repository.get(connection, enqueued.work.work_id)
                assert stored.status is WorkStatus.SUCCEEDED
                assert stored.result_payload is not None
                published_ids = stored.result_payload["candidate_ids"]
                assert isinstance(published_ids, list)
                candidate_ids = tuple(str(candidate_id) for candidate_id in published_ids)
                assert len(candidate_ids) == 2
                assert await _experience_candidate_count(contexts, scope_id) == 2
                owners = await RelationalAccessRepository(contexts.database).get_candidate_owners(
                    scope_id, candidate_ids
                )
                assert set(owners) == set(candidate_ids)
                assert all(attestation.proposed_owner == BACKGROUND for attestation in owners.values())
        finally:
            await contexts_cm.__aexit__(None, None, None)

    asyncio.run(scenario())


class _ProfileRecordingSecurity:
    """Records every Profile publication hook the handler is expected to call."""

    principal = BACKGROUND
    access = None
    context = AUDIT

    def __init__(self, recorded: dict[str, Any]) -> None:
        self._recorded = recorded

    async def authorize_transaction(self, connection: Any, scope_id: str) -> None:
        self._recorded["transaction_scope"] = scope_id

    async def experience_commit(self, connection: Any, candidates: Any, *, scope_id: str) -> None:
        self._recorded["experience_scope"] = scope_id

    async def authorize_profile(self, scope_id: str, current: Any) -> None:
        self._recorded["prepare_scope"] = scope_id

    async def authorize_profile_commit(self, connection: Any, current: Any, *, scope_id: str) -> None:
        # The grant is revoked between generation and the fenced publication.
        self._recorded["commit_scope"] = scope_id
        raise PermissionError("scope.contribute revoked during generation")  # noqa: TRY003

    async def profile_commit(self, connection: Any, artifact: Any, candidate: Any, *, scope_id: str) -> None:
        self._recorded["owner_scope"] = scope_id


class _RecordingProfiles:
    """Stands in for the Profile service and invokes the hooks the handler passes."""

    def __init__(self, recorded: dict[str, Any]) -> None:
        self._recorded = recorded

    async def prepare_flush(self, scope_id, *, high_watermark=None, authorize_snapshot=None):
        self._recorded["snapshot_hook"] = authorize_snapshot
        if authorize_snapshot is not None:
            await authorize_snapshot(None)
        return PreparedProfileFlush(scope_id=scope_id, after=0, high=high_watermark or 1, evidence_count=1, result=None)

    async def commit_flush(self, connection, prepared, *, authorize_commit=None, on_commit=None):
        self._recorded["commit_hook"] = authorize_commit
        self._recorded["owner_hook"] = on_commit
        if authorize_commit is not None:
            await authorize_commit(connection, None)
        return ProfileFlushResult(
            status="updated", previous_cursor=0, current_cursor=1, high_watermark=1, processed_source_count=1
        )


class _ProfileClaim:
    scope_id = "scp_publication"
    payload: ClassVar[dict[str, int]] = {"cursor_generation": 0, "after": 0, "high_watermark": 1}


def test_profile_handler_wires_the_publication_hooks_and_maps_a_denial() -> None:
    """The Profile ledger path carries the Family worker's snapshot/commit hooks."""

    recorded: dict[str, Any] = {}

    async def scenario() -> None:
        profiles = cast(RelationalProfileService, _RecordingProfiles(recorded))
        handler = ProfileWorkHandler(profiles, max_concurrency=1, security=_ProfileRecordingSecurity(recorded))
        prepared = await handler.prepare(cast(WorkClaim, _ProfileClaim()))
        assert prepared.commit is not None
        assert recorded["snapshot_hook"] is not None
        with pytest.raises(WorkExecutionError) as excinfo:
            await prepared.commit(cast(AsyncConnection, None))
        assert excinfo.value.failure.category == "authorization"
        assert excinfo.value.failure.code == "profile_write_denied"
        assert excinfo.value.failure.retryable is False
        assert recorded["prepare_scope"] == _ProfileClaim.scope_id
        assert recorded["commit_scope"] == _ProfileClaim.scope_id
        assert recorded["commit_hook"] is not None and recorded["owner_hook"] is not None

    asyncio.run(scenario())


def test_membership_gate_leaves_queued_work_unclaimed(tmp_path) -> None:
    """An incompatible rollout blocks claiming; the gate reopens when it ends."""

    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'claim-gate-work.db'}"))
        async with open_builtin_contexts(config, experience_pipeline=_RevokingExperiencePipeline()) as contexts:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Claim gate", summary="Incompatible rollout", idempotency_key="gate")
            )
            context = await contexts.get(scope.scope_id)
            await context.sources.capture(ContentCapture(source_id="one", content="First durable fact."))
            spec = await experience_work_spec(contexts, scope.scope_id, limit=10, max_attempts=3, payload_version=1)
            assert spec is not None
            repository = WorkRepository()
            async with contexts.database.transaction() as connection:
                enqueued = await repository.enqueue(connection, spec)

            compatible = DeploymentConfig(mode="distributed", role="worker", id="dep-a", behavior_revision="a")
            incompatible = DeploymentConfig(mode="distributed", role="worker", id="dep-b", behavior_revision="b")
            coordination = CoordinationConfig(member_ttl_seconds=3, member_heartbeat_seconds=1)
            membership = RuntimeMembership(
                database=contexts.database,
                deployment=compatible,
                coordination=coordination,
                build_version="test",
            )
            peer = RuntimeMembership(
                database=contexts.database,
                deployment=incompatible,
                coordination=coordination,
                build_version="test",
            )
            await membership.start()
            await peer.start()
            worker = DurableWorker(
                database=contexts.database,
                worker_id=f"{membership.instance_id}:worker",
                handlers=(ExperienceWorkHandler(contexts),),
                config=WorkerConfig(concurrency=1),
                claim_readiness=membership.readiness,
            )
            # The live peer advertises another behavior revision: membership
            # reports misconfigured and the worker claims nothing.
            assert await membership.readiness() == "misconfigured"
            assert await worker.readiness() == "misconfigured"
            assert await worker.run_once() == 0

            # Both memberships stop heartbeating; their rows expire with the TTL.
            await membership.stop()
            await peer.stop()
            await asyncio.sleep(3.4)

            membership = RuntimeMembership(
                database=contexts.database,
                deployment=compatible,
                coordination=coordination,
                build_version="test",
            )
            await membership.start()
            try:
                worker = DurableWorker(
                    database=contexts.database,
                    worker_id=f"{membership.instance_id}:worker",
                    handlers=(ExperienceWorkHandler(contexts),),
                    config=WorkerConfig(concurrency=1),
                    claim_readiness=membership.readiness,
                )
                assert await membership.readiness() == "ready"
                assert await worker.readiness() == "ready"
                assert await worker.run_once() == 1
                async with contexts.database.transaction() as connection:
                    stored = await repository.get(connection, enqueued.work.work_id)
                assert stored.status is WorkStatus.SUCCEEDED
            finally:
                await membership.stop()

    asyncio.run(scenario())
