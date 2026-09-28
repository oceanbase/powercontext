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

"""Preserve logical tags while removing the legacy family whitelist."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from powercontext.builtin.persistence.tables import ARTIFACT_TAGS_TABLE


async def ensure_topic_memory_tag_schema(connection: AsyncConnection) -> None:
    """Upgrade the existing tag table during serialized deployment startup."""

    if connection.dialect.name == "sqlite":
        # Start a real write transaction before DDL, including legacy sqlite3 mode.
        await connection.exec_driver_sql("UPDATE pc_artifact_tags SET tag = tag WHERE 0")
        ddl = await connection.scalar(text("SELECT sql FROM sqlite_master WHERE name = 'pc_artifact_tags'"))
        if ddl is None or "ck_pc_artifact_tags_family" not in ddl:
            return
        create = str(CreateTable(ARTIFACT_TAGS_TABLE).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(
            create.replace("CREATE TABLE pc_artifact_tags", "CREATE TABLE pc_artifact_tags_topic_memory")
        )
        columns = ", ".join(column.name for column in ARTIFACT_TAGS_TABLE.columns)
        await connection.exec_driver_sql(
            f"INSERT INTO pc_artifact_tags_topic_memory ({columns}) SELECT {columns} FROM pc_artifact_tags"  # noqa: S608
        )
        await connection.exec_driver_sql("DROP TABLE pc_artifact_tags")
        await connection.exec_driver_sql("ALTER TABLE pc_artifact_tags_topic_memory RENAME TO pc_artifact_tags")
        for index in ARTIFACT_TAGS_TABLE.indexes:
            await connection.run_sync(index.create)
        return
    if connection.dialect.name == "mysql":
        clause = await connection.scalar(
            text(
                "SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS "
                "WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME = 'ck_pc_artifact_tags_family'"
            )
        )
        if clause is not None:
            await connection.exec_driver_sql("ALTER TABLE pc_artifact_tags DROP CHECK ck_pc_artifact_tags_family")
