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

"""Scoped reads whose content, governance, and derived data share one transaction."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence import scope_reads
from powercontext.builtin.persistence.artifact_governance import decode_artifact_governance
from powercontext.builtin.persistence.atomic_memory import _atomic_state
from powercontext.builtin.persistence.errors import InvalidStoredPayloadError
from powercontext.builtin.persistence.revision_meta import meta_from_row
from powercontext.builtin.persistence.statistics import decode_daily_usage
from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE
from powercontext.builtin.records import InvalidBaseAccessRequestError
from powercontext.builtin.runtime.atomic_memory import ScopedAtomicMemory
from powercontext.builtin.runtime.statistics import (
    RelationalScopedStatistics,
    _as_utc,
    _recall_statistics,
    _resolve_period,
    _usage_statistics,
)
from powercontext.builtin.statistics import StatisticsPeriod


def _content_heading(content, fallback):
    """Use a saved field's first line without exposing a serialized content envelope."""
    for key in ("text", "title", "lesson", "objective", "description", "name", "content"):
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0].lstrip("# ")[:180]
    return fallback


class ScopeReadSnapshot:
    """Borrowed built-in read capability; the Server must authorize before using it.

    Rows stay undecoded until a caller selects a section, so a malformed content
    body does not prevent reading unrelated Families from the same snapshot.
    """

    def __init__(
        self,
        connection: AsyncConnection,
        scope_id: str,
        atomic: ScopedAtomicMemory,
        statistics: RelationalScopedStatistics,
        as_of: datetime,
        records,
    ) -> None:
        self.connection = connection
        self.scope_id = scope_id
        self._atomic = atomic
        self._artifacts = atomic.application.artifacts
        self._statistics = statistics
        self._as_of = as_of
        self._records = records

    async def content(
        self,
        *,
        state: str,
        limit: int,
        cursor: str | None = None,
        selected=None,
        families: tuple[str, ...] = (),
        browse: bool = True,
        include_scope: bool | None = None,
        context=None,
    ):
        statement = None
        bound = {}
        if browse:
            statement, bound, _ = self._atomic.list_query(
                self.connection, states=(state,), limit=limit, cursor=cursor, context=context
            )
        rows, descriptor = await scope_reads.content_rows(
            self.connection,
            self.scope_id,
            browse=statement,
            families=families,
            selected=selected,
            include_scope=browse if include_scope is None else include_scope,
        )
        notes = sorted((row for row in rows if row["read_kind"] == "notes"), key=lambda row: row["artifact_id"])
        cursor = (
            self._atomic.application.cursor.encode(bound, notes[limit - 1]["artifact_id"])
            if len(notes) > limit
            else None
        )
        return rows, notes[:limit], cursor, descriptor

    async def history(self, family, artifact_id, selected, *, limit, cursor=None, compare=None, include_current=False):
        columns = tuple(
            ARTIFACTS_TABLE.c[name]
            for name in ("revision", "created_at", "created_by", "restored_from_revision", "rollback_reason")
        )
        rows, head, next_cursor = await self._records.revision_rows(
            self.connection, self.scope_id, family, artifact_id, limit=limit, cursor=cursor, columns=columns
        )
        items = []
        for row in rows:
            meta = meta_from_row(row)
            items.append({
                "revision": row["revision"],
                "created_at": None if meta.created_at is None else meta.created_at.isoformat(),
                "created_by": None
                if meta.created_by_id is None
                else {
                    "type": meta.created_by_type,
                    "id": meta.created_by_id,
                },
                "restored_from_revision": meta.restored_from_revision,
                "reason": meta.reason,
            })
        revisions = set() if compare is None else {selected, compare}
        if include_current and selected != head:
            revisions.update((selected, head))
        bodies = await scope_reads.revision_contents(self.connection, self.scope_id, family, artifact_id, revisions)
        values = {
            row["revision"]: {"content": self._artifacts.decode_content(row).model_dump(mode="json", by_alias=True)}
            for row in bodies
        }
        return {"items": items, "head": head, "next_cursor": next_cursor, "values": values}

    async def scope(self):
        return await scope_reads.scope_header(self.connection, self.scope_id)

    async def source_processing_directory(self, *, limit: int, after: int | None = None, context=None):
        """Read one authorized Source page without reconstructing execution history."""
        if type(limit) is not int or not 1 <= limit <= 20:
            from powercontext.builtin.records import InvalidBaseAccessRequestError

            raise InvalidBaseAccessRequestError("limit", "must be from 1 to 20")
        await self._atomic.application.security.authorize(self.connection, self.scope_id, context, "read")
        from powercontext.artifacts import ArtifactRef

        result = await scope_reads.source_processing_directory(
            self.connection, self.scope_id, self._records._sources, limit, after
        )
        for item in result["items"]:
            for ref in item.pop("artifact_representatives"):
                await self._atomic.application.security.authorize(
                    self.connection, self.scope_id, context, "read", ArtifactRef.model_validate(ref)
                )
        return result

    async def usage(self, period: str, *, include_scope: bool = False):
        resolved = _resolve_period(StatisticsPeriod(period), _as_utc(self._as_of).date())
        estimator = self._statistics._token_estimator
        rows, descriptor = await scope_reads.usage_rows(
            self.connection, self.scope_id, resolved, estimator, include_scope=include_scope
        )
        usage, recall = decode_daily_usage(rows, (self.scope_id,))
        return (
            _usage_statistics(resolved, usage[self.scope_id]),
            _recall_statistics(resolved, estimator, recall[self.scope_id]),
            descriptor,
        )

    async def artifact(self, row):
        """Expand only the chosen immutable Revision's direct and publication lineage."""
        return (await self._artifacts.decode_many(self.connection, self.scope_id, (row,)))[0]

    def preview(self, row):
        content = self._artifacts.decode_content(row).model_dump(mode="json", by_alias=True)
        head = decode_artifact_governance({**row, "revision": row["current_revision"]})
        ref = {"family": row["family"], "artifact_id": row["artifact_id"], "revision": row["revision"]}
        value = {**content, **ref}
        if row["family"] == "atomic-memory":
            value.update(
                artifact=ref,
                **_atomic_state(head).model_dump(mode="json"),
                current_artifact=head.artifact.model_dump(mode="json"),
                is_current=ref == head.artifact.model_dump(),
            )
        elif row["family"] == "experience":
            value["source_count"] = int(row["source_count"])
        return value

    async def inventory(self):
        return await self._statistics.inventory(self.connection)

    async def recent_changes(self, *, limit=6, context=None):
        from powercontext.artifacts import ArtifactRef
        from powercontext.builtin.persistence.dashboard_activity import recent_revisions

        security = self._atomic.application.security
        await security.authorize(self.connection, self.scope_id, context, "read")
        result = []
        for row in await recent_revisions(self.connection, self.scope_id, limit=limit):
            ref = ArtifactRef(family=row["family"], artifact_id=row["artifact_id"], revision=row["revision"])
            await security.authorize(self.connection, self.scope_id, context, "read", ref)
            item = {**ref.model_dump(mode="json"), "at": row["saved_at"].isoformat()}
            try:
                content = self._artifacts.decode_content(row).model_dump(mode="json")
            except InvalidStoredPayloadError:
                # One unreadable head must not hide the rest of the overview.
                result.append({**item, "title": None, "unreadable": True})
                continue
            result.append({**item, "title": _content_heading(content, row["family"]), "unreadable": False})
        return result

    async def candidate_titles(self, identities, *, context=None):
        from powercontext.builtin.persistence.dashboard_activity import candidate_titles

        if len(identities) > 20:
            raise InvalidBaseAccessRequestError("candidates", "requires at most 20 candidates")
        await self._atomic.application.security.authorize(self.connection, self.scope_id, context, "read")
        return await candidate_titles(self.connection, self.scope_id, identities)

    async def work_followup(self, artifact, *, context=None):
        from pydantic import ValidationError

        from powercontext.builtin.persistence.dashboard_activity import work_records
        from powercontext.builtin.work.models import HandoffReceipt, TaskOutcome

        security = self._atomic.application.security
        await security.authorize(self.connection, self.scope_id, context, "read")
        await security.authorize(self.connection, self.scope_id, context, "read", artifact)
        rows = await work_records(self.connection, self.scope_id, artifact)
        sources = [self._records._sources.decode_source(row) for row in rows]
        receipts = {}
        results = []
        for value in sources:
            kind = value.value.metadata.get("kind")
            try:
                if kind == "handoff-receipt":
                    record = HandoffReceipt.model_validate_json(value.value.content)
                    if record.selected_revision != artifact or not value.value.handoff_receipt:
                        continue
                    receipts[value.ref.source_id] = value.journal_position
                    results.append({
                        "kind": kind,
                        "source_id": value.ref.source_id,
                        "status": record.status,
                        "summary": record.message,
                        "actor": record.receiver,
                    })
                elif kind == "task-outcome":
                    record = TaskOutcome.model_validate_json(value.value.content)
                    ref = record.handoff_receipt_ref
                    if (
                        ref is None
                        or ref.source_id not in receipts
                        or receipts[ref.source_id] >= value.journal_position
                    ):
                        continue
                    results.append({
                        "kind": kind,
                        "source_id": value.ref.source_id,
                        "status": record.status,
                        "summary": record.summary,
                        "checks": [item.model_dump(mode="json") for item in record.checks],
                        "remaining_work": record.remaining_work,
                    })
            except ValidationError:
                continue
        return results

    async def source_artifacts(self, source_id, *, limit=21, context=None):
        from powercontext.artifacts import ArtifactRef

        security = self._atomic.application.security
        await security.authorize(self.connection, self.scope_id, context, "read")
        refs = await scope_reads.source_artifacts(self.connection, self.scope_id, source_id, limit=limit)
        for ref in refs:
            await security.authorize(self.connection, self.scope_id, context, "read", ArtifactRef.model_validate(ref))
        return refs

    async def source_directory(self, *, limit=6, cursor=None, context=None):
        application = self._atomic.application
        bound = {
            "endpoint": "dashboard_sources",
            "version": 1,
            "scope_id": self.scope_id,
            "subject": application.security.subject(context),
        }
        after = application.cursor.after_text(cursor, bound)
        if after and (not after.isascii() or not after.isdecimal() or len(after) > 20 or int(after) < 1):
            from powercontext.builtin.records import InvalidCursorError

            raise InvalidCursorError
        result = await self.source_processing_directory(
            limit=limit, after=int(after) if after else None, context=context
        )
        position = result["next_position"]
        return {
            "items": result["items"],
            "next_cursor": application.cursor.encode(bound, str(position)) if position else None,
        }
