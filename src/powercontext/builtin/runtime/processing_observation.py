"""Read durable processing progress with explicitly local scheduling observations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_INTENTS_TABLE
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS
from powercontext.builtin.statistics.models import ProcessingObservation

if TYPE_CHECKING:
    from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingSupervisors


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
        requested, handled = row["requested_generation"], row["handled_generation"]
        dirty, clean = row["dirty_generation"], row["clean_generation"]
        state = (
            "pending" if requested > handled else "dirty" if dirty > clean else "acknowledged" if handled else "idle"
        )
        values[row["scope_id"]][row["binding_name"]] = ProcessingObservation(
            binding=row["binding_name"],
            family=families.get(row["binding_name"]),
            state=state,
            observed_at=observed_at,
            requested_generation=requested,
            handled_generation=handled,
            dirty_generation=dirty,
            clean_generation=clean,
        )
    if supervisors is not None:
        for scope in scope_ids:
            for observation in supervisors.observe_scope(scope):
                durable = values[scope].get(observation.binding)
                if durable is None:
                    values[scope][observation.binding] = observation
                else:
                    updates = observation.model_dump(
                        exclude={
                            "requested_generation",
                            "handled_generation",
                            "dirty_generation",
                            "clean_generation",
                        }
                    )
                    # An idle local scheduler does not override durable unhandled work.
                    if observation.state == "idle":
                        updates["state"] = durable.state
                        if durable.state == "pending":
                            updates["reason"] = "waiting_dispatch"
                            updates["next_check_seconds"] = None
                        elif durable.state in {"idle", "acknowledged"}:
                            updates["reason"] = "idle"
                    values[scope][observation.binding] = durable.model_copy(update=updates)
    return {scope: tuple(rows[key] for key in sorted(rows)) for scope, rows in values.items()}
