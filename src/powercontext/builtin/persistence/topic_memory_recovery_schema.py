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

"""Frozen Topic recovery shapes and a read-only gate before business initialization."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, create_engine, inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from powercontext.builtin.persistence.migrations.models import MigrationError, digest
from powercontext.builtin.persistence.migrations.mysql_schema import _matches, _parse_table

RESOURCE = Path(__file__).parent / "migration_resources/topic_memory_recovery/schema.json"
BUDGET = "pc_topic_memory_work_budgets"
ATTEMPTS = "pc_topic_memory_work_attempts"
BLOCKS = "pc_topic_memory_work_blocks"
LEASES = "pc_artifact_processing_leases"
OBJECTS = (BUDGET, ATTEMPTS, BLOCKS)
_BOOTSTRAP_COMPANIONS = frozenset({
    "pc_access_relationship_heads",
    "pc_access_relationships",
    "pc_access_owners",
    "pc_access_idempotency",
    "pc_access_audit",
    "pc_code_generations",
    "pc_code_nodes",
    "pc_code_edges",
})


@lru_cache
def recovery_resource() -> dict[str, Any]:
    return json.loads(RESOURCE.read_text(encoding="utf-8"))


def recovery_statements(shape: str, dialect: str) -> list[str]:
    return recovery_resource()["schemas"][shape][dialect]


@lru_cache
def recovery_initialization_prefixes(dialect: str) -> tuple[dict[str, Any], ...]:
    prefixes: list[dict[str, Any]] = [{}]
    for shape in recovery_resource()["initialization"]:
        prefixes.append({**prefixes[-1], **recovery_shape(shape, dialect)})
    return tuple(prefixes)


def _expression(value: str) -> str:
    return re.sub(r'[\s`"\[\]]', "", value).lower()


def _sqlite_table(connection: Connection, name: str) -> dict[str, Any]:
    inspector = inspect(connection)
    return {
        "sql": _expression(
            connection.exec_driver_sql(
                "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (name,)
            ).scalar_one()
        ),
        "index_sql": sorted(
            _expression(row[0])
            for row in connection.exec_driver_sql(
                "SELECT sql FROM sqlite_schema WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (name,)
            )
        ),
        "columns": [
            (item["name"], str(item["type"]).lower(), item["nullable"], item["default"])
            for item in inspector.get_columns(name)
        ],
        "primary": inspector.get_pk_constraint(name)["constrained_columns"],
        "unique": sorted(tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)),
        "checks": sorted(
            (item["name"], _expression(item["sqltext"])) for item in inspector.get_check_constraints(name)
        ),
        "indexes": sorted(
            (item["name"], tuple(item["column_names"]), item["unique"], str(item.get("dialect_options", {})))
            for item in inspector.get_indexes(name)
        ),
        "foreign_keys": inspector.get_foreign_keys(name),
    }


@lru_cache
def recovery_shape(shape: str, dialect: str) -> dict[str, Any]:
    statements = recovery_statements(shape, dialect)
    if dialect == "mysql":
        name, table = _parse_table(statements[0])
        if len(statements) > 1:
            # The one historical secondary index is part of its table definition.
            ddl = (
                statements[0].rstrip().removesuffix(")")
                + ", INDEX ix_pc_topic_attempt_frontier (scope_id, binding_name, source_after, blocked_at))"
            )
            name, table = _parse_table(ddl)
        return {name: table}
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)
            name = inspect(connection).get_table_names()[0]
            return {name: _sqlite_table(connection, name)}
    finally:
        engine.dispose()


def recovery_inventory(connection: Connection, *, include_leases: bool = False) -> dict[str, Any]:
    names = set(inspect(connection).get_table_names())
    if any(name.startswith("pc_topic_memory_work_") and name not in OBJECTS for name in names):
        raise MigrationError("incompatible_schema", "Unregistered Topic recovery objects exist.")
    selected = names.intersection(OBJECTS)
    if include_leases and LEASES in names:
        selected.add(LEASES)
    if any(
        name.startswith("pc_topic_memory_work_") or (include_leases and name == LEASES)
        for name in inspect(connection).get_view_names()
    ):
        raise MigrationError("incompatible_schema", "A view occupies the Topic recovery namespace.")
    if connection.dialect.name == "sqlite":
        if any(
            connection.exec_driver_sql(
                "SELECT 1 FROM sqlite_schema WHERE type IN ('trigger','view') AND tbl_name = ?", (name,)
            ).first()
            for name in selected
        ):
            raise MigrationError("incompatible_schema", "Unregistered Topic recovery triggers or views exist.")
        return {name: _sqlite_table(connection, name) for name in sorted(selected)}
    if connection.dialect.name != "mysql":
        raise MigrationError("unsupported_backend", "Topic recovery requires SQLite or a MySQL-compatible backend.")
    if any(row["Table"] in selected for row in connection.exec_driver_sql("SHOW TRIGGERS").mappings()):
        raise MigrationError("incompatible_schema", "Unregistered Topic recovery triggers exist.")
    result = {}
    for name in sorted(selected):
        ddl = connection.exec_driver_sql(f"SHOW CREATE TABLE `{name}`").one()[1]
        _, result[name] = _parse_table(ddl)
    return result


def recovery_layout(connection: Connection) -> str:
    actual = recovery_inventory(connection)
    dialect = connection.dialect.name
    if actual and recovery_matches(actual, recovery_initialization_prefixes(dialect)[1], dialect):
        names = set(inspect(connection).get_table_names())
        if (
            names <= (_BOOTSTRAP_COMPANIONS | {BLOCKS})
            and connection.exec_driver_sql("SELECT 1 FROM pc_topic_memory_work_blocks LIMIT 1").first() is None
        ):
            return "empty"
        raise MigrationError("unknown_baseline", "An incomplete recovery database contains existing evidence.")
    if not actual:
        if set(inspect(connection).get_table_names()) - _BOOTSTRAP_COMPANIONS:
            raise MigrationError("unknown_baseline", "An existing partial database has no registered Topic budget.")
        return "empty"
    for name, shapes in (
        ("budget-v0", ("budget_v0",)),
        ("attempts-v0", ("budget_v0", "attempts_v0")),
        ("ready", ("budget_v1", "blocks_v1")),
    ):
        expected = {}
        for shape in shapes:
            expected.update(recovery_shape(shape, dialect))
        if recovery_matches(actual, expected, dialect):
            return name
    raise MigrationError("incompatible_schema", "Topic recovery storage is not a registered complete layout.")


def recovery_matches(actual: dict[str, Any], expected: dict[str, Any], dialect: str) -> bool:
    if dialect == "mysql":
        return _matches(
            {"tables": actual, "views": [], "triggers": []},
            {"tables": expected, "views": [], "triggers": []},
        )
    return digest(actual) == digest(expected)


async def prepare_topic_memory_recovery_schema(engine: AsyncEngine, *, initialize: bool = True) -> None:
    """Initialize only an empty database; reject upgrades before any business writes."""
    async with engine.begin() as connection:
        if not initialize and not await connection.run_sync(recovery_inventory):
            return
        layout = await connection.run_sync(recovery_layout)
        if layout == "empty" and initialize:
            if connection.dialect.name == "sqlite":
                await connection.exec_driver_sql("BEGIN IMMEDIATE")
            actual = await connection.run_sync(recovery_inventory)
            for shape in recovery_resource()["initialization"]:
                name = {"blocks_v1": BLOCKS, "budget_v1": BUDGET}[shape]
                if name in actual:
                    continue
                for statement in recovery_statements(shape, connection.dialect.name):
                    await connection.exec_driver_sql(statement)
            if await connection.run_sync(recovery_layout) != "ready":
                raise MigrationError(
                    "recovery_required", "Empty Topic recovery initialization did not reach its target."
                )
        elif layout not in {"empty", "ready"}:
            raise MigrationError(
                "migration_required",
                "Stop writers and run powercontext server db-migrate apply --bundle topic-memory-recovery.",
            )
