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

"""Bounded projections of authoritative processing, revisions and Work evidence."""

from sqlalchemy import Integer, and_, case, cast, func, select

from powercontext.builtin.persistence.artifacts import with_artifact_content
from powercontext.builtin.persistence.memory_processing import _decode, json_text
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE as HEADS,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACTS_TABLE as REVISIONS,
)
from powercontext.builtin.persistence.tables import (
    CANDIDATE_VERSIONS_TABLE as CANDIDATES,
)
from powercontext.builtin.persistence.tables import (
    MEMORY_PROCESSING_RECORDS_TABLE as ATTEMPTS,
)
from powercontext.builtin.persistence.tables import (
    MEMORY_PROCESSING_SOURCES_TABLE as INPUTS,
)
from powercontext.builtin.persistence.tables import (
    SOURCES_TABLE as SOURCES,
)


async def processing(connection, scope_id, *, sources=None, limit=6):
    if sources is None:
        rows = (
            (
                await connection.execute(
                    select(ATTEMPTS)
                    .where(ATTEMPTS.c.scope_id == scope_id)
                    .order_by(ATTEMPTS.c.started_at.desc(), ATTEMPTS.c.attempt_id.desc())
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return [(None, _decode(row)) for row in rows]
    if not sources:
        return []
    ranked = (
        select(
            INPUTS.c.source_id,
            ATTEMPTS.c.attempt_id,
            func
            .row_number()
            .over(
                partition_by=INPUTS.c.source_id, order_by=(ATTEMPTS.c.started_at.desc(), ATTEMPTS.c.attempt_id.desc())
            )
            .label("rank"),
        )
        .join(ATTEMPTS, and_(INPUTS.c.scope_id == ATTEMPTS.c.scope_id, INPUTS.c.attempt_id == ATTEMPTS.c.attempt_id))
        .where(
            INPUTS.c.scope_id == scope_id,
            INPUTS.c.source_type == "content",
            INPUTS.c.source_id.in_(sources),
        )
        .subquery()
    )
    rows = (
        (
            await connection.execute(
                select(ATTEMPTS, ranked.c.source_id)
                .join(ranked, ATTEMPTS.c.attempt_id == ranked.c.attempt_id)
                .where(ATTEMPTS.c.scope_id == scope_id, ranked.c.rank == 1)
            )
        )
        .mappings()
        .all()
    )
    return [(row["source_id"], _decode(row)) for row in rows]


async def recent_revisions(connection, scope_id, *, limit=6):
    statement = (
        with_artifact_content(select(HEADS), HEADS)
        .add_columns(
            REVISIONS.c.created_at.label("saved_at"),
        )
        .where(HEADS.c.scope_id == scope_id, REVISIONS.c.created_at.is_not(None))
    )
    # with_artifact_content already joins the exact immutable head Revision.
    return (
        (
            await connection.execute(
                statement.order_by(REVISIONS.c.created_at.desc(), HEADS.c.family, HEADS.c.artifact_id).limit(limit)
            )
        )
        .mappings()
        .all()
    )


async def candidate_titles(connection, scope_id, identities):
    if not identities:
        return {}
    path = case(
        (CANDIDATES.c.family == "skill", "$.description"),
        (CANDIDATES.c.family == "experience", "$.lesson"),
        (CANDIDATES.c.family == "atomic-memory", "$.text"),
        (CANDIDATES.c.family == "topic-memory", "$.title"),
        (CANDIDATES.c.family == "handoff", "$.objective"),
        else_="$.reason",
    )
    from sqlalchemy import tuple_

    rows = (
        (
            await connection.execute(
                select(
                    CANDIDATES.c.candidate_id,
                    CANDIDATES.c.version,
                    func.substr(json_text(connection, CANDIDATES.c.proposal, path), 1, 160).label("title"),
                ).where(
                    CANDIDATES.c.scope_id == scope_id,
                    tuple_(CANDIDATES.c.candidate_id, CANDIDATES.c.version).in_(identities),
                )
            )
        )
        .mappings()
        .all()
    )
    return {row["candidate_id"]: row["title"] for row in rows if row["title"]}


async def work_records(connection, scope_id, handoff, *, limit=20):
    content = json_text(connection, SOURCES.c.payload, "$.value.content")
    # Arbitrary captured text is not necessarily JSON, even when its metadata claims a Work kind.
    safe = case((func.json_valid(content) == 1, content), else_="{}")
    kind = json_text(connection, SOURCES.c.payload, "$.value.metadata.kind")
    receipt = (
        select(SOURCES)
        .where(
            SOURCES.c.scope_id == scope_id,
            SOURCES.c.source_type == "content",
            kind == "handoff-receipt",
            json_text(connection, safe, "$.selected_revision.family") == handoff.family,
            json_text(connection, safe, "$.selected_revision.artifact_id") == handoff.artifact_id,
            cast(json_text(connection, safe, "$.selected_revision.revision"), Integer) == handoff.revision,
        )
        .order_by(SOURCES.c.journal_position.desc())
        .limit(limit)
    )
    rows = list((await connection.execute(receipt)).mappings())
    if rows:
        outcomes = (
            select(SOURCES)
            .where(
                SOURCES.c.scope_id == scope_id,
                SOURCES.c.source_type == "content",
                kind == "task-outcome",
                json_text(connection, safe, "$.handoff_receipt_ref.source_type") == "content",
                json_text(connection, safe, "$.handoff_receipt_ref.source_id").in_([row["source_id"] for row in rows]),
            )
            .order_by(SOURCES.c.journal_position.desc())
            .limit(limit)
        )
        rows.extend((await connection.execute(outcomes)).mappings())
    return sorted(rows, key=lambda row: row["journal_position"])
