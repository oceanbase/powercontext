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

"""Recognize the frozen MySQL layouts without importing application metadata.

Only the reviewed linear p0001--p0003 history is supported. DDL recovery accepts
an exact prefix of the next revision, never an arbitrary collection of objects.
The caller owns maintenance evidence, backup, the pinned database lock and the
version row; this module never writes or stamps a revision.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection
from sqlalchemy.exc import DBAPIError

from .bundle import MigrationBundle
from .models import MigrationError, digest

_LEXEME = re.compile(r"\s+|`(?:``|[^`])*`|'(?:''|\\.|[^'\\])*'|[A-Za-z_][A-Za-z_0-9$]*|[0-9]+|>=|<=|<>|!=|[(),.=<>+-]")
_REVISIONS = ("p0001", "p0002", "p0003")
_CITATION_TABLES = ("pc_artifacts", "pc_artifact_candidate_versions")
_FAMILY_CHECK = "ck_pc_artifact_tags_family"
_CONTROL_TABLE = "pc_schema_revision"
_PHYSICAL_VALUES = frozenset({
    "row_format",
    "compression",
    "replica_num",
    "block_size",
    "key_block_size",
    "use_bloom_filter",
    "enable_macro_block_bloom_filter",
    "tablet_size",
    "pctfree",
    "auto_increment",
})


@dataclass(frozen=True)
class MySQLRecovery:
    revision: str
    pending_statements: tuple[str, ...]

    @property
    def target_revision(self) -> str:
        return self.revision

    @property
    def completed_revision(self) -> str | None:
        """A version may advance only after the entire postcondition is present."""
        return self.revision if not self.pending_statements else None


def mysql_inventory(connection: Connection, *, exclude: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Reflect tables, views and triggers on the caller's existing connection.

    SHOW CREATE preserves constraints, index prefixes and enforcement that a
    partial column reflection can lose. Unknown syntax is rejected. Physical
    placement/compression options do not define the application's schema.
    """
    if connection.dialect.name != "mysql":
        raise MigrationError("unsupported_backend", "MySQL schema reflection needs a MySQL-compatible connection.")
    try:
        database = connection.exec_driver_sql("SELECT DATABASE()").scalar_one()
        if not isinstance(database, str) or not database:
            raise MigrationError("incompatible_schema", "A current database must be selected for schema reflection.")
        inventory = _empty_inventory()
        _reflect_tables(connection, database, exclude, inventory)
        # Do not hide a trigger merely because it writes an excluded version
        # table: it would still change the migration's authoritative state.
        inventory["triggers"] = sorted(
            [
                {field: row[field] for field in ("Trigger", "Table", "Timing", "Event", "Statement")}
                for row in connection.exec_driver_sql("SHOW TRIGGERS").mappings()
            ],
            key=lambda row: str(row.get("Trigger", "")),
        )
        inventory["views"].sort()
    except DBAPIError as error:
        raise MigrationError(
            "schema_reflection_failed", "Complete database schema reflection is unavailable."
        ) from error
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise MigrationError(
            "incompatible_schema", "A schema definition could not be completely interpreted."
        ) from error
    else:
        return inventory


def _reflect_tables(connection: Connection, database: str, exclude: frozenset[str], inventory: dict[str, Any]) -> None:
    for name, kind in connection.exec_driver_sql("SHOW FULL TABLES"):
        if kind == "VIEW":
            inventory["views"].append(name)
        elif kind != "BASE TABLE":
            raise MigrationError("incompatible_schema", "An unsupported database object was discovered.")
        elif name not in exclude:
            row = connection.exec_driver_sql(f"SHOW CREATE TABLE {_quote(name)}").first()
            if row is None or not isinstance(row[1], str):
                raise MigrationError("incompatible_schema", "A table definition could not be reflected.")
            reflected_name, table = _parse_table(row[1], database=database)
            if reflected_name != name:
                raise MigrationError("incompatible_schema", "A reflected table identity is inconsistent.")
            inventory["tables"][name] = table


def read_revision(connection: Connection, inventory: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate Alembic's only control table before reading its zero/one head."""
    _business_inventory(inventory)
    if _CONTROL_TABLE not in inventory["tables"]:
        return False, None
    rows = connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision LIMIT 2").all()
    if len(rows) > 1 or (rows and rows[0][0] not in _REVISIONS):
        raise MigrationError("incompatible_schema", "The database has multiple or unknown migration heads.")
    return True, rows[0][0] if rows else None


class MySQLSchema:
    """Validate source, intermediate and post-DDL shapes from frozen resources."""

    def __init__(self, bundle: MigrationBundle) -> None:
        if bundle.revisions != _REVISIONS:
            raise MigrationError("invalid_bundle", "MySQL schema recognition does not cover this revision chain.")
        self.bundle = bundle
        baseline = json.loads((bundle.snapshot_directory / "schemas/pre_dream.json").read_text())["mysql"]
        current = json.loads((bundle.snapshot_directory / "schemas/v1_1_0.json").read_text())["mysql"]
        self._statements = {
            "p0001": tuple(baseline),
            "p0002": tuple(f"ALTER TABLE {table} ADD COLUMN memory_citations MEDIUMBLOB" for table in _CITATION_TABLES),
            "p0003": (f"ALTER TABLE pc_artifact_tags DROP CHECK {_FAMILY_CHECK}",),
        }
        inventory = _empty_inventory()
        self._prefixes: dict[str, list[dict[str, Any]]] = {}
        self._schemas: dict[str, dict[str, Any]] = {}
        for revision in _REVISIONS:
            prefixes = [copy.deepcopy(inventory)]
            for statement in self._statements[revision]:
                _apply_statement(inventory, statement)
                prefixes.append(copy.deepcopy(inventory))
            self._prefixes[revision] = prefixes
            self._schemas[revision] = copy.deepcopy(inventory)
        frozen_current = _empty_inventory()
        for statement in current:
            _apply_statement(frozen_current, statement)
        if frozen_current != self._schemas["p0003"]:
            raise MigrationError("invalid_bundle", "Frozen MySQL layouts disagree with the registered revision steps.")

    @staticmethod
    def fingerprint(inventory: dict[str, Any]) -> str:
        return digest(inventory)

    def identify(self, inventory: dict[str, Any]) -> str | None:
        """Only complete known shapes may be adopted as an unversioned source."""
        inventory = _business_inventory(inventory)
        if inventory == _empty_inventory():
            return None
        for revision, expected in self._schemas.items():
            if _matches(inventory, expected):
                return revision
        raise MigrationError("incompatible_schema", "Actual MySQL schema is not a registered complete layout.")

    def verify(self, inventory: dict[str, Any], revision: str | None) -> None:
        inventory = _business_inventory(inventory)
        expected = _empty_inventory() if revision is None else self._schemas.get(revision)
        if expected is None or not _matches(inventory, expected):
            raise MigrationError("incompatible_schema", "Actual MySQL schema does not match its recorded revision.")

    def recovery(self, inventory: dict[str, Any], revision: str | None) -> MySQLRecovery:
        """Return only missing DDL in an exact prefix of the next revision.

        A runner must require matching active evidence before accepting a
        partially executed revision. A recognized postcondition is necessary
        for advancing the version row, and is not itself recovery authorization.
        """
        inventory = _business_inventory(inventory)
        if revision is None:
            target = _REVISIONS[0]
        elif revision not in _REVISIONS or revision == _REVISIONS[-1]:
            raise MigrationError("incompatible_schema", "There is no registered next MySQL revision to recover.")
        else:
            target = _REVISIONS[_REVISIONS.index(revision) + 1]
        for position, expected in enumerate(self._prefixes[target]):
            if _matches(inventory, expected):
                return MySQLRecovery(target, self._statements[target][position:])
        raise MigrationError("incompatible_schema", "Actual MySQL schema is not a controlled next-revision DDL state.")

    def validate_data(self, connection: Connection, revision: str) -> None:
        """Check stored CHECK/FK invariants before adoption or version advance.

        Enabling foreign_key_checks does not validate existing rows. The queries
        return at most one existence marker, never application payloads. They
        can scan tables and belong inside the offline maintenance window.
        """
        expected = self._schemas.get(revision)
        if expected is None:
            raise MigrationError("incompatible_schema", "Cannot validate data for an unknown MySQL revision.")
        for name, table in expected["tables"].items():
            for check in table["checks"].values():
                expression = _expression_sql(check["expression"])
                violation = connection.exec_driver_sql(
                    f"SELECT 1 FROM {_quote(name)} WHERE NOT ({expression}) LIMIT 1"  # noqa: S608 -- frozen expressions
                ).first()
                if violation is not None:
                    raise MigrationError("data_integrity_error", "Existing rows violate a registered CHECK constraint.")
            for foreign_key in table["foreign_keys"]:
                columns = foreign_key["columns"]
                referred = foreign_key["referred_columns"]
                nonnull = " AND ".join(f"s.{_quote(column)} IS NOT NULL" for column in columns)
                equality = " AND ".join(
                    f"s.{_quote(left)} = r.{_quote(right)}" for left, right in zip(columns, referred, strict=True)
                )
                # A FK-backed outer join can hide historical orphans on seekdb
                # after enforcement is re-enabled; use a correlated existence
                # check rather than trusting that already-declared constraint.
                violation = connection.exec_driver_sql(
                    f"SELECT 1 FROM {_quote(name)} AS s WHERE {nonnull} AND NOT EXISTS "  # noqa: S608 -- frozen, quoted identifiers
                    f"(SELECT 1 FROM {_quote(foreign_key['referred_table'])} AS r WHERE {equality}) LIMIT 1"
                ).first()
                if violation is not None:
                    raise MigrationError("data_integrity_error", "Existing rows violate a registered foreign key.")


def _empty_inventory() -> dict[str, Any]:
    return {"tables": {}, "views": [], "triggers": []}


def _business_inventory(inventory: dict[str, Any]) -> dict[str, Any]:
    table = inventory["tables"].get(_CONTROL_TABLE)
    if table is None:
        return inventory
    _, expected = _parse_table(
        "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) NOT NULL, PRIMARY KEY (version_num))"
    )
    if any(trigger.get("Table") == _CONTROL_TABLE for trigger in inventory["triggers"]) or not _matches(
        {"tables": {_CONTROL_TABLE: table}, "views": [], "triggers": []},
        {"tables": {_CONTROL_TABLE: expected}, "views": [], "triggers": []},
    ):
        raise MigrationError("incompatible_schema", "The migration version table has an unsupported definition.")
    business = copy.deepcopy(inventory)
    del business["tables"][_CONTROL_TABLE]
    return business


def _quote(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _tokens(statement: str) -> list[str]:
    tokens: list[str] = []
    position = 0
    for match in _LEXEME.finditer(statement):
        if match.start() != position:
            raise MigrationError("incompatible_schema", "An unsupported schema definition lexeme was discovered.")
        position = match.end()
        value = match.group()
        if value.isspace():
            continue
        if value.startswith("`"):
            value = value[1:-1].replace("``", "`")
            tokens.append(value)
        else:
            tokens.append(value if value.startswith("'") else value.lower())
    if position != len(statement):
        raise MigrationError("incompatible_schema", "An unsupported schema definition lexeme was discovered.")
    return tokens


def _group(tokens: list[str], position: int) -> tuple[list[str], int]:
    if position >= len(tokens) or tokens[position] != "(":
        raise MigrationError("incompatible_schema", "A schema definition is missing a parenthesized group.")
    depth = 0
    for end in range(position, len(tokens)):
        lexeme = tokens[end]
        if lexeme == "(":
            depth += 1
        elif lexeme == ")":
            depth -= 1
            if depth == 0:
                return tokens[position + 1 : end], end + 1
    raise MigrationError("incompatible_schema", "A schema definition has unbalanced parentheses.")


def _parts(tokens: list[str]) -> list[list[str]]:
    parts: list[list[str]] = []
    current: list[str] = []
    depth = 0
    for lexeme in tokens:
        if lexeme == "," and depth == 0:
            parts.append(current)
            current = []
        else:
            current.append(lexeme)
            depth += (lexeme == "(") - (lexeme == ")")
    parts.append(current)
    if any(not part for part in parts):
        raise MigrationError("incompatible_schema", "A schema definition has an empty clause.")
    return parts


def _physical(tokens: list[str]) -> dict[str, str | None]:
    options: dict[str, str | None] = {"charset": None, "collation": None}
    position = 0
    while position < len(tokens):
        lexeme = tokens[position]
        if lexeme in {"default", "global", "local", "visible"}:
            position += 1
            continue
        if lexeme == "character" and tokens[position + 1 : position + 2] == ["set"]:
            lexeme = "charset"
            position += 1
        if lexeme in {"charset", "collate", "engine", "using", "organization", *_PHYSICAL_VALUES}:
            position += 1
            if tokens[position : position + 1] == ["="]:
                position += 1
            if position >= len(tokens):
                raise MigrationError("incompatible_schema", "A schema option is missing its value.")
            value = tokens[position]
            _validate_physical_value(lexeme, value)
            if lexeme in {"charset", "collate"}:
                options["charset" if lexeme == "charset" else "collation"] = value
            position += 1
            continue
        raise MigrationError("incompatible_schema", "An unsupported schema option was discovered.")
    return options


def _validate_physical_value(option: str, value: str) -> None:
    supported = {"engine": {"innodb"}, "using": {"btree"}, "organization": {"index", "heap"}}
    if option in supported and value not in supported[option]:
        raise MigrationError("incompatible_schema", "An unsupported table engine or index organization was discovered.")


def _column(tokens: list[str], defaults: dict[str, str | None]) -> dict[str, Any]:
    if len(tokens) < 2:
        raise MigrationError("incompatible_schema", "A column definition is incomplete.")
    kind = {"integer": "int"}.get(tokens[1], tokens[1])
    position = 2
    arguments: list[str] = []
    if tokens[position : position + 1] == ["("]:
        arguments, position = _group(tokens, position)
    if kind in {"int", "bigint"}:
        if arguments and (len(arguments) != 1 or not arguments[0].isdigit()):
            raise MigrationError("incompatible_schema", "An integer column has unsupported arguments.")
        arguments = []
    if kind == "datetime" and arguments == ["0"]:
        arguments = []
    column: dict[str, Any] = {
        "type": [kind, arguments],
        "nullable": True,
        "default": None,
        "charset": None,
        "collation": None,
        "extra": [],
    }
    if kind in {"varchar", "char", "mediumtext", "text"}:
        column["charset"] = defaults["charset"]
        column["collation"] = defaults["collation"]
    _column_modifiers(tokens, position, column)
    if column["collation"] and not column["charset"]:
        column["charset"] = column["collation"].split("_", 1)[0]
    return column


def _column_character_option(tokens: list[str], position: int) -> tuple[str, str, int]:
    option = tokens[position]
    if option == "character":
        if tokens[position + 1 : position + 2] != ["set"]:
            raise MigrationError("incompatible_schema", "An unsupported character column option was discovered.")
        position += 1
    position += 1
    if position >= len(tokens):
        raise MigrationError("incompatible_schema", "A character column option has no value.")
    return ("collation" if option == "collate" else "charset"), tokens[position], position + 1


def _column_default(tokens: list[str], position: int, kind: str) -> tuple[list[Any] | None, int]:
    position += 1
    if tokens[position : position + 1] == ["_utf8mb4"]:
        position += 1
    if position >= len(tokens):
        raise MigrationError("incompatible_schema", "A column default has no value.")
    value = tokens[position]
    if value == "null":
        default = None
    elif value.startswith("'"):
        literal = _literal(value)
        default = ["number", int(literal)] if kind in {"int", "bigint"} and literal.isdigit() else ["literal", literal]
    elif value.isdigit():
        default = ["number", int(value)]
    else:
        raise MigrationError("incompatible_schema", "An unsupported column default was discovered.")
    return default, position + 1


def _column_modifiers(tokens: list[str], position: int, column: dict[str, Any]) -> None:
    while position < len(tokens):
        lexeme = tokens[position]
        if lexeme == "not" and tokens[position + 1 : position + 2] == ["null"]:
            column["nullable"] = False
            position += 2
        elif lexeme == "null":
            position += 1
        elif lexeme in {"character", "charset", "collate"}:
            option, value, position = _column_character_option(tokens, position)
            column[option] = value
        elif lexeme == "default":
            column["default"], position = _column_default(tokens, position, column["type"][0])
        else:
            column["extra"].append(lexeme)
            position += 1


def _key_columns(tokens: list[str]) -> list[list[Any]]:
    columns: list[list[Any]] = []
    for part in _parts(tokens):
        position = 1
        prefix = None
        if part[position : position + 1] == ["("]:
            length, position = _group(part, position)
            if len(length) != 1 or not length[0].isdigit():
                raise MigrationError("incompatible_schema", "An unsupported index prefix was discovered.")
            prefix = int(length[0])
        order = "asc"
        if part[position : position + 1] in (["asc"], ["desc"]):
            order = part[position]
            position += 1
        if position != len(part) or not re.fullmatch(r"[a-z_][a-z_0-9$]*", part[0]):
            raise MigrationError("incompatible_schema", "An unsupported index key was discovered.")
        columns.append([part[0], prefix, order])
    return columns


def _qualified(tokens: list[str], position: int, database: str | None) -> tuple[str | None, str, int]:
    name = tokens[position]
    position += 1
    schema = None
    if tokens[position : position + 1] == ["."]:
        schema = name
        name = tokens[position + 1]
        position += 2
    return (None if schema == database else schema), name, position


def _foreign_key(part: list[str], database: str | None) -> dict[str, Any]:
    columns, end = _group(part, 2)
    if part[end : end + 1] != ["references"]:
        raise MigrationError("incompatible_schema", "A foreign key has no referenced table.")
    schema, referred, end = _qualified(part, end + 1, database)
    referred_columns, end = _group(part, end)
    local_keys = _key_columns(columns)
    referred_keys = _key_columns(referred_columns)
    if len(local_keys) != len(referred_keys) or any(
        prefix is not None or order != "asc" for _, prefix, order in [*local_keys, *referred_keys]
    ):
        raise MigrationError("incompatible_schema", "A foreign key has unsupported key columns.")
    actions = {"delete": "restrict", "update": "restrict"}
    while end < len(part):
        if part[end] != "on" or part[end + 1] not in actions:
            raise MigrationError("incompatible_schema", "An unsupported foreign key option was discovered.")
        action = part[end + 1]
        end += 2
        value = part[end]
        end += 1
        if value in {"no", "set"}:
            value += " " + part[end]
            end += 1
        actions[action] = "restrict" if value == "no action" else value
    return {
        "columns": [column[0] for column in local_keys],
        "referred_schema": schema,
        "referred_table": referred,
        "referred_columns": [column[0] for column in referred_keys],
        "ondelete": actions["delete"],
        "onupdate": actions["update"],
    }


def _parse_table(statement: str, *, database: str | None = None) -> tuple[str, dict[str, Any]]:
    tokens = _tokens(statement)
    if tokens[:2] != ["create", "table"]:
        raise MigrationError("incompatible_schema", "An unsupported table definition was discovered.")
    schema, name, position = _qualified(tokens, 2, database)
    if schema is not None:
        raise MigrationError("incompatible_schema", "A table belongs to an unexpected database.")
    body, position = _group(tokens, position)
    defaults = _physical(tokens[position:])
    table: dict[str, Any] = {"columns": {}, "primary_key": [], "foreign_keys": [], "checks": {}, "indexes": {}}
    for part in _parts(body):
        _table_clause(table, part, defaults, database)
    table["foreign_keys"].sort(key=digest)
    return name, table


def _put_named(objects: dict[str, Any], name: str, definition: dict[str, Any]) -> None:
    if name in objects:
        raise MigrationError("incompatible_schema", "Duplicate schema object names were discovered.")
    objects[name] = definition


def _check_definition(part: list[str], constraint: str | None) -> tuple[str, dict[str, Any]]:
    expression, end = _group(part, 1)
    if constraint is None or part[end:] not in ([], ["enforced"], ["not", "enforced"]):
        raise MigrationError("incompatible_schema", "An unsupported CHECK definition was discovered.")
    return constraint, {"expression": _Expression(expression).parse(), "enforced": part[end:] != ["not", "enforced"]}


def _index_definition(part: list[str]) -> tuple[str, dict[str, Any]]:
    unique = part[0] == "unique"
    offset = 2 if unique and part[1] in {"key", "index"} else 1
    index_name = part[offset]
    columns, end = _group(part, offset + 1)
    _physical(part[end:])
    return index_name, {"columns": _key_columns(columns), "unique": unique}


def _table_clause(
    table: dict[str, Any], part: list[str], defaults: dict[str, str | None], database: str | None
) -> None:
    constraint = None
    if part[0] == "constraint":
        constraint = part[1]
        part = part[2:]
    if part[:2] == ["primary", "key"]:
        columns, end = _group(part, 2)
        _physical(part[end:])
        if table["primary_key"]:
            raise MigrationError("incompatible_schema", "Duplicate primary keys were discovered.")
        table["primary_key"] = _key_columns(columns)
    elif part[:2] == ["foreign", "key"]:
        table["foreign_keys"].append(_foreign_key(part, database))
    elif part[0] == "check":
        constraint, definition = _check_definition(part, constraint)
        _put_named(table["checks"], constraint, definition)
    elif part[0] in {"key", "index", "unique"}:
        index_name, definition = _index_definition(part)
        _put_named(table["indexes"], index_name, definition)
    elif constraint is None:
        _put_named(table["columns"], part[0], _column(part, defaults))
    else:
        raise MigrationError("incompatible_schema", "An unsupported constraint definition was discovered.")


def _apply_statement(inventory: dict[str, Any], statement: str) -> None:
    tokens = _tokens(statement)
    tables = inventory["tables"]
    if tokens[:2] == ["create", "table"]:
        name, table = _parse_table(statement)
        if name in tables:
            raise MigrationError("invalid_bundle", "A frozen table is declared more than once.")
        tables[name] = table
    elif tokens[:2] == ["create", "index"] and tokens[3:4] == ["on"]:
        columns, end = _group(tokens, 5)
        if end != len(tokens) or tokens[2] in tables[tokens[4]]["indexes"]:
            raise MigrationError("invalid_bundle", "An unsupported frozen index was discovered.")
        tables[tokens[4]]["indexes"][tokens[2]] = {"columns": _key_columns(columns), "unique": False}
    elif tokens[:2] == ["alter", "table"] and tokens[3:5] == ["add", "column"]:
        table = tables[tokens[2]]
        if tokens[5] in table["columns"]:
            raise MigrationError("invalid_bundle", "A frozen column is declared more than once.")
        table["columns"][tokens[5]] = _column(tokens[5:], {"charset": None, "collation": None})
    elif tokens[:2] == ["alter", "table"] and tokens[3:5] == ["drop", "check"] and len(tokens) == 6:
        del tables[tokens[2]]["checks"][tokens[5]]
    else:
        raise MigrationError("invalid_bundle", "A frozen MySQL revision operation is not recognized.")


def _matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    normalized = copy.deepcopy(actual)
    if set(normalized) != set(expected) or set(normalized["tables"]) != set(expected["tables"]):
        return False
    for name, table in normalized["tables"].items():
        expected_table = expected["tables"][name]
        if set(table["columns"]) != set(expected_table["columns"]):
            return False
        for column, specification in table["columns"].items():
            # The frozen SQL inherits backend defaults for display text only.
            # Identity columns explicitly require utf8mb4_bin and remain exact.
            for option in ("charset", "collation"):
                if expected_table["columns"][column][option] is None:
                    specification[option] = None
        automatic_keys: set[tuple[str, ...]] = set()
        for index_name, index in tuple(table["indexes"].items()):
            if index_name in expected_table["indexes"] or index["unique"]:
                continue
            columns = tuple(key[0] for key in index["columns"])
            foreign_columns = {tuple(key["columns"]) for key in table["foreign_keys"]}
            if (
                columns in foreign_columns
                and columns not in automatic_keys
                and all(prefix is None and order == "asc" for _, prefix, order in index["columns"])
            ):
                # MySQL can generate one child-key index; OceanBase need not.
                # Ignore its generated name, not arbitrary prefixes/unique keys.
                del table["indexes"][index_name]
                automatic_keys.add(columns)
    return normalized == expected


def _literal(lexeme: str) -> str:
    value = lexeme[1:-1].replace("''", "'")
    escapes = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a"}
    return re.sub(r"\\(.)", lambda match: escapes.get(match[1], match[1]), value)


class _Expression:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.position = 0

    def parse(self) -> list[Any]:
        value = self._expression(0)
        if self.position != len(self.tokens):
            raise MigrationError("incompatible_schema", "An unsupported CHECK expression was discovered.")
        return value

    def _expression(self, precedence: int) -> list[Any]:
        left = self._value()
        priorities = {"or": 1, "and": 2, "=": 3, ">": 3, "<": 3, ">=": 3, "<=": 3, "!=": 3, "<>": 3, "is": 3, "in": 3}
        while self.position < len(self.tokens):
            operator = self.tokens[self.position]
            priority = priorities.get(operator, 0)
            if priority <= precedence:
                break
            self.position += 1
            if operator == "is":
                negate = self.tokens[self.position : self.position + 1] == ["not"]
                self.position += int(negate)
                if self.tokens[self.position : self.position + 1] != ["null"]:
                    raise MigrationError("incompatible_schema", "An unsupported CHECK predicate was discovered.")
                self.position += 1
                left = ["is_not_null" if negate else "is_null", left]
            elif operator == "in":
                values, self.position = _group(self.tokens, self.position)
                left = ["in", left, [_Expression(part).parse() for part in _parts(values)]]
            else:
                left = ["binary", operator, left, self._expression(priority)]
        return left

    def _value(self) -> list[Any]:
        if self.position >= len(self.tokens):
            raise MigrationError("incompatible_schema", "A CHECK expression is incomplete.")
        lexeme = self.tokens[self.position]
        self.position += 1
        if lexeme == "(":
            value = self._expression(0)
            if self.tokens[self.position : self.position + 1] != [")"]:
                raise MigrationError("incompatible_schema", "A CHECK expression has unbalanced parentheses.")
            self.position += 1
            return value
        if lexeme == "_utf8mb4":
            if self.position >= len(self.tokens) or not self.tokens[self.position].startswith("'"):
                raise MigrationError("incompatible_schema", "A CHECK expression has an unsupported literal introducer.")
            lexeme = self.tokens[self.position]
            self.position += 1
        if lexeme.startswith("'"):
            return ["literal", _literal(lexeme)]
        if lexeme.isdigit():
            return ["number", int(lexeme)]
        if lexeme == "null":
            return ["null"]
        if not re.fullmatch(r"[a-z_][a-z_0-9$]*", lexeme):
            raise MigrationError("incompatible_schema", "An unsupported CHECK value was discovered.")
        return ["column", lexeme]


def _expression_sql(expression: list[Any]) -> str:
    kind = expression[0]
    if kind == "column":
        return _quote(expression[1])
    if kind == "literal":
        return "'" + expression[1].replace("'", "''") + "'"
    if kind == "number":
        return str(expression[1])
    if kind == "null":
        return "NULL"
    if kind in {"is_null", "is_not_null"}:
        return f"({_expression_sql(expression[1])} IS {'NOT ' if kind == 'is_not_null' else ''}NULL)"
    if kind == "in":
        return f"({_expression_sql(expression[1])} IN ({', '.join(_expression_sql(value) for value in expression[2])}))"
    if kind == "binary":
        return f"({_expression_sql(expression[2])} {expression[1].upper()} {_expression_sql(expression[3])})"
    raise MigrationError("invalid_bundle", "An unsupported frozen CHECK expression was discovered.")
