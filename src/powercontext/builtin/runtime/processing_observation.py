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

"""Read durable processing progress with explicitly local scheduling observations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_INTENTS_TABLE
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS
from powercontext.builtin.statistics.models import ProcessingObservation

if TYPE_CHECKING:
    from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingSupervisors


def project_processing_observation(
    row: Mapping[Any, Any], family: str | None, observed_at: datetime
) -> ProcessingObservation:
    requested, handled = row["requested_generation"], row["handled_generation"]
    dirty, clean = row["dirty_generation"], row["clean_generation"]
    return ProcessingObservation(
        binding=row["binding_name"],
        family=family,
        state="pending" if requested > handled else "dirty" if dirty > clean else "acknowledged" if handled else "idle",
        observed_at=observed_at,
        requested_generation=requested,
        handled_generation=handled,
        dirty_generation=dirty,
        clean_generation=clean,
    )


def merge_processing_observation(
    durable: ProcessingObservation | None, local: ProcessingObservation
) -> ProcessingObservation:
    if durable is None:
        return local
    updates = local.model_dump(
        exclude={
            "requested_generation",
            "handled_generation",
            "dirty_generation",
            "clean_generation",
        }
    )
    if local.state == "idle":
        updates["state"] = durable.state
        if durable.state == "pending":
            updates["reason"] = "waiting_dispatch"
            updates["next_check_seconds"] = None
        elif durable.state in {"idle", "acknowledged"}:
            updates["reason"] = "idle"
    return durable.model_copy(update=updates)


async def observe_processing(
    database: AsyncDatabase,
    scope_ids: tuple[str, ...],
    supervisors: ArtifactProcessingSupervisors | None,
) -> dict[str, tuple[ProcessingObservation, ...]]:
    """Read only selected Scopes; counters prove acknowledgement, never artifact creation.

    Local scheduler state is sampled after the durable transaction. Its timestamp and
    coverage are separate; a remote worker's state is never inferred from silence.
    """
    if not scope_ids:
        return {}
    table = ARTIFACT_PROCESSING_INTENTS_TABLE
    async with database.transaction() as connection:
        rows = (await connection.execute(select(table).where(table.c.scope_id.in_(scope_ids)))).mappings().all()
    observed_at = datetime.now(UTC)
    values: dict[str, dict[str, ProcessingObservation]] = {scope: {} for scope in scope_ids}
    families = {binding: family for family, binding in FAMILY_BINDINGS.items()}
    families[TOPIC_MEMORY_SOURCE_WINDOW_BINDING] = "topic-memory"
    for row in rows:
        values[row["scope_id"]][row["binding_name"]] = project_processing_observation(
            row, families.get(row["binding_name"]), observed_at
        )
    if supervisors is not None:
        for scope in scope_ids:
            for observation in supervisors.observe_scope(scope):
                durable = values[scope].get(observation.binding)
                values[scope][observation.binding] = merge_processing_observation(durable, observation)
    return {scope: tuple(rows[key] for key in sorted(rows)) for scope, rows in values.items()}
