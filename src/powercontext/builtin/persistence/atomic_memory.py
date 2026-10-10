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

"""Caller-transaction Atomic Memory state persistence and common head summaries."""

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.atomic_memory.errors import AtomicMemoryConflictError, AtomicMemoryRelationError
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryState, AtomicMemoryStateValue
from powercontext.builtin.persistence.atomic_memory_schema import ATOMIC_MEMORY_STATES_TABLE
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE


class AtomicMemoryStateRepository:
    async def get(
        self, connection: AsyncConnection, scope_id: str, artifact_id: str, /, *, for_update: bool = False
    ) -> AtomicMemoryState:
        statement = select(ATOMIC_MEMORY_STATES_TABLE).where(
            ATOMIC_MEMORY_STATES_TABLE.c.scope_id == scope_id,
            ATOMIC_MEMORY_STATES_TABLE.c.artifact_id == artifact_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await connection.execute(statement)).mappings().one_or_none()
        if row is None:
            raise RepositoryNotFoundError("atomic-memory-state", (scope_id, artifact_id))
        return AtomicMemoryState(
            state=AtomicMemoryStateValue(str(row["state"])),
            state_version=int(row["state_version"]),
            merged_into_id=row["merged_into_id"],
        )

    async def create(self, connection: AsyncConnection, scope_id: str, artifact_id: str, /) -> AtomicMemoryState:
        state = AtomicMemoryState()
        await connection.execute(
            insert(ATOMIC_MEMORY_STATES_TABLE).values(
                scope_id=scope_id, artifact_id=artifact_id, **state.model_dump(mode="json")
            )
        )
        await self.require_summary(connection, scope_id, artifact_id, state)
        return state

    async def require_summary(
        self, connection: AsyncConnection, scope_id: str, artifact_id: str, state: AtomicMemoryState, /
    ) -> None:
        row = (
            await connection.execute(
                select(
                    ARTIFACT_HEADS_TABLE.c.lifecycle_state,
                    ARTIFACT_HEADS_TABLE.c.governance_generation,
                    ARTIFACT_HEADS_TABLE.c.replacement_artifact_id,
                )
                .where(
                    ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                    ARTIFACT_HEADS_TABLE.c.family == "atomic-memory",
                    ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
                )
                .with_for_update()
            )
        ).one_or_none()
        if (
            row is None
            or row.lifecycle_state != _summary(state.state)
            or row.governance_generation != state.state_version
            or row.replacement_artifact_id is not None
        ):
            raise AtomicMemoryRelationError("Atomic Memory state and common governance summary disagree")  # noqa: TRY003

    async def transition(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        expected: AtomicMemoryState,
        state: AtomicMemoryStateValue,
        merged_into_id: str | None = None,
        /,
    ) -> AtomicMemoryState:
        if expected.state is AtomicMemoryStateValue.RETIRED and state is not AtomicMemoryStateValue.RETIRED:
            raise AtomicMemoryRelationError("retired memory identities cannot be restored")  # noqa: TRY003
        if expected.state == state and expected.merged_into_id == merged_into_id:
            return expected
        requested = AtomicMemoryState(
            state=state, state_version=expected.state_version + 1, merged_into_id=merged_into_id
        )
        await self.require_summary(connection, scope_id, artifact_id, expected)
        updated = await connection.execute(
            update(ATOMIC_MEMORY_STATES_TABLE)
            .where(
                ATOMIC_MEMORY_STATES_TABLE.c.scope_id == scope_id,
                ATOMIC_MEMORY_STATES_TABLE.c.artifact_id == artifact_id,
                ATOMIC_MEMORY_STATES_TABLE.c.state_version == expected.state_version,
            )
            .values(**requested.model_dump(mode="json"))
        )
        summarized = await connection.execute(
            update(ARTIFACT_HEADS_TABLE)
            .where(
                ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                ARTIFACT_HEADS_TABLE.c.family == "atomic-memory",
                ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
                ARTIFACT_HEADS_TABLE.c.governance_generation == expected.state_version,
            )
            .values(
                lifecycle_state=_summary(state),
                governance_generation=requested.state_version,
                replacement_artifact_id=None,
            )
        )
        if updated.rowcount != 1 or summarized.rowcount != 1:
            raise AtomicMemoryConflictError("Atomic Memory state changed during publication")  # noqa: TRY003
        return requested


def _summary(state: AtomicMemoryStateValue) -> str:
    if state is AtomicMemoryStateValue.ACTIVE:
        return "active"
    if state is AtomicMemoryStateValue.RETIRED:
        return "retired"
    return "deprecated"
