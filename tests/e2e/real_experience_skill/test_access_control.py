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
    AccessDeniedError,
    AccessResourceType,
    AccessRole,
    BindingReplacement,
    CreateBinding,
    PrincipalRef,
    ReplaceBinding,
    ResourceRef,
)
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.authz.repository import (
    ACCESS_AUDIT_EVENTS_TABLE,
    ACCESS_BINDING_LEASES_TABLE,
    ACCESS_BINDINGS_TABLE,
    ACCESS_CANDIDATE_OWNERS_TABLE,
    ACCESS_IDEMPOTENCY_TABLE,
    ACCESS_OWNERS_TABLE,
)
from powercontext.server.settings import ServerSettings

pytestmark = pytest.mark.real_e2e


def test_configured_database_persists_logical_skill_grant_and_revocation(pytestconfig: pytest.Config) -> None:
    if pytestconfig.getoption("real_e2e_mode") not in {"configured", "all"}:
        pytest.skip("configured Access Control acceptance runs in configured mode")

    load_dotenv(pytestconfig.getoption("real_e2e_env_file"), override=False)
    settings = ServerSettings()
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
            async with open_builtin_access_control(
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

            assert persisted_receiver is not None
            async with open_builtin_access_control(
                settings.database,
                deployment_id=deployment_id,
            ) as reopened:
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
        await connection.execute(
            delete(ACCESS_BINDING_LEASES_TABLE).where(
                ACCESS_BINDING_LEASES_TABLE.c.binding_id.in_(
                    select(ACCESS_BINDINGS_TABLE.c.binding_id).where(
                        or_(
                            ACCESS_BINDINGS_TABLE.c.scope_id == scope_id,
                            ACCESS_BINDINGS_TABLE.c.deployment_id == deployment_id,
                        )
                    )
                )
            )
        )
        await connection.execute(
            delete(ACCESS_CANDIDATE_OWNERS_TABLE).where(ACCESS_CANDIDATE_OWNERS_TABLE.c.scope_id == scope_id)
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
        candidate_count = int(
            await connection.scalar(
                select(func.count())
                .select_from(ACCESS_CANDIDATE_OWNERS_TABLE)
                .where(ACCESS_CANDIDATE_OWNERS_TABLE.c.scope_id == scope_id)
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
        return binding_count + audit_count + owner_count + candidate_count + idempotency_count


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
