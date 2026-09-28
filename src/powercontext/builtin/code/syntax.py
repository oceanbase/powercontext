# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Shared source positions and lexical facts for Tree-sitter language adapters."""

from __future__ import annotations

import ast
import re
import time
from bisect import bisect_right
from typing import TYPE_CHECKING, Any

from powercontext.builtin.code.capture import digest_bytes, json_bytes
from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.extract import FACTS_SCHEMA, file_node, symbol_id
from powercontext.builtin.code.languages import parser_builds

if TYPE_CHECKING:
    from tree_sitter import Node


class SyntaxCollector:
    def __init__(self, path: str, content: bytes, language: str, deadline: float) -> None:
        self.path, self.content, self.language, self.deadline = path, content, language, deadline
        self.line_offsets = [0, *(match.end() for match in re.finditer(b"\n", content))]
        self.file = file_node(path, content, language)
        self.nodes = [self.file]
        self.node_ids = {self.file["id"]: self.file}
        self.scopes: dict[str, dict[str, Any]] = {
            self.file["id"]: {"parent": None, "owner": self.file["id"], "kind": "file"}
        }
        self.bindings: list[dict[str, Any]] = []
        self.references: list[dict[str, Any]] = []
        self.exports: dict[str, dict[str, str]] = {}
        self.errors: list[dict[str, Any]] = []
        self.writes: list[dict[str, str]] = []
        self.metadata: dict[str, Any] = {}

    def line(self, offset: int) -> int:
        # The pinned binding's Point accessors return borrowed references. Use
        # source byte offsets, as the Python extractor does, to avoid heap damage.
        return bisect_right(self.line_offsets, offset)

    def text(self, node: Node | None) -> str:
        return "" if node is None else self.content[node.start_byte : node.end_byte].decode("utf-8")

    def literal(self, node: Node | None) -> str:
        value = self.text(node)
        if value.startswith("`") and value.endswith("`") and "${" not in value:
            return value[1:-1]
        try:
            result = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return ""
        return result if isinstance(result, str) else ""

    def issue(self, node: Node, reason: str) -> None:
        self.errors.append({"reason": reason, "line": self.line(node.start_byte)})

    def scope(self, node: Node, parent: str, kind: str = "block") -> str:
        scope_id = symbol_id(self.path, "scope", kind, node.start_byte)
        self.scopes[scope_id] = {"parent": parent, "owner": self.scopes[parent]["owner"], "kind": kind}
        return scope_id

    def local(self, name: str, scope: str) -> None:
        if name and name != "_":
            self.bindings.append({"scope": scope, "name": name, "kind": "local"})

    def define(
        self,
        node: Node,
        name: str,
        kind: str,
        scope: str,
        *,
        body: Node | None = None,
        bind: bool = True,
        qualified_name: str = "",
    ) -> dict[str, Any]:
        parent_id = self.scopes[scope]["owner"]
        parent = self.node_ids[parent_id]
        qualified = qualified_name or (name if parent["kind"] == "file" else f"{parent['qualified_name']}.{name}")
        item = {
            **self.file,
            "id": symbol_id(self.path, kind, qualified, node.start_byte),
            "kind": kind,
            "name": name,
            "qualified_name": qualified,
            "parent_id": parent_id,
            "file_id": self.file["id"],
            "start_line": self.line(node.start_byte),
            "end_line": self.line(max(node.start_byte, node.end_byte - 1)),
            "start_byte": node.start_byte,
            "end_byte": node.end_byte,
            "signature": self.content[node.start_byte : body.start_byte if body else node.end_byte].decode()[:4096],
            "parse_status": "partial" if node.has_error else "ok",
        }
        self.nodes.append(item)
        self.node_ids[item["id"]] = item
        self.scopes[item["id"]] = {"parent": scope, "owner": item["id"], "kind": kind}
        if bind:
            self.bindings.append({"scope": scope, "name": name, "kind": "definition", "target": item["id"]})
        return item

    def expression(self, node: Node | None) -> str:
        if node is None:
            return ""
        if node.type in {"identifier", "type_identifier", "property_identifier", "field_identifier", "this"}:
            return self.text(node)
        if node.type in {"member_expression", "selector_expression"}:
            base = self.expression(node.child_by_field_name("object") or node.child_by_field_name("operand"))
            member = node.child_by_field_name("property") or node.child_by_field_name("field")
            if base and member and member.type in {"property_identifier", "field_identifier"}:
                return f"{base}.{self.text(member)}"
        return ""

    def reference(self, node: Node, scope: str, kind: str, *, expression: str | None = None) -> str:
        expression = self.expression(node) if expression is None else expression
        key = digest_bytes(json_bytes([self.path, node.start_byte, kind, expression]))
        self.references.append({
            "id": key,
            "source": self.scopes[scope]["owner"],
            "scope": scope,
            "kind": kind,
            "expression": expression,
            "line": self.line(node.start_byte),
            "dynamic": not bool(expression),
        })
        return key

    def imported(self, node: Node, scope: str, name: str, module: str, member: str) -> None:
        reference_id = self.reference(node, scope, "imports", expression=module)
        self.bindings.append({
            "scope": scope,
            "name": name,
            "kind": "import",
            "module": module,
            "member": member,
            "reference_id": reference_id,
        })

    def collect(self, root: Node) -> dict[str, Any]:
        pending = [(root, self.file["id"])]
        while pending:
            if time.monotonic() >= self.deadline:
                raise CodeError("parse_timeout")
            node, scope = pending.pop()
            if node.type == "ERROR" or node.is_missing:
                self.issue(node, "parse_error")
                continue
            pending.extend(reversed(self.visit(node, scope)))
        if root.has_error and not self.errors:
            self.issue(root, "parse_error")
        self.file["parse_status"] = "partial" if self.errors else "ok"
        return {
            "schema": FACTS_SCHEMA,
            "parser_build": parser_builds()[self.language],
            "path": self.path,
            "file_sha256": self.file["file_sha256"],
            "nodes": self.nodes,
            "scopes": self.scopes,
            "bindings": self.bindings,
            "references": self.references,
            "exports": self.exports,
            "attribute_writes": self.writes,
            "errors": self.errors,
            "metadata": self.metadata,
        }

    def visit(self, node: Node, scope: str) -> list[tuple[Node, str]]:
        raise NotImplementedError
