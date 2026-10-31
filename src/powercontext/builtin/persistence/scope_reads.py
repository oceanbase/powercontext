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

"""Bounded Scope projections over authoritative Artifact heads."""

from sqlalchemy import func, literal, select, union_all

from powercontext.builtin.persistence.artifacts import with_artifact_content
from powercontext.builtin.persistence.statistics import daily_usage_statement
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACTS_TABLE,
    SCOPES_TABLE,
)

_HEAD_FIELDS = (
    "scope_id",
    "family",
    "artifact_id",
    "revision",
    "lifecycle_state",
    "replacement_artifact_id",
    "merged_into_id",
    "governance_generation",
    "content",
)
_HEADER_FIELDS = ("title", "summary", "parent_scope_id")


def _with_header(statement, scope, kind):
    """Keep an empty page distinguishable from a missing Scope in the same read."""
    data = statement.subquery("page_rows")
    header = SCOPES_TABLE
    return union_all(
        select(*data.c, *(literal(None, type_=header.c[name].type).label(name) for name in _HEADER_FIELDS)),
        select(
            *(
                literal("scope", type_=column.type).label(column.name)
                if column.name == kind
                else header.c.scope_id
                if column.name == "scope_id"
                else literal(None, type_=column.type).label(column.name)
                for column in data.c
            ),
            *(header.c[name] for name in _HEADER_FIELDS),
        ).where(header.c.scope_id == scope),
    )


def _content_rows(statement, kind, *, current_revision=None, source_count=None):
    rows = statement.subquery(f"{kind}_rows")
    return select(
        literal(kind).label("read_kind"),
        *(rows.c[name] for name in _HEAD_FIELDS),
        (rows.c.revision if current_revision is None else rows.c[current_revision]).label("current_revision"),
        (literal(None) if source_count is None else rows.c[source_count]).label("source_count"),
    )


def family_previews(scope, families):
    head, sources = ARTIFACT_HEADS_TABLE, ARTIFACT_LINEAGE_SOURCES_TABLE
    statements = []
    for family in families:
        page = (
            select(head)
            .where(
                head.c.scope_id == scope,
                head.c.family == family,
                head.c.merged_into_id.is_(None),
                head.c.lifecycle_state == "active" if family == "skill" else head.c.lifecycle_state != "retired",
            )
            .order_by(head.c.artifact_id)
            .limit(1)
            .subquery(f"{family}_head")
        )
        count = (
            select(func.count())
            .where(*(sources.c[name] == page.c[name] for name in ("scope_id", "family", "artifact_id", "revision")))
            .scalar_subquery()
            if family == "experience"
            else literal(None)
        )
        statement = with_artifact_content(select(page), page).add_columns(count.label("source_count"))
        statements.append(_content_rows(statement, family, source_count="source_count"))
    return statements


async def content_rows(connection, scope, *, browse=None, families=(), selected=None, include_scope=True):
    statements = [] if browse is None else [_content_rows(browse, "notes")]
    statements.extend(family_previews(scope, families))
    if selected is not None:
        revision = selected["revision"]
        head = ARTIFACT_HEADS_TABLE
        exact = select(
            *head.c,
            head.c.revision.label("current_revision"),
        ).where(
            head.c.scope_id == scope, head.c.family == "atomic-memory", head.c.artifact_id == selected["artifact_id"]
        )
        # Preserve the head for governance while selecting the requested immutable body.
        exact_head = exact.subquery("selected_note_head")
        requested = select(
            *(column for column in exact_head.c if column.name != "revision"),
            literal(revision).label("revision"),
        ).subquery("selected_note_revision")
        statements.append(
            _content_rows(
                with_artifact_content(select(requested), requested),
                "selected",
                current_revision="current_revision",
            )
        )
    if not statements:
        return [], await scope_header(connection, scope) if include_scope else None
    statement = union_all(*statements) if len(statements) > 1 else statements[0]
    if include_scope:
        statement = _with_header(statement, scope, "read_kind")
    rows = (await connection.execute(statement)).mappings().all()
    descriptor = None
    if include_scope:
        descriptor = next((row for row in rows if row["read_kind"] == "scope"), None)
    return rows, descriptor


async def source_artifacts(connection, scope, source_id, *, limit):
    """Read bounded exact revision identities that directly cite one Content Source."""
    sources = ARTIFACT_LINEAGE_SOURCES_TABLE
    statement = (
        select(sources.c.family, sources.c.artifact_id, sources.c.revision)
        .where(
            sources.c.scope_id == scope,
            sources.c.source_type == "content",
            sources.c.source_id == source_id,
        )
        .distinct()
        .order_by(sources.c.family, sources.c.artifact_id, sources.c.revision.desc())
        .limit(limit)
    )
    return [dict(row) for row in (await connection.execute(statement)).mappings()]


async def usage_rows(connection, scope, period, estimator, *, include_scope=False):
    statement = daily_usage_statement(
        (scope,),
        period.start_date,
        period.end_date,
        estimator=None if estimator is None else (estimator.estimator_id, estimator.version),
    )
    rows = (
        (await connection.execute(_with_header(statement, scope, "usage_kind") if include_scope else statement))
        .mappings()
        .all()
    )
    descriptor = next((row for row in rows if row["usage_kind"] == "scope"), None) if include_scope else None
    return [row for row in rows if row["usage_kind"] != "scope"], descriptor


async def scope_header(connection, scope):
    return (
        (await connection.execute(select(SCOPES_TABLE).where(SCOPES_TABLE.c.scope_id == scope)))
        .mappings()
        .one_or_none()
    )


async def revision_contents(connection, scope, family, artifact_id, revisions):
    if not revisions:
        return []
    return (
        (
            await connection.execute(
                select(ARTIFACTS_TABLE).where(
                    ARTIFACTS_TABLE.c.scope_id == scope,
                    ARTIFACTS_TABLE.c.family == family,
                    ARTIFACTS_TABLE.c.artifact_id == artifact_id,
                    ARTIFACTS_TABLE.c.revision.in_(revisions),
                )
            )
        )
        .mappings()
        .all()
    )
