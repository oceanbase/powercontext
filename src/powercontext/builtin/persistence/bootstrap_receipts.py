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

"""Content-free persistence for bootstrap delivery receipts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

from pydantic import TypeAdapter
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from powercontext.builtin.persistence.codec import stored_bytes
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import CONTEXT_BOOTSTRAP_RECEIPTS_TABLE
from powercontext.builtin.runtime.models import (
    BootstrapContextItem,
    BootstrapContextLifecycle,
    BootstrapContextProfile,
    BootstrapReceiptState,
    BootstrapSkipReason,
)

_ITEMS = TypeAdapter(tuple[BootstrapContextItem, ...])


@dataclass(frozen=True)
class StoredBootstrapReceipt:
    receipt_id: str
    scope_id: str
    event_key: str | None
    integration: str
    lifecycle: BootstrapContextLifecycle
    profile: BootstrapContextProfile
    state: BootstrapReceiptState
    reason: BootstrapSkipReason | None
    max_bytes: int
    package_digest: str | None
    content_bytes: int
    truncated: bool
    items: tuple[BootstrapContextItem, ...]


class BootstrapReceiptRepository:
    """Reserve stable events and apply monotonic delivery outcomes."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._database = database

    async def by_event(self, event_key: str) -> StoredBootstrapReceipt | None:
        async with self._database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE).where(
                            CONTEXT_BOOTSTRAP_RECEIPTS_TABLE.c.event_key == event_key
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _stored_receipt(row)

    async def create(self, receipt: StoredBootstrapReceipt) -> tuple[StoredBootstrapReceipt, bool]:
        values = _values(receipt)
        async with self._database.transaction() as connection:
            if receipt.event_key is None:
                await connection.execute(insert(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE).values(**values))
                return receipt, True
            if connection.dialect.name == "sqlite":
                statement = (
                    sqlite_insert(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=["event_key"])
                )
            elif connection.dialect.name == "mysql":
                statement = mysql_insert(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE).values(**values).prefix_with("IGNORE")
            else:
                statement = insert(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE).values(**values)
            result = await connection.execute(statement)
            if result.rowcount == 1:
                return receipt, True
            row = (
                (
                    await connection.execute(
                        select(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE).where(
                            CONTEXT_BOOTSTRAP_RECEIPTS_TABLE.c.event_key == receipt.event_key
                        )
                    )
                )
                .mappings()
                .one()
            )
            return _stored_receipt(row), False

    async def record_delivery(
        self,
        scope_id: str,
        receipt_id: str,
        outcome: BootstrapReceiptState,
    ) -> tuple[StoredBootstrapReceipt | None, bool]:
        if outcome not in {"injected", "failed", "skipped"}:
            raise ValueError("invalid bootstrap delivery outcome")  # noqa: TRY003
        async with self._database.transaction() as connection:
            table = CONTEXT_BOOTSTRAP_RECEIPTS_TABLE
            changed = await connection.execute(
                update(table)
                .where(table.c.scope_id == scope_id, table.c.receipt_id == receipt_id, table.c.state == "pending")
                .values(state=outcome)
            )
            row = (
                (
                    await connection.execute(
                        select(table).where(table.c.scope_id == scope_id, table.c.receipt_id == receipt_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None, False
        stored = _stored_receipt(row)
        claimed = changed.rowcount == 1
        return (replace(stored, state=outcome) if claimed else stored), claimed

    async def injected_memory_keys(self, scope_id: str, receipt_id: str) -> frozenset[tuple[object, ...]]:
        async with self._database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(
                            CONTEXT_BOOTSTRAP_RECEIPTS_TABLE.c.state,
                            CONTEXT_BOOTSTRAP_RECEIPTS_TABLE.c["items"],
                        ).where(
                            CONTEXT_BOOTSTRAP_RECEIPTS_TABLE.c.scope_id == scope_id,
                            CONTEXT_BOOTSTRAP_RECEIPTS_TABLE.c.receipt_id == receipt_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None or row["state"] != "injected":
            return frozenset()
        items = _load_items(row["items"], receipt_id)
        return frozenset(
            (
                item.scope_id,
                item.artifact.artifact_id,
                item.artifact.revision,
                item.entry_id,
                item.entry_version_id,
            )
            for item in items
            if item.kind == "memory_entry"
        )


def _values(receipt: StoredBootstrapReceipt) -> dict[str, object]:
    return {
        "receipt_id": receipt.receipt_id,
        "scope_id": receipt.scope_id,
        "event_key": receipt.event_key,
        "integration": receipt.integration,
        "lifecycle": receipt.lifecycle,
        "profile": receipt.profile,
        "state": receipt.state,
        "reason": receipt.reason,
        "max_bytes": receipt.max_bytes,
        "package_digest": receipt.package_digest,
        "content_bytes": receipt.content_bytes,
        "truncated": receipt.truncated,
        "items": _ITEMS.dump_json(receipt.items),
    }


def _load_items(value: object, receipt_id: str) -> tuple[BootstrapContextItem, ...]:
    return _ITEMS.validate_json(stored_bytes(value, column=f"bootstrap receipt {receipt_id} items"), strict=True)


def _stored_receipt(row: object) -> StoredBootstrapReceipt:
    values = cast(dict[str, object], row)
    receipt_id = cast(str, values["receipt_id"])
    return StoredBootstrapReceipt(
        receipt_id=receipt_id,
        scope_id=cast(str, values["scope_id"]),
        event_key=cast(str | None, values["event_key"]),
        integration=cast(str, values["integration"]),
        lifecycle=cast(BootstrapContextLifecycle, values["lifecycle"]),
        profile=cast(BootstrapContextProfile, values["profile"]),
        state=cast(BootstrapReceiptState, values["state"]),
        reason=cast(BootstrapSkipReason | None, values["reason"]),
        max_bytes=cast(int, values["max_bytes"]),
        package_digest=cast(str | None, values["package_digest"]),
        content_bytes=cast(int, values["content_bytes"]),
        truncated=cast(bool, values["truncated"]),
        items=_load_items(values["items"], receipt_id),
    )
