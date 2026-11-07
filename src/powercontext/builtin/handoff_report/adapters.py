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

"""Runtime adapter for read-only Handoff report projection."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from sqlalchemy import select

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff import Handoff
from powercontext.builtin.handoff_report.errors import HandoffReportInconsistentError
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE

_HEAD_BATCH = 100


class RuntimeHandoffReadAdapter:
    """Read the committed Handoff of every selected Scope in one snapshot."""

    def __init__(
        self,
        database: AsyncDatabase,
        artifacts: ArtifactRepository,
        handoff_artifact_id: str,
        /,
    ) -> None:
        self._database = database
        self._artifacts = artifacts
        self._handoff_artifact_id = handoff_artifact_id

    async def latest_many(self, scope_ids: Sequence[str], /) -> dict[str, Handoff | None]:
        if not scope_ids:
            return {}
        heads: dict[str, int] = {}
        loaded: dict[str, Handoff | None] = {}
        async with self._database.transaction(consistent_snapshot=True) as connection:
            for offset in range(0, len(scope_ids), _HEAD_BATCH):
                rows = (
                    await connection.execute(
                        select(ARTIFACT_HEADS_TABLE.c.scope_id, ARTIFACT_HEADS_TABLE.c.revision).where(
                            ARTIFACT_HEADS_TABLE.c.scope_id.in_(scope_ids[offset : offset + _HEAD_BATCH]),
                            ARTIFACT_HEADS_TABLE.c.family == Handoff.family,
                            ARTIFACT_HEADS_TABLE.c.artifact_id == self._handoff_artifact_id,
                        )
                    )
                ).all()
                heads.update((str(scope_id), int(revision)) for scope_id, revision in rows)
            for scope_id in scope_ids:
                revision = heads.get(scope_id)
                if revision is None:
                    loaded[scope_id] = None
                    continue
                reference = ArtifactRef(
                    family=Handoff.family,
                    artifact_id=self._handoff_artifact_id,
                    revision=revision,
                )
                handoff = cast(Handoff, await self._artifacts.get(connection, scope_id, reference))
                if handoff.as_ref() != reference:
                    raise HandoffReportInconsistentError(scope_id)
                loaded[scope_id] = handoff
        return loaded


__all__ = ["RuntimeHandoffReadAdapter"]
