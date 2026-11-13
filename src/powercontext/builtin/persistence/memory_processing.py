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

"""Transaction-owned processing history and indexed exact relationships."""

from datetime import datetime

from sqlalchemy import Text, and_, case, cast, func, insert, literal, or_, select, true, tuple_, union_all, update

from powercontext.builtin.memory_processing import MemoryProcessingRecord
from powercontext.builtin.persistence.codec import load_model, stored_bytes
from powercontext.builtin.persistence.errors import IdentityMismatchError, StoredPayloadConflictError
from powercontext.builtin.persistence.tables import (
    CONTEXT_BOOTSTRAP_RECEIPTS_TABLE,
    CONTEXT_USAGE_FEEDBACK_TABLE,
    CONTEXT_USAGE_RECORDS_TABLE,
    MEMORY_PROCESSING_ARTIFACTS_TABLE,
    MEMORY_PROCESSING_RECORDS_TABLE,
    MEMORY_PROCESSING_SOURCES_TABLE,
)
from powercontext.builtin.records import InvalidCursorError

REFERENCE_QUERY_BATCH = 250


def json_text(connection, column, path):
    """Extract one JSON string field in a dialect-safe way for bounded filters."""
    value = func.json_extract(cast(column, Text), path)
    return func.json_unquote(value) if connection.dialect.name == "mysql" else value


class MemoryProcessingRepository:
    async def start(self, connection, record):
        if record.previous_attempt_id is None:
            previous, _ = await self.page(connection, record.scope_id, source=record.sources[0], limit=1)
            if (
                previous
                and previous[0].status != "succeeded"
                and (previous[0].source_after, previous[0].source_through)
                == (record.source_after, record.source_through)
            ):
                record = record.model_copy(update={"previous_attempt_id": previous[0].attempt_id})
        await connection.execute(
            insert(MEMORY_PROCESSING_RECORDS_TABLE).values(
                scope_id=record.scope_id,
                attempt_id=record.attempt_id,
                started_at=record.started_at,
                payload=_payload(record),
            )
        )
        await connection.execute(
            insert(MEMORY_PROCESSING_SOURCES_TABLE),
            [
                {
                    "scope_id": record.scope_id,
                    "attempt_id": record.attempt_id,
                    "source_type": ref.source_type,
                    "source_id": ref.source_id,
                }
                for ref in record.sources
            ],
        )
        return record

    async def finish(self, connection, record):
        table = MEMORY_PROCESSING_RECORDS_TABLE
        selected = (
            select(table.c.payload)
            .where(
                table.c.scope_id == record.scope_id,
                table.c.attempt_id == record.attempt_id,
            )
            .with_for_update()
        )
        existing = (await connection.execute(selected)).scalar_one()
        payload = _payload(record)
        if stored_bytes(existing, column="payload") == payload:
            return
        current = MemoryProcessingRecord.model_validate_json(existing)
        if current.status != "started":
            raise StoredPayloadConflictError("memory-processing", (record.scope_id, record.attempt_id))
        updated = await connection.execute(
            update(table)
            .where(
                table.c.scope_id == record.scope_id,
                table.c.attempt_id == record.attempt_id,
                table.c.payload == existing,
            )
            .values(payload=payload)
        )
        if updated.rowcount != 1:
            raise StoredPayloadConflictError("memory-processing", (record.scope_id, record.attempt_id))
        refs = {
            (ref.artifact_id, ref.revision): ref
            for ref in (
                *(result.artifact for result in record.results),
                *record.retained_artifacts,
            )
        }
        if refs:
            await connection.execute(
                insert(MEMORY_PROCESSING_ARTIFACTS_TABLE),
                [
                    {
                        "scope_id": record.scope_id,
                        "attempt_id": record.attempt_id,
                        "artifact_id": ref.artifact_id,
                        "revision": ref.revision,
                    }
                    for ref in refs.values()
                ],
            )

    async def page_scope(self, connection, scope_id, *, limit=10, after="", status=None, since=None, until=None):
        """Page one Scope's attempts in reverse start order with bounded filters."""
        table = MEMORY_PROCESSING_RECORDS_TABLE
        criteria = [table.c.scope_id == scope_id]
        statuses = None if status is None else ((status,) if isinstance(status, str) else tuple(status))
        if statuses:
            criteria.append(json_text(connection, table.c.payload, "$.status").in_(statuses))
        if since is not None:
            criteria.append(table.c.started_at >= since)
        if until is not None:
            criteria.append(table.c.started_at < until)
        if after:
            try:
                timestamp, identity = after.split("|", 1)
                position = datetime.fromisoformat(timestamp)
            except ValueError as error:
                raise InvalidCursorError from error
            if not identity:
                raise InvalidCursorError
            criteria.append(
                or_(
                    table.c.started_at < position,
                    and_(table.c.started_at == position, table.c.attempt_id < identity),
                )
            )
        rows = (
            (
                await connection.execute(
                    select(table)
                    .where(*criteria)
                    .order_by(table.c.started_at.desc(), table.c.attempt_id.desc())
                    .limit(limit + 1)
                )
            )
            .mappings()
            .all()
        )
        items = tuple(_decode(row) for row in rows[:limit])
        cursor = "" if len(rows) <= limit else f"{items[-1].started_at.isoformat()}|{items[-1].attempt_id}"
        return items, cursor

    async def summarize(self, connection, scope_id, *, since, until):
        """Count attempts and published changes; a missing lower bound includes all history.

        Decoding is proportional to the selected attempt history.
        """
        table = MEMORY_PROCESSING_RECORDS_TABLE
        rows = (
            (
                await connection.execute(
                    select(table).where(
                        table.c.scope_id == scope_id,
                        table.c.started_at >= since if since is not None else true(),
                        table.c.started_at < until,
                    )
                )
            )
            .mappings()
            .all()
        )
        attempts = changed = created = updated = empty = failed = 0
        failed_by_stage: dict[str, int] = {}
        succeeded_inputs: set[tuple[str, str]] = set()
        pending_inputs: set[tuple[str, str]] = set()
        seen_inputs: set[tuple[str, str]] = set()
        for row in rows:
            record = _decode(row)
            attempts += 1
            inputs = {(ref.source_type, ref.source_id) for ref in record.sources}
            seen_inputs |= inputs
            if record.status == "started":
                pending_inputs |= inputs
                continue
            if record.status != "succeeded":
                failed += 1
                failed_by_stage[record.stage] = failed_by_stage.get(record.stage, 0) + 1
                continue
            succeeded_inputs |= inputs
            if not record.results:
                empty += 1
            else:
                changed += 1
            created += sum(1 for item in record.results if item.action == "create")
            updated += sum(1 for item in record.results if item.action in {"revise", "merge"})
        pending_inputs -= succeeded_inputs
        failed_inputs = seen_inputs - succeeded_inputs - pending_inputs
        deliveries, adopted, unreported = await self._delivery_counts(connection, scope_id, since=since, until=until)
        return {
            "attempts": attempts,
            "changed_attempts": changed,
            "created": created,
            "updated": updated,
            "empty_attempts": empty,
            "failed_attempts": failed,
            "failed_by_stage": failed_by_stage,
            "inputs_total": len(seen_inputs),
            "inputs_completed": len(succeeded_inputs),
            "inputs_pending": len(pending_inputs),
            "inputs_failed": len(failed_inputs),
            "deliveries": deliveries,
            "adopted": adopted,
            "unreported": unreported,
        }

    async def attempted_sources(self, connection, scope_id, refs, *, since, until):
        """Return requested Sources already represented by attempts in the window."""
        if not refs:
            return set()
        table = MEMORY_PROCESSING_SOURCES_TABLE
        records = MEMORY_PROCESSING_RECORDS_TABLE
        attempted = set()
        for start in range(0, len(refs), REFERENCE_QUERY_BATCH):
            chunk = refs[start : start + REFERENCE_QUERY_BATCH]
            rows = await connection.execute(
                select(table.c.source_type, table.c.source_id)
                .distinct()
                .join(records, and_(table.c.scope_id == records.c.scope_id, table.c.attempt_id == records.c.attempt_id))
                .where(
                    table.c.scope_id == scope_id,
                    tuple_(table.c.source_type, table.c.source_id).in_(chunk),
                    records.c.started_at >= since if since is not None else true(),
                    records.c.started_at < until,
                )
            )
            attempted.update(tuple(row) for row in rows)
        return attempted

    async def _delivery_counts(self, connection, scope_id, *, since, until):
        """Count confirmed deliveries and latest reports for records created in the window."""
        deliveries = await connection.scalar(
            select(func.count())
            .select_from(CONTEXT_BOOTSTRAP_RECEIPTS_TABLE)
            .where(
                CONTEXT_BOOTSTRAP_RECEIPTS_TABLE.c.scope_id == scope_id,
                CONTEXT_BOOTSTRAP_RECEIPTS_TABLE.c.created_at >= since if since is not None else true(),
                CONTEXT_BOOTSTRAP_RECEIPTS_TABLE.c.created_at < until,
                CONTEXT_BOOTSTRAP_RECEIPTS_TABLE.c.state == "injected",
            )
        )
        records = union_all(
            *(
                select(table.c[identity].label("record_id"), literal(kind).label("record_kind")).where(
                    table.c.scope_id == scope_id,
                    table.c.created_at >= since if since is not None else true(),
                    table.c.created_at < until,
                )
                for table, identity, kind in (
                    (CONTEXT_USAGE_RECORDS_TABLE, "usage_id", "preparation"),
                    (CONTEXT_BOOTSTRAP_RECEIPTS_TABLE, "receipt_id", "bootstrap"),
                )
            )
        ).subquery()
        feedback = CONTEXT_USAGE_FEEDBACK_TABLE
        ranked = (
            select(
                feedback.c.record_kind,
                feedback.c.record_id,
                json_text(connection, feedback.c.payload, "$.adoption").label("adoption"),
                func
                .row_number()
                .over(
                    partition_by=(feedback.c.record_kind, feedback.c.record_id),
                    order_by=(feedback.c.created_at.desc(), feedback.c.feedback_id.desc()),
                )
                .label("rank"),
            )
            .join(
                records,
                and_(
                    records.c.record_kind == feedback.c.record_kind,
                    records.c.record_id == feedback.c.record_id,
                ),
            )
            .where(feedback.c.scope_id == scope_id, feedback.c.created_at < until)
            .subquery()
        )
        latest = select(ranked).where(ranked.c.rank == 1).subquery()
        adopted, unreported = (
            await connection.execute(
                select(
                    func.coalesce(func.sum(case((latest.c.adoption == "yes", 1), else_=0)), 0),
                    func.coalesce(
                        func.sum(case((or_(latest.c.adoption.is_(None), latest.c.adoption == "unknown"), 1), else_=0)),
                        0,
                    ),
                ).select_from(
                    records.outerjoin(
                        latest,
                        and_(
                            records.c.record_kind == latest.c.record_kind,
                            records.c.record_id == latest.c.record_id,
                        ),
                    )
                )
            )
        ).one()
        return deliveries or 0, int(adopted), int(unreported)

    async def page(self, connection, scope_id, *, source=None, artifact=None, limit=10, after=""):
        table = MEMORY_PROCESSING_RECORDS_TABLE
        if source is not None:
            relations = MEMORY_PROCESSING_SOURCES_TABLE
            criteria = (relations.c.source_type == source.source_type, relations.c.source_id == source.source_id)
        elif artifact is not None:
            relations = MEMORY_PROCESSING_ARTIFACTS_TABLE
            criteria = (relations.c.artifact_id == artifact.artifact_id, relations.c.revision == artifact.revision)
        else:
            raise InvalidCursorError
        # Select exact relationship keys first. An ordered join can otherwise
        # scan every attempt in the Scope to find one Source's sparse history.
        keys = select(relations.c.scope_id, relations.c.attempt_id).where(relations.c.scope_id == scope_id, *criteria)
        statement = select(table).where(tuple_(table.c.scope_id, table.c.attempt_id).in_(keys))
        if after:
            try:
                timestamp, identity = after.split("|", 1)
                position = datetime.fromisoformat(timestamp)
            except ValueError as error:
                raise InvalidCursorError from error
            if not identity:
                raise InvalidCursorError
            statement = statement.where(
                or_(
                    table.c.started_at < position,
                    and_(table.c.started_at == position, table.c.attempt_id < identity),
                )
            )
        rows = (
            (
                await connection.execute(
                    statement.order_by(
                        table.c.started_at.desc(),
                        table.c.attempt_id.desc(),
                    ).limit(limit + 1)
                )
            )
            .mappings()
            .all()
        )
        items = tuple(_decode(row) for row in rows[:limit])
        cursor = "" if len(rows) <= limit else f"{items[-1].started_at.isoformat()}|{items[-1].attempt_id}"
        return items, cursor


def _payload(record):
    # The read set is private authorization data, excluded from transport output.
    value = record.model_dump(mode="json")
    value["read_artifacts"] = [ref.model_dump(mode="json") for ref in record.read_artifacts]
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _decode(row):
    record = load_model(
        MemoryProcessingRecord,
        stored_bytes(row["payload"], column="payload"),
        kind="memory-processing",
        name=row["attempt_id"],
    )
    indexed = row["scope_id"], row["attempt_id"]
    decoded = record.scope_id, record.attempt_id
    if indexed != decoded:
        raise IdentityMismatchError("memory-processing", indexed, decoded)
    return record
