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

"""Application service for one scoped external Skill Registry projection."""

from __future__ import annotations

import asyncio
from pathlib import Path

from powercontext.builtin.artifacts.skill.external import (
    CapturedExternalSkillPackage,
    ExternalSkillNotFoundError,
    ExternalSkillProvider,
    ExternalSkillProviderScan,
    ExternalSkillResolution,
    ExternalSkillResolutionStatus,
    ExternalSkillSnapshotUnavailableError,
)
from powercontext.builtin.artifacts.skill.package import SkillPackageError, capture_skill_directory
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.external_skills import ExternalSkillRepository


class ExternalSkillRegistryService:
    """Refresh and resolve one scope without taking ownership of package content."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        repository: ExternalSkillRepository,
        provider: ExternalSkillProvider,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._repository = repository
        self._provider = provider

    async def scan(self) -> ExternalSkillProviderScan:
        snapshot = await asyncio.to_thread(self._provider.scan)
        provider_names = getattr(self._provider, "provider_names", (self._provider.name,))
        async with self._database.transaction() as connection:
            for provider_name in provider_names:
                registrations = tuple(
                    registration for registration in snapshot.registrations if registration.provider == provider_name
                )
                await self._repository.replace(
                    connection,
                    self._scope_id,
                    provider_name,
                    self._provider.host_id,
                    registrations,
                )
        return snapshot

    async def list(self, /, *, include_unavailable: bool = False) -> tuple[ExternalSkillResolution, ...]:
        async with self._database.transaction() as connection:
            registrations = await self._repository.list(connection, self._scope_id)
        resolved = tuple(
            await asyncio.gather(
                *(asyncio.to_thread(self._provider.resolve, registration) for registration in registrations)
            )
        )
        if include_unavailable:
            return resolved
        return tuple(value for value in resolved if value.status is ExternalSkillResolutionStatus.AVAILABLE)

    async def resolve(
        self,
        external_skill_id: str,
        fingerprint: str,
        /,
    ) -> ExternalSkillResolution:
        try:
            async with self._database.transaction() as connection:
                registration = await self._repository.get(connection, self._scope_id, external_skill_id)
        except RepositoryNotFoundError:
            raise ExternalSkillNotFoundError(external_skill_id) from None
        if registration.fingerprint != fingerprint:
            return ExternalSkillResolution(
                registration=registration,
                status=ExternalSkillResolutionStatus.UNAVAILABLE,
            )
        return await asyncio.to_thread(self._provider.resolve, registration)

    async def snapshot(
        self,
        external_skill_id: str,
        fingerprint: str,
        /,
    ) -> CapturedExternalSkillPackage:
        """Capture a complete canonical package while the external fingerprint is stable."""

        resolution = await self.resolve(external_skill_id, fingerprint)
        if resolution.status is not ExternalSkillResolutionStatus.AVAILABLE or resolution.entrypoint is None:
            raise ExternalSkillSnapshotUnavailableError(external_skill_id)
        try:
            package = await asyncio.to_thread(capture_skill_directory, Path(resolution.entrypoint).parent)
        except (OSError, UnicodeError, SkillPackageError):
            raise ExternalSkillSnapshotUnavailableError(external_skill_id) from None
        confirmed = await self.resolve(external_skill_id, fingerprint)
        if confirmed.status is not ExternalSkillResolutionStatus.AVAILABLE:
            raise ExternalSkillSnapshotUnavailableError(external_skill_id)
        return CapturedExternalSkillPackage(registration=resolution.registration, package=package)


__all__ = ["ExternalSkillRegistryService"]
