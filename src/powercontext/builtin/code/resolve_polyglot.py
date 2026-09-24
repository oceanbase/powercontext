# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Conservative ECMAScript modules and Go packages over captured repository facts."""

from __future__ import annotations

import posixpath
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from powercontext.builtin.code.capture import check_deadline
from powercontext.builtin.code.resolve import RESOLVER_BUILD, PythonResolver, Resolution

RESOLVER_BUILDS = {"python": RESOLVER_BUILD, "ecmascript": "ecmascript-static-2", "go": "go-static-1"}


def resolve_facts(facts: list[dict[str, Any]], source_roots: tuple[str, ...], deadline: float):
    python = [fact for fact in facts if fact["nodes"][0]["language"] == "python"]
    edges, diagnostics = PythonResolver(python, source_roots, deadline).resolve()
    for languages in ({"javascript", "typescript"}, {"go"}):
        selected = [fact for fact in facts if fact["nodes"][0]["language"] in languages]
        if selected:
            added, issues = ModuleResolver(selected, facts, deadline).resolve()
            edges.extend(added)
            diagnostics.update(issues)
    for fact in facts:
        diagnostics.setdefault(fact["path"], [])
    return edges, diagnostics


class ModuleResolver:
    def __init__(self, facts: list[dict[str, Any]], all_facts: list[dict[str, Any]], deadline: float) -> None:
        self.facts, self.deadline = facts, deadline
        self.files = {fact["nodes"][0]["id"]: fact for fact in facts}
        self.paths = {fact["path"]: fact["nodes"][0]["id"] for fact in facts}
        self.nodes = {node["id"]: node for fact in facts for node in fact["nodes"]}
        self.scopes = {key: value for fact in facts for key, value in fact.get("scopes", {}).items()}
        self.bindings: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        self.packages: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.module_dirs = {
            str(PurePosixPath(fact["path"]).parent): fact["go_module"] for fact in all_facts if fact.get("go_module")
        }
        self.go_imports: dict[str, list[str]] = defaultdict(list)
        self.module_roots: dict[str, str] = {}
        self.rebound: set[str] = set()
        self.index_packages()
        for fact in facts:
            for binding in fact["bindings"]:
                if binding.get("implicit_alias"):
                    targets = self.go_imports.get(binding["module"], [])
                    names = {self.files[target]["metadata"]["package"] for target in targets}
                    if len(names) == 1:
                        binding = {**binding, "name": next(iter(names))}
                self.bindings[binding["scope"], binding["name"]].append(binding)
        for fact in facts:
            for write in fact["attribute_writes"]:
                self.rebound.update(self.lookup(write["scope"], write["expression"], frozenset()).targets)

    def index_packages(self) -> None:
        for file_id, fact in self.files.items():
            package = fact.get("metadata", {}).get("package")
            if package:
                directory = str(PurePosixPath(fact["path"]).parent)
                self.packages[directory, package].append(file_id)
                ancestors = [
                    root
                    for root in self.module_dirs
                    if root == "." or directory == root or directory.startswith(root + "/")
                ]
                if ancestors:
                    self.module_roots[file_id] = max(ancestors, key=len)
                if ancestors and not package.endswith("_test") and not fact["path"].endswith("_test.go"):
                    root = max(ancestors, key=len)
                    self.module_roots[file_id] = root
                    relative = posixpath.relpath(directory, root)
                    module = self.module_dirs[root] + ("/" + relative if relative != "." else "")
                    self.go_imports[module].append(file_id)

    def file_id(self, scope: str) -> str:
        owner = self.nodes[self.scopes[scope]["owner"]]
        return owner["file_id"] or owner["id"]

    def lookup(self, scope: str, expression: str, visited: frozenset[tuple[str, str]]) -> Resolution:
        check_deadline(self.deadline)
        key = scope, expression
        if key in visited or len(visited) >= 32:
            return Resolution(reason="cyclic_binding")
        visited = visited | {key}
        name, *suffix = expression.split(".")
        bindings = self.bindings.get((scope, name), [])
        if self.scopes[scope]["kind"] == "class":
            # Class members are properties, not lexical variables. Only a named
            # class expression contributes its own name to this lexical scope.
            bindings = [binding for binding in bindings if binding.get("target") == scope]
        if (
            self.scopes[scope]["kind"] == "file"
            and self.nodes[scope]["language"] == "go"
            and not any(binding["kind"] == "import" for binding in bindings)
        ):
            return self.package_binding(scope, name, suffix, visited)
        if bindings:
            if len(bindings) > 1:
                targets = tuple(sorted({binding["target"] for binding in bindings if binding["kind"] == "definition"}))
                return Resolution(targets, "candidate", "ambiguous_binding", "ambiguous_binding")
            binding = bindings[0]
            if binding["kind"] == "definition":
                return self.member(binding["target"], suffix, visited)
            if binding["kind"] == "import":
                if binding.get("type_only"):
                    return Resolution(reason="type_only_import")
                return self.imported(binding, self.file_id(scope), suffix, visited)
            return Resolution(reason="local_binding")
        parent = self.scopes[scope]["parent"]
        if parent is not None:
            return self.lookup(parent, expression, visited)
        return Resolution()

    def package_binding(
        self, scope: str, name: str, suffix: list[str], visited: frozenset[tuple[str, str]]
    ) -> Resolution:
        fact = self.files[self.file_id(scope)]
        package = fact.get("metadata", {}).get("package", "")
        directory = str(PurePosixPath(fact["path"]).parent)
        results = []
        for sibling in self.packages.get((directory, package), []):
            if self.nodes[sibling]["path"].endswith("_test.go") and not fact["path"].endswith("_test.go"):
                continue
            for binding in self.bindings.get((sibling, name), []):
                if binding["kind"] == "definition":
                    results.append(self.member(binding["target"], suffix, visited))
                elif binding["kind"] == "local":
                    return Resolution(reason="package_variable")
        return self.combine(results, "go_package")

    @staticmethod
    def combine(results: list[Resolution], rule: str) -> Resolution:
        targets = tuple(sorted({target for result in results for target in result.targets}))
        uncertain = len(targets) > 1 or any(result.strength == "candidate" for result in results)
        return Resolution(
            targets,
            "candidate" if uncertain else "resolved_static",
            rule,
            "ambiguous_target" if uncertain else "unresolved_name" if not targets else "",
        )

    def member(self, target: str, suffix: list[str], visited: frozenset[tuple[str, str]]) -> Resolution:
        if not suffix:
            if target in self.rebound:
                return Resolution((target,), "candidate", "binding_write", "binding_write")
            return Resolution((target,))
        node = self.nodes[target]
        if node["kind"] != "class":
            return Resolution(reason="dynamic_receiver")
        bindings = self.bindings.get((target, suffix[0]), [])
        if len(bindings) == 1 and bindings[0]["kind"] == "definition":
            child = bindings[0]["target"]
            result = self.member(child, suffix[1:], visited)
            if not self.nodes[child].get("static") or target in self.rebound:
                return Resolution(result.targets, "candidate", "receiver_hint", "dynamic_receiver")
            return result
        return Resolution(reason="unresolved_member")

    def js_modules(self, module: str, file_id: str) -> list[str]:
        if not module.startswith("."):
            return []
        path = posixpath.normpath(posixpath.join(posixpath.dirname(self.nodes[file_id]["path"]), module))
        if path == ".." or path.startswith("../"):
            return []
        candidates = [path]
        suffix = PurePosixPath(path).suffix
        if not suffix:
            candidates.extend(
                path + extension for extension in (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs")
            )
            candidates.extend(path + "/index" + extension for extension in (".ts", ".tsx", ".js", ".jsx"))
        elif suffix in {".js", ".jsx", ".mjs", ".cjs"}:
            alternatives = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
            candidates.extend(path[: -len(suffix)] + extension for extension in alternatives[suffix])
        return [self.paths[candidate] for candidate in candidates if candidate in self.paths]

    def exported(self, file_id: str, name: str, suffix: list[str], visited: frozenset[tuple[str, str]]) -> Resolution:
        key = file_id, "export:" + name + ".".join(suffix)
        if key in visited or len(visited) >= 32:
            return Resolution(reason="cyclic_export")
        visited = visited | {key}
        export = (self.files[file_id]["exports"] or {}).get(name)
        if export is None:
            return Resolution(reason="unresolved_export")
        if self.incomplete(self.files[file_id]):
            return Resolution(reason="incomplete_export")
        if "target" in export:
            return self.member(export["target"], suffix, visited)
        if "local" in export:
            return self.lookup(file_id, ".".join([export["local"], *suffix]), visited)
        binding = {"module": export["module"], "member": export["member"]}
        return self.imported(binding, file_id, suffix, visited)

    def imported(self, binding, file_id: str, suffix: list[str], visited: frozenset[tuple[str, str]]) -> Resolution:
        module, member = binding["module"], binding["member"]
        if self.nodes[file_id]["language"] == "go":
            files = [
                target
                for target in self.go_imports.get(module, [])
                if self.module_roots.get(target) == self.module_roots.get(file_id)
            ]
            if not suffix:
                return Resolution(tuple(files), rule="go_import", reason="external_import" if not files else "")
            if not suffix[0][0].isupper():
                return Resolution(reason="unexported_member")
            results = []
            for target in files:
                for item in self.bindings.get((target, suffix[0]), []):
                    if item["kind"] == "definition":
                        results.append(self.member(item["target"], suffix[1:], visited))
            return self.combine(results, "go_import")
        files = self.js_modules(module, file_id)
        if member == "*" and not suffix:
            return Resolution(
                tuple(files),
                "candidate" if len(files) > 1 else "resolved_static",
                "esm_import",
                "external_import" if not files else "",
            )
        parts = suffix if member == "*" else [member, *suffix]
        results = [self.exported(target, parts[0], parts[1:], visited) for target in files] if parts else []
        result = self.combine(results, "esm_import")
        if len(files) > 1:
            return Resolution(result.targets, "candidate", "esm_import", "ambiguous_module")
        return result

    def resolve(self):
        edges, diagnostics = [], {}
        for fact in self.facts:
            check_deadline(self.deadline)
            issues = []
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
                result = self.resolve_reference(fact, reference, imports)
                uncertain = self.incomplete(fact) or any(
                    self.incomplete(self.files[self.nodes[target]["file_id"] or target]) for target in result.targets
                )
                if uncertain and result.targets:
                    result = Resolution(result.targets, "candidate", "incomplete_analysis", "incomplete_analysis")
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

    @staticmethod
    def incomplete(fact: dict[str, Any]) -> bool:
        return fact["nodes"][0]["parse_status"] != "ok" or fact.get("metadata", {}).get("conditional_build", False)

    def resolve_reference(self, fact, reference, imports) -> Resolution:
        if reference["kind"] == "imports":
            result = self.imported(imports[reference["id"]], fact["nodes"][0]["id"], [], frozenset())
        elif reference["dynamic"]:
            result = Resolution(reason="dynamic_receiver")
        else:
            result = self.lookup(reference["scope"], reference["expression"], frozenset())
            if reference["kind"] == "calls":
                targets = tuple(
                    target for target in result.targets if self.nodes[target]["kind"] in {"function", "method", "class"}
                )
                result = Resolution(targets, result.strength, result.rule, result.reason)
        return result
