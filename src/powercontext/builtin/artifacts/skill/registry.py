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

from powercontext.builtin.artifacts.skill.external import (
    MAX_EXTERNAL_SKILL_MANIFEST_BYTES,
    ExternalSkillNotFoundError,
    ExternalSkillProvider,
    ExternalSkillProviderScan,
    ExternalSkillResolution,
    ExternalSkillResolutionStatus,
    ExternalSkillSnapshot,
    ExternalSkillSnapshotUnavailableError,
)
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
        async with self._database.transaction() as connection:
            await self._repository.replace(
                connection,
                self._scope_id,
                self._provider.name,
                self._provider.host_id,
                snapshot.registrations,
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
    ) -> ExternalSkillSnapshot:
        """Capture exact primary content only while the whole package fingerprint is stable."""

        resolution = await self.resolve(external_skill_id, fingerprint)
        if resolution.status is not ExternalSkillResolutionStatus.AVAILABLE or resolution.entrypoint is None:
            raise ExternalSkillSnapshotUnavailableError(external_skill_id)
        try:
            manifest = await asyncio.to_thread(_read_manifest, resolution.entrypoint)
        except (OSError, UnicodeError, ValueError):
            raise ExternalSkillSnapshotUnavailableError(external_skill_id) from None
        confirmed = await self.resolve(external_skill_id, fingerprint)
        if confirmed.status is not ExternalSkillResolutionStatus.AVAILABLE:
            raise ExternalSkillSnapshotUnavailableError(external_skill_id)
        return ExternalSkillSnapshot(registration=resolution.registration, manifest=manifest)


def _read_manifest(entrypoint: str) -> str:
    with open(entrypoint, "rb") as stream:
        content = stream.read(MAX_EXTERNAL_SKILL_MANIFEST_BYTES + 1)
    if len(content) > MAX_EXTERNAL_SKILL_MANIFEST_BYTES:
        raise ValueError("external Skill manifest exceeds the snapshot bound")  # noqa: TRY003
    return content.decode("utf-8")


__all__ = ["ExternalSkillRegistryService"]
