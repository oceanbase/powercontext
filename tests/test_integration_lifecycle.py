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

Claude Code runs its hooks with the ``python3`` on ``PATH``, and Hermes installs no dependency for its plugin. Each
plugin also puts its copy of a helper where it imports it. A helper may therefore import only the standard library,
and no sibling.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src/powercontext_integration_lifecycle"


@pytest.mark.parametrize("module", sorted(SOURCE.glob("*.py")), ids=lambda path: path.name)
def test_lifecycle_helper_imports_only_the_standard_library(module: Path) -> None:
    outside = [
        f"line {node.lineno}: {ast.unparse(node)}"
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8")))
        if isinstance(node, (ast.Import, ast.ImportFrom)) and not _top_level_names(node) <= sys.stdlib_module_names
    ]
    assert not outside, f"{module.name} imports outside the standard library: {'; '.join(outside)}"


def _top_level_names(node: ast.Import | ast.ImportFrom) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name.partition(".")[0] for alias in node.names}
    # A relative import names a sibling, which a plugin's copy cannot reach.
    return {""} if node.level else {(node.module or "").partition(".")[0]}


def test_plugin_copies_match_their_source() -> None:
    # `make check` runs the same check. Running it here as well lets the unit test jobs catch a stale copy, as the
    # shared-files test does for the JavaScript and TypeScript copies.
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_integration_lifecycle.py"), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
