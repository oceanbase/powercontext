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

"""Mutable governance state on authoritative Artifact heads."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.errors import RepositoryNotFoundError, StoredPayloadConflictError
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE
from powercontext.errors import PowerContextError


class InvalidArtifactLifecycleError(PowerContextError, ValueError):
    """Raised when an explicit governance transition is not valid."""


class ArtifactLifecycleState(StrEnum):
    """Working-set governance independent of immutable Artifact Revisions."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class ArtifactGovernance(BaseModel):
    """Current logical Artifact head plus its lifecycle CAS generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact: ArtifactRef
    lifecycle_state: ArtifactLifecycleState
    replacement_artifact_id: str | None = None
    governance_generation: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_replacement(self) -> ArtifactGovernance:
        if self.replacement_artifact_id is not None and self.lifecycle_state is not ArtifactLifecycleState.DEPRECATED:
            raise ValueError("only a deprecated Artifact may name a replacement")  # noqa: TRY003
        return self


class ArtifactGovernanceRepository:
    """Read and transition governance state without changing the head Revision."""

    async def get(
        self,
        connection: AsyncConnection,
        scope_id: str,
        family: str,
        artifact_id: str,
        /,
    ) -> ArtifactGovernance:
        row = (
            (
                await connection.execute(
                    select(ARTIFACT_HEADS_TABLE).where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                        ARTIFACT_HEADS_TABLE.c.family == family,
                        ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise RepositoryNotFoundError("artifact-head", (scope_id, family, artifact_id))
        return _governance(row)

    async def transition(
        self,
        connection: AsyncConnection,
        scope_id: str,
        family: str,
        artifact_id: str,
        expected_generation: int,
        lifecycle_state: ArtifactLifecycleState,
        replacement_artifact_id: str | None,
        /,
    ) -> ArtifactGovernance:
        current = await self.get(connection, scope_id, family, artifact_id)
        _validate_transition(current.lifecycle_state, lifecycle_state)
        if replacement_artifact_id is not None:
            if replacement_artifact_id == artifact_id:
                raise InvalidArtifactLifecycleError("an Artifact cannot replace itself")  # noqa: TRY003
            replacement = await self.get(connection, scope_id, family, replacement_artifact_id)
            if replacement.lifecycle_state is ArtifactLifecycleState.RETIRED:
                raise InvalidArtifactLifecycleError("a retired Artifact cannot be a replacement")  # noqa: TRY003
        requested = ArtifactGovernance(
            artifact=current.artifact,
            lifecycle_state=lifecycle_state,
            replacement_artifact_id=replacement_artifact_id,
            governance_generation=expected_generation + 1,
        )
        result = await connection.execute(
            update(ARTIFACT_HEADS_TABLE)
            .where(
                ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                ARTIFACT_HEADS_TABLE.c.family == family,
                ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
                ARTIFACT_HEADS_TABLE.c.governance_generation == expected_generation,
            )
            .values(
                lifecycle_state=lifecycle_state.value,
                replacement_artifact_id=replacement_artifact_id,
                governance_generation=expected_generation + 1,
            )
        )
        if result.rowcount != 1:
            raise StoredPayloadConflictError(
                "artifact-governance", (scope_id, family, artifact_id, expected_generation)
            )
        return requested


def _validate_transition(current: ArtifactLifecycleState, requested: ArtifactLifecycleState) -> None:
    if current is ArtifactLifecycleState.RETIRED and requested is not ArtifactLifecycleState.RETIRED:
        raise InvalidArtifactLifecycleError("retired Artifact lifecycle is irreversible")  # noqa: TRY003
    if requested is ArtifactLifecycleState.ACTIVE and current not in {
        ArtifactLifecycleState.ACTIVE,
        ArtifactLifecycleState.DEPRECATED,
    }:
        raise InvalidArtifactLifecycleError(  # noqa: TRY003
            "requested Artifact lifecycle transition is not allowed"
        )


def _governance(row) -> ArtifactGovernance:
    return ArtifactGovernance(
        artifact=ArtifactRef(
            family=str(row["family"]),
            artifact_id=str(row["artifact_id"]),
            revision=int(row["revision"]),
        ),
        lifecycle_state=ArtifactLifecycleState(str(row["lifecycle_state"])),
        replacement_artifact_id=(
            None if row["replacement_artifact_id"] is None else str(row["replacement_artifact_id"])
        ),
        governance_generation=int(row["governance_generation"]),
    )


__all__ = [
    "ArtifactGovernance",
    "ArtifactGovernanceRepository",
    "ArtifactLifecycleState",
    "InvalidArtifactLifecycleError",
]
