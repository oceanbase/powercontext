# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Deterministic Python syntax facts, independent of name resolution."""

from __future__ import annotations

import ast
import re
import time
from bisect import bisect_right
from typing import TYPE_CHECKING, Any

from powercontext.builtin.code.capture import digest_bytes, json_bytes
from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.languages import LANGUAGES, parser_builds, parser_for

if TYPE_CHECKING:
    from tree_sitter import Node

PARSER_BUILD = LANGUAGES["python"].build
FACTS_SCHEMA = 1
_DOTTED_NAME = re.compile(r"^[A-Za-z_\w]+(?:\.[A-Za-z_\w]+)*$", re.UNICODE)


def symbol_id(path: str, kind: str, name: str, offset: int) -> str:
    return digest_bytes(json_bytes([path, kind, name, offset]))


def file_node(path: str, content: bytes, language: str) -> dict[str, Any]:
    return {
        "id": symbol_id(path, "file", path, 0),
        "kind": "file",
        "name": path.rsplit("/", 1)[-1],
        "qualified_name": path,
        "path": path,
        "parent_id": None,
        "file_id": None,
        "language": language,
        "start_line": 1,
        "end_line": max(1, content.count(b"\n") + int(not content.endswith(b"\n"))),
        "start_byte": 0,
        "end_byte": len(content),
        "file_sha256": digest_bytes(content),
        "signature": "",
        "docstring": "",
        "parse_status": "ok" if language in LANGUAGES else "unsupported",
    }


def extraction_key(path: str, sha256: str, language: str) -> str:
    return digest_bytes(json_bytes([path, sha256, language, parser_builds()[language], FACTS_SCHEMA]))


def _literal_string(node: Node | None, content: bytes) -> str | None:
    if node is None or node.type not in {"string", "concatenated_string"}:
        return None
    try:
        value = ast.literal_eval(content[node.start_byte : node.end_byte].decode("utf-8"))
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


class PythonExtractor:
    """Extract syntax without importing or evaluating repository modules."""

    def __init__(self) -> None:
        self.parser = parser_for("python")

    def extract(self, path: str, content: bytes, *, timeout: float = 5) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        self.parser.reset()
        try:
            # The parent watchdog enforces the deadline even while native parsing
            # holds the GIL. Avoid callback ABI differences across Python versions.
            tree = self.parser.parse(content)
        except ValueError as error:
            raise CodeError("parse_timeout") from error
        if tree is None or time.monotonic() >= deadline:
            raise CodeError("parse_timeout")
        return _FactCollector(path, content, deadline).collect(tree.root_node)


class _FactCollector:
    def __init__(self, path: str, content: bytes, deadline: float) -> None:
        self.path = path
        self.content = content
        self.line_offsets = [0, *(match.end() for match in re.finditer(b"\n", content))]
        self.deadline = deadline
        self.file = file_node(path, content, "python")
        self.nodes: list[dict[str, Any]] = [self.file]
        self.references: list[dict[str, Any]] = []
        self.bindings: list[dict[str, Any]] = []
        self.attribute_writes: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.exports: list[str] | None = None

    def line(self, offset: int) -> int:
        # The pinned binding's Point accessors return borrowed references. Byte
        # offsets avoid heap corruption above row 256 and preserve CRLF ranges.
        return bisect_right(self.line_offsets, offset)

    def text(self, node: Node | None) -> str:
        return "" if node is None else self.content[node.start_byte : node.end_byte].decode("utf-8")

    def collect(self, root: Node) -> dict[str, Any]:
        stack: list[tuple[Node, dict[str, Any]]] = [(root, self.file)]
        while stack:
            if time.monotonic() >= self.deadline:
                raise CodeError("parse_timeout")
            node, scope = stack.pop()
            stack.extend(self.visit(node, scope))
        self.file["parse_status"] = "partial" if self.errors else "ok"
        return {
            "schema": FACTS_SCHEMA,
            "parser_build": PARSER_BUILD,
            "path": self.path,
            "file_sha256": self.file["file_sha256"],
            "nodes": self.nodes,
            "references": self.references,
            "bindings": self.bindings,
            "attribute_writes": self.attribute_writes,
            "exports": self.exports,
            "errors": self.errors,
        }

    def visit(self, node: Node, scope: dict[str, Any]) -> list[tuple[Node, dict[str, Any]]]:
        if node.type == "ERROR" or node.is_missing:
            self.errors.append({"reason": "parse_error", "line": self.line(node.start_byte)})
            return []
        if node.type in {
            "lambda",
            "list_comprehension",
            "set_comprehension",
            "dictionary_comprehension",
            "generator_expression",
        }:
            self.errors.append({"reason": "unsupported_scope", "line": self.line(node.start_byte)})
            return []
        if node.type in {"function_definition", "class_definition"}:
            return self.definition_children(node, scope)
        if node.type in {"import_statement", "import_from_statement"}:
            self.imports(node, scope)
            return []
        if node.type == "case_pattern":
            self.match_pattern(node, scope)
            return []
        self.record_expression(node, scope)
        return [(child, scope) for child in reversed(node.named_children)]

    def definition_children(self, node: Node, parent: dict[str, Any]) -> list[tuple[Node, dict[str, Any]]]:
        scope = self.definition(node, parent)
        body = node.child_by_field_name("body")
        children = [(body, scope)] if body is not None else []
        parameters = node.child_by_field_name("parameters")
        for parameter in parameters.named_children if parameters is not None else []:
            value = parameter.child_by_field_name("value")
            if value is not None:
                children.append((value, parent))
        return children

    def record_expression(self, node: Node, scope: dict[str, Any]) -> None:
        if node.type in {"assignment", "augmented_assignment", "named_expression"}:
            self.assignment(node, scope)
        elif node.type == "for_statement":
            self.bind_pattern(node.child_by_field_name("left"), scope)
        elif node.type == "as_pattern":
            self.bind_pattern(node.child_by_field_name("alias"), scope)
        elif node.type in {"global_statement", "nonlocal_statement", "delete_statement"}:
            for child in node.named_children:
                self.bind_pattern(child, scope)
        if node.type == "call":
            target = node.child_by_field_name("function")
            if target is not None:
                self.reference(target, scope, "calls")
        elif node.type == "decorator" and node.named_children and node.named_children[0].type != "call":
            self.reference(node.named_children[0], scope, "references")
        elif node.type == "identifier" and self.is_value_reference(node):
            self.reference(node, scope, "references")

    def bind_pattern(self, node: Node | None, scope: dict[str, Any]) -> None:
        if node is None or node.type == "subscript":
            return
        if node.type == "attribute":
            self.attribute_writes.append({"scope": scope["id"], "expression": self.text(node)})
            return
        if node.type == "identifier":
            self.bindings.append({"scope": scope["id"], "name": self.text(node), "kind": "local"})
            return
        for child in node.named_children:
            self.bind_pattern(child, scope)

    def match_pattern(self, node: Node, scope: dict[str, Any]) -> None:
        # A single name captures; dotted values, class names and keyword labels do
        # not. Skip normal expression walking so captures never become references.
        if node.type == "ERROR" or node.is_missing:
            self.errors.append({"reason": "parse_error", "line": self.line(node.start_byte)})
            return
        if node.type == "dotted_name":
            if len(node.named_children) == 1:
                self.bind_pattern(node, scope)
            else:
                self.reference(node, scope, "references")
            return
        if node.type == "identifier":
            if self.text(node) != "_":
                self.bind_pattern(node, scope)
            return
        for child in self.match_children(node, scope):
            self.match_pattern(child, scope)

    def match_children(self, node: Node, scope: dict[str, Any]) -> list[Node]:
        if node.type == "dict_pattern":
            for key in node.children_by_field_name("key"):
                if key.type == "dotted_name":
                    self.reference(key, scope, "references")
            return [
                *node.children_by_field_name("value"),
                *(child for child in node.named_children if child.type in {"splat_pattern", "ERROR"}),
            ]
        if node.type == "class_pattern":
            self.reference(node.named_children[0], scope, "references")
            return node.named_children[1:]
        if node.type == "keyword_pattern":
            return node.named_children[1:]
        if node.type in {
            "case_pattern",
            "list_pattern",
            "tuple_pattern",
            "union_pattern",
            "as_pattern",
            "splat_pattern",
        }:
            return node.named_children
        return []

    @staticmethod
    def conditional(node: Node) -> bool:
        parent = node.parent
        while parent is not None and parent.type not in {"function_definition", "class_definition", "module"}:
            if parent.type in {"if_statement", "try_statement", "for_statement", "while_statement", "match_statement"}:
                return True
            parent = parent.parent
        return False

    def docstring(self, body: Node | None) -> str:
        if body is None or not body.named_children:
            return ""
        first = body.named_children[0]
        if first.type != "expression_statement" or not first.named_children:
            return ""
        return _literal_string(first.named_children[0], self.content) or ""

    def parameters(self, node: Node, definition: dict[str, Any]) -> None:
        parameters = node.child_by_field_name("parameters")
        if parameters is None:
            return
        for parameter in parameters.named_children:
            name = parameter if parameter.type == "identifier" else parameter.child_by_field_name("name")
            if name is None and parameter.named_children:
                name = parameter.named_children[0]
            # Type annotations and defaults are not binding patterns.
            if name is not None and name.type in {"identifier", "list_splat_pattern", "dictionary_splat_pattern"}:
                self.bind_pattern(name, definition)

    def definition(self, node: Node, parent: dict[str, Any]) -> dict[str, Any]:
        name = self.text(node.child_by_field_name("name"))
        kind = "class" if node.type == "class_definition" else "method" if parent["kind"] == "class" else "function"
        qualified_name = name if parent["kind"] == "file" else f"{parent['qualified_name']}.{name}"
        body = node.child_by_field_name("body")
        decorated = node.parent is not None and node.parent.type == "decorated_definition"
        start = node.parent if decorated else node
        if start is None:
            start = node
        definition = {
            "id": symbol_id(self.path, kind, qualified_name, node.start_byte),
            "kind": kind,
            "name": name,
            "qualified_name": qualified_name,
            "path": self.path,
            "file_id": self.file["id"],
            "parent_id": parent["id"],
            "language": "python",
            "start_line": self.line(start.start_byte),
            "end_line": self.line(max(node.start_byte, node.end_byte - 1)),
            "start_byte": start.start_byte,
            "end_byte": node.end_byte,
            "signature": self
            .content[node.start_byte : body.start_byte if body is not None else node.end_byte]
            .decode()
            .strip(),
            "docstring": self.docstring(body),
            "file_sha256": self.file["file_sha256"],
            "parse_status": "partial" if node.has_error else "ok",
        }
        self.nodes.append(definition)
        self.bindings.append({
            "scope": parent["id"],
            "name": name,
            "kind": "definition",
            "target": definition["id"],
            "conditional": self.conditional(node),
        })
        bases = node.child_by_field_name("superclasses")
        if bases is not None:
            for base in bases.named_children:
                if base.type != "keyword_argument":
                    self.reference(base, parent, "inherits", source=definition["id"])
        self.parameters(node, definition)
        return definition

    def imports(self, node: Node, scope: dict[str, Any]) -> None:
        module = self.text(node.child_by_field_name("module_name"))
        names = node.children_by_field_name("name")
        if any(child.type == "wildcard_import" for child in node.named_children):
            names = [child for child in node.named_children if child.type == "wildcard_import"]
        for item in names:
            imported = item.child_by_field_name("name") if item.type == "aliased_import" else item
            original = self.text(imported)
            alias = self.text(item.child_by_field_name("alias"))
            if node.type == "import_statement":
                binding_module = original if alias else original.split(".")[0]
                name, member = alias or original.split(".")[0], ""
                reference_expression = original
            else:
                binding_module, member = module, original
                name = alias or original
                reference_expression = f"{module}.{original}" if not module.endswith(".") else f"{module}{original}"
            reference_id = self.reference(item, scope, "imports", expression=reference_expression)
            self.bindings.append({
                "scope": scope["id"],
                "name": name,
                "kind": "import",
                "module": binding_module,
                "member": member,
                "reference_id": reference_id,
                "conditional": self.conditional(node),
                "line": self.line(item.start_byte),
            })

    def assignment(self, node: Node, scope: dict[str, Any]) -> None:
        left = node.child_by_field_name("left") or node.child_by_field_name("name")
        right = node.child_by_field_name("right")
        if left is None:
            return
        if self.text(left) == "__all__" and scope["kind"] == "file":
            if right is not None and right.type in {"list", "tuple"}:
                values = [_literal_string(child, self.content) for child in right.named_children]
                self.exports = (
                    [value for value in values if value is not None]
                    if all(value is not None for value in values)
                    else None
                )
            return
        if left.type == "identifier":
            receiver = (
                self.text(right.child_by_field_name("function")) if right is not None and right.type == "call" else ""
            )
            self.bindings.append({"scope": scope["id"], "name": self.text(left), "kind": "local", "receiver": receiver})
        else:
            self.bind_pattern(left, scope)

    def reference(
        self,
        node: Node,
        scope: dict[str, Any],
        kind: str,
        *,
        expression: str | None = None,
        source: str | None = None,
    ) -> str:
        text = self.text(node) if expression is None else expression
        key = digest_bytes(json_bytes([self.path, node.start_byte, kind]))
        self.references.append({
            "id": key,
            "source": source or scope["id"],
            "scope": scope["id"],
            "kind": kind,
            "expression": text,
            "line": self.line(node.start_byte),
            "end_line": self.line(max(node.start_byte, node.end_byte - 1)),
            "start_byte": node.start_byte,
            "end_byte": node.end_byte,
            "dynamic": not bool(_DOTTED_NAME.fullmatch(text)) and kind != "imports",
        })
        return key

    @staticmethod
    def is_value_reference(node: Node) -> bool:
        parent = node.parent
        if parent is None:
            return False
        if parent.type in {"attribute", "dotted_name", "keyword_argument", "type", "decorator"}:
            return False
        if parent.type == "call" and parent.child_by_field_name("function") == node:
            return False
        return not (parent.type == "assignment" and parent.child_by_field_name("left") == node)


def text_facts(path: str, content: bytes) -> dict[str, Any]:
    node = file_node(path, content, "text")
    if path.endswith(".md"):
        node["docstring"] = "\n".join(line for line in content.decode().splitlines() if line.startswith("#"))[:4096]
    return {
        "schema": FACTS_SCHEMA,
        "parser_build": parser_builds()["text"],
        "path": path,
        "file_sha256": node["file_sha256"],
        "nodes": [node],
        "references": [],
        "bindings": [],
        "attribute_writes": [],
        "exports": None,
        "go_module": next(iter(re.findall(r"^module\s+([^\s]+)", content.decode(), re.MULTILINE)), "").strip('"')
        if path.rsplit("/", 1)[-1] == "go.mod"
        else "",
        "errors": [],
    }
