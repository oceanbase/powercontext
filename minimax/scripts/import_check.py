"""Import every module and report unused names that a linter would flag.

Ruff is not installable in this environment, so this small check keeps the shipped
scripts honest: it imports each module (catching import-time errors) and lists names
that are imported but never referenced.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

MINIMAX_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MINIMAX_ROOT))

MODULES = [
    "context.db",
    "context.powercontext",
    "context.build_context",
    "context.harness",
    "context.scenarios",
    "context.guard",
    "context.operations",
    "context.minimax",
    "context.patchutil",
    "context.config",
    "context.main",
    "grammar_check.static_check",
]


def unused_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[(alias.asname or alias.name).split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported[alias.asname or alias.name] = node.lineno
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    used |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    source = path.read_text(encoding="utf-8")
    return [
        f"{path.relative_to(MINIMAX_ROOT)}:{line} unused import {name}"
        for name, line in imported.items()
        if name != "annotations" and name not in used and source.count(name) <= 1
    ]


def main() -> int:
    failures = 0
    for module in MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            failures += 1
            print(f"IMPORT FAIL {module}: {type(exc).__name__}: {exc}")
    problems = []
    for path in sorted(MINIMAX_ROOT.rglob("*.py")):
        if ".git" in path.parts:
            continue
        problems.extend(unused_imports(path))
    for problem in problems:
        if "minimax.py" in problem:
            # Deliberate re-export: the agent driver and the operations layer import the
            # patch helpers from this module, which forwards them from ``patchutil``.
            continue
        print(problem)
    print(f"imported {len(MODULES)} modules, {failures} failures, {len(problems)} unused imports")
    return 0 if failures == 0 and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
