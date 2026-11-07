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

"""Inspect a disposable database using the installed release's own driver.

Run only after the Server has exited. --add-revision creates a deliberately
incomplete control table for a negative fixture; it never represents a migration.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
from collections.abc import Collection
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

REVISION_DDL = "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) PRIMARY KEY)"


def row_digest(rows):
    # The fixture contains no external data; retain only stable hashes, not payloads.
    def encode(value):
        if isinstance(value, bytes):
            return {"bytes": value.hex()}
        return {"type": type(value).__name__, "value": str(value)}

    hashes = sorted(
        hashlib.sha256(json.dumps(tuple(row), default=encode, sort_keys=True).encode()).hexdigest() for row in rows
    )
    return hashlib.sha256("\n".join(hashes).encode()).hexdigest()


def vector_snapshot(cursor: Any, tables: Collection[str], *, sqlite: bool) -> dict[str, Any]:
    if not os.environ.get("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL"):
        return {}
    result = {}
    for table in ("pc_atomic_memory_current", "pc_topic_memory_vector_topics", "pc_topic_memory_vector_chunks"):
        if table not in tables:
            continue
        if table == "pc_atomic_memory_current":
            query = (
                "SELECT scope_id,artifact_id,embedding,embedding_input_hash,profile_fingerprint "
                "FROM pc_atomic_memory_current WHERE embedding IS NOT NULL"
            )
        elif sqlite:
            helper = {
                "pc_topic_memory_vector_topics": "pc_topic_memory_topic_vec",
                "pc_topic_memory_vector_chunks": "pc_topic_memory_chunk_vec",
            }[table]
            query = f"SELECT m.*,v.embedding FROM {table} m JOIN {helper} v ON m.vector_id=v.rowid"  # noqa: S608 -- Both names come from the fixed fixture table mapping.
        else:
            query = f"SELECT * FROM {table} WHERE embedding IS NOT NULL"  # noqa: S608 -- The three fixture table names are fixed above.
        cursor.execute(query)
        rows = cursor.fetchall()
        result[table] = {"rows": len(rows), "sha256": row_digest(rows)}
    return result


def sqlite_snapshot(database: Path, *, add_revision: bool) -> dict[str, Any]:
    with sqlite3.connect(database) as connection:
        if os.environ.get("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL"):
            connection.enable_load_extension(True)
            try:
                import_module("sqlite_vec").load(connection)
            finally:
                connection.enable_load_extension(False)
        if add_revision:
            connection.execute(REVISION_DDL)
        objects = {
            plural: dict(connection.execute("SELECT name, sql FROM sqlite_schema WHERE type=? ORDER BY name", (kind,)))
            for kind, plural in (
                ("table", "tables"),
                ("index", "indexes"),
                ("view", "views"),
                ("trigger", "triggers"),
            )
        }
        schema_rows = list(connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"))
        data = {
            name: row_digest(connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"'))  # noqa: S608 -- Reflected identifier is quoted.
            for name in objects["tables"]
        }
        vectors = vector_snapshot(connection.cursor(), objects["tables"], sqlite=True)
    return {
        "data_sha256": data,
        "vectors": vectors,
        "engine_version": sqlite3.sqlite_version,
        "driver_version": version("aiosqlite"),
        "schema_rows": schema_rows,
        **objects,
    }


async def inspect(
    backend: str, database: Path, *, add_revision: bool, canonical: bool = False, seekdb_database: str = "test"
) -> dict[str, Any]:
    if backend == "sqlite":
        return sqlite_snapshot(database, add_revision=add_revision)
    # Use the driver's public API, not a current-checkout Profile that could run
    # startup guards or mutate an old schema before the target CLI is tested.
    pymysql = import_module("pymysql")
    instance = None
    details = {}
    if backend == "seekdb":
        binding = import_module("pylibseekdb")
        instance = await binding.aopen(str(database))
        options = {**instance.connection_options(), "database": seekdb_database}
        details["embedded_binding_version"] = version("pylibseekdb")
    else:
        url = urlsplit(os.environ["PC_UPGRADE_OCEANBASE_URL"])
        options = {
            "host": url.hostname,
            "port": url.port or 2881,
            "user": unquote(url.username or ""),
            "password": unquote(url.password or ""),
            "database": url.path.removeprefix("/"),
            "charset": "utf8mb4",
            "connect_timeout": 15,
        }
    try:
        with pymysql.connect(**options, autocommit=True) as connection, connection.cursor() as cursor:
            if add_revision:
                cursor.execute(REVISION_DDL)
            cursor.execute("SELECT VERSION()")
            engine = str(cursor.fetchone()[0])
            if backend == "oceanbase":
                cursor.execute("SHOW VARIABLES LIKE 'ob_compatibility_mode'")
                details["tenant_mode"] = str(cursor.fetchone()[1]).lower()
                details["proxy_version"] = "direct"
            cursor.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
            names = sorted(row[0] for row in cursor.fetchall())
            tables = {}
            data = {}
            for name in names:
                cursor.execute("SHOW CREATE TABLE `" + name.replace("`", "``") + "`")
                tables[name] = cursor.fetchone()[1]
                cursor.execute("SELECT * FROM `" + name.replace("`", "``") + "`")  # noqa: S608 -- Escaped identifiers reflected from the isolated fixture.
                data[name] = row_digest(cursor.fetchall())
            vectors = vector_snapshot(cursor, names, sqlite=False)
            cursor.execute("SHOW FULL TABLES WHERE Table_type = 'VIEW'")
            views = {}
            for name in sorted(row[0] for row in cursor.fetchall()):
                cursor.execute("SHOW CREATE VIEW `" + name.replace("`", "``") + "`")
                views[name] = cursor.fetchone()[1]
            cursor.execute("SHOW TRIGGERS")
            trigger_rows = [
                dict(zip((column[0] for column in cursor.description), row, strict=True)) for row in cursor.fetchall()
            ]
            triggers = {
                row["Trigger"]: [row["Timing"], row["Event"], row["Table"], row["Statement"]] for row in trigger_rows
            }
        logical = None
        if canonical:
            from powercontext.builtin.persistence.migrations.mysql_schema import _parse_table

            logical = {name: _parse_table(sql, database=options["database"])[1] for name, sql in tables.items()}
        return {
            "logical_tables": logical,
            "data_sha256": data,
            "vectors": vectors,
            "triggers": triggers,
            "trigger_rows": [
                {key: row[key] for key in ("Trigger", "Table", "Timing", "Event", "Statement")} for row in trigger_rows
            ],
            "database": options["database"],
            "engine_version": engine,
            "driver_version": version("pymysql"),
            **details,
            "tables": tables,
            "views": views,
        }
    finally:
        if instance is not None:
            instance.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("sqlite", "seekdb", "oceanbase"), required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--add-revision", action="store_true")
    parser.add_argument("--canonical", action="store_true")
    parser.add_argument("--seekdb-database", default="test")
    args = parser.parse_args()
    result = asyncio.run(
        inspect(
            args.backend,
            args.database,
            add_revision=args.add_revision,
            canonical=args.canonical,
            seekdb_database=args.seekdb_database,
        )
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
