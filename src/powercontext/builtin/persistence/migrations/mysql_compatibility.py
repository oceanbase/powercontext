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

"""Recoverable admission of historical Server storage on MySQL backends."""

from __future__ import annotations

import copy
import re
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import event, inspect

from . import historical
from .models import MigrationError, digest
from .mysql_schema import (
    _LEXEME,
    MySQLSchema,
    _column,
    _parse_table,
    _parts,
    _quote,
    _table_clause,
    _tokens,
    mysql_inventory,
)
from .pinned import pinned


def select_source(connection, bundles, *, embedded_code=False):
    if not historical.recognizes(connection):
        return None
    names = set(inspect(connection).get_table_names())
    for bundle in bundles:
        schema = MySQLSchema(bundle, embedded_code=embedded_code)
        revision = bundle.source_revision or bundle.revisions[0]
        if ("pc_candidate_heads" in names) == ("pc_candidate_heads" in schema.required(revision)["tables"]):
            return bundle, revision
    return None


def steps(connection, schema):
    revision = schema.bundle.source_revision or schema.bundle.revisions[0]
    inventory = mysql_inventory(connection)
    if "pc_atomic_memory_states" in inventory["tables"]:
        raise MigrationError("incompatible_schema", "Unreleased split Atomic authority requires explicit recovery.")
    pending = []
    if not schema._matches(inventory, schema.required(revision)):
        pending.append("historical_structure")
    if "pc_scopes" not in inventory["tables"]:
        pending.append("historical_scopes")
    if (
        "pc_artifact_processing_schema" not in inventory["tables"]
        or not connection.exec_driver_sql(
            "SELECT 1 FROM pc_artifact_processing_schema WHERE phase='complete' LIMIT 1"
        ).first()
    ):
        pending.append("historical_processing")
    if connection.exec_driver_sql("SELECT 1 FROM pc_artifacts WHERE family='memory' LIMIT 1").first():
        pending.append("historical_atomic_memory")
    return tuple(pending)


def _single(table, definition):
    return {"tables": {} if definition is None else {table: definition}, "views": [], "triggers": []}


def _postcondition(schema, name, observed, expected):
    if (observed is None) != (expected is None):
        return False
    if observed is not None and any(set(observed[key]) != set(expected[key]) for key in ("columns", "indexes")):
        return False
    return schema._matches(_single(name, observed), _single(name, expected))


def validate_resume(inventory, evidence, schema):
    """Accept only the last durable checkpoint or its one in-flight DDL."""
    fingerprint = schema.fingerprint(inventory)
    if fingerprint == evidence.compatibility_fingerprint:
        return
    pending = evidence.compatibility_pending
    if pending is not None:
        before = copy.deepcopy(inventory)
        for table, change in pending["changes"].items():
            observed = inventory["tables"].get(table)
            after = change["after"]
            if not _postcondition(schema, table, observed, after):
                break
            if change["before"] is None:
                before["tables"].pop(table, None)
            else:
                before["tables"][table] = change["before"]
        else:
            if schema.fingerprint(before) == evidence.compatibility_fingerprint:
                return
    raise MigrationError("recovery_required", "Historical DDL differs from the original maintenance intent.")


def _ddl_change(connection, statement, inventory):
    lexemes = _tokens(statement)
    if lexemes[:2] == ["create", "table"]:
        if lexemes[2:5] == ["if", "not", "exists"]:
            statement = re.sub(r"(?i)CREATE TABLE\s+IF NOT EXISTS", "CREATE TABLE", statement, count=1)
        name, after = _parse_table(statement)
        return {name: after}
    if lexemes[:2] == ["drop", "table"] and len(lexemes) == 3:
        return {lexemes[2]: None}
    if lexemes[:2] == ["rename", "table"] and len(lexemes) == 5 and lexemes[3] == "to":
        old, new = lexemes[2], lexemes[4]
        if new in inventory["tables"]:
            raise MigrationError("recovery_required", "A historical staging table already exists.")
        return {old: None, new: copy.deepcopy(inventory["tables"][old])}
    if lexemes[:2] != ["alter", "table"] or len(lexemes) < 5:
        raise MigrationError(
            "unsupported_historical_ddl", "Historical conversion emitted an unregistered DDL operation."
        )
    name = lexemes[2]
    after = copy.deepcopy(inventory["tables"][name])
    for operation in _parts(lexemes[3:]):
        _alter_postcondition(connection, name, after, operation)
    return {name: after}


def _alter_postcondition(connection, name, after, operation):
    defaults = {"charset": None, "collation": None}
    if operation[:2] in (["add", "column"], ["modify", "column"]):
        after["columns"][operation[2]] = _column(operation[2:], defaults)
    elif operation[:2] == ["drop", "column"]:
        del after["columns"][operation[2]]
    elif operation[:2] == ["drop", "check"]:
        del after["checks"][operation[2]]
    elif operation[:3] == ["drop", "foreign", "key"]:
        keys = inspect(connection).get_foreign_keys(name)
        key = next((key for key in keys if (key["name"] or "").casefold() == operation[3].casefold()), None)
        if key is None:
            raise MigrationError("incompatible_schema", "The historical foreign key no longer exists.")
        after["foreign_keys"] = [
            value
            for value in after["foreign_keys"]
            if not (
                value["columns"] == key["constrained_columns"]
                and value["referred_table"] == key["referred_table"]
                and value["referred_columns"] == key["referred_columns"]
            )
        ]
    elif operation[0] == "add":
        _table_clause(after, operation[1:], defaults, None)
        after["foreign_keys"].sort(key=digest)
    else:
        raise MigrationError(
            "unsupported_historical_ddl", "Historical conversion requires an explicit DDL postcondition."
        )


@contextmanager
def recorded_ddl(connection, schema, journal, evidence, guard):  # noqa: C901 -- Durable intent spans multi-table DDL.
    """Record each nontransactional DDL using the runner's existing journal."""
    current = mysql_inventory(connection)
    if evidence.compatibility_fingerprint is not None:
        validate_resume(current, evidence, schema)
    evidence.compatibility_fingerprint = schema.fingerprint(current)
    evidence.compatibility_pending = None
    journal.write(evidence)

    def before_execute(conn, _cursor, statement, parameters, _context, _many):
        if not re.match(r"\s*(CREATE|ALTER|DROP|RENAME)\s", statement, re.IGNORECASE):
            return
        guard()
        changes = _ddl_change(conn, statement, current)
        evidence.compatibility_pending = {
            "changes": {
                name: {"before": current["tables"].get(name), "after": after} for name, after in changes.items()
            },
            "operation": digest(statement),
        }
        journal.write(evidence)

    def after_execute(conn, _cursor, statement, _parameters, _context, _many):
        if not re.match(r"\s*(CREATE|ALTER|DROP|RENAME)\s", statement, re.IGNORECASE):
            return
        pending = evidence.compatibility_pending
        if pending is None:
            raise MigrationError("recovery_required", "A historical DDL operation has no durable intent.")
        names = set(inspect(conn).get_table_names())
        for name, change in pending["changes"].items():
            observed = None
            if name in names:
                row = conn.exec_driver_sql("SHOW CREATE TABLE " + _quote(name)).one()
                _, observed = _parse_table(row[1], database=conn.engine.url.database)
            if not _postcondition(schema, name, observed, change["after"]):
                raise MigrationError(
                    "verification_failed", "Historical DDL did not establish its declared postcondition."
                )
            if observed is None:
                current["tables"].pop(name, None)
            else:
                current["tables"][name] = observed
        evidence.compatibility_fingerprint = schema.fingerprint(current)
        evidence.compatibility_pending = None
        journal.write(evidence)
        guard()

    event.listen(connection, "before_cursor_execute", before_execute)
    event.listen(connection, "after_cursor_execute", after_execute)
    try:
        yield
    finally:
        event.remove(connection, "before_cursor_execute", before_execute)
        event.remove(connection, "after_cursor_execute", after_execute)


class _Database:
    def __init__(self, connection, guard):
        self.connection, self.guard = connection, guard

    @asynccontextmanager
    async def transaction(self):
        self.guard()
        self.connection.commit()
        self.connection.exec_driver_sql("START TRANSACTION")
        try:
            yield pinned(self.connection)
            self.guard()
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise


def _definitions(schema, revision):
    result = {}
    for statement in schema.statements(revision):
        if not statement.upper().startswith("CREATE TABLE"):
            continue
        name, _ = _parse_table(statement)
        clauses = []
        depth, start = 0, 0
        for match in _LEXEME.finditer(statement):
            lexeme = match.group()
            if lexeme == "(":
                depth += 1
                if depth == 1:
                    start = match.end()
            elif lexeme == ")":
                depth -= 1
                if depth == 0:
                    clauses.append(statement[start : match.start()].strip())
                    break
            elif lexeme == "," and depth == 1:
                clauses.append(statement[start : match.start()].strip())
                start = match.end()
        result[name] = (statement, clauses)
    return result


def _column_clause(clauses, name):
    return next(clause for clause in clauses if _tokens(clause)[0] == name)


def _copy_verified(connection, source, target, rebuild):
    """Compare every preserved column, including extensions, without loading rows in memory."""
    definition = rebuild["source"]
    primary_key = [column[0] for column in definition["primary_key"]]
    if not primary_key:
        raise MigrationError("data_conversion_required", "Historical table copying requires a stable primary key.")
    comparisons = {}
    joins = {}
    for name, column in definition["columns"].items():
        left, right = "s." + _quote(name), "t." + _quote(name)
        if name in primary_key:
            converted = "CONVERT(" + left + " USING utf8mb4) COLLATE utf8mb4_bin" if column["charset"] else left
            # Keep the destination key bare so the copy check can use its PK;
            # the full comparison below also checks exact text bytes/spaces.
            joins[name] = "(" + right + " = " + converted + ")"
        if column["charset"] is not None:
            left, right = ("CAST(CONVERT(" + value + " USING utf8mb4) AS BINARY)" for value in (left, right))
        comparisons[name] = "(" + left + " <=> " + right + ")"
    for name in (source, target):
        count = connection.exec_driver_sql("SELECT COUNT(*) FROM " + _quote(name)).scalar_one()  # noqa: S608 -- quoted identifier
        if count != rebuild["rows"]:
            raise MigrationError("verification_failed", "Historical table row counts changed during conversion.")
    statement = (
        "SELECT 1 FROM "  # noqa: S608 -- Every table and column identifier is quoted.
        + _quote(source)
        + " s LEFT JOIN "
        + _quote(target)
        + " t ON "
        + " AND ".join(joins[name] for name in primary_key)
        + " WHERE t."
        + _quote(primary_key[0])
        + " IS NULL OR NOT ("
        + " AND ".join(comparisons.values())
        + ") LIMIT 1"
    )
    if connection.exec_driver_sql(statement).first():
        raise MigrationError("verification_failed", "Historical table copy did not preserve every stored value.")


def _resume_rebuild(connection, schema, journal, evidence, guard):  # noqa: C901 -- Resume each durable table-swap state.
    rebuild = evidence.compatibility_rebuild
    if rebuild is None:
        return
    name, temporary, original = (rebuild[key] for key in ("table", "temporary", "original"))
    names = set(inspect(connection).get_table_names())
    if original not in names and name in names and temporary not in names:
        # The final DROP may already be durable. Its target has the new column
        # encodings, whereas the untouched source still has the old contract.
        _, target = _parse_table(rebuild["ddl"])
        row = connection.exec_driver_sql("SHOW CREATE TABLE " + _quote(name)).one()
        _, observed = _parse_table(row[1], database=connection.engine.url.database)
        if observed["columns"] != target["columns"]:
            connection.exec_driver_sql(rebuild["ddl"], execution_options={"no_parameters": True})
            names.add(temporary)
    if original not in names and name in names and temporary in names:
        guard()
        columns = ", ".join(_quote(column) for column in rebuild["source"]["columns"])
        connection.commit()
        connection.exec_driver_sql("START TRANSACTION")
        try:
            connection.exec_driver_sql("DELETE FROM " + _quote(temporary))  # noqa: S608 -- journal-bound identifier
            connection.exec_driver_sql(
                "INSERT INTO " + _quote(temporary) + " (" + columns + ") SELECT " + columns + " FROM " + _quote(name)  # noqa: S608 -- quoted identifiers
            )
            guard()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        _copy_verified(connection, name, temporary, rebuild)
        connection.exec_driver_sql("RENAME TABLE " + _quote(name) + " TO " + _quote(original))
        names.remove(name)
        names.add(original)
    if original in names and temporary in names and name not in names:
        _copy_verified(connection, original, temporary, rebuild)
        connection.exec_driver_sql("RENAME TABLE " + _quote(temporary) + " TO " + _quote(name))
        names.remove(temporary)
        names.add(name)
    if original in names and name in names and temporary not in names:
        _copy_verified(connection, original, name, rebuild)
        connection.exec_driver_sql("DROP TABLE " + _quote(original))
        names.remove(original)
    if name not in names or original in names or temporary in names:
        raise MigrationError("recovery_required", "Historical table swap has an unexpected state.")
    count = connection.exec_driver_sql("SELECT COUNT(*) FROM " + _quote(name)).scalar_one()  # noqa: S608 -- quoted identifier
    if count != rebuild["rows"]:
        raise MigrationError("verification_failed", "Historical table row count differs after its swap.")
    for temporary_check, clause in rebuild["checks"].items():
        row = connection.exec_driver_sql("SHOW CREATE TABLE " + _quote(name)).one()
        _, observed = _parse_table(row[1], database=connection.engine.url.database)
        if temporary_check in observed["checks"]:
            connection.exec_driver_sql("ALTER TABLE " + _quote(name) + " DROP CHECK " + _quote(temporary_check))
        original_check = _tokens(clause)[1]
        if original_check not in observed["checks"]:
            connection.exec_driver_sql("ALTER TABLE " + _quote(name) + " ADD " + clause)
    evidence.compatibility_rebuild = None
    journal.write(evidence)


def _rebuild_identity(connection, schema, name, columns, journal, evidence, guard):
    statement = connection.exec_driver_sql("SHOW CREATE TABLE " + _quote(name)).one()[1]
    _, source = _parse_table(statement, database=connection.engine.url.database)
    token = digest((evidence.maintenance_window_id, name))[:20]
    temporary, original = "pc_upgrade_new_" + token, "pc_upgrade_old_" + token
    if {temporary, original} & set(inspect(connection).get_table_names()):
        raise MigrationError("recovery_required", "Historical staging names are already occupied.")
    # SHOW CREATE emits each column/constraint on its own line. Preserve table
    # options, independent indexes and extension columns exactly as supplied.
    lines, checks = [], {}
    for line in statement.splitlines():
        tokens = _tokens(line.strip().rstrip(","))
        if tokens and tokens[0] in columns:
            line = "  " + columns[tokens[0]] + ("," if line.rstrip().endswith(",") else "")
        elif tokens[:1] == ["constraint"] and "check" in tokens:
            check = "pc_upgrade_ck_" + digest((token, tokens[1]))[:20]
            checks[check] = line.strip().rstrip(",")
            line = re.sub(
                r"(?i)(CONSTRAINT\s+)(`(?:``|[^`])+`|\w+)", lambda m, check=check: m[1] + _quote(check), line, count=1
            )
        lines.append(line)
    ddl = "\n".join(lines).replace("CREATE TABLE " + _quote(name), "CREATE TABLE " + _quote(temporary), 1)
    evidence.compatibility_rebuild = {
        "table": name,
        "temporary": temporary,
        "original": original,
        "ddl": ddl,
        "source": source,
        "checks": checks,
        "rows": connection.exec_driver_sql("SELECT COUNT(*) FROM " + _quote(name)).scalar_one(),  # noqa: S608 -- quoted identifier
    }
    journal.write(evidence)
    _resume_rebuild(connection, schema, journal, evidence, guard)


def _expand(connection, schema, revision):
    definitions = _definitions(schema, revision)
    required = schema.required(revision)["tables"]
    names = set(inspect(connection).get_table_names())
    for name, (statement, clauses) in definitions.items():
        if name not in names:
            connection.exec_driver_sql(statement, execution_options={"no_parameters": True})
            names.add(name)
            continue
        columns = {column["name"] for column in inspect(connection).get_columns(name)}
        for column, specification in required[name]["columns"].items():
            if column in columns:
                continue
            clause = _column_clause(clauses, column)
            if not specification["nullable"] and specification["default"] is None:
                has_rows = connection.exec_driver_sql("SELECT 1 FROM " + _quote(name) + " LIMIT 1").first()  # noqa: S608 -- quoted frozen identifier
                if has_rows:
                    if name in {"pc_scopes", "pc_scope_bindings", "pc_scope_external_references"} and column.endswith(
                        "_search"
                    ):
                        clause = clause.replace("NOT NULL", "NULL")
                    else:
                        raise MigrationError(
                            "data_conversion_required", f"{name}.{column} needs an explicit conversion."
                        )
            connection.exec_driver_sql("ALTER TABLE " + _quote(name) + " ADD COLUMN " + clause)
    return definitions


def _reconcile(connection, schema, revision, definitions):  # noqa: C901 -- Ordered constraints for one table.
    inventory = mysql_inventory(connection)
    required = schema.required(revision)["tables"]
    for name, target in required.items():
        observed = inventory["tables"][name]
        clauses = definitions[name][1]
        if observed["primary_key"] != target["primary_key"]:
            raise MigrationError("data_conversion_required", f"{name}: primary key needs explicit conversion.")
        for column, expected in target["columns"].items():
            actual = observed["columns"][column]
            if actual == expected:
                continue
            if any(actual[key] != expected[key] for key in ("type", "extra")):
                raise MigrationError("data_conversion_required", f"{name}.{column}: type needs explicit conversion.")
            if any(expected[key] is not None and actual[key] != expected[key] for key in ("charset", "collation")):
                raise MigrationError(
                    "data_conversion_required", f"{name}.{column}: text identity needs explicit conversion."
                )
            if actual["nullable"] != expected["nullable"] or actual["default"] != expected["default"]:
                connection.exec_driver_sql(
                    "ALTER TABLE " + _quote(name) + " MODIFY COLUMN " + _column_clause(clauses, column)
                )
        existing_checks = list(observed["checks"].values())
        if any(value not in target["checks"].values() for value in existing_checks):
            raise MigrationError(
                "data_conversion_required", f"{name}: additional constraints require an explicit conversion."
            )
        for clause in clauses:
            fragment = {"columns": {}, "primary_key": [], "foreign_keys": [], "checks": {}, "indexes": {}}
            _table_clause(fragment, _tokens(clause), {"charset": None, "collation": None}, None)
            missing = (
                any(value not in existing_checks for value in fragment["checks"].values())
                or any(value not in observed["foreign_keys"] for value in fragment["foreign_keys"])
                or any(key not in observed["indexes"] for key in fragment["indexes"])
            )
            if missing:
                connection.exec_driver_sql("ALTER TABLE " + _quote(name) + " ADD " + clause)


def _normalize_identity(connection, schema, revision, journal, evidence, guard):  # noqa: C901 -- Validate every conversion before dropping dependent keys.
    """Upgrade early inherited binary encodings without changing stored identities."""
    inventory = mysql_inventory(connection)
    required = schema.required(revision)["tables"]
    definitions = _definitions(schema, revision)
    changes = {}
    for name, expected in required.items():
        observed = inventory["tables"].get(name)
        if observed is None:
            continue
        for column, target in expected["columns"].items():
            actual = observed["columns"].get(column)
            if (
                actual is None
                or target["charset"] is None
                or (
                    actual["charset"] == target["charset"]
                    and (target["collation"] is None or actual["collation"] == target["collation"])
                )
            ):
                continue
            encoding = actual["charset"]
            if (
                target["charset"] != "utf8mb4"
                or target["collation"] not in {None, "utf8mb4_bin"}
                or encoding not in {"ascii", "latin1", "gbk", "utf8", "utf8mb3", "utf8mb4"}
                or not (actual["collation"] or "").startswith(encoding + "_")
                or actual["type"] != target["type"]
                or actual["extra"] != target["extra"]
            ):
                raise MigrationError("data_conversion_required", f"{name}.{column}: unsupported identity encoding.")
            field = _quote(column)
            if connection.exec_driver_sql(
                "SELECT 1 FROM " + _quote(name) + " WHERE " + field + " IS NOT NULL AND NOT ("  # noqa: S608 -- quoted identifiers and allowlisted encoding
                "CAST(CONVERT(CONVERT(" + field + " USING utf8mb4) USING " + encoding + ") AS BINARY) <=> "
                "CAST(" + field + " AS BINARY)) LIMIT 1"
            ).first():
                raise MigrationError(
                    "data_conversion_required", f"{name}.{column}: identity conversion is not lossless."
                )
            capacity = {"text": 65535, "mediumtext": 16777215}.get(target["type"][0])
            if (
                capacity
                and connection.exec_driver_sql(
                    "SELECT 1 FROM "  # noqa: S608 -- Quoted identifiers and an integer capacity.
                    + _quote(name)
                    + " WHERE OCTET_LENGTH(CONVERT("
                    + field
                    + " USING utf8mb4)) > "
                    + str(capacity)
                    + " LIMIT 1"
                ).first()
            ):
                raise MigrationError("data_conversion_required", f"{name}.{column}: converted text exceeds capacity.")
            clause = _column_clause(definitions[name][1], column)
            if "character" not in _tokens(clause) and "charset" not in _tokens(clause):
                clause = re.sub(
                    r"^(\S+\s+\w+(?:\([^)]*\))?)",
                    r"\1 CHARACTER SET utf8mb4 COLLATE utf8mb4_bin",
                    clause,
                    count=1,
                )
            changes.setdefault(name, {})[column] = clause
    rebuilds = set()
    for name, columns in changes.items():
        ddl = connection.exec_driver_sql("SHOW CREATE TABLE " + _quote(name)).one()[1]
        if set(columns) & {key[0] for key in inventory["tables"][name]["primary_key"]} and re.search(
            r"\bORGANIZATION\s+HEAP\b", ddl, re.IGNORECASE
        ):
            rebuilds.add(name)
    if any(trigger.get("Table") in rebuilds for trigger in inventory["triggers"]):
        raise MigrationError("data_conversion_required", "Historical table triggers require an explicit conversion.")
    foreign_keys = []
    for name in inventory["tables"]:
        for key in inspect(connection).get_foreign_keys(name):
            if not (
                set(key["constrained_columns"]) & changes.get(name, {}).keys()
                or set(key["referred_columns"]) & changes.get(key["referred_table"], {}).keys()
                or name in rebuilds
                or key["referred_table"] in rebuilds
            ):
                continue
            allowed = required.get(name, {}).get("foreign_keys", [])
            known = any(
                value in inventory["tables"][name]["foreign_keys"]
                and value["columns"] == key["constrained_columns"]
                and value["referred_table"] == key["referred_table"]
                and value["referred_columns"] == key["referred_columns"]
                for value in allowed
            )
            legacy = name in {"pc_memory_entry_heads", "pc_memory_entry_versions"} and (
                key["referred_table"] == "pc_artifacts"
                and key.get("referred_schema") in {None, connection.engine.url.database}
                and all(
                    (key.get("options", {}).get(option) or "RESTRICT").upper() == "RESTRICT"
                    for option in ("ondelete", "onupdate")
                )
                and key["constrained_columns"]
                == [
                    "scope_id",
                    "family",
                    "memory_artifact_id",
                    "head_revision" if name == "pc_memory_entry_heads" else "created_in_revision",
                ]
                and key["referred_columns"] == ["scope_id", "family", "artifact_id", "revision"]
            )
            if not known and not legacy:
                raise MigrationError(
                    "data_conversion_required", "An external foreign key needs an identity conversion."
                )
            foreign_keys.append((name, key["name"]))
    # Required foreign keys are restored by reconciliation; the legacy Memory
    # keys are deliberately detached by its registered Atomic conversion.
    for name, key in foreign_keys:
        connection.exec_driver_sql("ALTER TABLE " + _quote(name) + " DROP FOREIGN KEY " + _quote(key))
    for name, columns in changes.items():
        if name in rebuilds:
            _rebuild_identity(connection, schema, name, columns, journal, evidence, guard)
        else:
            connection.exec_driver_sql(
                "ALTER TABLE "
                + _quote(name)
                + " "
                + ", ".join("MODIFY COLUMN " + clause for clause in columns.values())
            )


def apply(connection, schema, configuration, journal, evidence, guard, *, embedding_factory=None):
    def record_counter(pending):
        evidence.compatibility_processing_counter = pending
        journal.write(evidence)

    revision = schema.bundle.source_revision or schema.bundle.revisions[0]
    with recorded_ddl(connection, schema, journal, evidence, guard):
        _resume_rebuild(connection, schema, journal, evidence, guard)
        _normalize_identity(connection, schema, revision, journal, evidence, guard)
        definitions = _expand(connection, schema, revision)
        _, legacy = _parse_table(
            "CREATE TABLE pc_artifact_tags (family VARCHAR(128), CONSTRAINT old_family "
            "CHECK (family IN ('memory', 'experience', 'skill', 'handoff')))"
        )
        current = mysql_inventory(connection)
        for name, check in current["tables"]["pc_artifact_tags"]["checks"].items():
            if check in legacy["checks"].values():
                connection.exec_driver_sql("ALTER TABLE pc_artifact_tags DROP CHECK " + _quote(name))
        historical._scopes(connection)
        historical._processing(
            connection,
            configuration,
            temporary_counter=evidence.compatibility_processing_counter,
            record_counter=record_counter,
        )
        historical.convert_memory(
            connection, schema.bundle, database=_Database(connection, guard), embedding_factory=embedding_factory
        )
        _reconcile(connection, schema, revision, definitions)
        connection.commit()
    schema.verify(mysql_inventory(connection), revision)
    schema.validate_data(connection, revision)
