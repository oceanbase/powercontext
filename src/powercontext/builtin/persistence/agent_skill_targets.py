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

"""CAS persistence for credential-bound remote Agent Skill targets."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.skill.external import AgentKind
from powercontext.builtin.persistence.errors import RepositoryNotFoundError, StoredPayloadConflictError
from powercontext.builtin.persistence.tables import AGENT_SKILL_TARGETS_TABLE


class RemoteAgentSkillTargetState(StrEnum):
    """Enrollment lifecycle for one remote Receiver installation."""

    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"


class RemoteAgentSkillTarget(BaseModel):
    """Durable remote Receiver identity without a Server-interpreted path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    target_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    display_name: str = Field(min_length=1, max_length=128, pattern=r".*\S.*")
    agent_kind: AgentKind
    installation_scope: Literal["project"] = "project"
    delivery_mode: Literal["agent_pull"] = "agent_pull"
    installation_id: str | None = Field(default=None, min_length=1, max_length=128)
    state: RemoteAgentSkillTargetState
    enrollment_token_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    enrollment_expires_at: datetime | None = None
    credential_subject: str | None = Field(default=None, min_length=1, max_length=128)
    credential_verifier: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    receiver_version: str | None = Field(default=None, min_length=1, max_length=64)
    environment_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    machine_hostname: str | None = Field(default=None, min_length=1, max_length=255, pattern=r".*\S.*")
    workspace_name: str | None = Field(default=None, min_length=1, max_length=128, pattern=r".*\S.*")
    last_seen_at: datetime | None = None
    generation: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_state_payload(self) -> RemoteAgentSkillTarget:
        if self.state is RemoteAgentSkillTargetState.PENDING:
            if self.enrollment_token_digest is None or self.enrollment_expires_at is None:
                raise ValueError("pending remote target requires an enrollment token and expiry")  # noqa: TRY003
            if (
                self.installation_id is not None
                or self.credential_subject is not None
                or self.credential_verifier is not None
            ):
                raise ValueError("pending remote target cannot have an installation credential")  # noqa: TRY003
        elif self.state is RemoteAgentSkillTargetState.ACTIVE:
            if self.installation_id is None or self.credential_subject is None or self.credential_verifier is None:
                raise ValueError("active remote target requires an installation credential")  # noqa: TRY003
            if self.enrollment_token_digest is not None or self.enrollment_expires_at is not None:
                raise ValueError("active remote target cannot retain enrollment credentials")  # noqa: TRY003
        elif self.enrollment_token_digest is not None or self.credential_verifier is not None:
            raise ValueError("revoked remote target cannot retain usable credentials")  # noqa: TRY003
        return self


class RemoteAgentSkillTargetRepository:
    """Create and advance remote target enrollment with generation CAS."""

    async def list_for_scope(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        limit: int,
    ) -> tuple[RemoteAgentSkillTarget, ...]:
        rows = (
            (
                await connection.execute(
                    select(AGENT_SKILL_TARGETS_TABLE)
                    .where(AGENT_SKILL_TARGETS_TABLE.c.scope_id == scope_id)
                    .order_by(
                        AGENT_SKILL_TARGETS_TABLE.c.created_at,
                        AGENT_SKILL_TARGETS_TABLE.c.target_id,
                    )
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return tuple(RemoteAgentSkillTarget.model_validate(row) for row in rows)

    async def find(
        self,
        connection: AsyncConnection,
        scope_id: str,
        target_id: str,
        /,
    ) -> RemoteAgentSkillTarget | None:
        row = (
            (
                await connection.execute(
                    select(AGENT_SKILL_TARGETS_TABLE).where(
                        AGENT_SKILL_TARGETS_TABLE.c.scope_id == scope_id,
                        AGENT_SKILL_TARGETS_TABLE.c.target_id == target_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else RemoteAgentSkillTarget.model_validate(row)

    async def find_by_enrollment_token(
        self,
        connection: AsyncConnection,
        token_digest: str,
        /,
    ) -> RemoteAgentSkillTarget | None:
        row = (
            (
                await connection.execute(
                    select(AGENT_SKILL_TARGETS_TABLE).where(
                        AGENT_SKILL_TARGETS_TABLE.c.enrollment_token_digest == token_digest
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else RemoteAgentSkillTarget.model_validate(row)

    async def find_by_credential(
        self,
        connection: AsyncConnection,
        credential_verifier: str,
        /,
    ) -> RemoteAgentSkillTarget | None:
        row = (
            (
                await connection.execute(
                    select(AGENT_SKILL_TARGETS_TABLE).where(
                        AGENT_SKILL_TARGETS_TABLE.c.credential_verifier == credential_verifier
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else RemoteAgentSkillTarget.model_validate(row)

    async def create(
        self,
        connection: AsyncConnection,
        target: RemoteAgentSkillTarget,
        /,
    ) -> RemoteAgentSkillTarget:
        if target.generation != 0:
            raise ValueError("new remote target generation must be zero")  # noqa: TRY003
        try:
            await connection.execute(insert(AGENT_SKILL_TARGETS_TABLE).values(**_values(target)))
        except IntegrityError as error:
            raise StoredPayloadConflictError("agent-skill-target", (target.scope_id, target.target_id)) from error
        return target

    async def replace(
        self,
        connection: AsyncConnection,
        target: RemoteAgentSkillTarget,
        expected_generation: int,
        /,
    ) -> RemoteAgentSkillTarget:
        revised = target.model_copy(update={"generation": expected_generation + 1})
        revised = revised.model_copy(update={"updated_at": datetime.now(UTC)})
        values = _values(revised, exclude_identity=True)
        try:
            result = await connection.execute(
                update(AGENT_SKILL_TARGETS_TABLE)
                .where(
                    AGENT_SKILL_TARGETS_TABLE.c.scope_id == target.scope_id,
                    AGENT_SKILL_TARGETS_TABLE.c.target_id == target.target_id,
                    AGENT_SKILL_TARGETS_TABLE.c.generation == expected_generation,
                )
                .values(**values)
            )
        except IntegrityError as error:
            raise StoredPayloadConflictError(
                "agent-skill-target",
                (target.scope_id, target.target_id, expected_generation),
            ) from error
        if result.rowcount == 1:
            return revised
        current = await self.find(connection, target.scope_id, target.target_id)
        if current is None:
            raise RepositoryNotFoundError("agent-skill-target", (target.scope_id, target.target_id))
        raise StoredPayloadConflictError(
            "agent-skill-target",
            (target.scope_id, target.target_id, expected_generation),
        )

    async def observe(
        self,
        connection: AsyncConnection,
        target: RemoteAgentSkillTarget,
        /,
        *,
        receiver_version: str,
        environment_fingerprint: str | None,
        observed_at: datetime,
    ) -> RemoteAgentSkillTarget:
        """Refresh liveness metadata without changing credential lifecycle generation."""

        result = await connection.execute(
            update(AGENT_SKILL_TARGETS_TABLE)
            .where(
                AGENT_SKILL_TARGETS_TABLE.c.scope_id == target.scope_id,
                AGENT_SKILL_TARGETS_TABLE.c.target_id == target.target_id,
                AGENT_SKILL_TARGETS_TABLE.c.state == RemoteAgentSkillTargetState.ACTIVE.value,
                AGENT_SKILL_TARGETS_TABLE.c.credential_verifier == target.credential_verifier,
            )
            .values(
                receiver_version=receiver_version,
                environment_fingerprint=environment_fingerprint,
                last_seen_at=observed_at,
                updated_at=observed_at,
            )
        )
        if result.rowcount != 1:
            raise RepositoryNotFoundError("active-agent-skill-target", (target.scope_id, target.target_id))
        return target.model_copy(
            update={
                "receiver_version": receiver_version,
                "environment_fingerprint": environment_fingerprint,
                "last_seen_at": observed_at,
                "updated_at": observed_at,
            }
        )


def _values(target: RemoteAgentSkillTarget, *, exclude_identity: bool = False) -> dict[str, object]:
    excluded = {"scope_id", "target_id"} if exclude_identity else set()
    values = target.model_dump(mode="python", exclude=excluded)
    values["state"] = target.state.value
    return values


__all__ = [
    "RemoteAgentSkillTarget",
    "RemoteAgentSkillTargetRepository",
    "RemoteAgentSkillTargetState",
]
