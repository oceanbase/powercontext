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

"""Transaction hooks shared by built-in Scope processors.

The scheduler's request generation acknowledges an invocation, while each
processor remains responsible for deciding whether domain work remains.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.processing_intents import (
    ArtifactProcessingIntentRepository,
    StoredArtifactProcessingIntent,
)
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkAssignment


class InvocationAlreadyHandled(Exception):
    """A duplicate invocation must finish without selecting any new input."""


class ScopeInvocation:
    """Bind every processor transaction to its term and accepted request."""

    def __init__(
        self,
        assignment: ArtifactProcessingWorkAssignment,
        *,
        authorize_transaction: Callable[[AsyncConnection], Awaitable[None]] | None = None,
    ) -> None:
        if assignment.fence.supervisor_group not in {"global", f"artifact:{assignment.artifact_family}"}:
            raise ValueError("processing fence belongs to another Family")  # noqa: TRY003
        self.assignment = assignment
        self.intents = ArtifactProcessingIntentRepository()
        self.leases = ArtifactProcessingLeaseRepository()
        self.dirty_generation: int | None = None
        self.authorize_transaction = authorize_transaction

    async def guard(self, connection: AsyncConnection) -> StoredArtifactProcessingIntent:
        work = self.assignment
        await self.leases.require_fence(connection, work.fence)
        intent = await self.intents.load(connection, work.scope_id, work.binding_name, for_update=True)
        if intent is None or intent.requested_generation < work.claimed_request_generation:
            raise ValueError("processor invocation has no accepted request")  # noqa: TRY003
        if intent.handled_generation >= work.claimed_request_generation:
            raise InvocationAlreadyHandled
        if self.authorize_transaction is not None:
            await self.authorize_transaction(connection)
        return intent

    async def start(self, connection: AsyncConnection) -> None:
        intent = await self.guard(connection)
        self.dirty_generation = intent.dirty_generation

    async def complete(self, connection: AsyncConnection, *, remaining_work: bool) -> None:
        await self.guard(connection)
        work = self.assignment
        await self.intents.acknowledge(
            connection,
            work.scope_id,
            work.binding_name,
            work.claimed_request_generation,
            clean_generation=None if remaining_work else self.dirty_generation,
        )
        await self.leases.require_fence(connection, work.fence)
