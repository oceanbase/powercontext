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

"""Lifecycle assembly for the built-in relational Authorization Provider."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.runtime.composition import BuiltinConfigurationError
from powercontext.builtin.runtime.config import DatabaseConfig
from powercontext.server.authz.casbin import CasbinAuthorizationProvider
from powercontext.server.authz.models import (
    DEFAULT_DEPLOYMENT_ID,
    AccessBinding,
    AccessBindingState,
    AccessRole,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.repository import ACCESS_TABLES, RelationalAccessRepository
from powercontext.server.authz.service import (
    AccessControlService,
    AccessProviderCapabilities,
    BuiltinAuthorizationProvider,
)


@asynccontextmanager
async def open_builtin_access_control(
    database: DatabaseConfig,
    *,
    bootstrap_administrators: Sequence[PrincipalRef] = (),
    deployment_id: str = DEFAULT_DEPLOYMENT_ID,
) -> AsyncIterator[AccessControlService]:
    """Open a Server-owned Access schema without coupling it to Runtime domains."""

    async with _open_access_repository(database) as repository:
        await _bootstrap_server_roles(repository, bootstrap_administrators, deployment_id=deployment_id)
        provider = BuiltinAuthorizationProvider(
            repository,
            deployment_id=deployment_id,
        )
        yield AccessControlService(
            provider,
            relationships=repository,
            audit=repository,
            deployment_id=deployment_id,
            provider_capabilities=AccessProviderCapabilities(
                safe_resource_filtering=True,
                multi_requirement_check=True,
                relationship_management=True,
                group_subjects=False,
            ),
            static_scope_principal=bootstrap_administrators[0] if len(bootstrap_administrators) == 1 else None,
        )


@asynccontextmanager
async def open_casbin_access_control(
    database: DatabaseConfig,
    *,
    bootstrap_administrators: Sequence[PrincipalRef] = (),
    deployment_id: str = DEFAULT_DEPLOYMENT_ID,
) -> AsyncIterator[AccessControlService]:
    """Open the writable embedded Casbin adapter over the canonical Access schema."""

    async with _open_access_repository(database) as repository:
        await _bootstrap_server_roles(repository, bootstrap_administrators, deployment_id=deployment_id)
        provider = CasbinAuthorizationProvider(
            repository,
            deployment_id=deployment_id,
        )
        yield AccessControlService(
            provider,
            relationships=repository,
            audit=repository,
            deployment_id=deployment_id,
            provider_capabilities=AccessProviderCapabilities(
                safe_resource_filtering=True,
                multi_requirement_check=True,
                relationship_management=True,
                group_subjects=False,
            ),
            static_scope_principal=bootstrap_administrators[0] if len(bootstrap_administrators) == 1 else None,
        )


@asynccontextmanager
async def _open_access_repository(
    database: DatabaseConfig,
) -> AsyncIterator[RelationalAccessRepository]:
    if isinstance(database, SQLiteConfig):
        profile_context = SQLiteProfile.open(database, tables=ACCESS_TABLES)
    elif isinstance(database, OceanBaseConfig):
        profile_context = OceanBaseProfile.open(database, tables=ACCESS_TABLES)
    elif isinstance(database, SeekDBConfig):
        profile_context = SeekDBProfile.open(database, tables=ACCESS_TABLES)
    else:
        raise BuiltinConfigurationError("database")
    async with profile_context as profile:
        yield RelationalAccessRepository(profile.database)


async def _bootstrap_server_roles(
    repository: RelationalAccessRepository,
    principals: Sequence[PrincipalRef],
    *,
    deployment_id: str,
) -> None:
    resource = ResourceRef.server(deployment_id)
    for principal in principals:
        for role in (AccessRole.SERVER_OBSERVER, AccessRole.SERVER_ADMIN):
            key = f"static-preset:{deployment_id}:{principal.id}:{role.value}"
            await repository.create_binding(
                AccessBinding(
                    binding_id=str(uuid4()),
                    subject=principal,
                    resource=resource,
                    role=role,
                    granted_by=principal,
                    reason="static bearer preset",
                    created_at=datetime.now(UTC),
                    expires_at=None,
                    state=AccessBindingState.ACTIVE,
                    version=1,
                    policy_revision="pending",
                    idempotency_key=key,
                )
            )


__all__ = ("open_builtin_access_control", "open_casbin_access_control")
