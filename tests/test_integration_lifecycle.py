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
a TypeScript helper may import only Node.js built-in modules. Kimi Code and ZCode run JavaScript copies of them without
a build step, so the generator removes the types, and refuses TypeScript it cannot remove.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest
from generate_integration_lifecycle import COPIES, javascript

from powercontext_integration_lifecycle.user_prompt_submit import UserPromptEvent

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
    # `make check` runs the same check. Running it here as well lets the unit test jobs catch a stale copy.
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_integration_lifecycle.py"), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("typescript", "expected"),
    [
        (
            "export function scrub(text: string, authorization?: string): string {",
            "export function scrub(text, authorization) {",
        ),
        ("] as const", "]"),
    ],
)
def test_javascript_copies_drop_parameter_types_and_const_assertions(typescript: str, expected: str) -> None:
    assert javascript(TYPESCRIPT_SOURCE / "prompt-privacy.ts", f"{typescript}\n") == f"{expected}\n"


@pytest.mark.parametrize(
    "typescript",
    [
        "export function scrub(text: string = ''): string {",
        "export function first<T>(items: T[]): T {",
        "export function scrub(options: { limit: number, text: string }): string {",
        "  return wrap<Error>(value)",
        "  return wrap<Promise<Error>>(value)",
        "export type Verdict = 'allow' | 'block'",
        "const limit: number = 3",
        "const verdict = value as Verdict",
        "const scrub = (text: string) => text",
        "export function scrub(this: void, text: string): string {\n  return text\n}",
        "export function scrub(this: Map<string, number>, text: string) {",
        "export function scrub(\n  this: void,\n  text: string,\n): string {",
        "class Scrubber {\n  scrub(this: Scrubber, text: string): string {",
        "class Scrubber {\n  scrub(\n    this: Scrubber,\n    text: string,\n  ) {",
        "class Scrubber {\n  scrub(text?: string) {",
        "const scrubber = {\n  scrub(text: string) {",
        "try {\n  scrub()\n} catch (error: unknown) {",
        "const scrub = (\n  text: string,\n) => text",
        "const scrub = flag\n  ? (text): string => text\n  : other",
        "const scrub = (text?) => text",
        "const scrub = (\n  limit?,\n  text: string,\n) => text",
        "const { limit }: Options = options",
        "  const size = Buffer.byteLength(characters[start - 1]!, 'utf8')",
        "  return scrub(text)!.trim()",
        "  return limit!",
    ],
)
def test_javascript_copies_refuse_typescript_they_cannot_drop(typescript: str) -> None:
    # Each would otherwise lose code, change its meaning, or leave the copy unloadable.
    with pytest.raises(SystemExit, match="cannot drop"):
        javascript(TYPESCRIPT_SOURCE / "prompt-privacy.ts", f"{typescript}\n")


@pytest.mark.parametrize(
    "javascript_source",
    [
        "scrub(this)",
        "const limit = this.limit",
        "const note = 'scrub(this: void)' // scrub(this: void)",
        "const pattern = /scrub\\(this: void\\)/u",
        "const pick = choose(flag ? this : other)",
        "const options = scrub({ limit: 3, [key]: [flag ? 1 : 2] })",
        "const limit = options?.limit ? options.limit : (fallback ?? 3)",
        "const limit = flag\n  ? (first)\n  : second",
        "switch (kind) {\n  case 'text':\n    break\n  default:\n    break\n}",
        "switch (true) {\n  case text.startsWith('key'):\n    break\n  case (flag ? 1 : 2):\n    break\n}",
        "const limit = flag ? (first) : second",
        "outer: for (const item of items) {\n  break outer\n}",
        "const changed = limit!==other && (first)!=second && !flag && ![value].includes(other)",
    ],
)
def test_javascript_copies_keep_colons_and_negations_that_javascript_allows(javascript_source: str) -> None:
    source = f"{javascript_source}\n"
    assert javascript(TYPESCRIPT_SOURCE / "prompt-privacy.ts", source) == source


@pytest.mark.parametrize(
    "typescript",
    [
        "const note = `first line\n] as const\n`",
        "const note = `${`\n] as const\n`}`",
        "const note = '`' + `\n] as const\n` + '`'",
        "const note = /[`]/.source + `\n] as const\n`",
        "const note = 1 /* ` */ + `\n] as const\n`",
        "const count = 2\n* `\n] as const\n*`.length",
        "const note = 'first line \\\n] as const'",
        "const half = (total) / 2",
        'let of = 4\nconst half = of / 2; const note = "/ ] as const"; // "',
        "const café = 1",
        "const x² = 1",
        'const half = this.#return / 2; const note = "/ ] as const"; // "',
        'const half = x\u200dreturn / 2; const note = "/ ] as const"; // "',
        'const half = x\\u{41}return / 2; const note = "/ ] as const"; // "',
    ],
)
def test_javascript_copies_refuse_literals_they_cannot_follow(typescript: str) -> None:
    # The conversion rewrites code only, so it must know where every literal ends; it would otherwise drop ` as const`
    # from a literal's runtime value.
    with pytest.raises(SystemExit, match="cannot convert"):
        javascript(TYPESCRIPT_SOURCE / "prompt-privacy.ts", f"{typescript}\n")


@pytest.mark.parametrize(
    "typescript",
    [
        "/*\n] as const\n*/",
        "const pattern = /] as const/u",
        "const note = '] as const' // ] as const",
        "export function scrub(text: string) { // (text: string): string {",
        'const half = limits.return / 2; const note = "/ ] as const"; // "',
        'export default /\'/; const note = "\' ] as const"; // "',
        '// note\f/*\nconst note = "*/ ] as const"; // "',
        '#!/usr/bin/env node /*\nconst note = "*/ ] as const"; // "',
        'const kinds = [...typeof /\'/]; const note = "\' ] as const"; // "',
        'const inside = 1. in /\'/; const note = "\' ] as const"; // "',
        'const half = limits?.return / 2; const note = "/ ] as const"; // "',
    ],
)
def test_javascript_copies_leave_comments_and_literals_unchanged(typescript: str) -> None:
    converted = javascript(TYPESCRIPT_SOURCE / "prompt-privacy.ts", f"{typescript}\n")
    assert converted.count("] as const") == typescript.count("] as const")
    assert converted.count("(text: string): string {") == typescript.count("(text: string): string {")


def test_prompt_event_repr_leaves_out_the_prompt_and_the_working_directory() -> None:
    event = UserPromptEvent(
        prompt="Remember the synthetic launch phrase.",
        cwd="/workspace/synthetic-private-project",
        session_id="session-1",
        prompt_id="prompt-1",
    )

    assert "synthetic launch phrase" not in repr(event)
    assert "synthetic-private-project" not in repr(event)
