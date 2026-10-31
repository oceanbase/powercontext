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

"""Authorize preparation history and feedback at the owning runtime boundary."""

import json
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

from sqlalchemy import case

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.context_usage import ContextUsageFeedback, ContextUsageItem, ContextUsageRecord
from powercontext.builtin.persistence.context_usage import ContextUsageRepository
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.records import InvalidBaseAccessRequestError, InvalidCursorError
from powercontext.builtin.runtime.atomic_memory import deferred_decision_audit
from powercontext.builtin.runtime.statistics import _resolve_period
from powercontext.builtin.statistics import StatisticsPeriod


class ContextUsageService:
    def __init__(self, database, security, cursor, clock):
        self.recording_enabled = False
        self.database = database
        self.security = security
        self.cursor = cursor
        self.clock = clock
        self.repository = ContextUsageRepository()

    async def record_preparation(self, scope_id, build):
        items = tuple(
            ContextUsageItem(
                scope_id=origin.scope_id if isinstance(origin, ArtifactAddress) else scope_id,
                artifact=origin.artifact if isinstance(origin, ArtifactAddress) else origin,
            )
            for origin in build.origins
        )
        record = ContextUsageRecord(
            usage_id=uuid4().hex,
            scope_id=scope_id,
            created_at=self.clock().astimezone(UTC),
            status=build.context.status,
            content_bytes=build.context.content_bytes,
            reason=build.degradation,
            truncated=build.omissions.truncated_items > 0,
            items=items,
        )
        async with self.database.transaction() as connection:
            await self.repository.record(connection, record)

    async def authorize_record(self, connection, scope_id, value, context):
        await self.security.authorize(connection, scope_id, context, "read")
        for item in value["items"]:
            from powercontext.artifacts import ArtifactRef

            await self.security.authorize(
                connection, item["scope_id"], context, "read", ArtifactRef.model_validate(item["artifact"])
            )

    async def artifact_usage(self, connection, scope_id, *, artifact, start, end, context=None):
        """Count only readable preparations of the exact revision, including references."""
        from powercontext.server.authz import AccessDeniedError

        await self.security.authorize(connection, scope_id, context, "read", artifact)
        records = await self.repository.artifact_preparations(
            connection, scope_id, artifact=artifact, start=start, end=end
        )
        preparations = 0
        last_used = None
        for record in records:
            try:
                await self.authorize_record(connection, record.scope_id, record.model_dump(mode="json"), context)
            except AccessDeniedError:
                continue
            preparations += 1
            if last_used is None or record.created_at > last_used:
                last_used = record.created_at
        return {
            "preparations": preparations,
            "last_used_at": None if last_used is None else last_used.isoformat(),
        }

    async def memory_list_query(
        self,
        connection,
        scope_id,
        atomic,
        *,
        states=("active",),
        kind=None,
        artifact_ids=None,
        tag_filter=None,
        limit=50,
        cursor=None,
        period="7d",
        context=None,
    ):
        """Rank authorized preparation counts before pagination; read only page bodies."""
        from powercontext.server.authz import AccessDeniedError

        await atomic.application.security.filters(connection, scope_id, context, tags=tag_filter)
        statement, bound, _ = atomic.list_query(
            connection,
            states=states,
            kind=kind,
            artifact_ids=artifact_ids,
            tag_filter=tag_filter,
            limit=limit,
            context=context,
            paginate=False,
        )
        now = self.clock().astimezone(UTC)
        window = _resolve_period(StatisticsPeriod(period), now.date())
        bound = {
            **bound,
            "endpoint": "atomic_memory_recall_list",
            "period": period,
            "start": str(window.start_date),
            "end": str(window.end_date),
        }
        after = self.cursor.after_text(cursor, bound)
        position = None
        as_of = now
        if after:
            try:
                count, identity, timestamp = json.loads(after)
                as_of = datetime.fromisoformat(timestamp)
                if (
                    type(count) is not int
                    or count < 0
                    or not isinstance(identity, str)
                    or not identity
                    or as_of.tzinfo is None
                    or as_of > now
                ):
                    raise ValueError  # noqa: TRY301 - Validate the signed pagination position together.
                position = (-count, identity)
            except (ValueError, TypeError) as error:
                raise InvalidCursorError from error
        start = datetime.combine(window.start_date, time.min, UTC)
        end = min(
            datetime.combine(window.end_date + timedelta(days=1), time.min, UTC), as_of + timedelta(microseconds=1)
        )
        records = await self.repository.artifact_preparations(connection, scope_id, artifact=None, start=start, end=end)
        counts = Counter()
        for record in records:
            try:
                await self.authorize_record(connection, record.scope_id, record.model_dump(mode="json"), context)
            except AccessDeniedError:
                continue
            counts.update({
                item.artifact.artifact_id
                for item in record.items
                if item.scope_id == scope_id and item.artifact.family == "atomic-memory"
            })
        identity_column = statement.selected_columns.artifact_id
        identities = (
            (await connection.execute(statement.with_only_columns(identity_column, maintain_column_froms=True)))
            .scalars()
            .all()
        )
        ranked = sorted(identities, key=lambda identity: (-counts[identity], identity))
        if position is not None:
            ranked = [identity for identity in ranked if (-counts[identity], identity) > position]
        page = ranked[:limit]
        next_cursor = (
            self.cursor.encode(bound, json.dumps([counts[page[-1]], page[-1], as_of.isoformat()]))
            if len(ranked) > limit
            else None
        )
        selected = statement.where(identity_column.in_(page)).order_by(None)
        if page:
            selected = selected.order_by(
                case({identity: index for index, identity in enumerate(page)}, value=identity_column)
            )
        return selected, {identity: counts[identity] for identity in page}, next_cursor

    async def read(
        self, connection, scope_id, *, period="7d", limit=6, cursor=None, record_kind=None, record_id=None, context=None
    ):
        await self.security.authorize(connection, scope_id, context, "read")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise InvalidBaseAccessRequestError("limit", "must be from 1 to 20")
        if record_id is not None:
            if record_kind not in {"preparation", "bootstrap"} or not 1 <= len(record_id) <= 64:
                raise InvalidBaseAccessRequestError("usage", "invalid record identity")
            values = await self.repository.get_many(
                connection, scope_id, [{"record_kind": record_kind, "record_id": record_id}]
            )
            if not values:
                raise RepositoryNotFoundError("context-usage", (scope_id, record_kind, record_id))
            await self.authorize_record(connection, scope_id, values[0], context)
            can_feedback = context is None or (context.access is None and context.trusted_local)
            if context is not None and context.access is not None:
                from powercontext.server.authz import AccessAction, ResourceRef

                decision = await context.access.for_read(connection).check(
                    context.principal, AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id), context=context.audit
                )
                can_feedback = decision.allowed
            return {"items": values, "next_cursor": None, "can_feedback": can_feedback}
        if record_kind is not None and record_kind not in {"preparation", "bootstrap"}:
            raise InvalidBaseAccessRequestError("record_kind", "invalid record kind")
        resolved = _resolve_period(StatisticsPeriod(period), self.clock().astimezone(UTC).date())
        bound = {
            "endpoint": "context_usage",
            "version": 1,
            "scope_id": scope_id,
            "subject": self.security.subject(context),
            "period": period,
            "start": str(resolved.start_date),
            "end": str(resolved.end_date),
        }
        if record_kind is not None:
            bound["record_kind"] = record_kind
        after = self.cursor.after_text(cursor, bound)
        values, following = await self.repository.page(
            connection,
            scope_id,
            start=datetime.combine(resolved.start_date, time.min, UTC),
            end=datetime.combine(resolved.end_date + timedelta(days=1), time.min, UTC),
            limit=limit,
            after=after,
            record_kind=record_kind,
        )
        for value in values:
            await self.authorize_record(connection, scope_id, value, context)
        return {"items": values, "next_cursor": self.cursor.encode(bound, following) if following else None}

    async def feedback(self, scope_id, request, *, context=None):
        async with deferred_decision_audit(context), self.database.transaction() as connection:
            await self.security.authorize(connection, scope_id, context, "create")
            await self.read(
                connection, scope_id, record_kind=request.record_kind, record_id=request.record_id, context=context
            )
            feedback = ContextUsageFeedback(
                **request.model_dump(),
                scope_id=scope_id,
                created_at=self.clock().astimezone(UTC),
                reported_by=self.security.subject(context),
            )
            return await self.repository.feedback(connection, feedback)
