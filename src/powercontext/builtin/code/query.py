# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Bounded graph queries with content-addressed source evidence."""

from __future__ import annotations

from collections import deque
from typing import Any

from powercontext.builtin.code.cache import Generation
from powercontext.builtin.code.capture import check_deadline, digest_bytes, source_lines
from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.graph import GraphReader
from powercontext.builtin.code.languages import (
    LANGUAGE_LIMITATIONS,
    LANGUAGES,
    is_test_path,
    language_for_path,
    test_stem,
)
from powercontext.builtin.code.models import (
    CodeQueryRequest,
    MapOperation,
    ReadOperation,
    RelationOperation,
    SearchOperation,
    TestsOperation,
)
from powercontext.builtin.code.telemetry import observed, stage


def within(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(prefix + "/")


def is_test(node: dict[str, Any]) -> bool:
    return is_test_path(node["path"])


class GraphQuery:
    def __init__(self, generation: Generation, deadline: float) -> None:
        self.generation = generation
        self.deadline = deadline
        self.limitations: set[str] = {"static_analysis_only", "dynamic_relationships_may_be_missing"}
        for language, counts in generation.manifest["coverage"].get("languages", {}).items():
            if counts["files"]:
                self.limitations.update(LANGUAGE_LIMITATIONS[language])
        self.truncated = False
        self.boundary_edges = 0
        self._counted_boundaries: set[tuple[str, bool, bool]] = set()

    def evidence(self, node: dict[str, Any], *, include_source: bool = True) -> dict[str, Any]:
        fields = (
            "id",
            "kind",
            "language",
            "name",
            "qualified_name",
            "path",
            "start_line",
            "end_line",
            "file_sha256",
            "parse_status",
        )
        item = {key: node[key] for key in fields}
        item["fingerprint"] = self.generation.fingerprint
        if include_source:
            snippet = self.read(
                node["path"], node["start_line"], min(node["end_line"], node["start_line"] + 199), maximum=2000
            )
            item.update(snippet)
        return item

    @observed("render_source")
    def read(self, path: str, start: int, end: int, *, maximum: int = 24_000) -> dict[str, Any]:
        content = self.generation.source(path, self.deadline)
        lines = [line.encode() for line in source_lines(content.decode())]
        if not lines:
            lines = [b""]
        if start > len(lines):
            raise CodeError("code_range_missing", status=422)
        end = min(end, len(lines))
        selected = bytearray()
        last = start - 1
        for offset, line in enumerate(lines[start - 1 : end], start):
            if len(selected) + len(line) > maximum:
                self.limitations.add("source_truncated")
                self.truncated = True
                break
            selected.extend(line)
            last = offset
        if last < start:
            self.limitations.add("source_line_too_large")
            return {"path": path, "source_omitted": "source_line_too_large"}
        return {
            "path": path,
            "start_line": start,
            "end_line": last,
            "file_sha256": digest_bytes(content),
            "snippet_sha256": digest_bytes(bytes(selected)),
            "content": selected.decode("utf-8"),
        }

    def execute(self, request: CodeQueryRequest) -> list[dict[str, Any]]:
        operation = request.operation
        with self.generation.graph(self.deadline) as connection:
            if isinstance(operation, ReadOperation):
                record = self.generation.manifest["included_files"].get(operation.path)
                if record is None:
                    raise CodeError("code_target_missing", status=422)
                if record["sha256"] != operation.file_sha256:
                    raise CodeError("code_changed", status=409)
                return [self.read(operation.path, operation.start_line, operation.end_line)]
            if isinstance(operation, MapOperation):
                return self.map(connection, operation)
            if isinstance(operation, SearchOperation):
                return self.search(connection, operation)
            if isinstance(operation, RelationOperation):
                seed = connection.node(operation.symbol_id)
                if seed is None or not within(seed["path"], operation.path_prefix):
                    raise CodeError("code_target_missing", status=422)
                depth = operation.depth if operation.kind == "impact" else 1
                return self.walk(
                    connection,
                    [seed],
                    operation.path_prefix,
                    depth,
                    reverse=operation.kind != "callees",
                    impact=operation.kind == "impact",
                    maximum=operation.limit,
                )
            if isinstance(operation, TestsOperation) and operation.kind == "affected_tests":
                seeds = self.path_seeds(connection, operation.paths)
                items = self.walk(
                    connection,
                    seeds,
                    operation.path_prefix,
                    5,
                    reverse=True,
                    impact=True,
                    tests=True,
                    maximum=operation.limit,
                )
                return self.test_hints(connection, operation, items)
        raise CodeError("unsupported_capability", status=501)

    def search(self, connection: GraphReader, operation: SearchOperation) -> list[dict[str, Any]]:
        with stage("search") as attributes:
            nodes = connection.search(operation.query, operation.path_prefix, operation.limit + 1)
            attributes["hit_count"] = len(nodes)
            attributes["hit_count_is_lower_bound"] = len(nodes) > operation.limit
        if len(nodes) > operation.limit:
            self.truncated = True
            self.limitations.add("item_limit")
        nodes = nodes[: operation.limit]
        if operation.kind == "symbols":
            return [self.evidence(node, include_source=False) for node in nodes]
        seeds = nodes[:4]
        items = {node["id"]: self.evidence(node) for node in seeds}
        for reverse in (True, False):
            for item in self.walk(connection, seeds, operation.path_prefix, 1, reverse=reverse, maximum=16):
                items.setdefault(item["id"], item)
        if len(items) > 16:
            self.truncated = True
            self.limitations.add("item_limit")
        return list(items.values())[:16]

    def map(self, connection: GraphReader, operation: MapOperation) -> list[dict[str, Any]]:
        items = []
        base = operation.path_prefix.count("/") + (1 if operation.path_prefix else 0)
        for node in connection.nodes(prefix=operation.path_prefix):
            check_deadline(self.deadline)
            if node["path"].count("/") - base >= operation.depth:
                continue
            if len(items) >= operation.limit:
                self.truncated = True
                self.limitations.add("item_limit")
                break
            items.append(self.evidence(node, include_source=False))
        return items

    def path_seeds(self, connection: GraphReader, paths: tuple[str, ...]) -> list[dict[str, Any]]:
        nodes = []
        for path in paths:
            node = next(connection.nodes(path=path, kind="file"), None)
            if node is None:
                raise CodeError("code_target_missing", status=422)
            if node["language"] not in LANGUAGES:
                raise CodeError("unsupported_capability", status=501)
            nodes.append(node)
        return nodes

    def seeds_with_members(self, connection: GraphReader, seeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = {node["id"]: node for node in seeds}
        pending = deque(node for node in seeds if node["kind"] in {"file", "class"})
        while pending and len(result) < 500:
            parent = pending.popleft()
            for node in connection.nodes(parent_id=parent["id"]):
                if node["id"] not in result:
                    result[node["id"]] = node
                    pending.append(node)
                if len(result) >= 500:
                    self.truncated = True
                    self.limitations.add("node_limit")
                    break
        return list(result.values())

    @observed("traverse")
    def walk(
        self,
        connection: GraphReader,
        seeds: list[dict[str, Any]],
        prefix: str,
        depth: int,
        *,
        reverse: bool,
        impact: bool = False,
        tests: bool = False,
        maximum: int = 20,
    ) -> list[dict[str, Any]]:
        seeds = self.seeds_with_members(connection, seeds) if impact else seeds
        seeds = [node for node in seeds if within(node["path"], prefix)]
        pending = deque((node, [], 0, False, False) for node in seeds)
        visited = {(node["id"], False) for node in seeds}
        reached: dict[tuple[str, bool], tuple[dict[str, Any], list[dict[str, Any]], bool]] = {}
        cutoffs: list[tuple[str, bool]] = []
        edge_count = 0
        while pending:
            check_deadline(self.deadline)
            node, witness, distance, coarse, candidate = pending.popleft()
            if (witness or (tests and is_test(node))) and (not tests or is_test(node)):
                reached.setdefault((node["id"], candidate), (node, witness, coarse))
            if impact and not self.truncated:
                self.enqueue_parent(connection, node, witness, distance, candidate, prefix, pending, visited)
            if distance >= depth:
                if impact:
                    cutoffs.append((node["id"], candidate))
                continue
            for edge, neighbor in self.neighbors(connection, node["id"], prefix, reverse=reverse, impact=impact):
                edge_count += 1
                if edge_count > 1000 or len(visited) >= 500:
                    self.truncated = True
                    self.limitations.add("traversal_limit")
                    pending.clear()
                    break
                uncertain = candidate or edge["resolution"] != "resolved_static"
                key = (neighbor["id"], uncertain)
                if key not in visited:
                    visited.add(key)
                    pending.append((neighbor, [*witness, edge], distance + 1, coarse, uncertain))
        self.check_depth_cutoffs(connection, cutoffs, visited, prefix, reverse=reverse, impact=impact)
        return self.walk_results(reached, maximum)

    def check_depth_cutoffs(self, connection, cutoffs, visited, prefix, *, reverse: bool, impact: bool) -> None:
        # A cutoff is partial only when eligible work is still unseen after all
        # queued paths finish. Cycles and paths outside the requested scope do not
        # imply an omitted result. Direct callers/callees are one-hop operations.
        for node_id, candidate in cutoffs:
            check_deadline(self.deadline)
            if any(
                (neighbor["id"], candidate or edge["resolution"] != "resolved_static") not in visited
                for edge, neighbor in self.neighbors(connection, node_id, prefix, reverse=reverse, impact=impact)
            ):
                self.truncated = True
                self.limitations.add("depth_limit")
                break

    def enqueue_parent(self, connection, node, witness, distance, candidate, prefix, pending, visited) -> None:
        if node["parent_id"] is None:
            return
        parent = connection.node(node["parent_id"])
        if parent is None or (parent["id"], candidate) in visited or not within(parent["path"], prefix):
            return
        visited.add((parent["id"], candidate))
        step = {
            "kind": "contains",
            "source_id": parent["id"],
            "target_id": node["id"],
            "rule_id": "syntax_contains",
            "resolution": "resolved_static",
        }
        pending.append((parent, [*witness, step], distance, True, candidate))

    def walk_results(
        self, reached: dict[tuple[str, bool], tuple[dict[str, Any], list[dict[str, Any]], bool]], maximum: int
    ) -> list[dict[str, Any]]:
        ordered = sorted(
            reached.items(),
            key=lambda pair: (pair[0][1], len(pair[1][1]), pair[1][0]["path"], pair[1][0]["start_line"]),
        )
        items: dict[str, dict[str, Any]] = {}
        for (node_id, candidate), (node, witness, coarse) in ordered:
            if node_id in items:
                continue
            if len(items) >= maximum:
                self.truncated = True
                self.limitations.add("item_limit")
                break
            item = self.evidence(node)
            item.update(
                witness_path=witness,
                reason="candidate_path" if candidate else "static_path",
                resolution="candidate" if candidate else "resolved_static",
                granularity="module" if coarse else "symbol",
            )
            items[node_id] = item
        return list(items.values())

    def neighbors(
        self,
        connection: GraphReader,
        node_id: str,
        prefix: str,
        *,
        reverse: bool,
        impact: bool,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        boundary_key = (node_id, reverse, impact)
        count_boundary = bool(prefix) and boundary_key not in self._counted_boundaries
        neighbors, count = connection.neighbors(
            node_id, prefix, reverse=reverse, impact=impact, count_boundary=count_boundary
        )
        if count_boundary:
            self._counted_boundaries.add(boundary_key)
            self.boundary_edges += count
            if count:
                self.limitations.add("edges_outside_path_scope")
        return neighbors

    def test_hints(
        self, connection: GraphReader, operation: TestsOperation, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        self.limitations.add("test_candidates_do_not_replace_required_tests")
        if len(items) >= operation.limit:
            return items
        stems = {(language_for_path(path), test_stem(path)) for path in operation.paths}
        existing = {item["path"] for item in items}
        for node in connection.nodes(kind="file", prefix=operation.path_prefix):
            check_deadline(self.deadline)
            if (
                node["path"] in existing
                or not is_test(node)
                or not any(
                    test_stem(node["path"]) == stem
                    and (node["language"] == language or {node["language"], language} <= {"javascript", "typescript"})
                    for language, stem in stems
                )
            ):
                continue
            item = self.evidence(node, include_source=False)
            item.update(reason="name_hint", witness_path=[], resolution="candidate")
            items.append(item)
            if len(items) >= operation.limit:
                break
        return items
