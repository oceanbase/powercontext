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

"""Atomic Memory state adapter over authoritative shared Artifact heads."""

from sqlalchemy import case

from powercontext.builtin.artifacts.atomic_memory.errors import AtomicMemoryConflictError, AtomicMemoryRelationError
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryState, AtomicMemoryStateValue
from powercontext.builtin.persistence.artifact_governance import (
    ArtifactGovernance,
    ArtifactGovernanceRepository,
    ArtifactLifecycleState,
    InvalidArtifactLifecycleError,
)
from powercontext.builtin.persistence.errors import StoredPayloadConflictError
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE


class AtomicMemoryStateRepository:
    """Translate Atomic vocabulary through the shared governance CAS boundary."""

    def __init__(self) -> None:
        self.governance = ArtifactGovernanceRepository()

    async def get(self, connection, scope_id, artifact_id, /, *, for_update=False) -> AtomicMemoryState:
        current = await self.governance.get(connection, scope_id, "atomic-memory", artifact_id, for_update=for_update)
        return _atomic_state(current)

    async def create(self, connection, scope_id, artifact_id, /) -> AtomicMemoryState:
        state = await self.get(connection, scope_id, artifact_id, for_update=True)
        if state != AtomicMemoryState():
            raise AtomicMemoryRelationError("a new Atomic Memory requires an unchanged active head")  # noqa: TRY003
        return state

    async def require_summary(self, connection, scope_id, artifact_id, state, /) -> None:
        if await self.get(connection, scope_id, artifact_id, for_update=True) != state:
            raise AtomicMemoryRelationError("Atomic Memory state changed from its expected shared head")  # noqa: TRY003

    async def transition(
        self, connection, scope_id, artifact_id, expected, state, merged_into_id=None, /
    ) -> AtomicMemoryState:
        await self.require_summary(connection, scope_id, artifact_id, expected)
        current = await self.governance.get(connection, scope_id, "atomic-memory", artifact_id, for_update=True)
        try:
            updated = await self.governance.transition_merge(
                connection,
                scope_id,
                "atomic-memory",
                artifact_id,
                current,
                ArtifactLifecycleState(_summary(state)),
                merged_into_id,
            )
        except StoredPayloadConflictError as error:
            raise AtomicMemoryConflictError("Atomic Memory state changed during publication") from error  # noqa: TRY003
        except InvalidArtifactLifecycleError as error:
            raise AtomicMemoryRelationError(str(error)) from error
        return _atomic_state(updated)


def _atomic_state(current: ArtifactGovernance) -> AtomicMemoryState:
    if current.replacement_artifact_id is not None:
        raise AtomicMemoryRelationError("Atomic Memory does not use generic governance replacements")  # noqa: TRY003
    state = {
        ArtifactLifecycleState.ACTIVE: AtomicMemoryStateValue.ACTIVE,
        ArtifactLifecycleState.DEPRECATED: AtomicMemoryStateValue.FORGOTTEN
        if current.merged_into_id is None
        else AtomicMemoryStateValue.MERGED,
        ArtifactLifecycleState.RETIRED: AtomicMemoryStateValue.RETIRED,
    }[current.lifecycle_state]
    return AtomicMemoryState(
        state=state, state_version=current.governance_generation, merged_into_id=current.merged_into_id
    )


def _summary(state: AtomicMemoryStateValue) -> str:
    if state is AtomicMemoryStateValue.ACTIVE:
        return "active"
    if state is AtomicMemoryStateValue.RETIRED:
        return "retired"
    return "deprecated"


def atomic_memory_state_expression():
    """Expose Atomic vocabulary for SQL selection against the single shared head."""

    return case(
        (ARTIFACT_HEADS_TABLE.c.lifecycle_state == "active", "active"),
        (ARTIFACT_HEADS_TABLE.c.lifecycle_state == "retired", "retired"),
        (ARTIFACT_HEADS_TABLE.c.merged_into_id.is_not(None), "merged"),
        else_="forgotten",
    )
