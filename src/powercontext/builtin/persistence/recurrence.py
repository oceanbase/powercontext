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

"""Append-only relational storage for the recurrence ledger.

This repository offers no update and no delete. Corrections are new rows, and
history stays attached to the exact revision that produced it.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.experience.recurrence import (
    RecurrenceMatch,
    RecurrenceObservation,
    TaskOutcomeItemRef,
    match_key,
    selection_key,
    verdict_key,
)
from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.tables import RECURRENCE_MATCH_TABLE, RECURRENCE_OBSERVATION_TABLE
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

_MATCH_KIND = "recurrence-match"
_OBSERVATION_KIND = "recurrence-observation"


class RecurrenceRepository:
    """Append matching decisions and ledger events, then read them back."""

    async def append_match(self, connection: AsyncConnection, match: RecurrenceMatch, /) -> str:
        """Append one immutable matching decision and return its key.

        Replaying the same ``(scope_id, task_outcome_ref, failure_ref)`` is a
        no-op: the ledger already holds the decision and the window must not
        re-run the generator.
        """

        scope = validate_scope_id(match.scope_id)
        key = match_key(match)
        values = {
            "scope_id": scope,
            "match_key": key,
            "task_outcome_source_type": match.task_outcome_ref.source_type,
            "task_outcome_source_id": match.task_outcome_ref.source_id,
            "task_outcome_position": match.task_outcome_position,
            "failure_item_kind": match.failure_ref.item_kind,
            "failure_item_index": match.failure_ref.item_index,
            "failure_item_digest": match.failure_ref.item_digest,
            "candidate_set_mode": match.candidate_set_mode,
            "candidate_set_digest": match.candidate_set_digest,
            "result": match.result,
            "target_family": None if match.artifact_ref is None else match.artifact_ref.family,
            "target_artifact_id": None if match.artifact_ref is None else match.artifact_ref.artifact_id,
            "target_revision": None if match.artifact_ref is None else match.artifact_ref.revision,
            "signature_key": match.signature_key,
            "payload": dump_model(match, kind=_MATCH_KIND, name=key),
        }
        try:
            await _ensure_sqlite_outer_transaction(connection)
            async with connection.begin_nested():
                await connection.execute(insert(RECURRENCE_MATCH_TABLE).values(**values))
        except IntegrityError:
            existing = (
                await connection.execute(
                    select(RECURRENCE_MATCH_TABLE.c.match_key).where(
                        RECURRENCE_MATCH_TABLE.c.scope_id == scope,
                        RECURRENCE_MATCH_TABLE.c.task_outcome_source_type == match.task_outcome_ref.source_type,
                        RECURRENCE_MATCH_TABLE.c.task_outcome_source_id == match.task_outcome_ref.source_id,
                        RECURRENCE_MATCH_TABLE.c.failure_item_kind == match.failure_ref.item_kind,
                        RECURRENCE_MATCH_TABLE.c.failure_item_index == match.failure_ref.item_index,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            key = str(existing)
        return key

    async def find_match(
        self,
        connection: AsyncConnection,
        scope_id: str,
        task_outcome_ref: SourceRef,
        failure_ref: TaskOutcomeItemRef,
        /,
    ) -> RecurrenceMatch | None:
        """Return the already recorded decision for one failure locator."""

        scope = validate_scope_id(scope_id)
        row = (
            (
                await connection.execute(
                    select(RECURRENCE_MATCH_TABLE).where(
                        RECURRENCE_MATCH_TABLE.c.scope_id == scope,
                        RECURRENCE_MATCH_TABLE.c.task_outcome_source_type == task_outcome_ref.source_type,
                        RECURRENCE_MATCH_TABLE.c.task_outcome_source_id == task_outcome_ref.source_id,
                        RECURRENCE_MATCH_TABLE.c.failure_item_kind == failure_ref.item_kind,
                        RECURRENCE_MATCH_TABLE.c.failure_item_index == failure_ref.item_index,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _decode_match(row)

    async def append_observation(self, connection: AsyncConnection, observation: RecurrenceObservation, /) -> bool:
        """Append one ledger event, reporting whether this call recorded it.

        A replayed Source window derives the same ``observation_id``, so the
        uniqueness constraints answer "already recorded" instead of failing.
        """

        scope = validate_scope_id(observation.scope_id)
        identifier = observation.observation_id
        values = {
            "scope_id": scope,
            "observation_id": identifier,
            "selection_key": selection_key(observation),
            "verdict_key": verdict_key(observation),
            "event": observation.event,
            "match_basis": observation.match_basis,
            "family": observation.artifact_ref.family,
            "artifact_id": observation.artifact_ref.artifact_id,
            "revision": observation.artifact_ref.revision,
            "signature_key": observation.signature_key,
            "signature_key_hash": sha256(observation.signature_key.encode()).digest(),
            "task_outcome_source_type": observation.task_outcome_ref.source_type,
            "task_outcome_source_id": observation.task_outcome_ref.source_id,
            "task_outcome_position": observation.task_outcome_position,
            "payload": dump_model(observation, kind=_OBSERVATION_KIND, name=identifier),
        }
        try:
            await _ensure_sqlite_outer_transaction(connection)
            async with connection.begin_nested():
                await connection.execute(insert(RECURRENCE_OBSERVATION_TABLE).values(**values))
        except IntegrityError:
            existing = (
                await connection.execute(
                    select(RECURRENCE_OBSERVATION_TABLE.c.observation_id).where(
                        RECURRENCE_OBSERVATION_TABLE.c.scope_id == scope,
                        (
                            (RECURRENCE_OBSERVATION_TABLE.c.observation_id == identifier)
                            | (RECURRENCE_OBSERVATION_TABLE.c.selection_key == values["selection_key"])
                            | (RECURRENCE_OBSERVATION_TABLE.c.verdict_key == values["verdict_key"])
                        ),
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            return False
        return True

    async def observations(self, connection: AsyncConnection, scope_id: str, /) -> tuple[RecurrenceObservation, ...]:
        """Return every ledger event for one scope in immutable journal order."""

        scope = validate_scope_id(scope_id)
        rows = (
            await connection.execute(
                select(RECURRENCE_OBSERVATION_TABLE)
                .where(RECURRENCE_OBSERVATION_TABLE.c.scope_id == scope)
                .order_by(
                    RECURRENCE_OBSERVATION_TABLE.c.task_outcome_position,
                    RECURRENCE_OBSERVATION_TABLE.c.family,
                    RECURRENCE_OBSERVATION_TABLE.c.artifact_id,
                    RECURRENCE_OBSERVATION_TABLE.c.revision,
                    RECURRENCE_OBSERVATION_TABLE.c.observation_id,
                )
            )
        ).mappings()
        return tuple(_decode_observation(row) for row in rows)

    async def unlinked_handoff_citations(
        self,
        connection: AsyncConnection,
        scope_id: str,
        linked: tuple[tuple[str, str, int, str], ...],
        /,
    ) -> int:
        """Count Handoff-cited Experience revisions with no linked observation.

        This is a bounded provenance-coverage signal, not a recall diagnostic
        and not a ``selected`` count: it only reports citations the ledger can
        see but cannot attach to a Task Outcome.
        """

        scope = validate_scope_id(scope_id)
        if not linked:
            return 0
        linked_keys = {(family, artifact_id, revision, key) for family, artifact_id, revision, key in linked}
        rows = (
            await connection.execute(
                select(
                    RECURRENCE_OBSERVATION_TABLE.c.family,
                    RECURRENCE_OBSERVATION_TABLE.c.artifact_id,
                    RECURRENCE_OBSERVATION_TABLE.c.revision,
                    RECURRENCE_OBSERVATION_TABLE.c.signature_key,
                ).where(
                    RECURRENCE_OBSERVATION_TABLE.c.scope_id == scope,
                    RECURRENCE_OBSERVATION_TABLE.c.event == "selected",
                )
            )
        ).all()
        observed = {
            (str(row[0]), str(row[1]), int(row[2]), str(row[3]))
            for row in rows
            if (str(row[0]), str(row[1]), int(row[2]), str(row[3])) in linked_keys
        }
        return len(linked_keys - observed)

    async def count_observations(self, connection: AsyncConnection, scope_id: str, /) -> int:
        """Return the number of ledger events recorded for one scope."""

        scope = validate_scope_id(scope_id)
        rows = (
            await connection.execute(
                select(RECURRENCE_OBSERVATION_TABLE.c.observation_id).where(
                    RECURRENCE_OBSERVATION_TABLE.c.scope_id == scope
                )
            )
        ).all()
        return len(rows)

    async def matches(self, connection: AsyncConnection, scope_id: str, /) -> tuple[RecurrenceMatch, ...]:
        """Return every matching decision recorded for one scope."""

        scope = validate_scope_id(scope_id)
        rows = (
            await connection.execute(
                select(RECURRENCE_MATCH_TABLE)
                .where(RECURRENCE_MATCH_TABLE.c.scope_id == scope)
                .order_by(
                    RECURRENCE_MATCH_TABLE.c.task_outcome_position,
                    RECURRENCE_MATCH_TABLE.c.failure_item_kind,
                    RECURRENCE_MATCH_TABLE.c.failure_item_index,
                    RECURRENCE_MATCH_TABLE.c.match_key,
                )
            )
        ).mappings()
        return tuple(_decode_match(row) for row in rows)

    async def matches_for_outcome(
        self,
        connection: AsyncConnection,
        scope_id: str,
        task_outcome_ref: SourceRef,
        /,
    ) -> tuple[RecurrenceMatch, ...]:
        """Return every matching decision recorded for one Task Outcome."""

        scope = validate_scope_id(scope_id)
        rows = (
            await connection.execute(
                select(RECURRENCE_MATCH_TABLE).where(
                    RECURRENCE_MATCH_TABLE.c.scope_id == scope,
                    RECURRENCE_MATCH_TABLE.c.task_outcome_source_type == task_outcome_ref.source_type,
                    RECURRENCE_MATCH_TABLE.c.task_outcome_source_id == task_outcome_ref.source_id,
                )
            )
        ).mappings()
        return tuple(_decode_match(row) for row in rows)


def _decode_match(row: Any) -> RecurrenceMatch:
    key = str(row["match_key"])
    return load_model(
        RecurrenceMatch,
        stored_bytes(row["payload"], column="payload"),
        kind=_MATCH_KIND,
        name=key,
    )


def _decode_observation(row: Any) -> RecurrenceObservation:
    identifier = str(row["observation_id"])
    return load_model(
        RecurrenceObservation,
        stored_bytes(row["payload"], column="payload"),
        kind=_OBSERVATION_KIND,
        name=identifier,
    )


async def _ensure_sqlite_outer_transaction(connection: AsyncConnection, /) -> None:
    """Force SQLite savepoints to participate in the caller's outer transaction."""

    if connection.dialect.name != "sqlite" or not connection.in_transaction() or connection.in_nested_transaction():
        return
    raw = await connection.get_raw_connection()
    driver = getattr(raw, "driver_connection", None)
    in_transaction = getattr(driver, "in_transaction", None)
    if in_transaction is None:
        in_transaction = getattr(getattr(driver, "_conn", None), "in_transaction", None)
    if in_transaction is False:
        await connection.exec_driver_sql("BEGIN")


__all__ = ["RecurrenceRepository"]
