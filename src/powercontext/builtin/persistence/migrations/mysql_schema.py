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

Registered resources describe the complete linear history. DDL recovery accepts
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

_LEXEME = re.compile(
    r"\s+|`(?:``|[^`])*`|'(?:''|\\.|[^'\\])*'|[A-Za-z_][A-Za-z_0-9$]*|[0-9]+|>=|<=|<>|!=|[(),.=<>+*/%\^&|~!?:@-]"
)

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
    "fts_index_type",  # OceanBase 4.6 reflects its server-selected FULLTEXT storage type.
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
        if "pc_artifact_vector_spaces" in inventory["tables"]:
            from sqlalchemy import select

            from powercontext.builtin.persistence.tables import ARTIFACT_VECTOR_SPACES_TABLE

            declarations = (
                connection
                .execute(select(ARTIFACT_VECTOR_SPACES_TABLE).order_by(ARTIFACT_VECTOR_SPACES_TABLE.c.space_id))
                .mappings()
                .all()
            )
            if declarations:
                inventory["artifact_spaces"] = [dict(space) for space in declarations]
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
            try:
                reflected_name, table = _parse_table(row[1], database=database)
            except MigrationError as error:
                detail = str(error).removeprefix(f"{error.code}: ")
                raise MigrationError(error.code, f"Table {name!r}: {detail}") from error
            if reflected_name != name:
                raise MigrationError("incompatible_schema", "A reflected table identity is inconsistent.")
            inventory["tables"][name] = table


def read_revision(
    connection: Connection, inventory: dict[str, Any], *, revisions: tuple[str, ...]
) -> tuple[bool, str | None]:
    """Validate Alembic's only control table before reading its zero/one head."""
    _business_inventory(inventory)
    if _CONTROL_TABLE not in inventory["tables"]:
        return False, None
    rows = connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision LIMIT 2").all()
    if len(rows) > 1 or (rows and rows[0][0] not in revisions):
        raise MigrationError("incompatible_schema", "The database has multiple or unknown migration heads.")
    return True, rows[0][0] if rows else None


class MySQLSchema:
    """Validate source, intermediate and post-DDL shapes from frozen resources."""

    def __init__(  # noqa: C901 -- Keep ordered admission checks with their operation.
        self, bundle: MigrationBundle, *, embedded_code: bool = False, allow_declared_spaces: bool = False
    ) -> None:
        self.allow_declared_spaces = allow_declared_spaces
        self.bundle = bundle
        self.revisions = bundle.revisions
        manifest = json.loads((bundle.snapshot_directory / "manifest.json").read_text())
        self.optional_table_options = {}
        self.affected_objects = bundle.affected_objects
        if "mysql_state_layouts" in manifest:
            self._load_state_layouts(manifest["mysql_state_layouts"])
            if embedded_code:
                self._extend_embedded_code(manifest)
            if bundle.vector_profile is not None:
                self._extend_vectors()
            return
        if embedded_code:
            raise MigrationError("invalid_bundle", "Embedded Code needs a registered complete Server layout.")
        self.bundle = bundle
        if not bundle.mysql_layouts:
            raise MigrationError("invalid_bundle", "MySQL layout steps are not registered in this bundle.")
        self._statements: dict[str, tuple[str, ...]] = {}
        checkpoints: dict[str, str] = {}
        for resource in bundle.mysql_layouts:
            layouts = json.loads((bundle.snapshot_directory / resource).read_text(encoding="utf-8"))
            if (
                not isinstance(layouts, dict)
                or layouts.get("format") != "powercontext.mysql-layouts.v1"
                or not isinstance(layouts.get("revisions"), dict)
                or not isinstance(layouts.get("checkpoints"), dict)
                or set(layouts["revisions"]) & self._statements.keys()
                or set(layouts["checkpoints"]) & checkpoints.keys()
                or any(
                    not isinstance(steps, list) or any(not isinstance(step, str) or not step.strip() for step in steps)
                    for steps in layouts["revisions"].values()
                )
                or any(
                    not isinstance(name, str) or name not in bundle.resources
                    for name in layouts["checkpoints"].values()
                )
            ):
                raise MigrationError("invalid_bundle", "MySQL layout registration is invalid or duplicated.")
            self._statements.update({revision: tuple(steps) for revision, steps in layouts["revisions"].items()})
            checkpoints.update(layouts["checkpoints"])
        if set(self._statements) != set(bundle.revisions) or not set(checkpoints) <= set(bundle.revisions):
            raise MigrationError("invalid_bundle", "MySQL layout registration does not cover the revision chain.")
        inventory = _empty_inventory()
        self._prefixes: dict[str, list[dict[str, Any]]] = {}
        self._schemas: dict[str, dict[str, Any]] = {}
        for revision in bundle.revisions:
            prefixes = [copy.deepcopy(inventory)]
            for statement in self._statements[revision]:
                _apply_statement(inventory, statement)
                prefixes.append(copy.deepcopy(inventory))
            self._prefixes[revision] = prefixes
            self._schemas[revision] = copy.deepcopy(inventory)
        for revision, resource in checkpoints.items():
            frozen = _empty_inventory()
            for statement in json.loads((bundle.snapshot_directory / resource).read_text(encoding="utf-8"))["mysql"]:
                _apply_statement(frozen, statement)
            if frozen != self._schemas[revision]:
                raise MigrationError(
                    "invalid_bundle", "Frozen MySQL layouts disagree with the registered revision steps."
                )

    def _load_state_layouts(self, resources: list[str]) -> None:  # noqa: C901 -- Keep ordered admission checks with their operation.
        registered = {"revisions": {}}
        for resource in resources:
            if resource not in self.bundle.resources:
                raise MigrationError("invalid_bundle", "A MySQL state layout is outside the frozen bundle.")
            fragment = json.loads((self.bundle.snapshot_directory / resource).read_text())
            if (
                fragment.get("format") != "powercontext.mysql-state-layouts.v1"
                or set(fragment["revisions"]) & registered["revisions"].keys()
            ):
                raise MigrationError("invalid_bundle", "A MySQL state layout is invalid or duplicated.")
            options = fragment.get("optional_table_options", {})
            if self.optional_table_options.keys() & options.keys():
                raise MigrationError("invalid_bundle", "A MySQL layout overrides an earlier option registration.")
            self.optional_table_options.update(options)
            registered["revisions"].update(fragment["revisions"])
        if set(registered["revisions"]) != set(self.bundle.revisions):
            raise MigrationError("invalid_bundle", "MySQL state layouts must cover exactly the shared revision chain.")
        inventory = _empty_inventory()
        self._statements = {}
        self._prefixes = {}
        self._schemas = {}
        for revision in self.bundle.revisions:
            entry = registered["revisions"][revision]
            statements, changes = entry["statements"], entry["changes"]
            if (
                not statements
                or len(statements) != len(changes)
                or any(not isinstance(sql, str) or not sql.strip() for sql in statements)
            ):
                raise MigrationError("invalid_bundle", "Each MySQL operation requires its exact schema transition.")
            prefixes = [copy.deepcopy(inventory)]
            for change in changes:
                for name, table in change.items():
                    if table is None:
                        if name not in inventory["tables"]:
                            raise MigrationError("invalid_bundle", "A transition removes an unknown table.")
                        del inventory["tables"][name]
                    else:
                        inventory["tables"][name] = table
                prefixes.append(copy.deepcopy(inventory))
            self._statements[revision] = tuple(statements)
            self._prefixes[revision] = prefixes
            self._schemas[revision] = copy.deepcopy(inventory)

    def execute(self, connection: Connection, statement: str, configuration: dict[str, Any]) -> None:
        if statement.startswith("@"):
            import runpy

            resource, name = statement.removeprefix("@").split(":", 1)
            if resource not in self.bundle.resources:
                raise MigrationError("invalid_bundle", "A conditional MySQL operation needs a frozen implementation.")
            operation = runpy.run_path(str(self.bundle.snapshot_directory / resource))["execute"]
            operation(connection, "@" + name, configuration)
        else:
            connection.exec_driver_sql(statement, execution_options={"no_parameters": True})

    def _extend_embedded_code(self, manifest: dict[str, Any]) -> None:
        resource = manifest.get("mysql_schema_extensions", {}).get("seekdb_code")
        if resource not in self.bundle.resources:
            raise MigrationError("invalid_bundle", "Embedded Code has no frozen schema registration.")
        profile = json.loads((self.bundle.snapshot_directory / resource).read_text(encoding="utf-8"))
        revision = profile["source_revision"]
        statements = profile["statements"]
        tables = profile["tables"]
        if (
            profile.get("format") != "powercontext.mysql-schema-extension.v1"
            or profile.get("backend") != "seekdb"
            or revision != self.revisions[0]
            or not statements
            or dict(_parse_table(sql) for sql in statements) != tables
            or any(set(tables) & set(state["tables"]) for state in self._schemas.values())
        ):
            raise MigrationError("invalid_bundle", "The embedded Code schema registration is inconsistent.")
        # Preserve the released Code tables through every core transition. A new
        # database creates the same registered extension in the baseline revision.
        state = copy.deepcopy(self._schemas[revision])
        for statement in statements:
            name, table = _parse_table(statement)
            state["tables"][name] = table
            self._prefixes[revision].append(copy.deepcopy(state))
        self._statements[revision] += tuple(statements)
        self._schemas[revision] = state
        for later in self.revisions[1:]:
            for prefix in self._prefixes[later]:
                prefix["tables"].update(copy.deepcopy(tables))
            self._schemas[later]["tables"].update(copy.deepcopy(tables))
        self.affected_objects = (*self.affected_objects, *tables)

    @staticmethod
    def fingerprint(inventory: dict[str, Any]) -> str:
        return digest(inventory)

    def _extend_vectors(self) -> None:
        profile = self.bundle.vector_profile
        if profile is None:
            raise MigrationError("invalid_bundle", "Vector schema selection needs an exact profile.")
        definitions = profile.mysql_tables()
        self.optional_table_options.update(profile.layout["mysql_optional_table_options"])
        tables = dict(_parse_table(sql) for sql in definitions.values())
        atomic_name = "pc_atomic_memory_current"
        atomic = tables.pop(atomic_name)
        source, target, *later = self.revisions
        for revision in self.revisions:
            for state in [*self._prefixes[revision], self._schemas[revision]]:
                if atomic_name in state["tables"]:
                    columns = state["tables"][atomic_name]["columns"]
                    state["tables"][atomic_name] = copy.deepcopy(atomic)
                    # Retain columns introduced after the frozen vector profile
                    # while preserving its released vector and charset metadata.
                    state["tables"][atomic_name]["columns"].update({
                        name: copy.deepcopy(column) for name, column in columns.items() if name not in atomic["columns"]
                    })
                if revision != source:
                    state["tables"].update(copy.deepcopy(tables))
        self._statements[source] = tuple(
            definitions[atomic_name] if sql.startswith(f"CREATE TABLE `{atomic_name}` (") else sql
            for sql in self._statements[source]
        )
        source_state = self._schemas[source]
        for name, table in tables.items():
            source_state["tables"][name] = copy.deepcopy(table)
            self._prefixes[source].append(copy.deepcopy(source_state))
            self._statements[source] += (definitions[name],)
        declaration_sql = profile.layout["mysql_profile_table"]
        declaration_name, declaration = _parse_table(declaration_sql)
        self._schemas[target]["tables"][declaration_name] = copy.deepcopy(declaration)
        self._prefixes[target].append(copy.deepcopy(self._schemas[target]))
        self._statements[target] += (declaration_sql,)
        for revision in later:
            for state in [*self._prefixes[revision], self._schemas[revision]]:
                state["tables"][declaration_name] = copy.deepcopy(declaration)
        self.affected_objects = tuple(dict.fromkeys([*self.affected_objects, *tables, declaration_name]))

    def _matches(self, inventory: dict[str, Any], expected: dict[str, Any]) -> bool:
        actual = {**inventory, "tables": dict(inventory["tables"])}
        declarations = actual.pop("artifact_spaces", [])
        if declarations:
            from powercontext.builtin.persistence.artifact_vector_space import registered_native_layouts

            if "pc_artifact_vector_spaces" not in expected["tables"]:
                return False
            try:
                layouts = registered_native_layouts(
                    declarations, actual["tables"], allow_declared=self.allow_declared_spaces
                )
            except (ValueError, TypeError, KeyError) as error:
                raise MigrationError(
                    "incompatible_schema", "A registered native vector space is incomplete or incompatible."
                ) from error
            expected = {**expected, "tables": dict(expected["tables"])}
            expected["tables"].update(layouts)
            self.affected_objects = tuple(dict.fromkeys((*self.affected_objects, *sorted(layouts))))
        # Compare table sets after adding the exactly validated registered spaces.
        if actual.keys() != expected.keys() or actual["tables"].keys() != expected["tables"].keys():
            return False
        for name, options in self.optional_table_options.items():
            table = actual["tables"].get(name)
            target = expected["tables"].get(name)
            if table is None or target is None:
                continue
            for option, allowed in options.items():
                if option not in target and table.get(option) in allowed:
                    table = dict(table)
                    actual["tables"][name] = table
                    del table[option]
        return _matches(actual, expected)

    def identify(self, inventory: dict[str, Any]) -> str | None:
        """Only complete known shapes may be adopted as an unversioned source."""
        inventory = _business_inventory(inventory)
        if inventory == _empty_inventory():
            return None
        for revision, expected in self._schemas.items():
            if self._matches(inventory, expected):
                return revision
        raise MigrationError("incompatible_schema", "Actual MySQL schema is not a registered complete layout.")

    def verify(self, inventory: dict[str, Any], revision: str | None) -> None:
        inventory = _business_inventory(inventory)
        expected = _empty_inventory() if revision is None else self._schemas.get(revision)
        if expected is None or not self._matches(inventory, expected):
            raise MigrationError("incompatible_schema", "Actual MySQL schema does not match its recorded revision.")

    def recovery(self, inventory: dict[str, Any], revision: str | None) -> MySQLRecovery:
        """Return only missing DDL in an exact prefix of the next revision.

        A runner must require matching active evidence before accepting a
        partially executed revision. A recognized postcondition is necessary
        for advancing the version row, and is not itself recovery authorization.
        """
        inventory = _business_inventory(inventory)
        pending = self.bundle.pending(revision)
        if not pending:
            raise MigrationError("incompatible_schema", "There is no registered next MySQL revision to recover.")
        target = pending[0]
        for position, expected in enumerate(self._prefixes[target]):
            if self._matches(inventory, expected):
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
        if self.bundle.readiness_scope == "server":
            expected = _business_inventory(mysql_inventory(connection))
        for name, table in expected["tables"].items():
            for check in table["checks"].values():
                expression = _expression_sql(check["expression"])
                # No bind parameters: keep literal percent signs out of DBAPI interpolation.
                violation = connection.exec_driver_sql(
                    f"SELECT 1 FROM {_quote(name)} WHERE NOT ({expression}) LIMIT 1",  # noqa: S608 -- frozen expressions
                    execution_options={"no_parameters": True},
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
    supported = {
        "engine": {"innodb"},
        "using": {"btree"},
        "organization": {"index", "heap"},
        # PHRASE_MATCH also supports the term matching used by the registered layouts.
        "fts_index_type": {"match", "phrase_match"},
    }
    if option in supported and value not in supported[option]:
        raise MigrationError("incompatible_schema", "An unsupported table engine or index organization was discovered.")


def _table_options(tokens: list[str]) -> tuple[dict[str, str | None], str | None]:
    positions = [position for position, option in enumerate(tokens) if option == "auto_increment_mode"]
    if not positions:
        return _physical(tokens), None
    if len(positions) != 1:
        raise MigrationError("incompatible_schema", "Duplicate AUTO_INCREMENT_MODE declarations were discovered.")
    start = positions[0]
    position = start + 1
    if tokens[position : position + 1] == ["="]:
        position += 1
    if position >= len(tokens):
        raise MigrationError("incompatible_schema", "AUTO_INCREMENT_MODE has no value.")
    value = tokens[position]
    mode = (_literal(value) if value.startswith("'") else value).lower()
    if mode not in {"order", "noorder"}:
        raise MigrationError("incompatible_schema", "An unsupported AUTO_INCREMENT_MODE was discovered.")
    return _physical(tokens[:start] + tokens[position + 1 :]), mode


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
    defaults, auto_increment_mode = _table_options(tokens[position:])
    table: dict[str, Any] = {"columns": {}, "primary_key": [], "foreign_keys": [], "checks": {}, "indexes": {}}
    if auto_increment_mode is not None:
        table["auto_increment_mode"] = auto_increment_mode
    for part in _parts(body):
        _table_clause(table, part, defaults, database)
    table["foreign_keys"].sort(key=digest)
    return name, table


def _put_named(objects: dict[str, Any], name: str, definition: dict[str, Any]) -> None:
    if name in objects:
        raise MigrationError("incompatible_schema", "Duplicate schema object names were discovered.")
    objects[name] = definition


def _check_definition(part: list[str], constraint: str | None) -> tuple[str, dict[str, Any]]:
    try:
        expression, end = _group(part, 1)
        if constraint is None or part[end:] not in ([], ["enforced"], ["not", "enforced"]):
            raise MigrationError("incompatible_schema", "An unsupported CHECK definition was discovered.")  # noqa: TRY301
        return constraint, {
            "expression": _Expression(expression).parse(),
            "enforced": part[end:] != ["not", "enforced"],
        }
    except MigrationError as error:
        detail = str(error).removeprefix(f"{error.code}: ")
        raise MigrationError(error.code, f"CHECK constraint {constraint!r}: {detail}") from error


def _index_definition(part: list[str], constraint: str | None = None) -> tuple[str, dict[str, Any]]:
    unique = part[0] == "unique"
    offset = 2 if unique and part[1] in {"key", "index"} else 1
    if unique and part[offset] == "(" and constraint is not None:
        index_name = constraint
        columns, end = _group(part, offset)
    else:
        index_name = part[offset]
        columns, end = _group(part, offset + 1)
    _physical(part[end:])
    return index_name, {"columns": _key_columns(columns), "unique": unique}


def _fulltext_definition(part: list[str]) -> tuple[str, dict[str, Any]]:
    if part[1:2] not in (["key"], ["index"]):
        raise MigrationError("incompatible_schema", "An unsupported FULLTEXT index definition was discovered.")
    columns, end = _group(part, 3)
    parser = None
    properties: dict[str, int] = {}
    if part[end : end + 2] == ["with", "parser"]:
        end += 2
        if end >= len(part) or not re.fullmatch(r"[a-z_][a-z_0-9$]*", part[end]):
            raise MigrationError("incompatible_schema", "A FULLTEXT parser has no valid identity.")
        parser = part[end]
        end += 1
        if part[end : end + 2] == ["parser_properties", "="]:
            arguments, end = _group(part, end + 2)
            for option in _parts(arguments):
                if (
                    len(option) != 3
                    or option[0] not in {"min_token_size", "max_token_size"}
                    or option[0] in properties
                    or option[1] != "="
                    or not option[2].isdigit()
                ):
                    raise MigrationError(
                        "incompatible_schema", "An unsupported FULLTEXT parser property was discovered."
                    )
                properties[option[0]] = int(option[2])
    _physical(part[end:])
    return part[2], {
        "columns": _key_columns(columns),
        "unique": False,
        "kind": "fulltext",
        "parser": parser,
        "parser_properties": properties,
    }


def _vector_definition(part: list[str]) -> tuple[str, dict[str, Any]]:
    columns, end = _group(part, 3)
    if part[end : end + 1] != ["with"]:
        raise MigrationError("incompatible_schema", "A VECTOR index is missing its parameter declaration.")
    arguments, end = _group(part, end + 1)
    parameters: dict[str, str | int] = {}
    for option in _parts(arguments):
        if len(option) != 3 or option[1] != "=" or option[0] in parameters:
            raise MigrationError("incompatible_schema", "A VECTOR index parameter is invalid or duplicated.")
        name, _, value = option
        if name in {"distance", "type", "lib"} and re.fullmatch(r"[a-z][a-z_0-9]*", value):
            parameters[name] = value
        elif name in {"m", "ef_construction", "ef_search"} and value.isdigit() and int(value) > 0:
            parameters[name] = int(value)
        else:
            raise MigrationError("incompatible_schema", "An unsupported VECTOR index parameter was discovered.")
    if not {"distance", "type"} <= parameters.keys():
        raise MigrationError("incompatible_schema", "A VECTOR index has no complete algorithm declaration.")
    _physical(part[end:])
    return part[2], {
        "columns": _key_columns(columns),
        "unique": False,
        "kind": "vector",
        "parameters": parameters,
    }


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
        index_name, definition = _index_definition(part, constraint)
        _put_named(table["indexes"], index_name, definition)
    elif part[0] == "fulltext":
        index_name, definition = _fulltext_definition(part)
        _put_named(table["indexes"], index_name, definition)
    elif part[0] == "vector" and part[1:2] in (["key"], ["index"]):
        index_name, definition = _vector_definition(part)
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
    if actual.keys() != expected.keys() or actual["tables"].keys() != expected["tables"].keys():
        return False
    if any(value != expected[key] for key, value in actual.items() if key != "tables"):
        return False
    return all(_table_matches(table, expected["tables"][name]) for name, table in actual["tables"].items())


def _table_matches(table: dict[str, Any], expected: dict[str, Any]) -> bool:
    if table["columns"].keys() != expected["columns"].keys():
        return False
    # Copy only the maps whose backend-generated defaults can be ignored.
    # Constraints and index definitions remain unchanged and compare exactly.
    normalized = {
        **table,
        "columns": {column: dict(specification) for column, specification in table["columns"].items()},
        "indexes": dict(table["indexes"]),
    }
    for column, specification in normalized["columns"].items():
        # The frozen SQL inherits backend defaults for display text only.
        # Identity columns explicitly require utf8mb4_bin and remain exact.
        for option in ("charset", "collation"):
            if expected["columns"][column][option] is None:
                specification[option] = None
    automatic_keys: set[tuple[str, ...]] = set()
    for index_name, index in table["indexes"].items():
        if index_name in expected["indexes"] or index["unique"] or index.get("kind") is not None:
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
            del normalized["indexes"][index_name]
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
            lexeme = self.tokens[self.position]
            construct = "string literal" if lexeme.startswith("'") else repr(lexeme)
            if (
                lexeme == "("
                and self.position > 0
                and re.fullmatch(r"[a-z_][a-z_0-9$]*", self.tokens[self.position - 1])
            ):
                construct = f"function {self.tokens[self.position - 1]!r}"
            raise MigrationError("incompatible_schema", f"Unsupported CHECK expression token {construct}.")
        return value

    def _expression(self, precedence: int) -> list[Any]:
        left = self._value()
        priorities = {
            "or": 1,
            "and": 2,
            "=": 3,
            ">": 3,
            "<": 3,
            ">=": 3,
            "<=": 3,
            "!=": 3,
            "<>": 3,
            "is": 3,
            "in": 3,
            "+": 4,
            "-": 4,
            "*": 5,
            "/": 5,
            "%": 5,
        }
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
            # Parse a balanced group independently so unsupported operators are
            # not mistaken for a missing closing parenthesis.
            group, self.position = _group(self.tokens, self.position - 1)
            return _Expression(group).parse()
        if lexeme in {"+", "-"}:
            return ["unary", lexeme, self._value()]
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
            raise MigrationError("incompatible_schema", f"Unsupported CHECK value token {lexeme!r}.")
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
    if kind == "unary":
        return f"({expression[1]}{_expression_sql(expression[2])})"
    if kind == "binary":
        return f"({_expression_sql(expression[2])} {expression[1].upper()} {_expression_sql(expression[3])})"
    raise MigrationError("invalid_bundle", "An unsupported frozen CHECK expression was discovered.")
