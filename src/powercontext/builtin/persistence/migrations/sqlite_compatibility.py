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

"""Compare SQLite requirements without treating SQL formatting as a contract.

The frozen revision still owns the required schema. Extra ordinary indexes and
nullable/defaulted columns may coexist with it; extra constraints and triggers
on managed tables cannot silently change the application's write semantics.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from alembic import command
from sqlalchemy import Connection, MetaData, Table, create_engine, event, inspect
from sqlalchemy.schema import CreateColumn

from .bundle import ALEMBIC_CONTEXT_LOCK, sqlite_inventory
from .models import MigrationError, digest

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .bundle import MigrationBundle


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sql_tokens(sql: str) -> tuple[str, ...]:
    """Keep literals and operators intact while ignoring identifier quotation."""
    tokens = re.findall(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^]]*\]|[\w]+|[^\s]", sql)
    result = []
    for token in tokens:
        if token.startswith("'"):
            result.append(token)
        elif token.startswith(('"', "`", "[")):
            result.append(token[1:-1].replace(token[0] * 2, token[0]).lower())
        else:
            result.append(token.lower())
    return tuple(result)


_layouts: OrderedDict[str, dict[str, list[list[Any]]]] = OrderedDict()


def layout(bundle: MigrationBundle, revision: str | None) -> list[list[Any]]:
    if revision is None:
        return []
    if revision not in bundle.fingerprints:
        raise MigrationError("incompatible_schema", "The database revision is not registered.")
    key = digest((bundle.checksum, bundle.vector_profile.checksum if bundle.vector_profile else None))
    with ALEMBIC_CONTEXT_LOCK:
        if key not in _layouts:
            # Derive requirements from the immutable scripts on an empty,
            # isolated database. Future revisions need no version switch here.
            from powercontext.builtin.runtime.config import BuiltinConfig
            from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest

            layouts = {}
            with reference([]) as connection:
                if bundle.vector_profile is not None:
                    from .vector_profile import load_sqlite_vector_extension

                    load_sqlite_vector_extension(connection)
                config = bundle.config(connection)
                config.attributes["configuration"] = {
                    "processing_manifest": canonical_processing_manifest(BuiltinConfig()),
                }
                excluded = frozenset({"pc_schema_revision", *bundle.retained_objects})
                for item in bundle.revisions:
                    command.upgrade(config, item)
                    if bundle.vector_profile is not None:
                        bundle.vector_profile.sqlite_upgrade(connection, item)
                    rows = sqlite_inventory(connection, exclude=excluded)
                    # A replay must agree with the released schema contract.
                    bundle.verify(item, digest(rows))
                    layouts[item] = rows
            _layouts[key] = layouts
            if len(_layouts) > 16:
                _layouts.popitem(last=False)
        _layouts.move_to_end(key)
        return _layouts[key][revision]


@contextmanager
def reference(rows: list[list[Any]]) -> Iterator[Connection]:
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            if any(sql and "using vec0" in sql.lower() for _, _, _, sql in rows):
                from .vector_profile import load_sqlite_vector_extension

                load_sqlite_vector_extension(connection)
            for kind in ("table", "index", "view", "trigger"):
                for row_kind, name, _, sql in rows:
                    if (
                        row_kind == kind
                        and sql
                        and not connection.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE name=?", (name,)).first()
                    ):
                        connection.exec_driver_sql(sql)
            yield connection
    finally:
        engine.dispose()


def _default(value: str | None, column_type: str) -> tuple[str, ...] | None:
    if value is None or re.fullmatch(r"[\s()]*null[\s()]*", value, re.IGNORECASE):
        return None
    result = sql_tokens(value)
    while len(result) >= 2 and result[0] == "(" and result[-1] == ")" and "(" not in result[1:-1]:
        result = result[1:-1]
    if len(result) == 1 and column_type in {"INTEGER", "BIGINT", "BOOLEAN", "FLOAT", "REAL"}:
        number = result[0].strip("'")
        if re.fullmatch(r"-?\d+(?:\.\d+)?", number):
            result = (number,)
    return result


@lru_cache(maxsize=256)
def _usable_default(expression: str | None) -> bool:
    if expression is None:
        return False
    # These direct SQL defaults always produce a non-NULL timestamp string.
    if re.fullmatch(r"[\s(]*CURRENT_(?:DATE|TIME|TIMESTAMP)[\s)]*", expression, re.IGNORECASE):
        return True
    # SQLite enforces deterministic functions in generated expressions, including
    # date/time arguments at evaluation. A successful one-off SELECT would also
    # accept defaults depending on connection state, randomness or the clock.
    prefix = "_pc_default"
    while any(token.startswith(prefix) for token in sql_tokens(expression)):
        prefix += "_"
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            f"CREATE TABLE probe ({prefix}_input INTEGER, {prefix}_value GENERATED ALWAYS AS ({expression}))"
        )
        connection.execute(f"INSERT INTO probe ({prefix}_input) VALUES (0)")  # noqa: S608 -- locally generated identifier
        return bool(connection.execute(f"SELECT {prefix}_value IS NOT NULL FROM probe").fetchone()[0])  # noqa: S608 -- locally generated identifier
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def _column_collation(clause: list[str]) -> str:
    # COLLATE inside a generated expression or CHECK is not a column attribute.
    depth = 0
    collation = "binary"
    for position, lexeme in enumerate(clause):
        if lexeme == "(":
            depth += 1
        elif lexeme == ")":
            depth -= 1
        elif depth == 0 and lexeme == "collate":
            name = clause[position + 1]
            collation = name[1:-1].replace("''", "'") if name.startswith("'") else name
    return collation.lower()


def extension_column_ddl(connection: Connection, column, *, collation: str) -> str:
    """Restore the column collation omitted by SQLite's SQLAlchemy reflection."""
    definition = str(CreateColumn(column).compile(dialect=connection.dialect))
    if collation != "binary":
        definition += " COLLATE " + quote(collation)
    return definition


def table_contract(connection: Connection, name: str) -> dict[str, Any]:
    inspector = inspect(connection)
    ddl = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (name,)
    ).scalar_one()
    tokens = sql_tokens(ddl)
    clauses = []
    clause = []
    depth = 0
    tail = ()
    for position, lexeme in enumerate(tokens[tokens.index("(") :], start=tokens.index("(")):
        if lexeme == "(":
            depth += 1
            if depth == 1:
                continue
        elif lexeme == ")":
            depth -= 1
            if depth == 0:
                clauses.append(clause)
                tail = tokens[position + 1 :]
                break
        if lexeme == "," and depth == 1:
            clauses.append(clause)
            clause = []
        else:
            clause.append(lexeme)
    collations = {clause[0]: _column_collation(clause) for clause in clauses if clause}
    columns = {
        column["name"]: {
            "type": str(column["type"]),
            "nullable": column["nullable"],
            "default": _default(column["default"], str(column["type"])),
            "computed": column.get("computed"),
            "collation": collations.get(column["name"].lower(), "binary"),
        }
        for column in inspector.get_columns(name)
    }
    foreign_keys = sorted(
        (
            tuple(key["constrained_columns"]),
            key["referred_table"],
            tuple(key["referred_columns"]),
            tuple(sorted(key.get("options", {}).items())),
        )
        for key in inspector.get_foreign_keys(name)
    )
    indexes = connection.exec_driver_sql(f"PRAGMA index_list({quote(name)})").mappings().all()
    # INTEGER PRIMARY KEY aliases have no backing index. Other primary keys
    # can override a column's collation in the key declaration itself.
    primary_key = [(column, "binary") for column in inspector.get_pk_constraint(name)["constrained_columns"]]
    unique = []
    for index in indexes:
        if index["unique"]:
            keys = tuple(
                (row[0], row[2], row[3], row[4])
                for row in connection.exec_driver_sql(f"PRAGMA index_xinfo({quote(index['name'])})")
                if row[5]
            )
            if index["origin"] == "pk":
                primary_key = [(column, collation.lower()) for _, column, _, collation in keys]
                continue
            definition = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_schema WHERE name=?", (index["name"],)
            ).scalar_one_or_none()
            index_tokens = sql_tokens(definition or "")
            predicate = index_tokens[index_tokens.index("where") + 1 :] if "where" in index_tokens else ()
            expression = index_tokens[index_tokens.index("(") :] if any(key[1] is None for key in keys) else ()
            unique.append((keys, predicate, expression))
    return {
        "columns": columns,
        "primary_key": primary_key,
        "foreign_keys": foreign_keys,
        "checks": sorted(sql_tokens(check["sqltext"]) for check in inspector.get_check_constraints(name)),
        "unique": sorted(unique, key=repr),
        "options": tail,
    }


@lru_cache(maxsize=64)
def _expected_contracts(serialized: str) -> dict[str, dict[str, Any]]:
    rows = json.loads(serialized)
    with reference(rows) as connection:
        return {
            name: table_contract(connection, name)
            for kind, name, _, sql in rows
            if kind == "table" and sql and not sql.upper().startswith("CREATE VIRTUAL TABLE")
        }


def differences(connection: Connection, rows: list[list[Any]]) -> tuple[str, ...]:  # noqa: C901 -- Compare each declared object and its attached write constraints.
    """Return incompatible requirements; unrelated objects are not requirements."""
    actual = {
        name: (kind, table, sql)
        for kind, name, table, sql in connection.exec_driver_sql(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE substr(name,1,7)!='sqlite_'"
        )
    }
    contracts = _expected_contracts(json.dumps(rows))
    required_tables = {table for kind, _, table, _ in rows if kind == "table"}
    expected_names = {name for _, name, _, _ in rows}
    issues = []
    for kind, name, _, sql in rows:
        if name not in actual:
            issues.append(f"missing {kind} {name}")
            continue
        actual_kind, _, actual_sql = actual[name]
        if actual_kind != kind:
            issues.append(f"object kind differs: {name}")
        elif name in contracts:
            expected = contracts[name]
            observed = table_contract(connection, name)
            for key in ("primary_key", "foreign_keys", "checks", "unique", "options"):
                if observed[key] != expected[key]:
                    issues.append(f"{name}: {key} differs")
            for column, definition in expected["columns"].items():
                if observed["columns"].get(column) != definition:
                    issues.append(f"{name}.{column}: column requirements differ")
            required_extensions = [
                column
                for column, definition in observed["columns"].items()
                if column not in expected["columns"] and not definition["nullable"]
            ]
            if required_extensions:
                defaults = {column["name"]: column["default"] for column in inspect(connection).get_columns(name)}
                for column in required_extensions:
                    if observed["columns"][column]["computed"] is not None:
                        issues.append(f"{name}.{column}: extra generated column must be nullable")
                    elif not _usable_default(defaults[column]):
                        issues.append(f"{name}.{column}: extra required column needs a supported non-NULL default")
        elif sql_tokens(actual_sql or "") != sql_tokens(sql or ""):
            issues.append(f"definition differs: {name}")
    for name, (kind, table, _) in actual.items():
        if kind == "trigger" and table in required_tables and name not in expected_names:
            issues.append(f"unregistered trigger on managed table: {name}")
    return tuple(issues)


def verify(connection: Connection, bundle: MigrationBundle, revision: str | None) -> None:
    issues = differences(connection, layout(bundle, revision))
    if issues:
        raise MigrationError("incompatible_schema", "; ".join(issues[:12]))


@contextmanager
def preserve_rebuild_views(connection: Connection) -> Iterator[None]:
    """Keep final-name references intact while a replacement table is renamed."""
    previous = connection.exec_driver_sql("PRAGMA legacy_alter_table").scalar_one()
    try:
        # Modern RENAME validates views while their final table is temporarily absent.
        connection.exec_driver_sql("PRAGMA legacy_alter_table=ON")
        yield
    finally:
        connection.exec_driver_sql(f"PRAGMA legacy_alter_table={int(previous)}")


@contextmanager
def adopt_existing_tables(connection: Connection, bundle: MigrationBundle, revision: str) -> Iterator[None]:  # noqa: C901 -- Adapt declared creation and preserve independent extensions.
    """Satisfy additive schema operations already completed by a released tool.

    Only the exact declared target operation is eligible. Data transformations
    still execute; incompatible objects never become an IF NOT EXISTS no-op.
    """
    expected = {
        name: row for row in layout(bundle, revision) for kind, name, _, sql in [row] if kind == "table" and sql
    }
    previous = bundle.revisions.index(revision) - 1
    managed = {row[1] for row in layout(bundle, bundle.revisions[previous] if previous >= 0 else None)} | {
        row[1] for row in layout(bundle, revision)
    }
    extra_indexes = [
        row for row in sqlite_inventory(connection) if row[0] == "index" and row[1] not in managed and row[3]
    ]
    extra_columns = {}
    candidate_sources = {}
    candidate_columns = {}
    generated_sources = {}
    rebuild_prefix = "pc_release1400_" if bundle.source_revision == "r1400" else "pc_full_upgrade_"
    replacements = {}
    if revision == "s1400":
        replacements = {rebuild_prefix + name: name for name in expected}
        if bundle.source_revision != "r1400":
            replacements.update({
                "pc_dream_upgrade_" + name.removeprefix("pc_"): name
                for name in ("pc_candidate_heads", "pc_candidate_versions")
            })
            schema = json.loads((bundle.snapshot_directory / "dream/schema.json").read_text())
            candidate_sources = {entry["target_name"]: entry["source_name"] for entry in schema["tables"].values()}
            candidate_columns = {entry["source_name"]: entry["source_columns"] for entry in schema["tables"].values()}
        # This released revision rebuilds tables and copies every source column.
        # Extend its temporary destination with independently owned columns.
        contracts = _expected_contracts(json.dumps(list(expected.values())))
        for name, contract in contracts.items():
            source = candidate_sources.get(name, name)
            if not inspect(connection).has_table(source):
                continue
            table = Table(source, MetaData(), autoload_with=connection)
            extensions = [column for column in table.columns if column.name not in contract["columns"]]
            if extensions:
                observed = table_contract(connection, source)["columns"]
                extra_columns[name] = [
                    extension_column_ddl(connection, column, collation=observed[column.name]["collation"])
                    for column in extensions
                ]
            if any(column.computed is not None for column in table.columns):
                generated_sources[rebuild_prefix + name] = name

    rebuild_settings = ExitStack()

    def before_execute(conn, _cursor, statement, parameters, _context, _many):  # noqa: C901 -- Adapt only exact frozen DDL and copy statements.
        tokens = sql_tokens(statement)
        # The frozen Dream validator requests an exact managed-column list.
        # Project only this reflection onto its registered source contract;
        # the outer admission check validates the complete real table first.
        reflected = re.fullmatch(r'PRAGMA (?:main\.)?(table_xinfo|table_info)\("([^"\n]+)"\)', statement, re.IGNORECASE)
        if reflected and reflected[2] in candidate_columns:
            columns = candidate_columns[reflected[2]]
            fields = 'cid,name,type,"notnull",dflt_value,pk' + (
                ",hidden" if reflected[1].lower() == "table_xinfo" else ""
            )
            placeholders = ",".join("?" for _ in columns)
            order = " ".join(f"WHEN ? THEN {index}" for index in range(len(columns)))
            return (
                f"SELECT {fields} FROM pragma_{reflected[1]}(?) WHERE name IN ({placeholders}) ORDER BY CASE name {order} END",  # noqa: S608 -- fixed PRAGMA name and placeholders
                (reflected[2], *columns, *columns),
            )
        if (
            len(tokens) == 6
            and tokens[:2] == ("alter", "table")
            and tokens[3:5] == ("rename", "to")
            and replacements.get(tokens[2]) == tokens[5]
        ):
            rebuild_settings.enter_context(preserve_rebuild_views(conn))
        dream_target = (
            replacements.get(tokens[2]) if len(tokens) > 2 and tokens[2].startswith("pc_dream_upgrade_") else None
        )
        if dream_target and tokens[:2] == ("insert", "into"):
            columns = inspect(conn).get_columns(dream_target)
            fields = ",".join(quote(column["name"]) for column in columns if column.get("computed") is None)
            return f"INSERT INTO {quote(tokens[2])} ({fields}) SELECT {fields} FROM {quote(dream_target)}", parameters  # noqa: S608 -- quoted identifiers
        if len(tokens) > 2 and tokens[:2] == ("insert", "into") and tokens[2] in generated_sources:
            target = tokens[2]
            name = generated_sources[target]
            # The revision may add ordinary columns before copying this table.
            # Read the writable set at execution, retaining its new data too.
            columns = inspect(conn).get_columns(name)
            fields = ",".join(quote(column["name"]) for column in columns)
            original = f"INSERT INTO {target} ({fields}) SELECT {fields} FROM {name}"  # noqa: S608 -- frozen table names and quoted columns
            if statement == original:
                writable = ",".join(quote(column["name"]) for column in columns if column.get("computed") is None)
                statement = f"INSERT INTO {target} ({writable}) SELECT {writable} FROM {name}"  # noqa: S608 -- frozen table names and quoted columns
            return statement, parameters
        if len(tokens) > 5 and tokens[:2] == ("alter", "table") and tokens[3:5] == ("add", "column"):
            name, column = tokens[2], tokens[5]
            if name in expected:
                actual = table_contract(conn, name)["columns"].get(column)
                if actual is not None:
                    required = _expected_contracts(json.dumps([expected[name]]))[name]["columns"].get(column)
                    if actual != required:
                        raise MigrationError("incompatible_schema", f"{name}.{column}: existing column differs.")
                    return "SELECT 1", ()
        if len(tokens) > 3 and tokens[:2] == ("create", "table"):
            name = tokens[2]
            if revision == "s1400" and name in replacements:
                source = replacements[name]
                additions = extra_columns.get(source, [])
                if additions:
                    position = statement.index("(") + 1
                    statement = statement[:position] + ", ".join(additions) + ", " + statement[position:]
                return statement, parameters
            row = expected.get(name)
            if (
                row is not None
                and sql_tokens(row[3]) == tokens
                and conn.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE name=?", (name,)).first()
            ):
                issues = differences(conn, [row])
                if issues:
                    raise MigrationError("incompatible_schema", "; ".join(issues))
                return "SELECT 1", ()
        return statement, parameters

    def after_execute(conn, _cursor, statement, *_args):
        rebuild_settings.close()
        tokens = sql_tokens(statement)
        if (
            len(tokens) == 6
            and tokens[:2] == ("alter", "table")
            and tokens[3:5] == ("rename", "to")
            and candidate_sources.get(tokens[5]) == tokens[2]
        ):
            # Let SQLite rewrite quoted identifiers and index expressions.
            renamed = {row[1]: row for row in sqlite_inventory(conn) if row[0] == "index" and row[2] == tokens[5]}
            extra_indexes[:] = [renamed.get(row[1], row) for row in extra_indexes]

    event.listen(connection, "before_cursor_execute", before_execute, retval=True)
    event.listen(connection, "after_cursor_execute", after_execute)
    try:
        # Restore settings after each statement, or here if its execution fails.
        with rebuild_settings:
            yield
        # Frozen table rebuilds drop attached indexes. Restore independently
        # owned indexes whose table survives; registered changes retain priority.
        for _, name, table, sql in extra_indexes:
            if (
                inspect(connection).has_table(table)
                and not connection.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE name=?", (name,)).first()
            ):
                connection.exec_driver_sql(sql)
    finally:
        event.remove(connection, "before_cursor_execute", before_execute)
        event.remove(connection, "after_cursor_execute", after_execute)
