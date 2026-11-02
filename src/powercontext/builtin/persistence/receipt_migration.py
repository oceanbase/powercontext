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

"""Idempotent receipt attestation upgrade using committed receipt records."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from sqlalchemy import delete, insert, select, tuple_

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sources import SourceRepository, _lock_journal_head
from powercontext.builtin.persistence.tables import RECEIPT_MIGRATION_REVIEW_TABLE, SOURCES_TABLE
from powercontext.builtin.sources.content import ContentSource
from powercontext.sources import SourceRef


async def migrate_handoff_receipts(  # noqa: C901 - Keep receipt classification and attestation together.
    database: AsyncDatabase,
    sources: SourceRepository,
    committed_identity_lookup: Callable[[str, str], Awaitable[object | None]],
    *,
    batch_size: int = 100,
) -> tuple[int, int]:
    """Return attested/unresolved counts; keep unresolved identities in the review table.

    Page identities and stream one payload at a time. Never infer provenance
    from content or from a pre-write identity reservation. Identity-store errors
    abort startup instead of being treated as missing evidence. Repeated runs can
    resolve previously missing committed receipt records.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")  # noqa: TRY003
    attested = unresolved = 0
    # Page one Scope at a time. A cursor that spans Scopes cannot steer the
    # (scope_id, source_type, source_id) primary key, so each page used to rescan every
    # row already visited and one Scope cost O(N²) index reads. Inside a Scope the
    # equality on the first two key columns turns the cursor into a bounded seek.
    after_scope = ""
    while (current_scope := await _next_content_scope(database, after_scope)) is not None:
        after_source_id = ""
        while True:
            rows = await _page_content_sources(database, current_scope, after_source_id, batch_size)
            if not rows:
                break
            cleanup = []
            candidates = []
            seen = set()
            async with database.transaction() as connection:
                statement = (
                    select(SOURCES_TABLE)
                    .where(
                        SOURCES_TABLE.c.scope_id == current_scope,
                        SOURCES_TABLE.c.source_type == "content",
                        SOURCES_TABLE.c.source_id.in_(rows),
                    )
                    .order_by(SOURCES_TABLE.c.source_id)
                )
                async with connection.stream(statement.execution_options(yield_per=1)) as result:
                    async for row in result.mappings():
                        seen.add(row["source_id"])
                        source = sources.decode_source(row).value
                        if isinstance(source, ContentSource):
                            identity = (row["scope_id"], row["source_id"])
                            if source.handoff_receipt:
                                cleanup.append(identity)
                            elif _is_receipt_candidate(source):
                                candidates.append(identity)
            missing = set(rows) - seen
            if missing:
                raise RepositoryNotFoundError(
                    "source",
                    (current_scope, SourceRef(source_type="content", source_id=min(missing))),
                )
            if cleanup:
                async with database.transaction() as connection:
                    await connection.execute(
                        delete(RECEIPT_MIGRATION_REVIEW_TABLE).where(
                            tuple_(
                                RECEIPT_MIGRATION_REVIEW_TABLE.c.scope_id, RECEIPT_MIGRATION_REVIEW_TABLE.c.source_id
                            ).in_(cleanup)
                        )
                    )
            after_source_id = rows[-1]
            for scope_id, source_id in candidates:
                ref = SourceRef(source_type="content", source_id=source_id)
                identity = await committed_identity_lookup(scope_id, source_id)
                async with database.transaction() as connection:
                    # Serialize concurrent startup migrations using the existing
                    # journal lock, without allocating a position or queueing work.
                    await _lock_journal_head(connection, scope_id)
                    current = await sources.get(connection, scope_id, ref)
                    target = current.value
                    if identity is not None:
                        target = target.model_copy(update={"handoff_receipt": True})
                    await sources.add_with_status(connection, scope_id, target)
                    await connection.execute(
                        delete(RECEIPT_MIGRATION_REVIEW_TABLE).where(
                            RECEIPT_MIGRATION_REVIEW_TABLE.c.scope_id == scope_id,
                            RECEIPT_MIGRATION_REVIEW_TABLE.c.source_id == source_id,
                        )
                    )
                    if identity is None and not getattr(target, "handoff_receipt", False):
                        await connection.execute(
                            insert(RECEIPT_MIGRATION_REVIEW_TABLE).values(
                                scope_id=scope_id,
                                source_id=source_id,
                                reason="missing_committed_receipt",
                            )
                        )
                        unresolved += 1
                    else:
                        attested += 1
        after_scope = current_scope
    return attested, unresolved


async def _next_content_scope(database: AsyncDatabase, after: str, /) -> str | None:
    """Return the next Scope holding a content Source, in primary-key order."""

    async with database.transaction() as connection:
        return await connection.scalar(
            select(SOURCES_TABLE.c.scope_id)
            .where(SOURCES_TABLE.c.scope_id > after, SOURCES_TABLE.c.source_type == "content")
            .order_by(SOURCES_TABLE.c.scope_id)
            .limit(1)
        )


async def _page_content_sources(
    database: AsyncDatabase, scope_id: str, after: str, batch_size: int, /
) -> tuple[str, ...]:
    """Return the next page of content Source identities inside one Scope."""

    async with database.transaction() as connection:
        return tuple(
            (
                await connection.execute(
                    select(SOURCES_TABLE.c.source_id)
                    .where(
                        SOURCES_TABLE.c.scope_id == scope_id,
                        SOURCES_TABLE.c.source_type == "content",
                        SOURCES_TABLE.c.source_id > after,
                    )
                    .order_by(SOURCES_TABLE.c.source_id)
                    .limit(batch_size)
                )
            ).scalars()
        )


def _is_receipt_candidate(source: ContentSource) -> bool:
    content = source.wire_content if source.wire_content_present or source.wire_content is not None else source.content
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (ValueError, RecursionError):
            return False
    return isinstance(content, dict) and content.get("schema") == "powercontext.handoff-receipt.v1"
