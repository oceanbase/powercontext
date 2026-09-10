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

"""Reconstruct Server authorization inside a processing child.

Runtime-only SDK users do not import or require this adapter. The serialized
identity is resolved from trusted deployment settings, never from model output.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory import Memory
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.runtime.composition import BuiltinConfigurationError
from powercontext.server.authz import (
    AccessAction,
    AccessAuditContext,
    AccessControlService,
    MemoryEntrySelector,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.repository import RelationalAccessRepository
from powercontext.server.authz.service import BuiltinAuthorizationProvider
from powercontext.server.settings import ServerSettings


class WorkerSecuritySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["builtin"] = "builtin"
    principal: PrincipalRef = Field(repr=False)
    deployment_id: str
    static_preset: bool = False


def build_worker_security(
    settings: ServerSettings,
    access: AccessControlService | None,
    *,
    legacy_static_principal: PrincipalRef | None = None,
    enabled: bool = True,
    injected: bool = False,
) -> dict[str, Any] | None:
    """Fail before dispatch when an enforced provider cannot be reconstructed."""

    if not enabled or settings.access.mode == "disabled":
        return None
    if injected or (
        access is not None
        and (
            type(access.provider) is not BuiltinAuthorizationProvider
            or type(access.relationships) is not RelationalAccessRepository
            or access.audit is not access.relationships
        )
    ):
        raise BuiltinConfigurationError("artifact-processing-worker-authorization-provider")
    if settings.access.background_principal_id is not None:
        principal = PrincipalRef(
            type="service",
            id=settings.access.background_principal_id,
            description=settings.access.background_principal_description,
        )
    elif legacy_static_principal is not None:
        principal = legacy_static_principal
    else:
        raise ValueError("scheduled processing in enforced mode requires ACCESS_BACKGROUND_PRINCIPAL_ID")  # noqa: TRY003
    return WorkerSecuritySpec(
        principal=principal,
        deployment_id=settings.access.deployment_id,
        static_preset=principal == legacy_static_principal,
    ).model_dump(mode="json")


class WorkerSecurity:
    def __init__(self, access: AccessControlService, principal: PrincipalRef) -> None:
        self.access = access
        self.principal = principal
        self.context = AccessAuditContext(transport="background", operation="artifact_processing")

    async def authorize_scope(self, scope_id: str) -> None:
        await self._authorize_scope(self.access, scope_id)

    async def _authorize_scope(self, access: AccessControlService, scope_id: str) -> None:
        await access.bootstrap_static_scope(self.principal, scope_id, context=self.context)
        await access.require(
            self.principal, AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id), context=self.context
        )

    async def authorize_transaction(self, connection: AsyncConnection, scope_id: str) -> None:
        await self._authorize_scope(self.access.with_connection(connection), scope_id)

    async def authorize_memory(self, scope_id: str, current: Memory | None) -> None:
        await self._authorize_memory(self.access, scope_id, current)

    async def _authorize_memory(self, access: AccessControlService, scope_id: str, current: Memory | None) -> None:
        if current is None:
            return
        await access.require_all(
            self.principal,
            tuple(
                (AccessAction.ARTIFACT_WRITE, self._memory_resource(scope_id, current, entry.entry_id))
                for entry in current.content.manifest.entries
            ),
            context=self.context,
        )

    @staticmethod
    def _memory_resource(scope_id: str, memory: Memory, entry_id: str) -> ResourceRef:
        return ResourceRef.artifact(
            scope_id, family="memory", artifact_id=memory.artifact_id, selector=MemoryEntrySelector(entry_id=entry_id)
        )

    async def memory_commit(
        self, connection: AsyncConnection, before: Memory | None, after: Memory | None, *, scope_id: str
    ) -> None:
        bound = self.access.with_connection(connection)
        await self._authorize_memory(bound, scope_id, before)
        if after is None:
            return
        old = set() if before is None else {entry.entry_id for entry in before.content.manifest.entries}
        for entry in after.content.manifest.entries:
            if entry.entry_id not in old:
                await bound.establish_artifact_owner(
                    self._memory_resource(scope_id, after, entry.entry_id),
                    self.principal,
                    idempotency_key=f"background-memory-owner:{scope_id}:{after.artifact_id}:{entry.entry_id}",
                    context=self.context,
                )

    async def experience_commit(self, connection: AsyncConnection, candidates: Sequence[Any], *, scope_id: str) -> None:
        bound = self.access.with_connection(connection)
        for candidate in candidates:
            await bound.attest_candidate_owner(
                scope_id=scope_id,
                candidate_id=candidate.candidate_id,
                family="experience",
                proposed_owner=self.principal,
                target=None,
                idempotency_key=f"background-candidate-owner:{scope_id}:{candidate.candidate_id}",
            )

    async def authorize_profile(self, scope_id: str, current: Any) -> None:
        if current is not None:
            await self.access.require(
                self.principal,
                AccessAction.ARTIFACT_WRITE,
                ResourceRef.artifact(scope_id, family="profile", artifact_id="profile"),
                context=self.context,
            )

    async def authorize_profile_commit(self, connection: AsyncConnection, current: Any, *, scope_id: str) -> None:
        if current is not None:
            await self.access.with_connection(connection).require(
                self.principal,
                AccessAction.ARTIFACT_WRITE,
                ResourceRef.artifact(scope_id, family="profile", artifact_id="profile"),
                context=self.context,
            )

    async def profile_commit(
        self, connection: AsyncConnection, artifact: Any, candidate: Any, *, scope_id: str
    ) -> None:
        bound = self.access.with_connection(connection)
        resource = ResourceRef.artifact(scope_id, family="profile", artifact_id="profile")
        if artifact is not None:
            if await bound.artifact_owner(resource) is None:
                await bound.establish_artifact_owner(
                    resource, self.principal, idempotency_key=f"profile-owner:{scope_id}", context=self.context
                )
            else:
                await bound.require(self.principal, AccessAction.ARTIFACT_WRITE, resource, context=self.context)
        if candidate is not None:
            if candidate.target is not None:
                await bound.require(self.principal, AccessAction.ARTIFACT_WRITE, resource, context=self.context)
            await bound.attest_candidate_owner(
                scope_id=scope_id,
                candidate_id=candidate.candidate_id,
                family="profile",
                proposed_owner=self.principal,
                target=None if candidate.target is None else resource,
                idempotency_key=f"candidate-owner:{scope_id}:{candidate.candidate_id}",
            )

    async def topic_commit(self, connection: AsyncConnection, scope_id: str, operations: Sequence[Any]) -> None:
        bound = self.access.with_connection(connection)
        await self._authorize_scope(bound, scope_id)
        for operation in operations:
            resource = ResourceRef.artifact(scope_id, family="topic-memory", artifact_id=operation.artifact_id)
            if operation.current is not None:
                await bound.require(self.principal, AccessAction.ARTIFACT_WRITE, resource, context=self.context)
            else:
                await bound.establish_artifact_owner(
                    resource,
                    self.principal,
                    idempotency_key=f"background-topic-owner:{scope_id}:{operation.artifact_id}",
                    context=self.context,
                )


@asynccontextmanager
async def open_worker_security(
    security: dict[str, Any] | None, database: AsyncDatabase
) -> AsyncIterator[WorkerSecurity | None]:
    if security is None:
        yield None
        return
    spec = WorkerSecuritySpec.model_validate(security)
    repository = RelationalAccessRepository(database)
    access = AccessControlService(
        BuiltinAuthorizationProvider(repository, deployment_id=spec.deployment_id),
        relationships=repository,
        audit=repository,
        deployment_id=spec.deployment_id,
        static_scope_principal=spec.principal if spec.static_preset else None,
    )
    yield WorkerSecurity(access, spec.principal)
