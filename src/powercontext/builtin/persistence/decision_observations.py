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

"""Scope-private persistence for bounded RFC 1770 decision observations."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.decision_observations import DecisionObservation
from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.errors import StoredPayloadConflictError
from powercontext.builtin.persistence.tables import DECISION_OBSERVATIONS_TABLE
from powercontext.builtin.sources import validate_scope_id

_KIND = "decision-observation"
_SAFE_REASON = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")
_SAFE_REFERENCE = re.compile(
    r"(?:candidate|evidence):[0-9]+|(?:candidate:[0-9]+ )?(?:entry|source|artifact):[A-Za-z0-9_.:@-]{1,500}\Z"
)
_SAFE_METADATA_KEYS = frozenset({"candidate_count", "evidence_count"})


class DecisionObservationRepository:
    """Append and read bounded decision sidecars without retaining input bodies."""

    async def append(self, connection: AsyncConnection, observation: DecisionObservation, /) -> bool:
        """Append an observation, accepting only an exact idempotent replay."""

        _validate_observation(observation)
        scope = validate_scope_id(observation.scope_id)
        values = _values(observation, scope)
        try:
            await _ensure_sqlite_outer_transaction(connection)
            async with connection.begin_nested():
                await connection.execute(insert(DECISION_OBSERVATIONS_TABLE).values(**values))
        except IntegrityError:
            existing = (
                await connection.execute(
                    select(DECISION_OBSERVATIONS_TABLE.c.payload).where(
                        DECISION_OBSERVATIONS_TABLE.c.scope_id == scope,
                        DECISION_OBSERVATIONS_TABLE.c.observation_id == observation.observation_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            if stored_bytes(existing, column="payload") != values["payload"]:
                raise StoredPayloadConflictError(_KIND, (scope, observation.observation_id)) from None
            return False
        return True

    async def observations(self, connection: AsyncConnection, scope_id: str, /) -> tuple[DecisionObservation, ...]:
        """Read one Scope's sidecars in immutable creation order."""

        scope = validate_scope_id(scope_id)
        rows = (
            await connection.execute(
                select(DECISION_OBSERVATIONS_TABLE)
                .where(DECISION_OBSERVATIONS_TABLE.c.scope_id == scope)
                .order_by(
                    DECISION_OBSERVATIONS_TABLE.c.created_at,
                    DECISION_OBSERVATIONS_TABLE.c.observation_id,
                )
            )
        ).mappings()
        return tuple(_decode(row) for row in rows)

    async def prune_before(self, connection: AsyncConnection, scope_id: str, before: datetime, /) -> int:
        """Remove only this Scope's sidecars older than its explicit retention boundary."""

        scope = validate_scope_id(scope_id)
        result = await connection.execute(
            delete(DECISION_OBSERVATIONS_TABLE).where(
                DECISION_OBSERVATIONS_TABLE.c.scope_id == scope,
                DECISION_OBSERVATIONS_TABLE.c.created_at < before,
            )
        )
        return int(result.rowcount or 0)


def _values(observation: DecisionObservation, scope_id: str) -> dict[str, object]:
    assessment = observation.assessment
    usage = assessment.usage
    return {
        "scope_id": scope_id,
        "observation_id": observation.observation_id,
        "operation_id": observation.operation_id,
        "created_at": observation.created_at,
        "consumer": observation.consumer,
        "policy_id": observation.policy_id,
        "policy_version": observation.policy_version,
        "mode": observation.mode.value,
        "privacy_boundary": observation.privacy_boundary.value,
        "coverage": assessment.coverage.value,
        "verdict": assessment.verdict.value,
        "assessment_source": assessment.source.value,
        "final_action": observation.final_action,
        "used_fallback": assessment.used_fallback,
        "fallback_reason": observation.fallback_reason,
        "provider_id": observation.provider_id,
        "backend_model_id": observation.backend_model_id,
        "model_policy_id": observation.model_policy_id,
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "latency_ms": assessment.latency_ms,
        "payload": dump_model(observation, kind=_KIND, name=observation.observation_id),
    }


def _decode(row: Any) -> DecisionObservation:
    identifier = str(row["observation_id"])
    return load_model(
        DecisionObservation,
        stored_bytes(row["payload"], column="payload"),
        kind=_KIND,
        name=identifier,
    )


def _validate_observation(observation: DecisionObservation, /) -> None:
    assessment = observation.assessment
    if (
        assessment.policy_id != observation.policy_id
        or assessment.policy_version != observation.policy_version
        or assessment.mode is not observation.mode
    ):
        raise ValueError("decision observation assessment policy must match the observation")  # noqa: TRY003
    if not all(_SAFE_REFERENCE.fullmatch(value) for value in (*observation.subject_refs, *observation.evidence_refs)):
        raise ValueError("decision observation references must be structured identifiers")  # noqa: TRY003
    for reason in (observation.assessment.reason, observation.fallback_reason):
        if reason is not None and _SAFE_REASON.fullmatch(reason) is None:
            raise ValueError("decision observation reasons must be bounded codes")  # noqa: TRY003
    if set(observation.metadata) - _SAFE_METADATA_KEYS or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in observation.metadata.values()
    ):
        raise ValueError("decision observation metadata must contain only nonnegative safe counters")  # noqa: TRY003


async def _ensure_sqlite_outer_transaction(connection: AsyncConnection, /) -> None:
    if connection.dialect.name != "sqlite" or not connection.in_transaction() or connection.in_nested_transaction():
        return
    raw = await connection.get_raw_connection()
    driver = getattr(raw, "driver_connection", None)
    in_transaction = getattr(driver, "in_transaction", None)
    if in_transaction is None:
        in_transaction = getattr(getattr(driver, "_conn", None), "in_transaction", None)
    if in_transaction is False:
        await connection.exec_driver_sql("BEGIN")


__all__ = ["DecisionObservationRepository"]
