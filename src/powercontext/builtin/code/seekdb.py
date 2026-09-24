# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Immutable code graphs in a locally owned embedded seekdb instance."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pymysql

from powercontext.builtin.code.cache import read_json, read_private
from powercontext.builtin.code.capture import check_deadline, digest_bytes, json_bytes, write_private
from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.graph import GraphReader
from powercontext.builtin.code.store import identifier_terms

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS pc_code_generations (
        id CHAR(32) PRIMARY KEY, binding CHAR(64) NOT NULL, content_hash CHAR(64) NOT NULL,
        node_count BIGINT NOT NULL, edge_count BIGINT NOT NULL, payload_bytes BIGINT NOT NULL,
        KEY ix_code_binding (binding)
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    """CREATE TABLE IF NOT EXISTS pc_code_nodes (
        generation_id CHAR(32) NOT NULL, id CHAR(64) NOT NULL,
        kind VARCHAR(32) NOT NULL, path TEXT NOT NULL, file_id CHAR(64), parent_id CHAR(64),
        name TEXT NOT NULL, qualified_name TEXT NOT NULL, start_line BIGINT NOT NULL, end_line BIGINT NOT NULL,
        payload LONGTEXT NOT NULL, searchable_text LONGTEXT NOT NULL,
        PRIMARY KEY (generation_id, id),
        KEY ix_code_path (generation_id, path(190), start_line),
        KEY ix_code_name (generation_id, name(190)),
        KEY ix_code_qualified (generation_id, qualified_name(190)),
        KEY ix_code_parent (generation_id, parent_id)
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
    """CREATE TABLE IF NOT EXISTS pc_code_edges (
        generation_id CHAR(32) NOT NULL, ordinal BIGINT NOT NULL,
        source_id CHAR(64) NOT NULL, target_id CHAR(64) NOT NULL,
        kind VARCHAR(32) NOT NULL, resolution VARCHAR(32) NOT NULL, payload LONGTEXT NOT NULL,
        PRIMARY KEY (generation_id, ordinal),
        KEY ix_code_source (generation_id, source_id, kind),
        KEY ix_code_target (generation_id, target_id, kind)
    ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
)
_NODE_COLUMNS = (
    "id, kind, path, file_id, parent_id, name, qualified_name, start_line, end_line, payload, searchable_text"
)
_EDGE_COLUMNS = "ordinal, source_id, target_id, kind, resolution, payload"


def _path_clause(prefix: str, column: str = "path") -> tuple[str, tuple[str, ...]]:
    if not prefix:
        return "1", ()
    # Binary comparisons preserve case and trailing spaces, including in path components.
    return f"(BINARY {column} = BINARY %s OR LOCATE(BINARY %s, BINARY {column}) = 1)", (prefix, prefix + "/")


class SeekDBGraphReader:
    def __init__(self, connection: Any, generation: str, deadline: float) -> None:
        self.connection = connection
        self.generation = generation
        self.deadline = deadline

    def rows(self, sql: str, parameters: tuple[Any, ...] = ()) -> Iterator[tuple[Any, ...]]:
        check_deadline(self.deadline)
        with self.connection.cursor() as cursor:
            cursor.execute("SET ob_query_timeout = %s", (max(1, int((self.deadline - time.monotonic()) * 1e6)),))
            cursor.execute(sql, parameters)
            for row in cursor:
                check_deadline(self.deadline)
                yield row

    def node(self, node_id: str) -> dict[str, Any] | None:
        row = next(
            self.rows(
                "SELECT payload FROM pc_code_nodes WHERE generation_id = %s AND id = %s", (self.generation, node_id)
            ),
            None,
        )
        return json.loads(row[0]) if row else None

    def nodes(
        self, *, prefix: str = "", path: str | None = None, kind: str | None = None, parent_id: str | None = None
    ) -> Iterator[dict[str, Any]]:
        clause, values = _path_clause(prefix)
        predicates, parameters = ["generation_id = %s", clause], [self.generation, *values]
        for field, value in (("path", path), ("kind", kind), ("parent_id", parent_id)):
            if value is not None:
                predicates.append(f"{field} = %s AND BINARY {field} = BINARY %s")
                parameters.extend((value, value))
        rows = self.rows(
            "SELECT payload FROM pc_code_nodes WHERE " + " AND ".join(predicates) + " ORDER BY BINARY path, start_line",  # noqa: S608 - fixed predicates.
            tuple(parameters),
        )
        return (json.loads(row[0]) for row in rows)

    def search(self, query: str, prefix: str, limit: int) -> list[dict[str, Any]]:
        clause, values = _path_clause(prefix)
        results: dict[str, dict[str, Any]] = {}
        for field in ("path", "qualified_name", "name"):
            for row in self.rows(
                f"SELECT payload FROM pc_code_nodes WHERE generation_id = %s AND {clause} "  # noqa: S608 - fixed predicates.
                f"AND {field} = %s AND BINARY {field} = BINARY %s ORDER BY BINARY path, start_line LIMIT %s",
                (self.generation, *values, query, query, limit),
            ):
                node = json.loads(row[0])
                results.setdefault(node["id"], node)
            if len(results) >= limit:
                return list(results.values())[:limit]
        terms = " ".join(identifier_terms(query)[:32])
        if terms:
            for row in self.rows(
                "SELECT payload FROM pc_code_nodes WHERE generation_id = %s "  # noqa: S608 - fixed path predicate.
                f"AND {clause} AND MATCH(searchable_text) AGAINST (%s IN NATURAL LANGUAGE MODE) "
                "ORDER BY MATCH(searchable_text) AGAINST (%s IN NATURAL LANGUAGE MODE) DESC, "
                "BINARY path, start_line LIMIT %s",
                (self.generation, *values, terms, terms, limit),
            ):
                node = json.loads(row[0])
                results.setdefault(node["id"], node)
        return list(results.values())[:limit]

    def neighbors(
        self, node_id: str, prefix: str, *, reverse: bool, impact: bool, count_boundary: bool
    ) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], int]:
        current, other = ("target_id", "source_id") if reverse else ("source_id", "target_id")
        clause, values = _path_clause(prefix, "n.path")
        kinds = "e.kind != 'contains'" if impact else "e.kind = 'calls'"
        join = (
            f"FROM pc_code_edges e JOIN pc_code_nodes n ON n.id = e.{other} AND n.generation_id = e.generation_id "
            f"WHERE e.generation_id = %s AND e.{current} = %s AND {kinds}"
        )
        parameters = (self.generation, node_id, *values)
        boundary = next(self.rows(f"SELECT COUNT(*) {join} AND NOT ({clause})", parameters))[0] if count_boundary else 0
        rows = self.rows(
            f"SELECT e.payload, n.payload {join} AND {clause} ORDER BY BINARY n.path, n.start_line LIMIT 1001",
            parameters,
        )
        return [(json.loads(row[0]), json.loads(row[1])) for row in rows], boundary


class SeekDBGraphStore:
    def __init__(self, path: Path, connection_options: Mapping[str, object]) -> None:
        self.identity = "seekdb:" + digest_bytes(str(path.expanduser().resolve()).encode())
        self.options = dict(connection_options)
        self._condition = threading.Condition()
        self._active = 0
        self._closed = False

    @contextmanager
    def connection(self, deadline: float) -> Iterator[Any]:
        check_deadline(deadline)
        with self._condition:
            if self._closed:
                raise CodeError("code_storage_unavailable")
            self._active += 1
        try:
            remaining = max(1, deadline - time.monotonic())
            with pymysql.connect(
                **self.options,
                database="test",
                charset="utf8mb4",
                autocommit=False,
                connect_timeout=min(10, int(remaining) + 1),
                read_timeout=remaining,
                write_timeout=remaining,
                init_command="SET autocommit = 0",
            ) as connection:
                try:
                    connection.begin()
                    yield connection
                finally:
                    connection.rollback()
        except pymysql.MySQLError as error:
            reason = "code_timeout" if time.monotonic() >= deadline else "code_storage_unavailable"
            raise CodeError(reason) from error
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def close(self) -> None:
        """Drain synchronous readers before the owning embedded profile is closed."""
        with self._condition:
            self._closed = True
            self._condition.wait_for(lambda: self._active == 0)

    def initialize(self, deadline: float) -> None:
        with self.connection(deadline) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
            existing = {row[0] for row in cursor.fetchall()}
            # Even IF NOT EXISTS acquires seekdb's DDL lock. Reopening a complete
            # index must not wait for unrelated internal schema initialization.
            for table, statement in zip(
                ("pc_code_generations", "pc_code_nodes", "pc_code_edges"), _SCHEMA, strict=True
            ):
                check_deadline(deadline)
                if table not in existing:
                    cursor.execute(statement)
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() "
                "AND table_name = 'pc_code_nodes' AND index_name = 'ix_code_search'"
            )
            if cursor.fetchone()[0] == 0:
                try:
                    cursor.execute(
                        "CREATE FULLTEXT INDEX ix_code_search ON pc_code_nodes (searchable_text) WITH PARSER SPACE"
                    )
                except pymysql.MySQLError as error:
                    if error.args[0] != 1061:  # A concurrent local owner may have finished initialization.
                        raise
            cursor.execute(
                "SELECT id FROM pc_code_nodes WHERE MATCH(searchable_text) AGAINST ('pcbackendprobe314159265') LIMIT 1"
            )
            connection.commit()

    def _reference(self, directory: Path) -> dict[str, Any]:
        reference = read_json(directory / "graph.json", maximum=4096)
        if (
            reference.get("backend") != self.identity
            or reference.get("binding") != directory.parent.name
            or not isinstance(reference.get("id"), str)
            or len(reference["id"]) != 32
            or any(character not in "0123456789abcdef" for character in reference["id"])
        ):
            raise CodeError("index_integrity_failed")
        return reference

    def create(
        self, directory: Path, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], deadline: float
    ) -> dict[str, Any]:
        self.initialize(deadline)
        reference = {"backend": self.identity, "binding": directory.parent.name, "id": uuid.uuid4().hex}
        # Persist the cleanup handle before any database commit can leave orphan rows.
        # A killed writer may leave the temporary file incomplete, but remove() must
        # only see a complete descriptor or no committed database work at all.
        temporary = directory / ("graph-pending-" + uuid.uuid4().hex)
        try:
            write_private(temporary, json_bytes(reference))
            os.replace(temporary, directory / "graph.json")
        finally:
            temporary.unlink(missing_ok=True)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        node_ids = {node["id"] for node in nodes}
        if len(node_ids) != len(nodes) or any(
            edge["source_id"] not in node_ids or edge["target_id"] not in node_ids for edge in edges
        ):
            raise CodeError("index_integrity_failed")
        size = 0
        with self.connection(deadline) as connection, connection.cursor() as cursor:
            for node in sorted(nodes, key=lambda item: item["id"]):
                check_deadline(deadline)
                searchable = " ".join(
                    identifier_terms(
                        node["name"]
                        + " "
                        + node["qualified_name"]
                        + " "
                        + node["path"]
                        + " "
                        + node["signature"]
                        + " "
                        + node["docstring"]
                    )
                )
                row = (
                    *(
                        node[key]
                        for key in (
                            "id",
                            "kind",
                            "path",
                            "file_id",
                            "parent_id",
                            "name",
                            "qualified_name",
                            "start_line",
                            "end_line",
                        )
                    ),
                    json_bytes(node).decode(),
                    searchable,
                )
                encoded = json_bytes(row)
                size += len(encoded)
                cursor.execute(
                    f"INSERT INTO pc_code_nodes (generation_id, {_NODE_COLUMNS}) VALUES ({', '.join(['%s'] * 12)})",  # noqa: S608 - constant column names.
                    (reference["id"], *row),
                )
            for ordinal, edge in enumerate(edges):
                check_deadline(deadline)
                row = (
                    ordinal,
                    edge["source_id"],
                    edge["target_id"],
                    edge["kind"],
                    edge["resolution"],
                    json_bytes(edge).decode(),
                )
                encoded = json_bytes(row)
                size += len(encoded)
                cursor.execute("INSERT INTO pc_code_edges VALUES (%s, %s, %s, %s, %s, %s, %s)", (reference["id"], *row))
            checksum, node_count, edge_count = self._checksum(SeekDBGraphReader(connection, reference["id"], deadline))
            if (node_count, edge_count) != (len(nodes), len(edges)):
                raise CodeError("index_integrity_failed")
            cursor.execute(
                "INSERT INTO pc_code_generations VALUES (%s, %s, %s, %s, %s, %s)",
                (reference["id"], reference["binding"], checksum, len(nodes), len(edges), size),
            )
            check_deadline(deadline)
            connection.commit()
        return {"graph": {**reference, "sha256": checksum, "node_count": len(nodes), "edge_count": len(edges)}}

    def verify(self, directory: Path, manifest: dict[str, Any], deadline: float) -> None:
        reference = self._reference(directory)
        graph = manifest.get("graph", {})
        if any(graph.get(key) != value for key, value in reference.items()):
            raise CodeError("index_integrity_failed")
        with self.connection(deadline) as connection:
            reader = SeekDBGraphReader(connection, reference["id"], deadline)
            row = next(
                reader.rows(
                    "SELECT content_hash, node_count, edge_count FROM pc_code_generations WHERE id = %s AND binding = %s",
                    (reference["id"], reference["binding"]),
                ),
                None,
            )
            if row != (graph.get("sha256"), graph.get("node_count"), graph.get("edge_count")):
                raise CodeError("index_integrity_failed")
            # An exact symbol lookup must not advertise readiness after the lexical index is dropped.
            index = next(
                reader.rows(
                    "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() "
                    "AND table_name = 'pc_code_nodes' AND index_name = 'ix_code_search'"
                )
            )
            if not index[0]:
                raise CodeError("code_storage_unavailable")
            if self._checksum(reader) != (graph["sha256"], graph["node_count"], graph["edge_count"]):
                raise CodeError("index_integrity_failed")

    @staticmethod
    def _checksum(reader: SeekDBGraphReader) -> tuple[str, int, int]:
        # Hash actual columns in SQL, then hash ordered row digests. This detects changed
        # search/traversal columns without transferring every source payload.
        digest, counts = hashlib.sha256(), []
        for tag, table, columns, order in (
            (b"N", "pc_code_nodes", _NODE_COLUMNS, "id"),
            (b"E", "pc_code_edges", _EDGE_COLUMNS, "ordinal"),
        ):
            count = 0
            for row in reader.rows(
                f"SELECT SHA2(CAST(JSON_ARRAY({columns}) AS CHAR CHARACTER SET utf8mb4), 256) "  # noqa: S608 - constant columns and tables.
                f"FROM {table} WHERE generation_id = %s ORDER BY {order}",
                (reader.generation,),
            ):
                digest.update(tag + bytes.fromhex(row[0]))
                count += 1
            counts.append(count)
        return digest.hexdigest(), counts[0], counts[1]

    @contextmanager
    def open(self, directory: Path, manifest: dict[str, Any], deadline: float) -> Iterator[GraphReader]:
        reference = self._reference(directory)
        if any(manifest.get("graph", {}).get(key) != value for key, value in reference.items()):
            raise CodeError("index_integrity_failed")
        with self.connection(deadline) as connection:
            yield SeekDBGraphReader(connection, reference["id"], deadline)

    def remove(self, directory: Path, deadline: float) -> None:
        if not (directory / "graph.json").exists():
            return
        try:
            reference = self._reference(directory)
        except CodeError:
            if self._incomplete_staging(directory, deadline):
                return
            raise
        with self.connection(deadline) as connection, connection.cursor() as cursor:
            for table in ("pc_code_edges", "pc_code_nodes"):
                check_deadline(deadline)
                cursor.execute(f"DELETE FROM {table} WHERE generation_id = %s", (reference["id"],))  # noqa: S608 - constant tables.
            cursor.execute(
                "DELETE FROM pc_code_generations WHERE id = %s AND binding = %s",
                (reference["id"], reference["binding"]),
            )
            connection.commit()

    def _incomplete_staging(self, directory: Path, deadline: float) -> bool:
        # Older builders wrote graph.json in place before opening the transaction.
        # Recover their interrupted writes without accepting corrupt published graphs
        # or discarding an unknown committed generation's only cleanup handle.
        if not directory.name.startswith("staging-") or (directory / "manifest.json").exists():
            return False
        try:
            json.loads(read_private(directory / "graph.json", maximum=4096))
        except (UnicodeError, ValueError):
            pass
        else:
            return False
        known = set()
        for pattern in ("generation-*", "staging-*"):
            for other in directory.parent.glob(pattern):
                check_deadline(deadline)
                if other == directory or other.is_symlink() or not other.is_dir():
                    continue
                try:
                    known.add(self._reference(other)["id"])
                except CodeError:
                    continue
        with self.connection(deadline) as connection:
            reader = SeekDBGraphReader(connection, "", deadline)
            return all(
                row[0] in known
                for row in reader.rows(
                    "SELECT id FROM pc_code_generations WHERE binding = %s", (directory.parent.name,)
                )
            )

    def size(self, directory: Path, deadline: float) -> int:
        with self.connection(deadline) as connection:
            return int(
                next(
                    SeekDBGraphReader(connection, "", deadline).rows(
                        "SELECT COALESCE(SUM(payload_bytes), 0) FROM pc_code_generations WHERE binding = %s",
                        (directory.name,),
                    )
                )[0]
            )
