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

"""Explicitly enabled Access Control acceptance against the configured database."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete, func, or_, select

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.runtime import DatabaseConfig
from powercontext.server.authz import (
    AccessAction,
    AccessAuditContext,
    AccessBinding,
    AccessConflictError,
    AccessControlService,
    AccessDeniedError,
    AccessResourceType,
    AccessRole,
    BindingReplacement,
    BuiltinAuthorizationProvider,
    CreateBinding,
    PrincipalRef,
    ReplaceBinding,
    ResourceRef,
)
from powercontext.server.authz.composition import open_builtin_access_control, open_casbin_access_control
from powercontext.server.authz.models import HandoffReceiptIdentity
from powercontext.server.authz.repository import (
    ACCESS_AUDIT_EVENTS_TABLE,
    ACCESS_BINDINGS_TABLE,
    ACCESS_IDEMPOTENCY_TABLE,
    ACCESS_OWNERS_TABLE,
    RelationalAccessRepository,
)
from powercontext.server.settings import ServerSettings

pytestmark = pytest.mark.real_e2e


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_configured_database_persists_logical_skill_grant_and_revocation(
    pytestconfig: pytest.Config, backend: str
) -> None:
    if pytestconfig.getoption("real_e2e_mode") not in {"configured", "all"}:
        pytest.skip("configured Access Control acceptance runs in configured mode")

    load_dotenv(pytestconfig.getoption("real_e2e_env_file"), override=False)
    settings = ServerSettings()
    opener = open_builtin_access_control if backend == "builtin" else open_casbin_access_control
    suffix = uuid4().hex
    scope_id = f"configured-real-access:{suffix}"
    deployment_id = f"configured-real-access-{suffix}"
    admin = PrincipalRef(type="service", id=f"{deployment_id}:admin")
    receiver = PrincipalRef(type="user", id=f"{deployment_id}:receiver")
    competing_receiver = PrincipalRef(type="user", id=f"{deployment_id}:competing-receiver")
    replacement_receivers = (
        PrincipalRef(type="user", id=f"{deployment_id}:replacement-a"),
        PrincipalRef(type="user", id=f"{deployment_id}:replacement-b"),
    )

    async def scenario() -> None:
        exact = ResourceRef.artifact(
            scope_id,
            family="skill",
            artifact_id=f"managed-skill-{suffix}",
        )
        other = ResourceRef.artifact(
            scope_id,
            family="skill",
            artifact_id=f"other-skill-{suffix}",
        )
        handoff = ResourceRef.artifact(
            scope_id,
            family="handoff",
            artifact_id=f"handoff-{suffix}",
        )
        context = AccessAuditContext(transport="test", operation="configured-real-access")
        persisted_receiver: PrincipalRef | None = None
        try:
            async with opener(
                settings.database,
                bootstrap_administrators=(admin,),
                deployment_id=deployment_id,
            ) as access:
                await access.establish_artifact_owner(
                    exact,
                    admin,
                    idempotency_key=f"owner-skill-{suffix}",
                    context=context,
                )
                await access.establish_artifact_owner(
                    other,
                    admin,
                    idempotency_key=f"owner-other-skill-{suffix}",
                    context=context,
                )
                await access.establish_artifact_owner(
                    handoff,
                    admin,
                    idempotency_key=f"owner-handoff-{suffix}",
                    context=context,
                )
                binding = await access.create_binding(
                    admin,
                    CreateBinding(
                        subject=receiver,
                        resource=exact,
                        role=AccessRole.ARTIFACT_VIEWER,
                        idempotency_key=f"share-logical-skill-{suffix}",
                    ),
                    context=context,
                )
                assert (await access.require(receiver, AccessAction.ARTIFACT_READ, exact, context=context)).allowed
                with pytest.raises(AccessDeniedError):
                    await access.require(receiver, AccessAction.ARTIFACT_WRITE, exact, context=context)
                with pytest.raises(AccessDeniedError):
                    await access.require(receiver, AccessAction.ARTIFACT_READ, other, context=context)

                visible = await access.list_resources(
                    receiver,
                    action=AccessAction.ARTIFACT_READ,
                    resource_type=AccessResourceType.ARTIFACT,
                    family="skill",
                    context=context,
                )
                assert visible.items == (exact,)
                assert visible.total == 1

                revoked = await access.revoke_binding(
                    admin,
                    binding.binding_id,
                    expected_version=binding.version,
                    idempotency_key=f"revoke-skill-share-{suffix}",
                    context=context,
                )
                assert revoked.version == binding.version + 1
                with pytest.raises(AccessDeniedError):
                    await access.require(receiver, AccessAction.ARTIFACT_READ, exact, context=context)
                assert (
                    await access.list_resources(
                        receiver,
                        action=AccessAction.ARTIFACT_READ,
                        resource_type=AccessResourceType.ARTIFACT,
                        family="skill",
                        context=context,
                    )
                ).total == 0

                async def create_receiver(subject: PrincipalRef) -> AccessBinding:
                    return await access.create_binding(
                        admin,
                        CreateBinding(
                            subject=subject,
                            resource=handoff,
                            role=AccessRole.HANDOFF_RECEIVER,
                            idempotency_key=f"handoff-receiver-{subject.id}",
                        ),
                        context=context,
                    )

                create_results = await asyncio.gather(
                    create_receiver(receiver),
                    create_receiver(competing_receiver),
                    return_exceptions=True,
                )
                created = [result for result in create_results if isinstance(result, AccessBinding)]
                create_conflicts = [result for result in create_results if isinstance(result, AccessConflictError)]
                assert len(created) == 1
                assert len(create_conflicts) == 1

                original = created[0]

                async def replace_receiver(subject: PrincipalRef) -> BindingReplacement:
                    return await access.replace_binding(
                        admin,
                        ReplaceBinding(
                            binding_id=original.binding_id,
                            expected_version=original.version,
                            subject=subject,
                            idempotency_key=f"replace-handoff-receiver-{subject.id}",
                        ),
                        context=context,
                    )

                replace_results = await asyncio.gather(
                    *(replace_receiver(subject) for subject in replacement_receivers),
                    return_exceptions=True,
                )
                replacements = [result for result in replace_results if isinstance(result, BindingReplacement)]
                replace_conflicts = [result for result in replace_results if isinstance(result, AccessConflictError)]
                assert len(replacements) == 1
                assert len(replace_conflicts) == 1
                replacement = replacements[0]
                assert (
                    await access.replace_binding(
                        admin,
                        ReplaceBinding(
                            binding_id=original.binding_id,
                            expected_version=original.version,
                            subject=replacement.current.subject,
                            idempotency_key=replacement.current.idempotency_key,
                        ),
                        context=context,
                    )
                ) == replacement
                assert isinstance(replacement.current.subject, PrincipalRef)
                persisted_receiver = replacement.current.subject
                await _exercise_ownership_and_expiration(access, scope_id, admin, receiver, competing_receiver, context)
                assert await access.readiness()
                await access.record_receipt_identity(
                    HandoffReceiptIdentity(scope_id, "configured-receipt", receiver, False)
                )
                receipt_results = await asyncio.gather(
                    access.record_receipt_identity(
                        HandoffReceiptIdentity(scope_id, "configured-receipt-race", receiver, True)
                    ),
                    access.record_receipt_identity(
                        HandoffReceiptIdentity(scope_id, "configured-receipt-race", competing_receiver, False)
                    ),
                    return_exceptions=True,
                )
                receipt_winners = [value for value in receipt_results if isinstance(value, HandoffReceiptIdentity)]
                assert len(receipt_winners) == 1
                assert sum(isinstance(value, AccessConflictError) for value in receipt_results) == 1
                assert await access.record_receipt_identity(receipt_winners[0]) == receipt_winners[0]

            assert persisted_receiver is not None
            async with opener(
                settings.database,
                deployment_id=deployment_id,
            ) as reopened:
                assert await reopened.readiness()
                assert await reopened.receipt_identity(scope_id, "configured-receipt") == HandoffReceiptIdentity(
                    scope_id, "configured-receipt", receiver, False
                )
                assert await reopened.receipt_identity(scope_id, "configured-receipt-race") == receipt_winners[0]
                assert (
                    await reopened.require(
                        persisted_receiver,
                        AccessAction.HANDOFF_ACKNOWLEDGE,
                        handoff,
                        context=context,
                    )
                ).allowed
        finally:
            remaining = await _purge_scope(
                settings.database,
                scope_id=scope_id,
                deployment_id=deployment_id,
                actor_ids=(
                    admin.id,
                    receiver.id,
                    competing_receiver.id,
                    *(principal.id for principal in replacement_receivers),
                ),
            )
            assert remaining == 0

    asyncio.run(scenario())


async def _exercise_ownership_and_expiration(
    access: AccessControlService,
    scope_id: str,
    admin: PrincipalRef,
    first_receiver: PrincipalRef,
    second_receiver: PrincipalRef,
    context: AccessAuditContext,
) -> None:
    candidate_target = ResourceRef.artifact(scope_id, family="skill", artifact_id="candidate-same-id")
    attestation = await access.attest_candidate_owner(
        scope_id=scope_id,
        candidate_id="candidate-same-id",
        family="skill",
        proposed_owner=first_receiver,
        target=candidate_target,
        idempotency_key="candidate-owner",
    )
    assert await access.artifact_owner(candidate_target) is None
    assert (
        await access.list_resources(
            first_receiver,
            action=AccessAction.ARTIFACT_READ,
            resource_type=AccessResourceType.ARTIFACT,
            family="skill",
            context=context,
        )
    ).items == ()
    await access.establish_artifact_owner(
        candidate_target, admin, idempotency_key="candidate-formal-owner", context=context
    )
    assert await access.candidate_owner(scope_id, "candidate-same-id") == attestation
    assert (
        await access.attest_candidate_owner(
            scope_id=scope_id,
            candidate_id="candidate-same-id",
            family="skill",
            proposed_owner=first_receiver,
            target=candidate_target,
            idempotency_key="candidate-owner",
        )
        == attestation
    )
    with pytest.raises(AccessConflictError):
        await access.attest_candidate_owner(
            scope_id=scope_id,
            candidate_id="candidate-same-id",
            family="skill",
            proposed_owner=second_receiver,
            target=candidate_target,
            idempotency_key="candidate-wrong-owner",
        )
    with pytest.raises(AccessDeniedError):
        await access.require(first_receiver, AccessAction.ARTIFACT_WRITE, candidate_target, context=context)

    repository = access.relationships
    assert isinstance(repository, RelationalAccessRepository)
    now = datetime.now(UTC)
    timed = AccessControlService(
        BuiltinAuthorizationProvider(repository, deployment_id=access.deployment_id, clock=lambda: now),
        relationships=repository,
        audit=repository,
        deployment_id=access.deployment_id,
        clock=lambda: now,
    )
    expiring = ResourceRef.artifact(scope_id, family="handoff", artifact_id="expiring-handoff")
    await timed.establish_artifact_owner(expiring, admin, idempotency_key="expiring-owner", context=context)
    old = await timed.create_binding(
        admin,
        CreateBinding(
            subject=first_receiver,
            resource=expiring,
            role=AccessRole.HANDOFF_RECEIVER,
            idempotency_key="expiring-receiver",
            expires_at=(now + timedelta(seconds=30)).astimezone(timezone(timedelta(hours=8))),
        ),
        context=context,
    )

    async def claim(subject: PrincipalRef) -> AccessBinding:
        return await timed.create_binding(
            admin,
            CreateBinding(
                subject=subject,
                resource=expiring,
                role=AccessRole.HANDOFF_RECEIVER,
                idempotency_key=f"expiration-claim-{subject.id}",
            ),
            context=context,
        )

    with pytest.raises(AccessConflictError):
        await claim(second_receiver)
    now += timedelta(seconds=30)
    results = await asyncio.gather(claim(first_receiver), claim(second_receiver), return_exceptions=True)
    winners = [value for value in results if isinstance(value, AccessBinding)]
    assert len(winners) == 1, results
    assert sum(isinstance(value, AccessConflictError) for value in results) == 1
    winner = winners[0]
    assert await repository.get_binding(old.binding_id) == old
    assert await repository.create_binding(old) == old
    with pytest.raises(AccessConflictError):
        await timed.replace_binding(
            admin,
            ReplaceBinding(
                binding_id=old.binding_id,
                expected_version=old.version,
                subject=admin,
                idempotency_key="replace-expired-receiver",
            ),
            context=context,
        )
    revoked = await timed.revoke_binding(
        admin,
        old.binding_id,
        expected_version=old.version,
        idempotency_key="revoke-expired",
        context=context,
    )
    assert (
        await timed.revoke_binding(
            admin,
            old.binding_id,
            expected_version=old.version,
            idempotency_key="revoke-expired",
            context=context,
        )
        == revoked
    )
    assert isinstance(winner.subject, PrincipalRef)
    assert (await timed.require(winner.subject, AccessAction.HANDOFF_ACKNOWLEDGE, expiring, context=context)).allowed
    with pytest.raises(AccessConflictError):
        await claim(admin)
    await timed.revoke_binding(
        admin,
        winner.binding_id,
        expected_version=winner.version,
        idempotency_key="release-reclaimed",
        context=context,
    )
    assert (await claim(admin)).subject == admin


async def _purge_scope(
    database: DatabaseConfig,
    *,
    scope_id: str,
    deployment_id: str,
    actor_ids: tuple[str, ...],
) -> int:
    async with _profile(database) as profile, profile.database.transaction() as connection:
        await connection.execute(
            delete(ACCESS_AUDIT_EVENTS_TABLE).where(
                or_(
                    ACCESS_AUDIT_EVENTS_TABLE.c.scope_id == scope_id,
                    ACCESS_AUDIT_EVENTS_TABLE.c.deployment_id == deployment_id,
                )
            )
        )
        await connection.execute(delete(ACCESS_OWNERS_TABLE).where(ACCESS_OWNERS_TABLE.c.scope_id == scope_id))
        await connection.execute(
            delete(ACCESS_BINDINGS_TABLE).where(
                or_(
                    ACCESS_BINDINGS_TABLE.c.scope_id == scope_id,
                    ACCESS_BINDINGS_TABLE.c.deployment_id == deployment_id,
                )
            )
        )
        await connection.execute(
            delete(ACCESS_IDEMPOTENCY_TABLE).where(ACCESS_IDEMPOTENCY_TABLE.c.actor_id.in_(actor_ids))
        )
        binding_count = int(
            await connection.scalar(
                select(func.count())
                .select_from(ACCESS_BINDINGS_TABLE)
                .where(
                    or_(
                        ACCESS_BINDINGS_TABLE.c.scope_id == scope_id,
                        ACCESS_BINDINGS_TABLE.c.deployment_id == deployment_id,
                    )
                )
            )
            or 0
        )
        audit_count = int(
            await connection.scalar(
                select(func.count())
                .select_from(ACCESS_AUDIT_EVENTS_TABLE)
                .where(
                    or_(
                        ACCESS_AUDIT_EVENTS_TABLE.c.scope_id == scope_id,
                        ACCESS_AUDIT_EVENTS_TABLE.c.deployment_id == deployment_id,
                    )
                )
            )
            or 0
        )
        owner_count = int(
            await connection.scalar(
                select(func.count()).select_from(ACCESS_OWNERS_TABLE).where(ACCESS_OWNERS_TABLE.c.scope_id == scope_id)
            )
            or 0
        )
        idempotency_count = int(
            await connection.scalar(
                select(func.count())
                .select_from(ACCESS_IDEMPOTENCY_TABLE)
                .where(ACCESS_IDEMPOTENCY_TABLE.c.actor_id.in_(actor_ids))
            )
            or 0
        )
        return binding_count + audit_count + owner_count + idempotency_count


@asynccontextmanager
async def _profile(database: DatabaseConfig) -> AsyncIterator[OceanBaseProfile | SeekDBProfile | SQLiteProfile]:
    if isinstance(database, OceanBaseConfig):
        context = OceanBaseProfile.open(database, tables=())
    elif isinstance(database, SeekDBConfig):
        context = SeekDBProfile.open(database, tables=())
    else:
        assert isinstance(database, SQLiteConfig)
        context = SQLiteProfile.open(database, tables=())
    async with context as profile:
        yield profile
