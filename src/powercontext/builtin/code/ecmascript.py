# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""JavaScript, JSX, TypeScript and TSX declarations and lexical references."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from powercontext.builtin.code.syntax import SyntaxCollector

if TYPE_CHECKING:
    from tree_sitter import Node

_FUNCTIONS = {
    "function_declaration",
    "generator_function_declaration",
    "function_expression",
    "generator_function",
    "arrow_function",
}
_TYPES = {
    "class_declaration": "class",
    "class": "class",
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}


class ECMAScriptCollector(SyntaxCollector):
    def write_pattern(self, node: Node | None, scope: str) -> None:
        if node is None:
            return
        expression = self.expression(node)
        if node.type == "shorthand_property_identifier_pattern":
            expression = self.text(node)
        if expression:
            self.writes.append({"scope": scope, "expression": expression})
        elif node.type == "pair_pattern":
            self.write_pattern(node.child_by_field_name("value"), scope)
        elif node.type in {"assignment_pattern", "object_assignment_pattern"}:
            self.write_pattern(node.child_by_field_name("left"), scope)
        elif node.type in {"object_pattern", "array_pattern", "rest_pattern", "parenthesized_expression"}:
            for child in node.named_children:
                self.write_pattern(child, scope)

    def bind_pattern(self, node: Node | None, scope: str) -> None:
        if node is None:
            return
        if node.type in {"identifier", "shorthand_property_identifier_pattern"}:
            self.local(self.text(node), scope)
            return
        if node.type in {"required_parameter", "optional_parameter"}:
            self.bind_pattern(node.child_by_field_name("pattern"), scope)
        elif node.type in {"pair_pattern", "object_assignment_pattern"}:
            self.bind_pattern(node.child_by_field_name("value") or node.child_by_field_name("left"), scope)
        elif node.type == "assignment_pattern":
            self.bind_pattern(node.child_by_field_name("left"), scope)
        elif node.type in {"formal_parameters", "object_pattern", "array_pattern", "rest_pattern"}:
            for child in node.named_children:
                self.bind_pattern(child, scope)

    def exported(self, node: Node, name: str) -> None:
        current = node.parent
        while current is not None and current.type in {"lexical_declaration", "variable_declaration"}:
            current = current.parent
        if current is not None and current.type == "export_statement":
            exported = "default" if any(child.type == "default" for child in current.children) else name
            self.exports[exported] = {"local": name}

    def function(
        self, node: Node, scope: str, *, name: str = "", declaration: Node | None = None
    ) -> list[tuple[Node, str]]:
        explicit = self.text(node.child_by_field_name("name"))
        name = name or explicit
        anonymous = not name
        if not name:
            name = f"<anonymous@{self.line(node.start_byte)}:{node.start_byte}>"
        body = node.child_by_field_name("body")
        kind = "method" if node.type == "method_definition" else "function"
        bind = not anonymous and (
            declaration is not None
            or node.type in {"function_declaration", "generator_function_declaration"}
            or (kind == "method" and self.scopes[scope]["kind"] == "class")
        )
        item = self.define(declaration or node, name, kind, scope, body=body, bind=bind)
        item["static"] = any(child.type == "static" for child in node.children)
        name_node = node.child_by_field_name("name")
        if any(child.type in {"get", "set"} for child in node.children) or (
            name_node is not None and name_node.type == "computed_property_name"
        ):
            self.issue(node, "dynamic_class_member")
        self.bind_pattern(node.child_by_field_name("parameters") or node.child_by_field_name("parameter"), item["id"])
        if explicit and node.type in {"function_expression", "generator_function"}:
            self.bindings.append({"scope": item["id"], "name": explicit, "kind": "definition", "target": item["id"]})
        parent = node.parent
        if (
            parent is not None
            and parent.type == "export_statement"
            and any(child.type == "default" for child in parent.children)
        ):
            self.exports["default"] = {"target": item["id"]}
        else:
            self.exported(declaration or node, name)
        return [(body, item["id"])] if body else []

    def imports(self, node: Node, scope: str) -> None:
        module = self.literal(node.child_by_field_name("source"))
        clause = next((child for child in node.named_children if child.type == "import_clause"), None)
        if clause is None:
            if any(child.type == "import_require_clause" for child in node.named_children):
                self.issue(node, "commonjs_import_unsupported")
            self.imported(node, scope, "", module, "*")
            return
        type_only = any(child.type == "type" for child in node.children)
        for child in clause.named_children:
            if child.type == "identifier":
                self.imported(child, scope, self.text(child), module, "default")
                self.bindings[-1]["type_only"] = type_only
            elif child.type == "namespace_import":
                self.imported(child, scope, self.text(child.named_children[-1]), module, "*")
                self.bindings[-1]["type_only"] = type_only
            elif child.type == "named_imports":
                for spec in child.named_children:
                    original = self.text(spec.child_by_field_name("name"))
                    alias = self.text(spec.child_by_field_name("alias")) or original
                    self.imported(spec, scope, alias, module, original)
                    self.bindings[-1]["type_only"] = type_only or any(part.type == "type" for part in spec.children)

    def exports_from(self, node: Node, scope: str) -> None:
        module = self.literal(node.child_by_field_name("source"))
        if any(child.type == "type" for child in node.children):
            return
        clause = next((child for child in node.named_children if child.type == "export_clause"), None)
        if clause is not None:
            for spec in clause.named_children:
                if any(child.type == "type" for child in spec.children):
                    continue
                original = self.text(spec.child_by_field_name("name"))
                alias = self.text(spec.child_by_field_name("alias")) or original
                self.exports[alias] = {"module": module, "member": original} if module else {"local": original}
                if module:
                    self.imported(spec, scope, "", module, original)
        elif module:
            self.issue(node, "wildcard_export_unsupported")
            self.imported(node, scope, "", module, "*")
        value = node.child_by_field_name("value")
        if value is not None and value.type == "identifier":
            self.exports["default"] = {"local": self.text(value)}

    def variable(self, node: Node, scope: str) -> list[tuple[Node, str]]:
        name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        if node.parent is not None and node.parent.type == "variable_declaration":
            while self.scopes[scope]["kind"] not in {"file", "function", "method"}:
                scope = self.scopes[scope]["parent"]
        if name is not None and name.type == "identifier" and value is not None and value.type in _FUNCTIONS:
            return self.function(value, scope, name=self.text(name), declaration=node)
        self.bind_pattern(name, scope)
        if name is not None and name.type == "identifier":
            self.exported(node, self.text(name))
        return [(value, scope)] if value else []

    @override
    def visit(self, node: Node, scope: str) -> list[tuple[Node, str]]:
        if node.type == "import_statement":
            self.imports(node, scope)
            return []
        if node.type == "export_statement":
            self.exports_from(node, scope)
        if node.type in _FUNCTIONS or node.type == "method_definition":
            return self.function(node, scope)
        if node.type in _TYPES:
            return self.type_declaration(node, scope)
        if node.type == "variable_declarator":
            return self.variable(node, scope)
        if node.type in {"statement_block", "for_statement", "for_in_statement", "catch_clause", "switch_body"}:
            scope = self.scope(node, scope)
            self.bind_pattern(node.child_by_field_name("parameter"), scope)
        self.expression_facts(node, scope)
        if node.type in {"with_statement", "internal_module", "module"}:
            self.issue(node, "unsupported_scope")
            return []
        if node.type in {"type_annotation", "type_arguments", "type_parameters", "comment"}:
            return []
        return [(child, scope) for child in node.named_children]

    def type_declaration(self, node: Node, scope: str) -> list[tuple[Node, str]]:
        name = (
            self.text(node.child_by_field_name("name")) or f"<anonymous@{self.line(node.start_byte)}:{node.start_byte}>"
        )
        body = node.child_by_field_name("body")
        item = self.define(node, name, _TYPES[node.type], scope, body=body, bind=node.type != "class")
        if node.type == "class":
            if node.child_by_field_name("name") is not None:
                self.bindings.append({"scope": item["id"], "name": name, "kind": "definition", "target": item["id"]})
            if node.parent is not None and node.parent.type == "export_statement":
                self.exports["default"] = {"target": item["id"]}
        else:
            self.exported(node, name)
        if item["kind"] != "class":
            return []
        heritage = next((child for child in node.named_children if child.type == "class_heritage"), None)
        if heritage is not None:
            self.issue(heritage, "inheritance_resolution_unsupported")
        return [(body, item["id"])] if body else []

    def expression_facts(self, node: Node, scope: str) -> None:
        if node.type == "for_in_statement":
            if node.child_by_field_name("kind") is None:
                self.write_pattern(node.child_by_field_name("left"), scope)
            else:
                self.bind_pattern(node.child_by_field_name("left"), scope)
        if node.type in {"field_definition", "public_field_definition"}:
            name = node.child_by_field_name("property") or node.child_by_field_name("name")
            if name is not None:
                self.local(self.text(name), scope)
        if node.type in {"computed_property_name", "class_static_block", "decorator"}:
            self.issue(node, "dynamic_class_member")
        if node.type in {"assignment_expression", "augmented_assignment_expression", "update_expression"}:
            target = node.child_by_field_name("left") or node.child_by_field_name("argument")
            self.write_pattern(target, scope)
        if node.type in {"call_expression", "new_expression"}:
            target = node.child_by_field_name("function") or node.child_by_field_name("constructor")
            if target is not None:
                self.reference(target, scope, "calls")
                if self.expression(target) in {"eval", "require"}:
                    self.issue(node, "dynamic_module_or_scope")
