# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Go declarations, package imports and conservative lexical call facts."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from powercontext.builtin.code.syntax import SyntaxCollector

if TYPE_CHECKING:
    from tree_sitter import Node


class GoCollector(SyntaxCollector):
    def bind_names(self, node: Node | None, scope: str) -> None:
        if node is None:
            return
        if node.type == "identifier":
            self.local(self.text(node), scope)
        elif node.type in {"expression_list", "parameter_list", "type_parameter_list"}:
            for child in node.named_children:
                self.bind_names(child, scope)
        elif node.type in {"parameter_declaration", "variadic_parameter_declaration", "type_parameter_declaration"}:
            for name in node.children_by_field_name("name"):
                self.local(self.text(name), scope)

    def function(self, node: Node, scope: str) -> list[tuple[Node, str]]:
        name = self.text(node.child_by_field_name("name"))
        receiver = node.child_by_field_name("receiver")
        receiver_type = ""
        if receiver is not None and receiver.named_children:
            declared_type = receiver.named_children[0].child_by_field_name("type")
            if declared_type is not None and declared_type.type == "pointer_type":
                declared_type = declared_type.named_children[0]
            receiver_type = self.text(declared_type)
        kind = "method" if receiver else "function"
        anonymous = not name
        name = name or f"<anonymous@{self.line(node.start_byte)}:{node.start_byte}>"
        body = node.child_by_field_name("body")
        item = self.define(
            node,
            name,
            kind,
            scope,
            body=body,
            bind=not anonymous and receiver is None,
            qualified_name=f"{receiver_type}.{name}" if receiver_type else "",
        )
        item["receiver_type"] = receiver_type
        for field in ("parameters", "result", "type_parameters", "receiver"):
            self.bind_names(node.child_by_field_name(field), item["id"])
        return [(body, item["id"])] if body else []

    @override
    def visit(self, node: Node, scope: str) -> list[tuple[Node, str]]:
        if node.type == "package_clause":
            self.metadata["package"] = self.text(node.named_children[0]) if node.named_children else ""
            return []
        if node.type == "comment":
            if self.text(node).startswith(("//go:build", "// +build")):
                self.metadata["conditional_build"] = True
            return []
        if node.type == "import_spec":
            self.import_spec(node, scope)
            return []
        if node.type in {"function_declaration", "method_declaration", "func_literal"}:
            return self.function(node, scope)
        if node.type in {"type_spec", "type_alias"}:
            name = self.text(node.child_by_field_name("name"))
            value = node.child_by_field_name("type")
            kind = (
                "struct"
                if value and value.type == "struct_type"
                else "interface"
                if value and value.type == "interface_type"
                else "type"
            )
            self.define(node, name, kind, scope, body=value)
            return []
        if node.type in {
            "block",
            "if_statement",
            "for_statement",
            "expression_switch_statement",
            "type_switch_statement",
            "select_statement",
            "communication_case",
            "type_case",
            "expression_case",
            "default_case",
        }:
            scope = self.scope(node, scope)
        self.expression_facts(node, scope)
        return [(child, scope) for child in node.named_children]

    @override
    def collect(self, root: Node) -> dict[str, Any]:
        facts = super().collect(root)
        # Filename constraints also select alternate package definitions. Keep them
        # visible without choosing a host GOOS/GOARCH or invoking the Go toolchain.
        stem = PurePosixPath(self.path).stem.removesuffix("_test")
        if "_" in stem:
            suffixes = set(stem.split("_")[1:])
            platforms = {
                "aix",
                "android",
                "darwin",
                "dragonfly",
                "freebsd",
                "illumos",
                "ios",
                "js",
                "linux",
                "netbsd",
                "openbsd",
                "plan9",
                "solaris",
                "wasip1",
                "windows",
                "386",
                "amd64",
                "arm",
                "arm64",
                "loong64",
                "mips",
                "mipsle",
                "mips64",
                "mips64le",
                "ppc64",
                "ppc64le",
                "riscv64",
                "s390x",
                "wasm",
            }
            if suffixes & platforms:
                self.metadata["conditional_build"] = True
        return facts

    def import_spec(self, node: Node, scope: str) -> None:
        module = self.literal(node.child_by_field_name("path"))
        name = self.text(node.child_by_field_name("name"))
        self.imported(node, scope, name or module.rsplit("/", 1)[-1], module, "*")
        self.bindings[-1]["implicit_alias"] = not name
        if name == ".":
            self.issue(node, "dot_import_unsupported")

    def expression_facts(self, node: Node, scope: str) -> None:
        if node.type in {"var_spec", "const_spec"}:
            for name in node.children_by_field_name("name"):
                self.local(self.text(name), scope)
        if node.type in {"short_var_declaration", "range_clause", "receive_statement"}:
            self.bind_names(node.child_by_field_name("left"), scope)
        if node.type == "type_switch_statement":
            self.bind_names(node.child_by_field_name("alias"), scope)
        if node.type in {"assignment_statement", "inc_statement", "dec_statement"}:
            target = node.child_by_field_name("left")
            targets = target.named_children if target is not None else node.named_children[:1]
            for item in targets:
                expression = self.expression(item)
                if expression:
                    self.writes.append({"scope": scope, "expression": expression})
        if node.type == "call_expression":
            target = node.child_by_field_name("function")
            if target is not None:
                self.reference(target, scope, "calls")
