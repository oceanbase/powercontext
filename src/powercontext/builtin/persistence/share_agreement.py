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

"""Persistence for the standing agreement: rule versions, acceptances and pins.

The repository stores one immutable version per row and never rewrites an accepted
version's terms. A pin is written once with ``dream_run_id`` NULL; the claiming update is
the caller's, inside its own transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.tables import (
    SHARE_ACCEPTANCES_TABLE,
    SHARE_RULE_HEADS_TABLE,
    SHARE_RULE_VERSIONS_TABLE,
    SHARE_SNAPSHOTS_TABLE,
)
from powercontext.builtin.scope.models import ScopeSelection
from powercontext.builtin.sharing.models import (
    ShareAcceptance,
    ShareInput,
    ShareRule,
    ShareRuleNotFoundError,
    ShareRuleTerms,
    ShareSnapshot,
)


def _ticks(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000)


def _from_ticks(value: int) -> datetime:
    from datetime import UTC

    return datetime.fromtimestamp(value / 1_000_000, tz=UTC)


class _RulePayload(BaseModel):
    """The immutable per-version body."""

    model_config = ConfigDict(extra="forbid")

    selection: ScopeSelection
    terms: ShareRuleTerms


class _SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: tuple[ShareInput, ...]
    inputs_digest: str


class _CoveredScopes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_ids: tuple[str, ...]


class ShareAgreementRepository:
    """One writer of the agreement records."""

    async def head(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, /, *, for_update: bool = False
    ) -> Mapping[Any, Any] | None:
        statement = select(SHARE_RULE_HEADS_TABLE).where(
            SHARE_RULE_HEADS_TABLE.c.scope_id == scope_id,
            SHARE_RULE_HEADS_TABLE.c.rule_id == rule_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await connection.execute(statement)).mappings().one_or_none()

    async def insert_head(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, version: int, now: datetime, /
    ) -> None:
        await connection.execute(
            SHARE_RULE_HEADS_TABLE.insert().values(
                scope_id=scope_id,
                rule_id=rule_id,
                version=version,
                status="active",
                updated_at=_ticks(now),
            )
        )

    async def advance_head(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, expected: int, version: int, /
    ) -> bool:
        changed = await connection.execute(
            update(SHARE_RULE_HEADS_TABLE)
            .where(
                SHARE_RULE_HEADS_TABLE.c.scope_id == scope_id,
                SHARE_RULE_HEADS_TABLE.c.rule_id == rule_id,
                SHARE_RULE_HEADS_TABLE.c.version == expected,
            )
            .values(version=version, updated_at=SHARE_RULE_HEADS_TABLE.c.updated_at)
        )
        return changed.rowcount == 1

    async def invalidate_head(self, connection: AsyncConnection, scope_id: str, rule_id: str, expected: int, /) -> bool:
        changed = await connection.execute(
            update(SHARE_RULE_HEADS_TABLE)
            .where(
                SHARE_RULE_HEADS_TABLE.c.scope_id == scope_id,
                SHARE_RULE_HEADS_TABLE.c.rule_id == rule_id,
                SHARE_RULE_HEADS_TABLE.c.version == expected,
            )
            .values(status="invalidated")
        )
        return changed.rowcount == 1

    async def insert_version(
        self,
        connection: AsyncConnection,
        scope_id: str,
        rule_id: str,
        version: int,
        payload: _RulePayload,
        terms_digest: str,
        now: datetime,
        /,
    ) -> None:
        await connection.execute(
            SHARE_RULE_VERSIONS_TABLE.insert().values(
                scope_id=scope_id,
                rule_id=rule_id,
                version=version,
                terms_digest=terms_digest,
                payload=dump_model(payload, kind="share-rule", name=rule_id),
                created_at=_ticks(now),
            )
        )

    async def version_row(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, version: int, /
    ) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(SHARE_RULE_VERSIONS_TABLE).where(
                        SHARE_RULE_VERSIONS_TABLE.c.scope_id == scope_id,
                        SHARE_RULE_VERSIONS_TABLE.c.rule_id == rule_id,
                        SHARE_RULE_VERSIONS_TABLE.c.version == version,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def acceptances(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, version: int, /
    ) -> tuple[ShareAcceptance, ...]:
        rows = (
            await connection.execute(
                select(SHARE_ACCEPTANCES_TABLE).where(
                    SHARE_ACCEPTANCES_TABLE.c.scope_id == scope_id,
                    SHARE_ACCEPTANCES_TABLE.c.rule_id == rule_id,
                    SHARE_ACCEPTANCES_TABLE.c.version == version,
                )
            )
        ).mappings()
        return tuple(
            ShareAcceptance(
                acceptance_id=str(row["acceptance_id"]),
                actor=str(row["actor"]),
                side="origin" if str(row["side"]) == "origin" else "audience",
                covered_scope_ids=load_model(
                    _CoveredScopes,
                    stored_bytes(row["covered_scope_ids"], column="covered_scope_ids"),
                    kind="share-acceptance",
                    name="covered",
                ).scope_ids,
                terms_digest=str(row["terms_digest"]),
                accepted_at=_from_ticks(int(row["accepted_at"])),
            )
            for row in rows
        )

    async def insert_acceptance(
        self,
        connection: AsyncConnection,
        scope_id: str,
        rule_id: str,
        version: int,
        acceptance: ShareAcceptance,
        /,
    ) -> None:
        await connection.execute(
            SHARE_ACCEPTANCES_TABLE.insert().values(
                scope_id=scope_id,
                rule_id=rule_id,
                version=version,
                actor=acceptance.actor,
                side=acceptance.side,
                terms_digest=acceptance.terms_digest,
                covered_scope_ids=dump_model(
                    _CoveredScopes(scope_ids=acceptance.covered_scope_ids),
                    kind="share-acceptance",
                    name="covered",
                ),
                acceptance_id=acceptance.acceptance_id,
                accepted_at=_ticks(acceptance.accepted_at),
            )
        )

    async def build_rule(self, connection: AsyncConnection, scope_id: str, rule_id: str, version: int, /) -> ShareRule:
        head = await self.head(connection, scope_id, rule_id)
        row = await self.version_row(connection, scope_id, rule_id, version)
        if head is None or row is None:
            raise ShareRuleNotFoundError(rule_id)
        payload = load_model(
            _RulePayload,
            stored_bytes(row["payload"], column="payload"),
            kind="share-rule",
            name=rule_id,
        )
        return ShareRule(
            scope_id=scope_id,
            rule_id=rule_id,
            version=version,
            status="active" if str(head["status"]) == "active" else "invalidated",
            selection=payload.selection,
            terms=payload.terms,
            terms_digest=str(row["terms_digest"]),
            created_at=_from_ticks(int(row["created_at"])),
            acceptances=await self.acceptances(connection, scope_id, rule_id, version),
        )

    async def insert_snapshot(
        self,
        connection: AsyncConnection,
        snapshot: ShareSnapshot,
        /,
    ) -> None:
        await connection.execute(
            SHARE_SNAPSHOTS_TABLE.insert().values(
                scope_id=snapshot.scope_id,
                snapshot_id=snapshot.snapshot_id,
                rule_id=snapshot.rule_id,
                rule_version=snapshot.rule_version,
                terms_digest=snapshot.terms_digest,
                membership_digest=snapshot.membership_digest,
                notification_key=snapshot.notification_key,
                payload=dump_model(
                    _SnapshotPayload(inputs=snapshot.inputs, inputs_digest=snapshot.inputs_digest),
                    kind="share-snapshot",
                    name=snapshot.snapshot_id,
                ),
                dream_run_id=None,
                created_at=_ticks(snapshot.created_at),
            )
        )

    async def snapshot_row(
        self, connection: AsyncConnection, scope_id: str, snapshot_id: str, /, *, for_update: bool = False
    ) -> Mapping[Any, Any] | None:
        statement = select(SHARE_SNAPSHOTS_TABLE).where(
            SHARE_SNAPSHOTS_TABLE.c.scope_id == scope_id,
            SHARE_SNAPSHOTS_TABLE.c.snapshot_id == snapshot_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await connection.execute(statement)).mappings().one_or_none()

    async def claim_snapshot(
        self, connection: AsyncConnection, scope_id: str, snapshot_id: str, run_id: str, /
    ) -> str | None:
        """Bind one unclaimed pin to one run, joining the caller's transaction.

        The conditional update is the exclusivity primitive: only a NULL binding can be
        taken, so two admissions racing on the same pin cannot both win, and the durable
        ``uq_pc_share_snapshots_run`` keeps one run from holding two pins. Returns the
        run id that now owns the pin, or ``None`` when the pin does not exist.
        """

        result = await connection.execute(
            update(SHARE_SNAPSHOTS_TABLE)
            .where(
                SHARE_SNAPSHOTS_TABLE.c.scope_id == scope_id,
                SHARE_SNAPSHOTS_TABLE.c.snapshot_id == snapshot_id,
                SHARE_SNAPSHOTS_TABLE.c.dream_run_id.is_(None),
            )
            .values(dream_run_id=run_id)
        )
        if result.rowcount == 1:
            return run_id
        row = await self.snapshot_row(connection, scope_id, snapshot_id)
        return None if row is None else row["dream_run_id"]

    async def snapshot_by_notification(
        self, connection: AsyncConnection, scope_id: str, rule_id: str, notification_key: str, /
    ) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(SHARE_SNAPSHOTS_TABLE).where(
                        SHARE_SNAPSHOTS_TABLE.c.scope_id == scope_id,
                        SHARE_SNAPSHOTS_TABLE.c.rule_id == rule_id,
                        SHARE_SNAPSHOTS_TABLE.c.notification_key == notification_key,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    def decode_snapshot(self, row: Mapping[Any, Any], /) -> ShareSnapshot:
        payload = load_model(
            _SnapshotPayload,
            stored_bytes(row["payload"], column="payload"),
            kind="share-snapshot",
            name=str(row["snapshot_id"]),
        )
        return ShareSnapshot(
            scope_id=str(row["scope_id"]),
            snapshot_id=str(row["snapshot_id"]),
            rule_id=str(row["rule_id"]),
            rule_version=int(row["rule_version"]),
            terms_digest=str(row["terms_digest"]),
            membership_digest=str(row["membership_digest"]),
            inputs_digest=payload.inputs_digest,
            inputs=payload.inputs,
            notification_key=row["notification_key"],
            dream_run_id=row["dream_run_id"],
            created_at=_from_ticks(int(row["created_at"])),
        )


__all__ = ["ShareAgreementRepository"]
