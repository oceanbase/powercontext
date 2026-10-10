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

"""Relational sidecar sink for bounded decision-policy observations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from powercontext.builtin.decision_observations import DecisionObservation
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.decision_observations import DecisionObservationRepository


class RelationalDecisionObservationSink:
    """Persist each observation independently of the domain operation it describes."""

    def __init__(
        self,
        database: AsyncDatabase,
        repository: DecisionObservationRepository,
        /,
        *,
        retention_days: int | None = None,
    ) -> None:
        self._database = database
        self._repository = repository
        self._retention_days = retention_days

    async def record(self, observation: DecisionObservation, /) -> None:
        """Append one sidecar in a short caller-independent transaction."""

        async with self._database.transaction() as connection:
            await self._repository.append(connection, observation)
            if self._retention_days is not None:
                await self._repository.prune_before(
                    connection,
                    observation.scope_id,
                    datetime.now(UTC) - timedelta(days=self._retention_days),
                )


__all__ = ["RelationalDecisionObservationSink"]
