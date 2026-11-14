# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The integration lifecycle helpers run inside plugins that are installed without the PowerContext package.

Claude Code runs its hooks with the ``python3`` on ``PATH``, and Hermes installs no dependency for its plugin, so a
helper may import only the standard library. WorkBuddy and Hermes also ship single modules, which cannot reach their
siblings, so a module they ship may not import a sibling either.

The TypeScript helpers are bundled into, or loaded as source by, plugins that each install their own dependencies, so
a TypeScript helper may import only Node.js built-in modules.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest
from generate_integration_lifecycle import COPIES

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src/powercontext_integration_lifecycle"
TYPESCRIPT_SOURCE = ROOT / "integrations/lifecycle"
# Static, side-effect, and dynamic imports, and re-exports, all name their module after `from` or `import`.
_TYPESCRIPT_SPECIFIER = re.compile(r"""\b(?:from|import)\s*\(?\s*['"]([^'"]+)['"]""")


@pytest.mark.parametrize("module", sorted(SOURCE.glob("*.py")), ids=lambda path: path.name)
def test_lifecycle_helper_imports_only_the_standard_library(module: Path) -> None:
    # Claude Code and Codex ship the whole package, so a module that only they ship may import its siblings.
    allowed = sys.stdlib_module_names if module.name in COPIES else sys.stdlib_module_names | {""}
    outside = [
        f"line {node.lineno}: {ast.unparse(node)}"
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8")))
        if isinstance(node, (ast.Import, ast.ImportFrom)) and not _top_level_names(node) <= allowed
    ]
    assert not outside, f"{module.name} imports outside the standard library: {'; '.join(outside)}"


def _top_level_names(node: ast.Import | ast.ImportFrom) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name.partition(".")[0] for alias in node.names}
    # A relative import names a sibling, which a single-module copy cannot reach.
    return {""} if node.level else {(node.module or "").partition(".")[0]}


@pytest.mark.parametrize("module", sorted(TYPESCRIPT_SOURCE.glob("*.ts")), ids=lambda path: path.name)
def test_typescript_helper_imports_only_node_builtins(module: Path) -> None:
    outside = [
        specifier
        for specifier in _TYPESCRIPT_SPECIFIER.findall(module.read_text(encoding="utf-8"))
        if not specifier.startswith("node:")
    ]
    assert not outside, f"{module.name} imports outside the Node.js built-in modules: {', '.join(outside)}"


def test_plugin_copies_match_their_source() -> None:
    # `make check` runs the same check. Running it here as well lets the unit test jobs catch a stale copy, as the
    # shared-files test does for the JavaScript copies.
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_integration_lifecycle.py"), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
