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

"""Relational projection repository for host-local external Skills."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.skill.external import ExternalSkillRegistration
from powercontext.builtin.persistence.errors import (
    InvalidRepositoryArgumentError,
    RepositoryNotFoundError,
)
from powercontext.builtin.persistence.tables import EXTERNAL_SKILL_REGISTRATIONS_TABLE
from powercontext.limits import MAX_SCOPE_ID_LENGTH


class ExternalSkillRepository:
    """Replace and query rebuildable provider snapshots within one scope."""

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        provider: str,
        host_id: str,
        registrations: tuple[ExternalSkillRegistration, ...],
        /,
    ) -> tuple[ExternalSkillRegistration, ...]:
        _require_text("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        if not registrations:
            _require_text("provider", provider, 128)
            _require_text("host_id", host_id, 128)
        if any(value.provider != provider or value.host_id != host_id for value in registrations):
            raise InvalidRepositoryArgumentError(
                "registrations",
                "must belong to the replaced provider and host",
            )
        identities = [value.external_skill_id for value in registrations]
        if len(identities) != len(set(identities)):
            raise InvalidRepositoryArgumentError("registrations", "must have unique external Skill identities")

        await connection.execute(
            delete(EXTERNAL_SKILL_REGISTRATIONS_TABLE).where(
                EXTERNAL_SKILL_REGISTRATIONS_TABLE.c.scope_id == scope_id,
                EXTERNAL_SKILL_REGISTRATIONS_TABLE.c.provider == provider,
                EXTERNAL_SKILL_REGISTRATIONS_TABLE.c.host_id == host_id,
            )
        )
        if registrations:
            await connection.execute(
                insert(EXTERNAL_SKILL_REGISTRATIONS_TABLE),
                [_row(scope_id, value) for value in registrations],
            )
        return registrations

    async def get(
        self,
        connection: AsyncConnection,
        scope_id: str,
        external_skill_id: str,
        /,
    ) -> ExternalSkillRegistration:
        _require_text("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        row = (
            (
                await connection.execute(
                    select(EXTERNAL_SKILL_REGISTRATIONS_TABLE).where(
                        EXTERNAL_SKILL_REGISTRATIONS_TABLE.c.scope_id == scope_id,
                        EXTERNAL_SKILL_REGISTRATIONS_TABLE.c.external_skill_id == external_skill_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise RepositoryNotFoundError("external-skill", (scope_id, external_skill_id))
        return _registration(row)

    async def list(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
    ) -> tuple[ExternalSkillRegistration, ...]:
        _require_text("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        rows = (
            (
                await connection.execute(
                    select(EXTERNAL_SKILL_REGISTRATIONS_TABLE)
                    .where(EXTERNAL_SKILL_REGISTRATIONS_TABLE.c.scope_id == scope_id)
                    .order_by(
                        EXTERNAL_SKILL_REGISTRATIONS_TABLE.c.name,
                        EXTERNAL_SKILL_REGISTRATIONS_TABLE.c.external_skill_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        return tuple(_registration(row) for row in rows)


def _row(scope_id: str, value: ExternalSkillRegistration) -> dict[str, object]:
    return {
        "scope_id": scope_id,
        "locator_hash": hashlib.sha256(value.locator.encode()).hexdigest(),
        **value.model_dump(mode="python"),
    }


def _registration(row: Mapping[Any, Any]) -> ExternalSkillRegistration:
    return ExternalSkillRegistration.model_validate({
        field: row[field] for field in ExternalSkillRegistration.model_fields
    })


def _require_text(field: str, value: object, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidRepositoryArgumentError(field, "must be a non-empty trimmed string")
    if len(value) > maximum:
        raise InvalidRepositoryArgumentError(field, f"must not exceed {maximum} characters")


__all__ = ["ExternalSkillRepository"]
