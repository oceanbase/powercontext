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

"""Source reads whose purpose is Artifact generation."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.sources import SourceRepository, StoredSource
from powercontext.builtin.source_eligibility import is_generation_eligible, require_source_eligible
from powercontext.sources import SourceRef


class GenerationSourceAccess:
    """Keep generation reads and Source eligibility inseparable."""

    def __init__(self, repository: SourceRepository, /) -> None:
        self._repository = repository

    async def require_for_generation(
        self,
        connection: AsyncConnection,
        scope_id: str,
        refs: Sequence[SourceRef],
        /,
    ) -> tuple[StoredSource, ...]:
        """Resolve explicit references, rejecting the whole request if any is ineligible."""

        rows = await self._repository.get_many(connection, scope_id, refs)
        for row in rows:
            require_source_eligible(row.ref, row.value)
        return rows

    async def list_window_for_generation(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        after: int,
        through: int,
    ) -> tuple[StoredSource, ...]:
        """Resolve one bounded journal window and omit valid lineage-only Sources."""

        rows = await self._repository.list_window(connection, scope_id, after=after, through=through)
        return tuple(row for row in rows if is_generation_eligible(row.value))


__all__ = ["GenerationSourceAccess"]
