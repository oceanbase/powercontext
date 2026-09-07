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


"""Profile policy persistence; processing metadata lives in existing content BLOBs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.profile.models import ProfilePolicy
from powercontext.builtin.persistence.tables import PROFILE_POLICIES_TABLE
from powercontext.builtin.records import BaseValueConflictError


class ProfilePolicyRepository:
    async def get(
        self, connection: AsyncConnection, scope_id: str, *, for_update: bool = False
    ) -> ProfilePolicy | None:
        query = select(PROFILE_POLICIES_TABLE).where(PROFILE_POLICIES_TABLE.c.scope_id == scope_id)
        if for_update:
            query = query.with_for_update()
        row = (await connection.execute(query)).mappings().one_or_none()
        if row is None:
            return None
        values = dict(row)
        timestamp = values["updated_at"]
        if timestamp.tzinfo is None:
            values["updated_at"] = timestamp.replace(tzinfo=UTC)
        return ProfilePolicy.model_validate(values)

    async def create(
        self, connection: AsyncConnection, scope_id: str, *, enabled: bool = False, activation_mode="automatic"
    ) -> ProfilePolicy:
        policy = ProfilePolicy(
            scope_id=scope_id,
            generation_enabled=enabled,
            activation_mode=activation_mode,
            version=1,
            updated_at=datetime.now(UTC),
        )
        await connection.execute(insert(PROFILE_POLICIES_TABLE).values(**policy.model_dump()))
        return policy

    async def update(self, connection: AsyncConnection, policy: ProfilePolicy, **changes) -> ProfilePolicy:
        updated = policy.model_copy(update={**changes, "version": policy.version + 1, "updated_at": datetime.now(UTC)})
        result = await connection.execute(
            update(PROFILE_POLICIES_TABLE)
            .where(
                PROFILE_POLICIES_TABLE.c.scope_id == policy.scope_id, PROFILE_POLICIES_TABLE.c.version == policy.version
            )
            .values(**updated.model_dump())
        )
        if result.rowcount != 1:
            raise BaseValueConflictError("profile_policy", (policy.scope_id,))
        return updated
