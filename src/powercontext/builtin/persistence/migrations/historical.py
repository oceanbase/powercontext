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

"""State-based admission of released, pre-revision PowerContext databases.

These steps run inside the existing maintenance lock, backup and transaction.
They establish a real source postcondition before a revision may be stamped;
software version strings never authorize adopting a database.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, MetaData, Table, inspect, text
from sqlalchemy.schema import CreateColumn
from typing_extensions import override

from .bundle import MigrationBundle
from .models import MigrationError, digest
from .pinned import finish, pinned
from .sqlite_compatibility import (
    differences,
    extension_column_ddl,
    layout,
    preserve_rebuild_views,
    quote,
    reference,
    sql_tokens,
    table_contract,
)


def checksum() -> str:
    root = Path(__file__).parent
    return digest({
        name: (root / name).read_text()
        for name in (
            "historical.py",
            "sqlite_compatibility.py",
            "atomic_memory_v1.py",
            "atomic_memory_archive.py",
            "atomic_memory_references.py",
            "pinned.py",
            "mysql_compatibility.py",
            "mysql_schema.py",
            "../processing_migration.py",
            "../processing_intents.py",
            "../processing.py",
            "../scope_search_schema.py",
            "../tables.py",
        )
    })


def select_source(connection: Connection, bundles: tuple[MigrationBundle, ...]):
    matches = []
    for bundle in bundles:
        revision = bundle.source_revision or bundle.revisions[0]
        if not differences(connection, layout(bundle, revision)):
            matches.append((bundle, revision, steps(connection, bundle) if recognizes(connection) else ()))
    if len(matches) > 1:
        raise MigrationError("incompatible_schema", "More than one source storage contract matches.")
    if matches:
        return matches[0]
    if not recognizes(connection):
        return None
    names = set(inspect(connection).get_table_names())
    current_candidates = "pc_candidate_heads" in names
    for bundle in bundles:
        revision = bundle.source_revision or bundle.revisions[0]
        target_names = {row[1] for row in layout(bundle, revision)}
        if ("pc_candidate_heads" in target_names) == current_candidates:
            return bundle, revision, steps(connection, bundle)
    return None


def recognizes(connection: Connection) -> bool:
    required = {
        "pc_sources": {"scope_id", "source_type", "source_id", "payload", "journal_position"},
        "pc_artifacts": {"scope_id", "family", "artifact_id", "revision", "content"},
        "pc_artifact_heads": {"scope_id", "family", "artifact_id", "revision"},
        "pc_artifact_lineage_sources": {"scope_id", "family", "artifact_id", "revision"},
        "pc_artifact_lineage_artifacts": {"scope_id", "family", "artifact_id", "revision"},
    }
    inspector = inspect(connection)
    names = set(inspector.get_table_names())
    if not required.keys() <= names or "pc_schema_revision" in names:
        return False
    return all(
        columns <= {column["name"] for column in inspector.get_columns(table)} for table, columns in required.items()
    )


def steps(connection: Connection, bundle: MigrationBundle) -> tuple[str, ...]:
    if not recognizes(connection):
        raise MigrationError("unknown_baseline", "No supported PowerContext storage contract was found.")
    if "pc_atomic_memory_states" in inspect(connection).get_table_names():
        raise MigrationError("incompatible_schema", "Unreleased split Atomic authority requires explicit recovery.")
    pending = []
    source = bundle.source_revision or bundle.revisions[0]
    if differences(connection, layout(bundle, source)):
        pending.append("historical_structure")
    names = set(inspect(connection).get_table_names())
    if "pc_scopes" not in names:
        pending.append("historical_scopes")
    if (
        "pc_artifact_processing_schema" not in names
        or not connection.exec_driver_sql(
            "SELECT 1 FROM pc_artifact_processing_schema WHERE phase='complete' LIMIT 1"
        ).first()
    ):
        pending.append("historical_processing")
    if connection.exec_driver_sql("SELECT 1 FROM pc_artifacts WHERE family='memory' LIMIT 1").first():
        pending.append("historical_atomic_memory")
    return tuple(pending)


class _PinnedDatabase:
    def __init__(self, connection: Connection):
        self.connection = connection

    @asynccontextmanager
    async def transaction(self):
        # The outer runner owns the single SQLite transaction and recovery point.
        yield pinned(self.connection)


def _create_missing(connection: Connection, rows: list[list[Any]]) -> None:
    for kind in ("table", "index", "view", "trigger"):
        for actual_kind, name, _, sql in rows:
            if (
                kind == actual_kind
                and sql
                and not connection.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE name=?", (name,)).first()
            ):
                if kind == "index":
                    # Some indexes reference columns that this step has not added yet.
                    continue
                connection.exec_driver_sql(sql)


def _expand(connection: Connection, expected: Connection, rows: list[list[Any]]) -> None:
    target_inspector = inspect(expected)
    for kind, name, _, sql in rows:
        if kind != "table" or not sql or sql.upper().startswith("CREATE VIRTUAL TABLE"):
            continue
        existing = {column["name"] for column in inspect(connection).get_columns(name)}
        table = Table(name, MetaData(), autoload_with=expected)
        for column in target_inspector.get_columns(name):
            if column["name"] in existing:
                continue
            definition = str(CreateColumn(table.c[column["name"]]).compile(dialect=connection.dialect))
            has_rows = connection.exec_driver_sql(f"SELECT 1 FROM {quote(name)} LIMIT 1").first() is not None  # noqa: S608 -- quoted frozen identifier
            if not column["nullable"] and column["default"] is None and has_rows:
                if name in {"pc_scopes", "pc_scope_bindings", "pc_scope_external_references"} and column[
                    "name"
                ].endswith("_search"):
                    definition += " DEFAULT ''"
                else:
                    raise MigrationError(
                        "data_conversion_required", f"{name}.{column['name']} requires a data conversion."
                    )
            connection.exec_driver_sql(f"ALTER TABLE {quote(name)} ADD COLUMN {definition}")


def _scopes(connection: Connection) -> None:
    from powercontext.builtin.persistence.scope_search_schema import ensure_scope_search_schema

    # Early releases used arbitrary scope IDs before Scope became a stored domain.
    # Retain those exact IDs; no historical title, owner or timestamp is invented.
    scope_ids = set()
    for name in inspect(connection).get_table_names():
        if name.startswith("pc_") and "scope_id" in {c["name"] for c in inspect(connection).get_columns(name)}:
            quoted = connection.dialect.identifier_preparer.quote(name)
            scope_ids.update(
                connection.exec_driver_sql(
                    f"SELECT DISTINCT scope_id FROM {quoted} WHERE scope_id IS NOT NULL"  # noqa: S608 -- dialect-quoted identifier
                ).scalars()
            )
    for scope_id in scope_ids:
        connection.execute(
            text(
                "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
                "SELECT :scope,'','',:scope,'','',1 WHERE NOT EXISTS (SELECT 1 FROM pc_scopes WHERE scope_id=:scope)"
            ),
            {"scope": scope_id},
        )
    finish(ensure_scope_search_schema(pinned(connection)))


def _processing(  # noqa: C901 -- Recover the temporary column before completing the domain conversion.
    connection: Connection,
    configuration: dict[str, Any],
    *,
    temporary_counter: bool = False,
    record_counter: Callable[[bool], None] | None = None,
) -> None:
    from powercontext.builtin.persistence.processing_migration import apply_processing_migration

    manifest = dict(configuration["processing_manifest"])
    manifest["bindings"] = {
        key: value for key, value in manifest["bindings"].items() if value not in {"handoff", "prompt"}
    }
    manifest["capabilities"] = [value for value in manifest["capabilities"] if value not in {"handoff", "prompt"}]
    complete = bool(
        connection.exec_driver_sql("SELECT 1 FROM pc_artifact_processing_schema WHERE phase='complete' LIMIT 1").first()
    )
    if complete and not temporary_counter:
        return
    # The converter needs this counter before its frozen revision introduces it.
    # Native DDL is durable before the next invocation; ownership is journaled
    # before ADD and cleared only after DROP, including an already-complete phase.
    column = "consecutive_dream_attempts"
    existing = next(
        (c for c in inspect(connection).get_columns("pc_artifact_processing_intents") if c["name"] == column), None
    )
    if (
        temporary_counter
        and existing is not None
        and (
            str(existing["type"]).upper() != "INTEGER"
            or existing["nullable"]
            or str(existing["default"]).strip("()'\"") != "0"
            or existing.get("computed") is not None
        )
    ):
        raise MigrationError("recovery_required", "The temporary Processing counter definition changed.")
    if not complete:
        if existing is None:
            if record_counter is not None:
                record_counter(True)
            temporary_counter = True
            connection.exec_driver_sql(
                "ALTER TABLE pc_artifact_processing_intents ADD COLUMN consecutive_dream_attempts INTEGER NOT NULL DEFAULT 0"
            )
        while True:
            progress = finish(
                apply_processing_migration(
                    pinned(connection), config_manifest=manifest, migration_id="historical-server-adoption"
                )
            )
            if progress.complete:
                break
    if temporary_counter:
        if column in {c["name"] for c in inspect(connection).get_columns("pc_artifact_processing_intents")}:
            if connection.exec_driver_sql(
                "SELECT 1 FROM pc_artifact_processing_intents "
                "WHERE consecutive_dream_attempts IS NULL OR consecutive_dream_attempts <> 0 LIMIT 1"
            ).first():
                raise MigrationError("data_conversion_required", "Historical Processing contains newer Dream work.")
            connection.exec_driver_sql(
                "ALTER TABLE pc_artifact_processing_intents DROP COLUMN consecutive_dream_attempts"
            )
        if record_counter is not None:
            record_counter(False)


def _reconcile(connection: Connection, expected: Connection, rows: list[list[Any]]) -> None:  # noqa: C901 -- Preserve each table's ordered validation and rebuild.
    for kind, name, _, ddl in rows:
        if kind != "table" or not ddl or ddl.upper().startswith("CREATE VIRTUAL TABLE"):
            continue
        actual_contract = table_contract(connection, name)
        target_contract = table_contract(expected, name)
        if actual_contract == target_contract:
            continue
        for column, required in target_contract["columns"].items():
            actual = actual_contract["columns"][column]
            for attribute in ("type", "computed", "collation"):
                if actual[attribute] != required[attribute]:
                    raise MigrationError(
                        "data_conversion_required", f"{name}.{column}: {attribute} needs an explicit conversion."
                    )
        if (
            actual_contract["primary_key"] != target_contract["primary_key"]
            or actual_contract["options"] != target_contract["options"]
        ):
            raise MigrationError(
                "data_conversion_required", f"{name}: primary key or table storage requires explicit conversion."
            )
        for requirement in ("checks", "unique", "foreign_keys"):
            removed = set()
            if requirement == "checks" and name == "pc_artifact_tags":
                # Released 1.0 restricted tags to four families. The Atomic
                # transition deliberately opens this domain to artifact families.
                removed.add(sql_tokens("family IN ('memory', 'experience', 'skill', 'handoff')"))
            if any(
                value not in target_contract[requirement] and value not in removed
                for value in actual_contract[requirement]
            ):
                raise MigrationError(
                    "data_conversion_required",
                    f"{name}: additional {requirement} must be preserved by an explicit conversion.",
                )
        extras = set(actual_contract["columns"]) - set(target_contract["columns"])
        actual_table = Table(name, MetaData(), autoload_with=connection)
        extra_definitions = [
            extension_column_ddl(
                connection, actual_table.c[column], collation=actual_contract["columns"][column]["collation"]
            )
            for column in sorted(extras)
        ]
        if extra_definitions:
            position = ddl.index("(") + 1
            ddl = ddl[:position] + ", ".join(extra_definitions) + ", " + ddl[position:]
        temporary = "pc_historical_" + name
        if inspect(connection).has_table(temporary):
            raise MigrationError("recovery_required", f"Unexpected migration staging table: {temporary}")
        create = re.sub(r'(?i)^CREATE TABLE\s+(?:"[^"]+"|\w+)', "CREATE TABLE " + quote(temporary), ddl, count=1)
        objects = list(
            connection.exec_driver_sql(
                "SELECT type,name,sql FROM sqlite_schema WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",
                (name,),
            )
        )
        connection.exec_driver_sql(create)
        fields = ",".join(
            quote(column) for column, definition in actual_contract["columns"].items() if definition["computed"] is None
        )
        connection.exec_driver_sql(f"INSERT INTO {quote(temporary)} ({fields}) SELECT {fields} FROM {quote(name)}")  # noqa: S608 -- all identifiers quoted
        with preserve_rebuild_views(connection):
            connection.exec_driver_sql(f"DROP TABLE {quote(name)}")
            connection.exec_driver_sql(f"ALTER TABLE {quote(temporary)} RENAME TO {quote(name)}")
        for _, _, sql in objects:
            connection.exec_driver_sql(sql)
    for kind, name, _, sql in rows:
        if (
            kind == "index"
            and not connection.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE name=?", (name,)).first()
        ):
            connection.exec_driver_sql(sql)


def apply(
    connection: Connection, bundle: MigrationBundle, configuration: dict[str, Any], *, embedding_factory=None
) -> None:
    source = bundle.source_revision or bundle.revisions[0]
    rows = layout(bundle, source)
    _create_missing(connection, rows)
    with reference(rows) as expected:
        _expand(connection, expected, rows)
        _scopes(connection)
        _processing(connection, configuration)
        _reconcile(connection, expected, [row for row in rows if row[2] == "pc_artifact_tags"])
        convert_memory(connection, bundle, database=_PinnedDatabase(connection), embedding_factory=embedding_factory)
        _reconcile(connection, expected, rows)
    issues = differences(connection, rows)
    if issues:
        raise MigrationError("incompatible_schema", "; ".join(issues[:12]))


class _EmbeddingBridge:
    def __init__(self, model, run):
        self.model, self.run, self.profile = model, run, model.profile

    async def embed(self, texts):
        # SQL stays on the maintenance connection's thread/greenlet; only the
        # provider coroutine crosses the driver's asynchronous boundary.
        return self.run(self.model.embed(texts))


@contextmanager
def _opened_embedding(factory, run):
    manager = factory()
    model = run(manager.__aenter__())
    try:
        yield _EmbeddingBridge(model, run)
    finally:
        run(manager.__aexit__(None, None, None))


@contextmanager
def _embedding(factory):
    from sqlalchemy.util.concurrency import (
        await_only,  # ty: ignore[possibly-missing-import] -- SQLAlchemy exports depend on greenlet.
        in_greenlet,  # ty: ignore[possibly-missing-import]
    )

    if factory is None:
        yield None
    elif in_greenlet():
        with _opened_embedding(factory, await_only) as model:
            yield model
    else:
        with asyncio.Runner() as runner, _opened_embedding(factory, runner.run) as model:
            yield model


def convert_memory(connection, bundle, *, database, embedding_factory=None):  # noqa: C901 -- Validate archive payloads and adapt both historical projection backends.
    if not connection.exec_driver_sql("SELECT 1 FROM pc_artifacts WHERE family='memory' LIMIT 1").first():
        return
    from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
    from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex

    from .atomic_memory_archive import (
        _LINEAGE_ARTIFACT_COLUMNS,
        _LINEAGE_SOURCE_COLUMNS,
        ARTIFACT_COLUMNS,
        BINDING_COLUMNS,
        HEAD_COLUMNS,
        OWNER_COLUMNS,
        TAG_COLUMNS,
    )
    from .atomic_memory_v1 import apply_atomic_memory_migration

    # The frozen archive cannot preserve values from user-added columns. Empty
    # extensions remain harmless, but populated ones need an explicit mapping.
    for name, archived in (
        ("pc_artifacts", ARTIFACT_COLUMNS),
        ("pc_artifact_heads", HEAD_COLUMNS),
        ("pc_artifact_tags", TAG_COLUMNS),
        ("pc_access_owners", OWNER_COLUMNS),
        ("pc_access_relationships", BINDING_COLUMNS),
        ("pc_artifact_lineage_sources", ("scope_id", "family", "artifact_id", "revision", *_LINEAGE_SOURCE_COLUMNS)),
        (
            "pc_artifact_lineage_artifacts",
            ("scope_id", "family", "artifact_id", "revision", *_LINEAGE_ARTIFACT_COLUMNS),
        ),
    ):
        columns = {column["name"] for column in inspect(connection).get_columns(name)} - set(archived)
        if name == "pc_artifact_lineage_artifacts" and "is_merge_input" in columns:
            # The archive records ordinary reference payloads, not merge flags.
            if connection.exec_driver_sql(
                "SELECT 1 FROM pc_artifact_lineage_artifacts WHERE family='memory' "
                "AND (is_merge_input IS NULL OR is_merge_input <> 0) LIMIT 1"
            ).first():
                raise MigrationError("data_conversion_required", "Memory merge lineage needs a mapping.")
            columns.remove("is_merge_input")
        for column in columns:
            statement = (
                "SELECT 1 FROM "  # noqa: S608 -- Every identifier is quoted by the connection dialect.
                + connection.dialect.identifier_preparer.quote(name)
                + " WHERE family='memory' AND "
                + connection.dialect.identifier_preparer.quote(column)
                + " IS NOT NULL LIMIT 1"
            )
            if connection.exec_driver_sql(statement).first():
                raise MigrationError(
                    "data_conversion_required", f"{name}.{column}: Memory extension data needs a mapping."
                )

    class HistoricalAtomicIndex(SQLiteAtomicMemoryIndex):
        @override
        async def initialize(self, connection, /):
            # Frozen DDL established current and its FTS helper. The subsequent
            # revision owns the native vector helper and its verified backfill.
            await self.require_current_schema(connection)
            await self.rebuild_fts(connection)

    class HistoricalNativeAtomicIndex(OceanBaseAtomicMemoryIndex):
        @override
        async def require_current_schema(self, connection):
            columns = await connection.run_sync(lambda c: inspect(c).get_columns("pc_atomic_memory_current"))
            if "description" not in {column["name"] for column in columns}:
                # The current converter writes this metadata before its frozen
                # revision. Use that revision's explicit text identity so native
                # recovery recognizes the completed ADD instead of repeating it.
                await connection.exec_driver_sql(
                    "ALTER TABLE pc_atomic_memory_current ADD COLUMN description "
                    "VARCHAR(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL"
                )
            await super().require_current_schema(connection)

    profile = bundle.vector_profile.profile if bundle.vector_profile else None
    index = (
        HistoricalAtomicIndex(profile) if connection.dialect.name == "sqlite" else HistoricalNativeAtomicIndex(profile)
    )
    if profile is not None and embedding_factory is None:
        raise MigrationError("embedding_required", "Memory conversion requires the configured embedding provider.")
    with _embedding(embedding_factory if profile is not None else None) as model:
        report = finish(
            apply_atomic_memory_migration(
                database, index, maintenance_confirmed=True, embedding_model=model, require_evidence=False
            )
        )
    if not report.ready:
        raise MigrationError("data_conversion_required", "; ".join(report.errors))
