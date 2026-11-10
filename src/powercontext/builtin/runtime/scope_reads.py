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

from datetime import UTC, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence import scope_reads
from powercontext.builtin.persistence.artifact_governance import decode_artifact_governance
from powercontext.builtin.persistence.atomic_memory import _atomic_state
from powercontext.builtin.persistence.errors import InvalidStoredPayloadError
from powercontext.builtin.persistence.memory_processing import MemoryProcessingRepository
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

PROCESSING_WAITING_SCAN = 1000


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
        context_usage=None,
    ) -> None:
        self.connection = connection
        self.scope_id = scope_id
        self._atomic = atomic
        self._artifacts = atomic.application.artifacts
        self._statistics = statistics
        self._as_of = as_of
        self._records = records
        self._context_usage = context_usage

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
        sort_by="identity",
        period="7d",
    ):
        statement = None
        bound = {}
        counts = None
        ranked_cursor = None
        if browse and sort_by == "recall_count":
            if self._context_usage is None:
                raise InvalidBaseAccessRequestError("sort_by", "recall counts are unavailable")
            statement, counts, ranked_cursor = await self._context_usage.memory_list_query(
                self.connection,
                self.scope_id,
                self._atomic,
                states=(state,),
                limit=limit,
                cursor=cursor,
                period=period,
                context=context,
            )
        elif browse:
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
        if counts is not None:
            notes = sorted(notes, key=lambda row: (-counts[row["artifact_id"]], row["artifact_id"]))
            notes = [{**row, "recall_count": counts[row["artifact_id"]]} for row in notes]
            cursor = ranked_cursor
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

    async def inventory(self):
        return await self._statistics.inventory(self.connection)

    async def processing_coverage(self, *, start_date, end_date, context=None):
        """Read current inventory and bounded daily evidence without inventing event history."""
        await self._atomic.application.security.authorize(self.connection, self.scope_id, context, "read")
        days = await self._statistics._repository.preparation_days(self.connection, self.scope_id, start_date, end_date)
        inventory = await self.inventory()
        return {
            "sources": inventory.sources.total,
            "memories": inventory.memory.entries.active,
            "preparations": sum(count for _, count in days),
            "days": [{"date": day.isoformat(), "preparations": count} for day, count in days],
        }

    async def source_artifacts(self, source_id, *, limit=21):
        return await scope_reads.source_artifacts(self.connection, self.scope_id, source_id, limit=limit)

    async def memory_processing(self, *, source=None, artifact=None, limit=10, cursor=None, context=None):
        """Read private processing history only after checking every disclosed input."""
        if (source is None) == (artifact is None) or type(limit) is not int or not 1 <= limit <= 20:
            raise InvalidBaseAccessRequestError("processing", "requires one selector and a limit from 1 to 20")
        application = self._atomic.application
        security = application.security
        await security.authorize(self.connection, self.scope_id, context, "read")
        source_mode = None
        direct_artifact = None
        consumption_state = None
        if source is not None:
            await security.authorize_sources(self.connection, self.scope_id, context, (source,))
            stored = await self._records._sources.get(self.connection, self.scope_id, source)
            from powercontext.builtin.sources import ContentSource

            source_mode = "ordinary"
            if isinstance(stored.value, ContentSource):
                if stored.value.internal is not None:
                    source_mode = "internal"
                    target = stored.value.internal.target
                    from powercontext.artifacts import ArtifactRef

                    direct_artifact = ArtifactRef(
                        family=target.family,
                        artifact_id=target.artifact_id,
                        revision=target.revision,
                    )
                    await security.authorize(self.connection, self.scope_id, context, "read", direct_artifact)
                elif stored.value.is_working_note:
                    source_mode = "working_note"
            if source_mode == "ordinary":
                from powercontext.builtin.persistence.cursors import SourceCursorRepository
                from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME

                progress = await SourceCursorRepository().load(
                    self.connection, self.scope_id, SOURCE_WINDOW_TRIGGER_NAME
                )
                sequence = 0 if progress is None else progress.cursor.sequence
                consumption_state = "consumed" if sequence >= stored.journal_position else "unconsumed"
            else:
                consumption_state = "not_applicable"
        elif artifact is not None:
            await application.service.get(
                self.connection,
                self.scope_id,
                artifact.artifact_id,
                context,
                revision=artifact.revision,
            )
        bound = {
            "endpoint": "memory_processing",
            "version": 1,
            "scope_id": self.scope_id,
            "subject": security.subject(context),
            "source": None if source is None else source.model_dump(mode="json"),
            "artifact": None if artifact is None else artifact.model_dump(mode="json"),
        }
        after = application.cursor.after_text(cursor, bound)
        items, following = await MemoryProcessingRepository().page(
            self.connection,
            self.scope_id,
            source=source,
            artifact=artifact,
            limit=limit,
            after=after,
        )
        for item in items:
            await security.authorize_sources(self.connection, self.scope_id, context, item.sources)
            refs = {
                (ref.family, ref.artifact_id, ref.revision): ref
                for ref in (
                    *item.read_artifacts,
                    *(result.artifact for result in item.results),
                    *item.retained_artifacts,
                )
            }
            for ref in refs.values():
                await security.authorize(self.connection, self.scope_id, context, "read", ref)
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "next_cursor": application.cursor.encode(bound, following) if following else None,
            "source_mode": source_mode,
            "consumption_state": consumption_state,
            "direct_artifact": None if direct_artifact is None else direct_artifact.model_dump(mode="json"),
        }

    async def artifact_usage(self, artifact, *, period="7d", context=None):
        """Count preparations in the selected period that included this artifact."""
        if self._context_usage is None:
            from powercontext.builtin.records import BaseOperationNotSupportedError

            raise BaseOperationNotSupportedError("artifact_family", "atomic-memory", "recorded preparations")
        resolved = _resolve_period(StatisticsPeriod(period), _as_utc(self._as_of).date())
        start = datetime.combine(resolved.start_date, time.min, tzinfo=UTC)
        end = datetime.combine(resolved.end_date, time.min, tzinfo=UTC) + timedelta(days=1)
        return await self._context_usage.artifact_usage(
            self.connection, self.scope_id, artifact=artifact, start=start, end=end, context=context
        )

    async def processing_attempts(self, *, limit=10, cursor=None, status=None, since=None, until=None, context=None):
        """Read the Scope's automatic processing attempts in reverse start order."""
        if type(limit) is not int or not 1 <= limit <= 20:
            raise InvalidBaseAccessRequestError("limit", "must be from 1 to 20")
        if status is not None and status not in {"started", "succeeded", "failed", "cancelled", "conflict"}:
            raise InvalidBaseAccessRequestError("status", "is not a processing status")
        application = self._atomic.application
        security = application.security
        await security.authorize(self.connection, self.scope_id, context, "read")
        since = None if since is None else (since if since.tzinfo else since.replace(tzinfo=UTC)).astimezone(UTC)
        until = None if until is None else (until if until.tzinfo else until.replace(tzinfo=UTC)).astimezone(UTC)
        bound = {
            "endpoint": "memory_processing_scope",
            "version": 1,
            "scope_id": self.scope_id,
            "subject": security.subject(context),
            "status": status,
            "since": None if since is None else since.isoformat(),
            "until": None if until is None else until.isoformat(),
        }
        after = application.cursor.after_text(cursor, bound)
        items, following = await MemoryProcessingRepository().page_scope(
            self.connection,
            self.scope_id,
            limit=limit,
            after=after,
            status=status,
            since=since,
            until=until,
        )
        for item in items:
            await security.authorize_sources(self.connection, self.scope_id, context, item.sources)
            refs = {
                (ref.family, ref.artifact_id, ref.revision): ref
                for ref in (
                    *item.read_artifacts,
                    *(result.artifact for result in item.results),
                    *item.retained_artifacts,
                )
            }
            for ref in refs.values():
                await security.authorize(self.connection, self.scope_id, context, "read", ref)
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "next_cursor": application.cursor.encode(bound, following) if following else None,
            "source_mode": None,
            "consumption_state": None,
            "direct_artifact": None,
        }

    async def _waiting_inputs(self, *, since, until) -> tuple[int, bool]:
        """Count current outstanding Sources absent from the window's attempts.

        The journal cursor only advances with a domain commit, so everything after
        it is unprocessed. Attempts inside the selected window already contribute
        their status; older failed or unconfirmed attempts must remain visible
        as outstanding work rather than disappearing when their history ages out.
        """
        from powercontext.builtin.persistence.cursors import SourceCursorRepository
        from powercontext.builtin.source_eligibility import is_generation_eligible
        from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME

        progress = await SourceCursorRepository().load(self.connection, self.scope_id, SOURCE_WINDOW_TRIGGER_NAME)
        sequence = 0 if progress is None else progress.cursor.sequence
        rows = await self._records._sources.list(
            self.connection, self.scope_id, after=sequence, limit=PROCESSING_WAITING_SCAN + 1
        )
        refs = tuple(
            dict.fromkeys(
                (row.ref.source_type, row.ref.source_id)
                for row in rows[:PROCESSING_WAITING_SCAN]
                if is_generation_eligible(row.value)
            )
        )
        attempted = await MemoryProcessingRepository().attempted_sources(
            self.connection, self.scope_id, refs, since=since, until=until
        )
        waiting = sum(ref not in attempted for ref in refs)
        return waiting, len(rows) > PROCESSING_WAITING_SCAN

    async def processing_summary(self, *, since=None, until=None, all_time=False, context=None):
        """Count a bounded window, or all recorded history for the Home projection."""
        application = self._atomic.application
        await application.security.authorize(self.connection, self.scope_id, context, "read")
        until = datetime.now(UTC) if until is None else until
        until = until if until.tzinfo else until.replace(tzinfo=UTC)
        until = until.astimezone(UTC)
        if all_time:
            since = None
        else:
            since = until - timedelta(days=7) if since is None else since
            since = since if since.tzinfo else since.replace(tzinfo=UTC)
            since = since.astimezone(UTC)
            if since >= until or until - since > timedelta(days=92):
                raise InvalidBaseAccessRequestError("window", "must be a positive range of at most 92 days")
        summary = await MemoryProcessingRepository().summarize(self.connection, self.scope_id, since=since, until=until)
        waiting, waiting_lower_bound = await self._waiting_inputs(since=since, until=until)
        return {
            "since": since.isoformat() if since is not None else None,
            "until": until.isoformat(),
            **summary,
            "inputs_waiting": waiting,
            "inputs_waiting_is_lower_bound": waiting_lower_bound,
        }

    async def source_directory(self, *, limit=6, cursor=None, context=None):
        from sqlalchemy import select

        from powercontext.builtin.persistence.records import _source_record
        from powercontext.builtin.persistence.tables import SOURCES_TABLE

        if type(limit) is not int or not 1 <= limit <= 20:
            raise InvalidBaseAccessRequestError("limit", "must be from 1 to 20")
        application = self._atomic.application
        await application.security.authorize(self.connection, self.scope_id, context, "read")
        bound = {
            "endpoint": "dashboard_sources",
            "version": 1,
            "scope_id": self.scope_id,
            "subject": application.security.subject(context),
        }
        after = application.cursor.after_text(cursor, bound)
        statement = select(SOURCES_TABLE).where(
            SOURCES_TABLE.c.scope_id == self.scope_id, SOURCES_TABLE.c.source_type == "content"
        )
        if after:
            if not after.isascii() or not after.isdecimal() or len(after) > 20 or int(after) < 1:
                from powercontext.builtin.records import InvalidCursorError

                raise InvalidCursorError
            statement = statement.where(SOURCES_TABLE.c.journal_position < int(after))
        rows = (
            (
                await self.connection.execute(
                    statement.order_by(SOURCES_TABLE.c.journal_position.desc()).limit(limit + 1)
                )
            )
            .mappings()
            .all()
        )
        items = []
        for row in rows[:limit]:
            stored = self._records._sources.decode_source(row)
            item = _source_record(self.scope_id, stored).model_dump(mode="json")
            item["saved_at"] = stored.saved_at.isoformat() if stored.saved_at else None
            items.append(item)
        following = (
            application.cursor.encode(bound, str(rows[limit - 1]["journal_position"])) if len(rows) > limit else None
        )
        return {"items": items, "next_cursor": following}

    async def activity(self, *, sources=None, limit=6, context=None):  # noqa: C901 -- Authorize all batch evidence before projecting per-Source outcomes.
        from powercontext.artifacts import ArtifactRef
        from powercontext.builtin.persistence import dashboard_activity
        from powercontext.builtin.sources import ContentSource
        from powercontext.sources import SourceRef

        if not 1 <= limit <= 20 or (sources is not None and len(sources) > 20):
            raise InvalidBaseAccessRequestError("activity", "requires at most 20 selected records")
        security = self._atomic.application.security
        await security.authorize(self.connection, self.scope_id, context, "read")
        attempts = await dashboard_activity.processing(self.connection, self.scope_id, sources=sources, limit=limit)
        refs = (
            [SourceRef(source_type="content", source_id=value) for value in sources]
            if sources is not None
            else [source for _, record in attempts for source in record.sources[:1] if source.source_type == "content"]
        )
        stored = await self._records._sources.get_many(self.connection, self.scope_id, refs)
        source_values = {value.ref.source_id: value.value for value in stored}
        checked = set()
        for _, record in attempts:
            if record.attempt_id in checked:
                continue
            checked.add(record.attempt_id)
            await security.authorize_sources(self.connection, self.scope_id, context, record.sources)
            for ref in (
                *record.read_artifacts,
                *(item.artifact for item in record.results),
                *record.retained_artifacts,
            ):
                await security.authorize(self.connection, self.scope_id, context, "read", ref)
        summaries = []
        selected = dict(attempts) if sources is not None else {}
        identities = sources if sources is not None else [record.sources[0].source_id for _, record in attempts]
        for index, source_id in enumerate(identities):
            source = source_values.get(source_id)
            record = selected.get(source_id) if sources is not None else attempts[index][1]
            state = "unknown"
            result_refs = []
            if isinstance(source, ContentSource) and source.internal is not None:
                state = "internal"
                target = source.internal.target
                ref = ArtifactRef(family=target.family, artifact_id=target.artifact_id, revision=target.revision)
                await security.authorize(self.connection, self.scope_id, context, "read", ref)
                result_refs = [ref.model_dump(mode="json")]
            elif isinstance(source, ContentSource) and source.is_working_note:
                state = "working_note"
            elif record is not None:
                relevant = [
                    item
                    for item in record.results
                    if any(ref.source_id == source_id and ref.source_type == "content" for ref in item.source_refs)
                ]
                result_refs = [item.artifact.model_dump(mode="json") for item in relevant]
                if record.status == "started":
                    state = "unconfirmed"
                elif record.status != "succeeded":
                    state = "failed"
                elif relevant:
                    state = "created" if all(item.action == "create" for item in relevant) else "changed"
                elif record.retained_artifacts and (
                    len(record.sources) == 1
                    or any(
                        decision.action == "noop" and any(ref.source_id == source_id for ref in decision.source_refs)
                        for decision in record.decisions
                    )
                ):
                    state = "retained"
                    result_refs = (
                        [ref.model_dump(mode="json") for ref in record.retained_artifacts]
                        if len(record.sources) == 1
                        else []
                    )
                else:
                    state = "empty"
            summaries.append({
                "source_id": source_id,
                "title": _content_heading(
                    source.wire_content if isinstance(source.wire_content, dict) else {"text": source.content},
                    source_id,
                )
                if isinstance(source, ContentSource)
                else source_id,
                "state": state,
                "results": result_refs,
                "attempt_id": None if record is None else record.attempt_id,
                "at": None if record is None else (record.completed_at or record.started_at).isoformat(),
                "batch_size": 0 if record is None else len(record.sources),
            })
        return summaries

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

    async def context_usage(self, **params):
        if self._context_usage is None:
            from powercontext.builtin.records import BaseOperationNotSupportedError

            raise BaseOperationNotSupportedError("artifact_family", "atomic-memory", "recorded preparations")
        return await self._context_usage.read(self.connection, self.scope_id, **params)

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
        if "recall_count" in row:
            value["recall_count"] = row["recall_count"]
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
