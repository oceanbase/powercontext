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

from sqlalchemy import Integer, Text, case, cast, func, select

from powercontext.builtin.persistence.artifacts import with_artifact_content
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
    SOURCES_TABLE as SOURCES,
)


def json_text(connection, column, path):
    """Extract one JSON string field in a dialect-safe way for bounded filters."""
    value = func.json_extract(cast(column, Text), path)
    return func.json_unquote(value) if connection.dialect.name == "mysql" else value


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
