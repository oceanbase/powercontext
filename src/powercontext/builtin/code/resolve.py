# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Conservative lexical and import resolution over immutable syntax facts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from powercontext.builtin.code.capture import check_deadline

RESOLVER_BUILD = "python-static-4"


@dataclass(frozen=True)
class Resolution:
    targets: tuple[str, ...] = ()
    strength: str = "resolved_static"
    rule: str = "lexical_binding"
    reason: str = "unresolved_name"


class PythonResolver:
    """Resolve explicit relationships without treating name similarity as proof."""

    def __init__(self, facts: list[dict[str, Any]], source_roots: tuple[str, ...], deadline: float) -> None:
        self.facts = facts
        self.source_roots = source_roots
        self.deadline = deadline
        self.nodes = {node["id"]: node for fact in facts for node in fact["nodes"]}
        self.files = {fact["nodes"][0]["id"]: fact for fact in facts}
        self.bindings: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        self.modules: dict[str, set[str]] = defaultdict(set)
        self.file_modules: dict[str, list[str]] = defaultdict(list)
        self.rebound_targets: set[str] = set()
        for fact in facts:
            for binding in fact["bindings"]:
                self.bindings[(binding["scope"], binding["name"])].append(binding)
            for name in self.module_names(fact["path"]):
                file_id = fact["nodes"][0]["id"]
                self.modules[name].add(file_id)
                self.file_modules[file_id].append(name)
        # Collect all writes before resolving relationships, including writes in
        # other modules or conditional code. Execution order is not static proof.
        rebound_targets: set[str] = set()
        for fact in facts:
            for write in fact["attribute_writes"]:
                rebound_targets.update(self.lookup(write["scope"], write["expression"], frozenset()).targets)
        self.rebound_targets = rebound_targets

    def module_names(self, path: str) -> list[str]:
        if not path.endswith((".py", ".pyi")):
            return []
        result: list[str] = []
        for root in self.source_roots:
            if root != "." and not path.startswith(root + "/"):
                continue
            relative = path if root == "." else path[len(root) + 1 :]
            parts = list(PurePosixPath(relative).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            if parts:
                result.append(".".join(parts))
        return list(dict.fromkeys(result))

    def resolve(self) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        edges: list[dict[str, Any]] = []
        diagnostics: dict[str, list[dict[str, Any]]] = {}
        for fact in self.facts:
            check_deadline(self.deadline)
            issues: list[dict[str, Any]] = []
            diagnostics[fact["path"]] = issues
            for node in fact["nodes"]:
                if node["parent_id"] is not None:
                    edges.append({
                        "source_id": node["parent_id"],
                        "target_id": node["id"],
                        "kind": "contains",
                        "resolution": "resolved_static",
                        "rule_id": "syntax_contains",
                        "reference_key": None,
                        "path": fact["path"],
                        "line": node["start_line"],
                    })
            imports = {binding["reference_id"]: binding for binding in fact["bindings"] if binding["kind"] == "import"}
            for reference in fact["references"]:
                check_deadline(self.deadline)
                if reference["kind"] == "imports":
                    binding = imports.get(reference["id"])
                    result = (
                        self.import_target(binding, fact["nodes"][0]["id"], frozenset()) if binding else Resolution()
                    )
                elif reference["dynamic"]:
                    result = Resolution(reason="dynamic_receiver")
                else:
                    result = self.lookup(reference["scope"], reference["expression"], frozenset())
                if any(error["reason"] == "parse_error" for error in fact["errors"]) and result.targets:
                    result = Resolution(result.targets, "candidate", "partial_parse", "partial_parse")
                if not result.targets or result.strength == "candidate":
                    issues.append({
                        "reference_key": reference["id"],
                        "line": reference["line"],
                        "kind": reference["kind"],
                        "reason": result.reason,
                        "candidates": list(result.targets),
                    })
                for target in result.targets:
                    edges.append({
                        "source_id": reference["source"],
                        "target_id": target,
                        "kind": reference["kind"],
                        "resolution": result.strength,
                        "rule_id": result.rule,
                        "reference_key": reference["id"],
                        "path": fact["path"],
                        "line": reference["line"],
                    })
        return edges, diagnostics

    def lookup(self, scope_id: str, expression: str, visited: frozenset[tuple[str, str]]) -> Resolution:
        check_deadline(self.deadline)
        key = (scope_id, expression)
        if key in visited or len(visited) >= 32:
            return Resolution(reason="cyclic_binding")
        visited = visited | {key}
        parts = expression.split(".")
        scope = self.nodes[scope_id]
        bindings = self.bindings.get((scope_id, parts[0]), [])
        if bindings:
            if len(bindings) != 1:
                targets = tuple(sorted({binding["target"] for binding in bindings if binding["kind"] == "definition"}))
                return Resolution(targets, "candidate", "ambiguous_binding", "ambiguous_binding")
            return self.bound_target(scope, bindings[0], parts, visited)
        parent = scope.get("parent_id")
        if parent is not None:
            # Method bodies do not close over the class namespace.
            if scope["kind"] in {"method", "function"} and self.nodes[parent]["kind"] == "class":
                parent = self.nodes[parent]["parent_id"]
            if parent is not None:
                return self.lookup(parent, expression, visited)
        return Resolution()

    def bound_target(
        self, scope: dict[str, Any], binding: dict[str, Any], parts: list[str], visited: frozenset[tuple[str, str]]
    ) -> Resolution:
        if binding["kind"] == "definition":
            result = self.members(binding["target"], parts[1:], visited)
        elif binding["kind"] == "import":
            file_id = scope["id"] if scope["kind"] == "file" else scope["file_id"]
            result = self.import_target(binding, file_id, visited, suffix=parts[1:])
        else:
            return self.local_target(scope, binding, parts, visited)
        # A star import can overwrite any binding in this namespace. Keep the
        # uncertainty for lexical lookups and exported module members alike.
        if self.bindings.get((scope["id"], "*")):
            return Resolution(result.targets, "candidate", "wildcard_import", "wildcard_import")
        if binding.get("conditional"):
            return Resolution(result.targets, "candidate", "conditional_binding", "conditional_binding")
        return result

    def local_target(
        self, scope: dict[str, Any], binding: dict[str, Any], parts: list[str], visited: frozenset[tuple[str, str]]
    ) -> Resolution:
        if len(parts) > 1:
            receiver = binding.get("receiver")
            if receiver:
                found = self.lookup(scope["id"], receiver, visited)
                return self.receiver_members(found.targets, parts[1:], visited)
            if parts[0] in {"self", "cls"} and scope["kind"] == "method":
                return self.receiver_members((scope["parent_id"],), parts[1:], visited)
        return Resolution(reason="local_binding")

    def members(self, target: str, suffix: list[str], visited: frozenset[tuple[str, str]]) -> Resolution:
        result = self._members(target, suffix, visited)
        if target in self.rebound_targets:
            return Resolution(result.targets, "candidate", "attribute_rebinding", "attribute_rebinding")
        return result

    def _members(self, target: str, suffix: list[str], visited: frozenset[tuple[str, str]]) -> Resolution:
        if not suffix:
            return Resolution((target,))
        node = self.nodes[target]
        if node["kind"] not in {"file", "class"}:
            return Resolution(reason="dynamic_receiver")
        bindings = self.bindings.get((target, suffix[0]), [])
        if len(bindings) == 1 and bindings[0]["kind"] == "definition":
            return self.bound_target(node, bindings[0], suffix, visited)
        if node["kind"] == "file" and len(bindings) == 1 and bindings[0]["kind"] == "import":
            return self.bound_target(node, bindings[0], suffix, visited)
        return Resolution(reason="ambiguous_member" if bindings else "unresolved_member")

    def receiver_members(
        self, targets: tuple[str, ...], suffix: list[str], visited: frozenset[tuple[str, str]]
    ) -> Resolution:
        members = {member for target in targets for member in self.members(target, suffix, visited).targets}
        return Resolution(tuple(sorted(members)), "candidate", "receiver_hint", "dynamic_receiver")

    def import_target(
        self,
        binding: dict[str, Any],
        file_id: str,
        visited: frozenset[tuple[str, str]],
        *,
        suffix: list[str] | None = None,
    ) -> Resolution:
        check_deadline(self.deadline)
        module = binding["module"]
        member = binding["member"]
        if member == "*":
            return Resolution(reason="wildcard_import")
        candidates = self.absolute_modules(module, file_id)
        targets: set[str] = set()
        strengths: set[str] = set()
        for candidate in candidates:
            pieces = [candidate, *([member] if member else []), *(suffix or [])]
            path = ".".join(part for part in pieces if part)
            key = (file_id, "import:" + path)
            if key in visited or len(visited) >= 32:
                continue
            result = self.module_target(path, visited | {key})
            targets.update(result.targets)
            strengths.add(result.strength)
        if not targets:
            return Resolution(reason="external_or_unresolved_import")
        ambiguous = len(targets) > 1 or "candidate" in strengths
        return Resolution(
            tuple(sorted(targets)),
            "candidate" if ambiguous else "resolved_static",
            "explicit_import",
            "ambiguous_module" if ambiguous else "",
        )

    def absolute_modules(self, module: str, file_id: str) -> list[str]:
        if not module.startswith("."):
            return [module]
        levels = len(module) - len(module.lstrip("."))
        rest = module[levels:]
        results = []
        is_package = self.nodes[file_id]["path"].endswith(("/__init__.py", "/__init__.pyi"))
        for current in self.file_modules[file_id]:
            package = current.split(".") if is_package else current.split(".")[:-1]
            if levels > len(package):
                continue
            base = package[: len(package) - levels + 1]
            results.append(".".join([*base, *([rest] if rest else [])]))
        return list(dict.fromkeys(results))

    def module_target(self, dotted: str, visited: frozenset[tuple[str, str]]) -> Resolution:
        parts = dotted.split(".")
        for length in range(len(parts), 0, -1):
            modules = self.modules.get(".".join(parts[:length]), set())
            if not modules:
                continue
            targets: set[str] = set()
            uncertain = False
            for file_id in modules:
                result = self.members(file_id, parts[length:], visited)
                targets.update(result.targets)
                uncertain |= result.strength == "candidate"
            if targets:
                ambiguous = uncertain or len(modules) > 1 or len(targets) > 1
                return Resolution(
                    tuple(sorted(targets)), "candidate" if ambiguous else "resolved_static", "module_export"
                )
            return Resolution(reason="unresolved_export")
        return Resolution(reason="external_import")
