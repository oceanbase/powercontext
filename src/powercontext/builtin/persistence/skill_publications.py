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

"""CAS persistence for host-local managed Skill publications."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.skill.projection import AgentSkillProjectionState
from powercontext.builtin.persistence.errors import RepositoryNotFoundError, StoredPayloadConflictError
from powercontext.builtin.persistence.tables import SKILL_PUBLICATIONS_TABLE


class SkillPublicationDesiredState(StrEnum):
    """Server-owned desired presence for one target binding."""

    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"


class SkillPublication(BaseModel):
    """Authoritative publication intent plus the last exact local observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    target_id: str
    artifact_id: str
    desired_state: SkillPublicationDesiredState = SkillPublicationDesiredState.PUBLISHED
    desired_revision: int = Field(ge=1)
    desired_tree_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_revision: int | None = Field(default=None, ge=1)
    observed_tree_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    observed_generation: int | None = Field(default=None, ge=0)
    destination: str | None = None
    state: AgentSkillProjectionState
    selected_runtime_variant: str | None = None
    environment_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    last_error_code: str | None = Field(default=None, min_length=1, max_length=128)
    observed_at: datetime | None = None
    generation: int = Field(ge=0)
    updated_at: datetime


class SkillPublicationRepository:
    """Create and advance publication observations with generation CAS."""

    async def find(
        self,
        connection: AsyncConnection,
        scope_id: str,
        target_id: str,
        artifact_id: str,
        /,
    ) -> SkillPublication | None:
        row = (
            (
                await connection.execute(
                    select(SKILL_PUBLICATIONS_TABLE).where(
                        SKILL_PUBLICATIONS_TABLE.c.scope_id == scope_id,
                        SKILL_PUBLICATIONS_TABLE.c.target_id == target_id,
                        SKILL_PUBLICATIONS_TABLE.c.artifact_id == artifact_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else SkillPublication.model_validate(row)

    async def list_for_target(
        self,
        connection: AsyncConnection,
        scope_id: str,
        target_id: str,
        /,
    ) -> tuple[SkillPublication, ...]:
        rows = (
            (
                await connection.execute(
                    select(SKILL_PUBLICATIONS_TABLE)
                    .where(
                        SKILL_PUBLICATIONS_TABLE.c.scope_id == scope_id,
                        SKILL_PUBLICATIONS_TABLE.c.target_id == target_id,
                    )
                    .order_by(SKILL_PUBLICATIONS_TABLE.c.artifact_id)
                )
            )
            .mappings()
            .all()
        )
        return tuple(SkillPublication.model_validate(row) for row in rows)

    async def create(
        self,
        connection: AsyncConnection,
        publication: SkillPublication,
        /,
    ) -> SkillPublication:
        if publication.generation != 0:
            raise ValueError("new Skill publication generation must be zero")  # noqa: TRY003
        values = publication.model_dump(mode="python")
        values["desired_state"] = publication.desired_state.value
        values["state"] = publication.state.value
        try:
            await connection.execute(insert(SKILL_PUBLICATIONS_TABLE).values(**values))
        except IntegrityError as error:
            raise StoredPayloadConflictError(
                "skill-publication",
                (publication.scope_id, publication.target_id, publication.artifact_id),
            ) from error
        return publication

    async def replace(
        self,
        connection: AsyncConnection,
        publication: SkillPublication,
        expected_generation: int,
        /,
    ) -> SkillPublication:
        revised = publication.model_copy(
            update={"generation": expected_generation + 1, "updated_at": datetime.now(UTC)}
        )
        values = revised.model_dump(mode="python", exclude={"scope_id", "target_id", "artifact_id"})
        values["desired_state"] = revised.desired_state.value
        values["state"] = revised.state.value
        result = await connection.execute(
            update(SKILL_PUBLICATIONS_TABLE)
            .where(
                SKILL_PUBLICATIONS_TABLE.c.scope_id == publication.scope_id,
                SKILL_PUBLICATIONS_TABLE.c.target_id == publication.target_id,
                SKILL_PUBLICATIONS_TABLE.c.artifact_id == publication.artifact_id,
                SKILL_PUBLICATIONS_TABLE.c.generation == expected_generation,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            current = await self.find(
                connection,
                publication.scope_id,
                publication.target_id,
                publication.artifact_id,
            )
            if current is None:
                raise RepositoryNotFoundError(
                    "skill-publication",
                    (publication.scope_id, publication.target_id, publication.artifact_id),
                )
            raise StoredPayloadConflictError(
                "skill-publication",
                (publication.scope_id, publication.target_id, publication.artifact_id, expected_generation),
            )
        return revised

    async def observe(
        self,
        connection: AsyncConnection,
        publication: SkillPublication,
        expected_generation: int,
        /,
        *,
        preserve_success: bool,
    ) -> SkillPublication:
        """Write an observation without advancing Server-owned desired generation.

        ``preserve_success`` protects an accepted success from a later out-of-order
        failure Receipt or a nonterminal local inspection. An authenticated remote
        drift observation passes ``False`` so it can replace stale success state.
        """

        revised = publication.model_copy(update={"generation": expected_generation, "updated_at": datetime.now(UTC)})
        values = revised.model_dump(mode="python", exclude={"scope_id", "target_id", "artifact_id", "generation"})
        values["desired_state"] = revised.desired_state.value
        values["state"] = revised.state.value
        statement = update(SKILL_PUBLICATIONS_TABLE).where(
            SKILL_PUBLICATIONS_TABLE.c.scope_id == publication.scope_id,
            SKILL_PUBLICATIONS_TABLE.c.target_id == publication.target_id,
            SKILL_PUBLICATIONS_TABLE.c.artifact_id == publication.artifact_id,
            SKILL_PUBLICATIONS_TABLE.c.generation == expected_generation,
        )
        if preserve_success:
            statement = statement.where(
                ~(
                    (SKILL_PUBLICATIONS_TABLE.c.observed_generation == expected_generation)
                    & SKILL_PUBLICATIONS_TABLE.c.state.in_({"current", "unpublished"})
                )
            )
        result = await connection.execute(statement.values(**values))
        if result.rowcount == 1:
            return revised
        current = await self.find(
            connection,
            publication.scope_id,
            publication.target_id,
            publication.artifact_id,
        )
        if current is None:
            raise RepositoryNotFoundError(
                "skill-publication",
                (publication.scope_id, publication.target_id, publication.artifact_id),
            )
        if current.generation == expected_generation and preserve_success:
            return current
        raise StoredPayloadConflictError(
            "skill-publication",
            (publication.scope_id, publication.target_id, publication.artifact_id, expected_generation),
        )


__all__ = ["SkillPublication", "SkillPublicationDesiredState", "SkillPublicationRepository"]
