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

"""Trusted identities and transaction-bound authorization for Atomic Memory."""

from __future__ import annotations

from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexFilter,
    AtomicMemoryProjectionSecurity,
    AtomicMemoryReadGrant,
)
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACT_TAGS_TABLE
from powercontext.builtin.tags import TagFilter


@dataclass(frozen=True, slots=True)
class AtomicMemoryExecutionContext:
    """A server-authenticated or explicitly trusted local execution identity."""

    principal: Any
    access: Any = None
    audit: Any = None
    trusted_local: bool = False
    read_connection: AsyncConnection | None = None


class AtomicMemorySecurity:
    def __init__(self, database: AsyncDatabase) -> None:
        self.database = database

    @asynccontextmanager
    async def read_transaction(self, context: AtomicMemoryExecutionContext):
        # The read-local binding shares the caller's audit deferral. Its original
        # store receives decisions after the snapshot closes, including nested reads.
        async with (
            context.access.defer_decision_audit() if context.access is not None else nullcontext(),
            self.database.transaction(consistent_snapshot=True) as connection,
        ):
            if connection.dialect.name == "sqlite":
                # SAVEPOINT alone does not pin SQLite's data snapshot. Read a
                # persistent table before a policy provider on another database
                # can allow a request while newer private content is committed.
                await connection.execute(select(ARTIFACT_HEADS_TABLE.c.scope_id).limit(1))
            if context.access is not None:
                from powercontext.server.authz import AccessUnavailableError
                from powercontext.server.authz.repository import RelationalAccessRepository
                from powercontext.server.authz.service import AccessControlService, BuiltinAuthorizationProvider

                access = context.access
                provider = access.provider
                canonical = type(provider) is BuiltinAuthorizationProvider
                if not canonical and type(provider).__module__ == "powercontext.server.authz.casbin":
                    from powercontext.server.authz.casbin import CasbinAuthorizationProvider

                    canonical = type(provider) is CasbinAuthorizationProvider
                if canonical and type(provider._repository) is RelationalAccessRepository:
                    repository = provider._repository
                    try:
                        bound_repository = repository.with_connection(connection)
                    except AccessUnavailableError as error:
                        if error.code != "transactional_relationships_unavailable":
                            raise
                    else:
                        bound = AccessControlService(
                            provider.with_repository(bound_repository),
                            relationships=bound_repository
                            if access.relationships is repository
                            else access.relationships,
                            audit=access.audit,
                            deployment_id=access.deployment_id,
                            provider_capabilities=access.provider_capabilities,
                            clock=access._clock,
                            cursor_secret=access._cursor_secret,
                            static_scope_principal=access._static_scope_principal,
                        )
                        bound._deferred_decisions = access._deferred_decisions
                        access = bound
                context = replace(context, access=access, read_connection=connection)
            yield connection, context

    async def lock_transaction(self, connection: AsyncConnection, scope_id: str, context: Any) -> None:
        from powercontext.server.authz import AccessUnavailableError
        from powercontext.server.authz.repository import ACCESS_POLICY_HEADS_TABLE

        result = await connection.execute(
            update(ACCESS_POLICY_HEADS_TABLE)
            .where(ACCESS_POLICY_HEADS_TABLE.c.name == "authorization")
            .values(revision=ACCESS_POLICY_HEADS_TABLE.c.revision)
        )
        if result.rowcount != 1:
            raise AccessUnavailableError("authorization_policy_pending")

    @staticmethod
    def subject(context: AtomicMemoryExecutionContext) -> str:
        return f"{context.principal.type}:{context.principal.id}"

    async def authorize(
        self,
        connection: AsyncConnection,
        scope_id: str,
        context: AtomicMemoryExecutionContext,
        action: str,
        ref: ArtifactRef | None = None,
    ) -> None:
        from powercontext.server.authz import AccessAction, AccessDeniedError, ResourceRef
        from powercontext.server.authz.repository import RelationalAccessRepository

        if context.access is None:
            if not context.trusted_local:
                raise AccessDeniedError()
            if ref is not None and action == "write":
                owner = await RelationalAccessRepository(self.database, connection=connection).get_artifact_owner(
                    ResourceRef.artifact(scope_id, family=ref.family, artifact_id=ref.artifact_id)
                )
                if owner is None or owner.owner != context.principal:
                    raise AccessDeniedError()
            return
        access = context.access
        if context.read_connection is not connection:
            access = access.with_connection(connection)
        if ref is not None and ref.family in {"memory", "topic-memory", "prompt"} and action == "read":
            # Operational Prompt, Topic Memory and frozen collection lineage use Scope read.
            # Internal exact lineage validation follows; Prompt writes keep their own scope.admin boundary.
            await access.require(
                context.principal, AccessAction.SCOPE_READ, ResourceRef.scope(scope_id), context=context.audit
            )
            return
        if ref is None or action == "create":
            permission = AccessAction.SCOPE_READ if action == "read" else AccessAction.SCOPE_CONTRIBUTE
            resource = ResourceRef.scope(scope_id)
            await access.bootstrap_static_scope(context.principal, scope_id, context=context.audit)
        else:
            permission = AccessAction.ARTIFACT_READ if action == "read" else AccessAction.ARTIFACT_WRITE
            resource = ResourceRef.artifact(scope_id, family=ref.family, artifact_id=ref.artifact_id)
        await access.require(context.principal, permission, resource, context=context.audit)
        # Scope authority cannot turn a shared or foreign-owned memory into a write target.
        if ref is not None and action == "write":
            owner = await access.artifact_owner(resource)
            if owner is None or owner.owner != context.principal:
                raise AccessDeniedError()

    async def authorize_sources(
        self, connection: AsyncConnection, scope_id: str, context: AtomicMemoryExecutionContext, sources
    ) -> None:
        if sources:
            await self.authorize(connection, scope_id, context, "read")

    async def establish_owner(
        self, connection: AsyncConnection, scope_id: str, artifact_id: str, context: AtomicMemoryExecutionContext
    ) -> None:
        from powercontext.server.authz import ArtifactOwnerRelation, ResourceRef
        from powercontext.server.authz.repository import RelationalAccessRepository

        resource = ResourceRef.artifact(scope_id, family="atomic-memory", artifact_id=artifact_id)
        key = f"atomic-memory-owner:{scope_id}:{artifact_id}"
        if context.access is not None:
            await context.access.with_connection(connection).establish_artifact_owner(
                resource, context.principal, idempotency_key=key, context=context.audit
            )
        elif context.trusted_local:
            await RelationalAccessRepository(self.database, connection=connection).establish_artifact_owner(
                ArtifactOwnerRelation(resource, context.principal, datetime.now(UTC), "0", key)
            )
        else:
            from powercontext.server.authz import AccessIdentityRequiredError

            raise AccessIdentityRequiredError

    async def filters(
        self,
        scope_id: str,
        context: AtomicMemoryExecutionContext,
        *,
        tags: TagFilter | None = None,
        writable: bool = False,
        connection: AsyncConnection | None = None,
    ) -> AtomicMemoryIndexFilter:
        scope_read = context.trusted_local and context.access is None
        groups: tuple[str, ...] = ()
        if context.access is not None:
            from powercontext.server.authz import AccessAction, AccessUnavailableError, ResourceRef
            from powercontext.server.authz.service import BuiltinAuthorizationProvider

            provider = context.access.provider
            supported = type(provider) is BuiltinAuthorizationProvider
            if not supported and type(provider).__module__ == "powercontext.server.authz.casbin":
                from powercontext.server.authz.casbin import CasbinAuthorizationProvider

                # The repository's fixed Casbin policy shares the exact
                # relationship rules represented by this projection.
                supported = type(provider) is CasbinAuthorizationProvider
            if not supported:
                raise AccessUnavailableError("atomic_memory_projection_authorization_unavailable")
            access = context.access
            if connection is not None and context.read_connection is not connection:
                access = access.with_connection(connection)
            decision = await access.check(
                context.principal, AccessAction.SCOPE_READ, ResourceRef.scope(scope_id), context=context.audit
            )
            scope_read = decision.allowed
            groups = tuple(group.id for group in context.audit.subject_groups)
        elif not context.trusted_local:
            from powercontext.server.authz import AccessIdentityRequiredError

            raise AccessIdentityRequiredError
        return AtomicMemoryIndexFilter(
            tag_filter=tags,
            principal_type=context.principal.type,
            principal_id=context.principal.id,
            scope_read=scope_read,
            writable=writable,
            group_ids=groups,
        )


async def load_atomic_memory_tags(connection: AsyncConnection, scope_id: str, artifact_id: str) -> tuple[str, ...]:
    rows = (
        await connection.execute(
            select(ARTIFACT_TAGS_TABLE.c.tag_key)
            .where(
                ARTIFACT_TAGS_TABLE.c.scope_id == scope_id,
                ARTIFACT_TAGS_TABLE.c.family == "atomic-memory",
                ARTIFACT_TAGS_TABLE.c.artifact_id == artifact_id,
                ARTIFACT_TAGS_TABLE.c.target_type == "artifact",
                ARTIFACT_TAGS_TABLE.c.target_id == artifact_id,
            )
            .order_by(ARTIFACT_TAGS_TABLE.c.tag_key)
            .with_for_update()
        )
    ).scalars()
    return tuple(str(row) for row in rows)


async def load_atomic_memory_security(
    connection: AsyncConnection, scope_id: str, artifact_id: str, _context: Any = None
) -> AtomicMemoryProjectionSecurity:
    from powercontext.server.authz import AccessUnavailableError
    from powercontext.server.authz.repository import ACCESS_BINDINGS_TABLE, ACCESS_OWNERS_TABLE

    owner = (
        await connection.execute(
            select(ACCESS_OWNERS_TABLE.c.owner_type, ACCESS_OWNERS_TABLE.c.owner_id)
            .where(
                ACCESS_OWNERS_TABLE.c.owner_kind == "artifact",
                ACCESS_OWNERS_TABLE.c.scope_id == scope_id,
                ACCESS_OWNERS_TABLE.c.family == "atomic-memory",
                ACCESS_OWNERS_TABLE.c.artifact_id == artifact_id,
                ACCESS_OWNERS_TABLE.c.selector_type.is_(None),
            )
            .with_for_update()
        )
    ).one_or_none()
    if owner is None:
        raise AccessUnavailableError("artifact_owner_pending")
    from powercontext.server.authz import AccessRole

    bindings = (
        await connection.execute(
            select(ACCESS_BINDINGS_TABLE)
            .where(
                ACCESS_BINDINGS_TABLE.c.resource_type == "artifact",
                ACCESS_BINDINGS_TABLE.c.scope_id == scope_id,
                ACCESS_BINDINGS_TABLE.c.family == "atomic-memory",
                ACCESS_BINDINGS_TABLE.c.artifact_id == artifact_id,
                ACCESS_BINDINGS_TABLE.c.selector_type.is_(None),
                ACCESS_BINDINGS_TABLE.c.state == "active",
                ACCESS_BINDINGS_TABLE.c.role.in_((AccessRole.ARTIFACT_VIEWER.value, AccessRole.ARTIFACT_OWNER.value)),
            )
            .order_by(ACCESS_BINDINGS_TABLE.c.binding_id)
            .with_for_update()
        )
    ).mappings()
    grants = tuple(
        AtomicMemoryReadGrant(
            str(row["binding_id"]),
            str(row["subject_type"]),
            str(row["subject_id"]),
            None if row["expires_at"] is None else datetime.fromisoformat(str(row["expires_at"])),
        )
        for row in bindings
    )
    return AtomicMemoryProjectionSecurity(str(owner.owner_type), str(owner.owner_id), grants)
