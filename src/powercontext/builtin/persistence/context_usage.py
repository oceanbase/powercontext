# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Bounded reads of recorded preparations and native delivery receipts."""

import json
from datetime import datetime

from sqlalchemy import Text, and_, cast, func, insert, literal, or_, select, tuple_, union_all
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from powercontext.builtin.context_usage import ContextUsageFeedback, ContextUsageRecord
from powercontext.builtin.persistence.bootstrap_receipts import _stored_receipt
from powercontext.builtin.persistence.codec import load_model, stored_bytes
from powercontext.builtin.persistence.errors import IdentityMismatchError, StoredPayloadConflictError
from powercontext.builtin.persistence.tables import (
    CONTEXT_BOOTSTRAP_RECEIPTS_TABLE as DELIVERIES,
)
from powercontext.builtin.persistence.tables import (
    CONTEXT_USAGE_FEEDBACK_TABLE as FEEDBACK,
)
from powercontext.builtin.persistence.tables import (
    CONTEXT_USAGE_RECORDS_TABLE as PREPARATIONS,
)
from powercontext.builtin.records import InvalidCursorError


class ContextUsageRepository:
    async def record(self, connection, record):
        await connection.execute(
            insert(PREPARATIONS).values(
                scope_id=record.scope_id,
                usage_id=record.usage_id,
                created_at=record.created_at,
                payload=record.model_dump_json().encode(),
            )
        )

    async def artifact_preparations(self, connection, scope_id, *, artifact, start, end):
        """Select exact addresses, or all Atomic Memories of a Scope when artifact is None."""
        if connection.dialect.name == "mysql":
            contains = (
                func.json_contains(
                    cast(PREPARATIONS.c.payload, Text),
                    json.dumps({
                        "scope_id": scope_id,
                        "artifact": {"family": "atomic-memory"}
                        if artifact is None
                        else artifact.model_dump(mode="json"),
                    }),
                    "$.items",
                )
                == 1
            )
        else:
            items = func.json_each(cast(PREPARATIONS.c.payload, Text), "$.items").table_valued("value")
            contains = (
                select(1)
                .select_from(items)
                .where(
                    func.json_extract(items.c.value, "$.scope_id") == scope_id,
                    func.json_extract(items.c.value, "$.artifact.family")
                    == ("atomic-memory" if artifact is None else artifact.family),
                    *(
                        []
                        if artifact is None
                        else [
                            func.json_extract(items.c.value, "$.artifact.artifact_id") == artifact.artifact_id,
                            func.json_extract(items.c.value, "$.artifact.revision") == artifact.revision,
                        ]
                    ),
                )
                .correlate(PREPARATIONS)
                .exists()
            )
        rows = (
            (
                await connection.execute(
                    select(PREPARATIONS).where(
                        PREPARATIONS.c.created_at >= start,
                        PREPARATIONS.c.created_at < end,
                        contains,
                    )
                )
            )
            .mappings()
            .all()
        )
        return tuple(_preparation(row) for row in rows)

    async def page(self, connection, scope_id, *, start, end, limit, after="", record_kind=None):
        queries = []
        for table, identity, kind in [
            (PREPARATIONS, "usage_id", "preparation"),
            (DELIVERIES, "receipt_id", "bootstrap"),
        ]:
            if record_kind is not None and kind != record_kind:
                continue
            statement = select(
                table.c.created_at.label("created_at"),
                table.c[identity].label("record_id"),
                literal(kind).label("record_kind"),
            ).where(table.c.scope_id == scope_id, table.c.created_at >= start, table.c.created_at < end)
            if after:
                try:
                    timestamp, previous_kind, previous_id = after.split("|", 2)
                    position = datetime.fromisoformat(timestamp)
                except ValueError as error:
                    raise InvalidCursorError from error
                if previous_kind not in {"preparation", "bootstrap"} or not previous_id:
                    raise InvalidCursorError
                statement = statement.where(
                    or_(
                        table.c.created_at < position,
                        and_(table.c.created_at == position, literal(kind) < previous_kind),
                        and_(
                            table.c.created_at == position,
                            literal(kind) == previous_kind,
                            table.c[identity] < previous_id,
                        ),
                    )
                )
            queries.append(statement)
        page = union_all(*queries).subquery()
        rows = (
            (
                await connection.execute(
                    select(page)
                    .order_by(
                        page.c.created_at.desc(),
                        page.c.record_kind.desc(),
                        page.c.record_id.desc(),
                    )
                    .limit(limit + 1)
                )
            )
            .mappings()
            .all()
        )
        selected = rows[:limit]
        values = await self.get_many(connection, scope_id, selected)
        following = ""
        if len(rows) > limit:
            last = selected[-1]
            following = f"{last['created_at'].isoformat()}|{last['record_kind']}|{last['record_id']}"
        return values, following

    async def get_many(self, connection, scope_id, identities):
        result = {}
        for kind, table, identity in [
            ("preparation", PREPARATIONS, "usage_id"),
            ("bootstrap", DELIVERIES, "receipt_id"),
        ]:
            keys = [item["record_id"] for item in identities if item["record_kind"] == kind]
            if not keys:
                continue
            rows = (
                (
                    await connection.execute(
                        select(table).where(
                            table.c.scope_id == scope_id,
                            table.c[identity].in_(keys),
                        )
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                if kind == "preparation":
                    value = _preparation(row)
                    item = value.model_dump(mode="json")
                    item.update(integration="context.prepare", state="prepared" if value.status == "ready" else "empty")
                else:
                    receipt = _stored_receipt(row)
                    item = {
                        "scope_id": scope_id,
                        "created_at": receipt.created_at.isoformat(),
                        "integration": receipt.integration,
                        "state": receipt.state,
                        "content_bytes": receipt.content_bytes,
                        "reason": receipt.reason,
                        "truncated": receipt.truncated,
                        "items": [entry.model_dump(mode="json") for entry in receipt.items],
                    }
                item.update(record_kind=kind, record_id=row[identity], feedback=None)
                result[kind, row[identity]] = item
        if result:
            ranked = (
                select(
                    FEEDBACK.c.payload,
                    FEEDBACK.c.record_kind,
                    FEEDBACK.c.record_id,
                    func
                    .row_number()
                    .over(
                        partition_by=(FEEDBACK.c.record_kind, FEEDBACK.c.record_id),
                        order_by=(FEEDBACK.c.created_at.desc(), FEEDBACK.c.feedback_id.desc()),
                    )
                    .label("rank"),
                )
                .where(
                    FEEDBACK.c.scope_id == scope_id,
                    tuple_(FEEDBACK.c.record_kind, FEEDBACK.c.record_id).in_(tuple(result)),
                )
                .subquery()
            )
            for row in (await connection.execute(select(ranked).where(ranked.c.rank == 1))).mappings():
                feedback = load_model(
                    ContextUsageFeedback,
                    stored_bytes(row["payload"], column="payload"),
                    kind="context-feedback",
                    name=row["record_id"],
                )
                key = row["record_kind"], row["record_id"]
                if (feedback.scope_id, feedback.record_kind, feedback.record_id) != (scope_id, *key):
                    raise IdentityMismatchError(
                        "context-feedback",
                        (scope_id, *key),
                        (feedback.scope_id, feedback.record_kind, feedback.record_id),
                    )
                result[key]["feedback"] = feedback.model_dump(mode="json")
        return [
            result[item["record_kind"], item["record_id"]]
            for item in identities
            if (item["record_kind"], item["record_id"]) in result
        ]

    async def feedback(self, connection, feedback):
        values = {
            "scope_id": feedback.scope_id,
            "feedback_id": feedback.feedback_id,
            "record_kind": feedback.record_kind,
            "record_id": feedback.record_id,
            "created_at": feedback.created_at,
            "payload": feedback.model_dump_json().encode(),
        }
        if connection.dialect.name == "sqlite":
            statement = (
                sqlite_insert(FEEDBACK)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["scope_id", "feedback_id"])
            )
        elif connection.dialect.name == "mysql":
            statement = (
                mysql_insert(FEEDBACK).values(**values).on_duplicate_key_update(feedback_id=FEEDBACK.c.feedback_id)
            )
        else:
            statement = insert(FEEDBACK).values(**values)
        await connection.execute(statement)
        raw = (
            await connection.execute(
                select(FEEDBACK.c.payload).where(
                    FEEDBACK.c.scope_id == feedback.scope_id,
                    FEEDBACK.c.feedback_id == feedback.feedback_id,
                )
            )
        ).scalar_one()
        saved = load_model(
            ContextUsageFeedback,
            stored_bytes(raw, column="payload"),
            kind="context-feedback",
            name=feedback.feedback_id,
        )
        if saved.model_dump(exclude={"created_at"}) != feedback.model_dump(exclude={"created_at"}):
            raise StoredPayloadConflictError("context-feedback", (feedback.scope_id, feedback.feedback_id))
        return saved


def _preparation(row):
    value = load_model(
        ContextUsageRecord,
        stored_bytes(row["payload"], column="payload"),
        kind="context-usage",
        name=row["usage_id"],
    )
    if (value.scope_id, value.usage_id) != (row["scope_id"], row["usage_id"]):
        raise IdentityMismatchError(
            "context-usage", (row["scope_id"], row["usage_id"]), (value.scope_id, value.usage_id)
        )
    return value
