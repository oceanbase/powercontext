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


"""Profile support for the existing Artifact Create/Replace commands."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from typing_extensions import override

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.profile.models import (
    PROFILE_ARTIFACT_ID,
    Profile,
    ProfileContent,
    ProfileGeneration,
    ProfileWriteContent,
)
from powercontext.builtin.persistence.family_management import _RepositoryFamilyWriter
from powercontext.builtin.persistence.profile import ProfilePolicyRepository
from powercontext.builtin.records import InvalidBaseAccessRequestError


class ProfileManagementWriter(_RepositoryFamilyWriter):
    family = "profile"
    content_type = ProfileWriteContent

    @override
    def artifact_id_for_create(self, generated: str, /) -> str:
        return PROFILE_ARTIFACT_ID

    @override
    def validate_create(self, content):
        command = cast(ProfileWriteContent, super().validate_create(content))
        if command.restored_from_revision is not None:
            raise InvalidBaseAccessRequestError("restored_from_revision", "requires Replace")
        return command

    async def _coordinate(self, connection, scope_id):
        policies = ProfilePolicyRepository()
        policy = await policies.get(connection, scope_id, for_update=True)
        if policy is None:
            policy = await policies.create(connection, scope_id)
        return await policies.update(connection, policy)

    async def create(self, connection, scope_id, artifact_id, content, direct_source, /) -> Profile:
        await self._coordinate(connection, scope_id)
        command = cast(ProfileWriteContent, content)
        snapshot = ProfileContent(
            content=command.content,
            generation=ProfileGeneration(mode="manual_create", created_at=datetime.now(UTC)),
        )
        return cast(Profile, await self._create_artifact(connection, scope_id, artifact_id, snapshot, direct_source))

    async def replace(self, connection, scope_id, current, content, direct_source, /) -> Profile:
        await self._coordinate(connection, scope_id)
        command = cast(ProfileWriteContent, content)
        restored = command.restored_from_revision
        if restored is not None:
            historical = await self._artifacts.get(
                connection,
                scope_id,
                ArtifactRef(family="profile", artifact_id=PROFILE_ARTIFACT_ID, revision=restored),
            )
            if historical.content.content != command.content:
                raise InvalidBaseAccessRequestError("content", "does not match the restored revision")
        snapshot = ProfileContent(
            content=command.content,
            generation=ProfileGeneration(
                mode="manual_replace" if restored is None else "rollback",
                created_at=datetime.now(UTC),
                restored_from_revision=restored,
            ),
        )
        return cast(Profile, await self._revise_artifact(connection, scope_id, current, snapshot, direct_source))
