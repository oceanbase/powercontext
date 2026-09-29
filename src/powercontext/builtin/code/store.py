# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Three-table immutable SQLite graph and lexical index."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from powercontext.builtin.code.capture import check_deadline, json_bytes
from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.graph import GraphReader

SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE code_nodes (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, path TEXT NOT NULL,
    file_id TEXT, parent_id TEXT, name TEXT NOT NULL, qualified_name TEXT NOT NULL,
    start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX code_nodes_path ON code_nodes(path, start_line);
CREATE INDEX code_nodes_name ON code_nodes(name);
CREATE INDEX code_nodes_qualified ON code_nodes(qualified_name);
CREATE INDEX code_nodes_parent ON code_nodes(parent_id);
CREATE TABLE code_edges (
    source_id TEXT NOT NULL REFERENCES code_nodes(id), target_id TEXT NOT NULL REFERENCES code_nodes(id),
    kind TEXT NOT NULL, resolution TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX code_edges_source ON code_edges(source_id, kind);
CREATE INDEX code_edges_target ON code_edges(target_id, kind);
CREATE VIRTUAL TABLE code_search_fts USING fts5(node_id UNINDEXED, name, path, terms, body, tokenize='unicode61');
"""


def identifier_terms(value: str) -> list[str]:
    segmented = re.sub(r"([a-z\d])([A-Z])", r"\1 \2", value)
    segmented = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", segmented)
    return list(dict.fromkeys(word.casefold() for word in re.findall(r"[^\W_]+", segmented, re.UNICODE)))


def create_graph(path: Path, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], deadline: float) -> None:
    try:
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            connection.executescript(_SCHEMA)
            for node in nodes:
                check_deadline(deadline)
                connection.execute(
                    "INSERT INTO code_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        node["id"],
                        node["kind"],
                        node["path"],
                        node["file_id"],
                        node["parent_id"],
                        node["name"],
                        node["qualified_name"],
                        node["start_line"],
                        node["end_line"],
                        json_bytes(node).decode(),
                    ),
                )
                terms = " ".join(identifier_terms(node["qualified_name"] + " " + node["path"]))
                connection.execute(
                    "INSERT INTO code_search_fts VALUES (?, ?, ?, ?, ?)",
                    (node["id"], node["name"], node["path"], terms, node["signature"] + "\n" + node["docstring"]),
                )
            connection.executemany(
                "INSERT INTO code_edges VALUES (?, ?, ?, ?, ?)",
                (
                    (edge["source_id"], edge["target_id"], edge["kind"], edge["resolution"], json_bytes(edge).decode())
                    for edge in edges
                ),
            )
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise CodeError("index_integrity_failed")
            connection.execute("INSERT INTO code_search_fts(code_search_fts) VALUES ('integrity-check')")
    except sqlite3.Error as error:
        reason = "code_timeout" if time.monotonic() >= deadline else "code_storage_unavailable"
        raise CodeError(reason) from error
    path.chmod(0o600)


@contextmanager
def open_graph(path: Path, deadline: float) -> Iterator[sqlite3.Connection]:
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        try:
            yield connection
        finally:
            connection.close()
    except sqlite3.Error as error:
        reason = "code_timeout" if time.monotonic() >= deadline else "index_integrity_failed"
        raise CodeError(reason) from error


def path_clause(prefix: str, *, column: str = "path") -> tuple[str, tuple[str, ...]]:
    if not prefix:
        return "1", ()
    # instr avoids LIKE wildcard behavior for legitimate '%' and '_' filenames.
    return f"({column} = ? OR instr({column}, ?) = 1)", (prefix, prefix + "/")


def node_by_id(connection: sqlite3.Connection, node_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT payload FROM code_nodes WHERE id = ?", (node_id,)).fetchone()
    return json.loads(row[0]) if row else None


def search_nodes(connection: sqlite3.Connection, query: str, prefix: str, limit: int) -> list[dict[str, Any]]:
    clause, parameters = path_clause(prefix, column="n.path")
    results: dict[str, dict[str, Any]] = {}
    for field in ("path", "qualified_name", "name"):
        rows = connection.execute(
            f"SELECT n.payload FROM code_nodes n WHERE {clause} AND n.{field} = ? ORDER BY n.path, n.start_line LIMIT ?",  # noqa: S608 - fixed columns; values are bound.
            (*parameters, query, limit),
        )
        for row in rows:
            node = json.loads(row[0])
            results.setdefault(node["id"], node)
        if len(results) >= limit:
            return list(results.values())[:limit]
    terms = identifier_terms(query)[:32]
    if terms:
        expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        rows = connection.execute(
            f"SELECT n.payload FROM code_search_fts f JOIN code_nodes n ON n.id = f.node_id "  # noqa: S608 - fixed path clause; values are bound.
            f"WHERE code_search_fts MATCH ? AND {clause} "
            "ORDER BY bm25(code_search_fts), n.path, n.start_line LIMIT ?",
            (expression, *parameters, limit),
        )
        for row in rows:
            node = json.loads(row[0])
            results.setdefault(node["id"], node)
    return list(results.values())[:limit]


class SQLiteGraphReader:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def node(self, node_id: str) -> dict[str, Any] | None:
        return node_by_id(self.connection, node_id)

    def search(self, query: str, prefix: str, limit: int) -> list[dict[str, Any]]:
        return search_nodes(self.connection, query, prefix, limit)

    def nodes(
        self, *, prefix: str = "", path: str | None = None, kind: str | None = None, parent_id: str | None = None
    ) -> Iterator[dict[str, Any]]:
        clause, values = path_clause(prefix)
        predicates, parameters = [clause], list(values)
        for field, value in (("path", path), ("kind", kind), ("parent_id", parent_id)):
            if value is not None:
                predicates.append(f"{field} = ?")
                parameters.append(value)
        rows = self.connection.execute(
            "SELECT payload FROM code_nodes WHERE " + " AND ".join(predicates) + " ORDER BY path, start_line",  # noqa: S608 - fixed predicates.
            parameters,
        )
        return (json.loads(row[0]) for row in rows)

    def neighbors(
        self, node_id: str, prefix: str, *, reverse: bool, impact: bool, count_boundary: bool
    ) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], int]:
        current, other = ("target_id", "source_id") if reverse else ("source_id", "target_id")
        clause, parameters = path_clause(prefix, column="n.path")
        kinds = "e.kind != 'contains'" if impact else "e.kind = 'calls'"
        join = f"FROM code_edges e JOIN code_nodes n ON n.id = e.{other} WHERE e.{current} = ? AND {kinds}"
        boundary = 0
        if count_boundary:
            boundary = self.connection.execute(
                f"SELECT COUNT(*) {join} AND NOT ({clause})", (node_id, *parameters)
            ).fetchone()[0]
        rows = self.connection.execute(
            f"SELECT e.payload, n.payload {join} AND {clause} ORDER BY n.path, n.start_line LIMIT 1001",
            (node_id, *parameters),
        )
        return [(json.loads(row[0]), json.loads(row[1])) for row in rows], boundary


class SQLiteGraphStore:
    identity = "sqlite"

    def create(
        self, directory: Path, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], deadline: float
    ) -> dict[str, Any]:
        from powercontext.builtin.code.cache import hash_file

        path = directory / "graph.sqlite"
        create_graph(path, nodes, edges, deadline)
        return {"database_sha256": hash_file(path, deadline)}

    def verify(self, directory: Path, manifest: dict[str, Any], deadline: float) -> None:
        from powercontext.builtin.code.cache import hash_file

        if hash_file(directory / "graph.sqlite", deadline) != manifest.get("database_sha256"):
            raise CodeError("index_integrity_failed")

    @contextmanager
    def open(self, directory: Path, manifest: dict[str, Any], deadline: float) -> Iterator[GraphReader]:
        del manifest
        with open_graph(directory / "graph.sqlite", deadline) as connection:
            yield SQLiteGraphReader(connection)

    def remove(self, directory: Path, deadline: float) -> None:
        # The cache removes the entire SQLite generation directory after readers exit.
        del directory, deadline

    def size(self, directory: Path, deadline: float) -> int:
        del directory, deadline
        return 0  # Already counted with the local generation files.
