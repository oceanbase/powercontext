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

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.runtime.composition import BuiltinConfigurationError
from powercontext.server.authz import (
    AccessAction,
    AccessAuditContext,
    AccessControlService,
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

    async def topic_commit(self, connection: AsyncConnection, scope_id: str, _operations: Sequence[Any]) -> None:
        """Authorize one Scope-owned Topic Memory publication.

        Topic Memory is shared Scope knowledge: it registers no Artifact Family
        Access Profile and carries no per-topic owner, so its publications are
        authorized by Scope authority alone. The batch is accepted unchanged.
        """
        await self._authorize_scope(self.access.with_connection(connection), scope_id)


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
