# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Refresh changed derived words using existing index initialization and transactions."""

from sqlalchemy import Table, select, tuple_, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.search import TEXT_ANALYZER_VERSION, analyze_text
from powercontext.builtin.persistence.tables import (
    LEXICAL_PROJECTION_VERSIONS_TABLE,
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
)

# Bump when document segmentation or token encoding changes; query stopwords do not affect projections.
LEXICAL_ANALYZER_VERSION = TEXT_ANALYZER_VERSION


async def refresh_lexical_projections(connection: AsyncConnection, atomic_table: Table | None) -> None:
    """Refresh each analyzer version once without changing records, identities or vectors."""
    versions = dict((await connection.execute(select(LEXICAL_PROJECTION_VERSIONS_TABLE))).tuples().all())
    projections = [
        (TOPIC_MEMORY_ACTIVE_TOPICS_TABLE, ("title", "summary")),
        (TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE, ("chunk_text",)),
    ]
    if atomic_table is not None:
        projections.append((atomic_table, ("kind", "text")))
    topic_changed = False
    for table, fields in projections:
        if versions.get(table.name) == LEXICAL_ANALYZER_VERSION:
            continue
        changed = await _refresh_projection(connection, table, fields)
        topic_changed |= changed and table is not atomic_table
        await record_lexical_projection_version(connection, table.name)
    if topic_changed and connection.dialect.name == "sqlite":
        from powercontext.builtin.persistence.sqlite.topic_memory_index import SQLITE_TOPIC_MEMORY_FTS_MARKER_TABLE

        await connection.execute(SQLITE_TOPIC_MEMORY_FTS_MARKER_TABLE.delete())


async def lexical_projection_is_current(
    connection: AsyncConnection, projection: str, *, version: str = LEXICAL_ANALYZER_VERSION
) -> bool:
    table = LEXICAL_PROJECTION_VERSIONS_TABLE
    return await connection.scalar(select(table.c.analyzer_version).where(table.c.projection == projection)) == version


async def record_lexical_projection_version(
    connection: AsyncConnection, projection: str, *, version: str = LEXICAL_ANALYZER_VERSION
) -> None:
    """Record a completed refresh in the same transaction as its derived data."""
    values = {"projection": projection, "analyzer_version": version}
    if connection.dialect.name == "sqlite":
        statement = (
            sqlite_insert(LEXICAL_PROJECTION_VERSIONS_TABLE)
            .values(values)
            .on_conflict_do_update(index_elements=["projection"], set_={"analyzer_version": version})
        )
    else:
        statement = (
            mysql_insert(LEXICAL_PROJECTION_VERSIONS_TABLE)
            .values(values)
            .on_duplicate_key_update(analyzer_version=version)
        )
    await connection.execute(statement)


async def _refresh_projection(connection: AsyncConnection, table: Table, fields: tuple[str, ...]) -> bool:
    """Page one projection and write only changed tokens in the caller's transaction."""
    keys = tuple(table.primary_key.columns)
    columns = tuple(
        dict.fromkeys((*keys, table.c.revision, table.c.searchable_text, *(table.c[name] for name in fields)))
    )
    after = None
    changed = False
    while True:
        statement = select(*columns).order_by(*keys).limit(100)
        if after is not None:
            statement = statement.where(tuple_(*keys) > after)
        rows = (await connection.execute(statement)).mappings().all()
        if not rows:
            return changed
        for row in rows:
            searchable = analyze_text("\n".join(row[name] for name in fields))
            if row["searchable_text"] != searchable:
                await connection.execute(
                    update(table)
                    .where(*(key == row[key.name] for key in keys), table.c.revision == row["revision"])
                    .values(searchable_text=searchable)
                )
                changed = True
        after = tuple(rows[-1][key.name] for key in keys)
