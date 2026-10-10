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

"""Grade a MemoryCode recall session with MemoryCode's own object checks.

The object extraction and per-instruction scoring follow ``code/extract_objects.py`` and
``code/evaluate_model_output.py`` of https://github.com/Cohere-Labs-Community/MemoryCode (Apache-2.0), including their
quirks: an output that defines no object an instruction applies to is not scored for that instruction, and a
name check looks at the first name of each object. Only the delivery differs: each eval query's code is read from its
own file, which is graded whole when it parses (upstream takes the first fenced block of a chat answer, which would cut
a file at a fenced example in a docstring), and a missing or empty file scores 0 on every instruction, as an answer
without code does upstream.

``expected.json`` is uploaded only with the recall step's tests, so no earlier session can read the instructions.
The trial reward is 1 only when at least one instruction is scored and every scored instruction passes in every output;
``memorycode_score`` keeps the upstream macro average over outputs and is left out, as upstream leaves the dialogue out,
when no instruction is scored.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

OBJECTS = (
    "function",
    "function argument",
    "function docstring",
    "function try",
    "function assert",
    "function annotation",
    "function decorator",
    "class",
    "class decorator",
    "method",
    "method docstring",
    "method try",
    "method assert",
    "method annotation",
    "method decorator",
    "attribute",
    "variable",
    "import",
    "comment",
)


def _decorator_id(decorator: ast.expr) -> str | None:
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    if isinstance(decorator, ast.Call):
        return _decorator_id(decorator.func)
    return None


def _decorator_ids(decorators: list[ast.expr]) -> list[str]:
    return [name for name in map(_decorator_id, decorators) if name]


def _annotations(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.expr]:
    annotations = [arg.annotation for arg in node.args.args if arg.annotation]
    if node.returns:
        annotations.append(node.returns)
    return annotations


def _self_attributes(nodes: list[ast.stmt]) -> list[str]:
    return [
        target.attr
        for stmt in nodes
        if isinstance(stmt, ast.Assign)
        for target in stmt.targets
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self"
    ]


def _variable_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Tuple):
            names.extend(element.id for element in target.elts if isinstance(element, ast.Name))
    return names


class _Extractor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.objects: dict[str, list] = {name: [] for name in OBJECTS}
        self.current_class: ast.ClassDef | None = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Upstream visits FunctionDef only, so async functions are not collected.
        doc = ast.get_docstring(node)
        tries = [n for n in node.body if isinstance(n, ast.Try)]
        asserts = [n for n in node.body if isinstance(n, ast.Assert)]
        if self.current_class is None:
            self.objects["function"].append([node.name])
            self.objects["function argument"].append([arg.arg for arg in node.args.args])
            self.objects["function docstring"].append([doc] if doc else [])
            self.objects["function try"].append(tries)
            self.objects["function assert"].append(asserts)
            self.objects["function annotation"].append(_annotations(node))
            self.objects["function decorator"].append(_decorator_ids(node.decorator_list))
        elif node.name == "__init__":
            self.objects["attribute"].append(_self_attributes(node.body))
        elif not node.name.startswith("__"):
            self.objects["method"].append([node.name])
            self.objects["method docstring"].append([doc] if doc else [])
            self.objects["method try"].append(tries)
            self.objects["method assert"].append(asserts)
            self.objects["method annotation"].append(_annotations(node))
            self.objects["method decorator"].append(_decorator_ids(node.decorator_list))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.objects["class"].append([node.name])
        self.objects["class decorator"].append(_decorator_ids(node.decorator_list))
        previous, self.current_class = self.current_class, node
        self.generic_visit(node)
        self.current_class = previous

    def visit_Assign(self, node: ast.Assign) -> None:
        self.objects["variable"].extend([name] for name in _variable_names(node))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.objects["variable"].extend([name] for name in _variable_names(node))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.objects["import"].extend(alias.name for alias in node.names)
        self.generic_visit(node)


def extract_code(text: str) -> str:
    """Return the first fenced block, as upstream does for model answers, or the whole text."""

    for pattern in (r"```python\n(.*?)```", r"```(.*?)```"):
        if found := re.findall(pattern, text, re.DOTALL):
            return found[0]
    return text


def objects(code: str) -> dict[str, list]:
    extractor = _Extractor()
    extractor.visit(ast.parse(code))
    extractor.objects["comment"] = [re.findall(r"#.*", code)]
    return extractor.objects


def _mean_is_one(values: list[bool]) -> bool:
    # numpy's mean of an empty list is nan, which never equals 1.
    return bool(values) and sum(values) == len(values)


def compute_score(text: str, object_type: str, regex: object) -> float | None:
    """Score one instruction on a chat answer, as upstream does; None when no object it applies to is defined."""

    return score_code(extract_code(text), object_type, regex)


def file_code(text: str) -> str:
    """Return a file's code: the whole file when it parses, else its first fenced block, as for a chat answer."""

    try:
        ast.parse(text)
    except SyntaxError:
        return extract_code(text)
    return text


def score_code(code: str, object_type: str, regex: object) -> float | None:
    try:
        found_objects = objects(code)[object_type]
    except SyntaxError:
        return 0.0
    if not found_objects and object_type not in ("comment", "import"):
        return None
    if isinstance(regex, bool):
        present = [len(found) > 0 for found in found_objects]
        return float(all(present) if regex else not any(present))
    if isinstance(regex, list):
        name, value = regex
        if object_type not in ("comment", "import"):
            return float(_mean_is_one([(name in found) == value for found in found_objects]))
        return float((name in found_objects) == value)
    return float(_mean_is_one([bool(re.match(rf"{regex}", found[0])) for found in found_objects if len(found) > 0]))


def grade(expected: dict, workspace: Path) -> dict:
    outputs = []
    for output in expected["outputs"]:
        path = workspace / output["file"]
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        checks = []
        for object_type, regex in expected["history_regex"]:
            score = 0.0 if not text.strip() else score_code(file_code(text), object_type, regex)
            checks.append({"object": object_type, "regex": regex, "score": score})
        scored = [check["score"] for check in checks if check["score"] is not None]
        outputs.append({
            "file": output["file"],
            "present": bool(text.strip()),
            "score": sum(scored) / len(scored) if scored else None,
            "checks": checks,
        })
    output_scores = [output["score"] for output in outputs if output["score"] is not None]
    applicable = [check["score"] for output in outputs for check in output["checks"] if check["score"] is not None]
    result = {
        "reward": int(bool(applicable) and all(score == 1 for score in applicable)),
        "applicable_checks": len(applicable),
        "passed_checks": sum(score == 1 for score in applicable),
    }
    if output_scores:
        result["memorycode_score"] = sum(output_scores) / len(output_scores)
    return {**result, "outputs": outputs}


def main(expected_path: Path, workspace: Path, verifier_dir: Path) -> None:
    result = grade(json.loads(expected_path.read_text(encoding="utf-8")), workspace)
    rewards = {key: value for key, value in result.items() if key != "outputs"}
    (verifier_dir / "reward.json").write_text(json.dumps(rewards) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
