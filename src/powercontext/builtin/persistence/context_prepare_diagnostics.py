# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Append and keyset-page durable prepare-context diagnostics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.codec import stored_bytes
from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError
from powercontext.builtin.persistence.tables import CONTEXT_PREPARE_DIAGNOSTICS_TABLE
from powercontext.builtin.sources import validate_scope_id

PAGE_LIMIT = 25


@dataclass(frozen=True, slots=True)
class PrepareDiagnosticCursor:
    finished_at: datetime
    record_id: str


class ContextPrepareDiagnosticsRepository:
    """Persist immutable summaries and the exact result of one prepare execution."""

    async def append(self, connection: AsyncConnection, record: dict[str, Any]) -> None:
        values = dict(record)
        values["receipt_payload"] = _json_bytes(values.get("receipt_payload"))
        values["diagnostics_payload"] = _json_bytes(values["diagnostics_payload"])
        await connection.execute(insert(CONTEXT_PREPARE_DIAGNOSTICS_TABLE).values(**values))

    async def list_page(
        self,
        connection: AsyncConnection,
        scope_id: str,
        *,
        status: str | None = None,
        before: PrepareDiagnosticCursor | None = None,
        limit: int = PAGE_LIMIT,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[tuple[dict[str, Any], ...], PrepareDiagnosticCursor | None]:
        scope = validate_scope_id(scope_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= PAGE_LIMIT:
            raise InvalidRepositoryArgumentError("limit", "must be between 1 and 25")
        if status is not None and status not in {"ready", "empty", "failed", "cancelled"}:
            raise InvalidRepositoryArgumentError("status", "is not a prepare diagnostic status")
        table = CONTEXT_PREPARE_DIAGNOSTICS_TABLE
        statement = select(
            table.c.record_id,
            table.c.scope_id,
            table.c.started_at,
            table.c.finished_at,
            table.c.duration_ms,
            table.c.status,
            table.c.query_digest,
            table.c.query_text,
            table.c.requested_max_bytes,
            table.c.content_bytes,
            table.c.selected_count,
            table.c.error_code,
        ).where(table.c.scope_id == scope)
        if status is not None:
            statement = statement.where(table.c.status == status)
        if since is not None:
            statement = statement.where(table.c.finished_at >= since)
        if until is not None:
            statement = statement.where(table.c.finished_at < until)
        if before is not None:
            statement = statement.where(
                or_(
                    table.c.finished_at < before.finished_at,
                    and_(table.c.finished_at == before.finished_at, table.c.record_id < before.record_id),
                )
            )
        rows = (
            (
                await connection.execute(
                    statement.order_by(table.c.finished_at.desc(), table.c.record_id.desc()).limit(limit + 1)
                )
            )
            .mappings()
            .all()
        )
        has_more = len(rows) > limit
        page = tuple(dict(row) for row in rows[:limit])
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = PrepareDiagnosticCursor(last["finished_at"], last["record_id"])
        return page, next_cursor

    async def get(self, connection: AsyncConnection, scope_id: str, record_id: str) -> dict[str, Any] | None:
        scope = validate_scope_id(scope_id)
        table = CONTEXT_PREPARE_DIAGNOSTICS_TABLE
        row = (
            (await connection.execute(select(table).where(table.c.scope_id == scope, table.c.record_id == record_id)))
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        result = dict(row)
        result["receipt_payload"] = _decode_json(result["receipt_payload"], "receipt_payload")
        result["diagnostics_payload"] = _decode_json(result["diagnostics_payload"], "diagnostics_payload")
        return result


def _json_bytes(value: object) -> bytes | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode_json(value: object, column: str) -> Any:
    if value is None:
        return None
    try:
        return json.loads(stored_bytes(value, column=column).decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise InvalidRepositoryArgumentError(column, "contains invalid stored JSON") from error
