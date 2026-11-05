"""Focused-request validation: a model patch must survive the pipeline's checks.

These tests pin the behaviours that the real MiniMax runs exposed:
verbatim code excerpts, header completion, path whitelisting and clear diagnostics.
"""

from __future__ import annotations

import sys
from pathlib import Path

MINIMAX_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MINIMAX_ROOT))

from context import patchutil  # noqa: E402

MISSING_HEADERS = """diff --git a/src/sql/parser/sql_parser_mysql_mode.y b/src/sql/parser/sql_parser_mysql_mode.y
@@ -3171,4 +3171,8 @@
 | '[' expr_list ']'
 {
+| column_ref '[' expr ']'
+{
+  malloc_non_terminal_node($$, result->malloc_pool_, T_OP_ARRAY_INDEX, 2, $1, $3);
+}
   malloc_non_terminal_node($$, result->malloc_pool_, T_FUN_SYS_ARRAY, 1, $2);
 }
"""


def test_missing_headers_are_derived_from_the_diff_line():
    normalized = patchutil.normalize(MISSING_HEADERS)
    assert "--- a/src/sql/parser/sql_parser_mysql_mode.y" in normalized
    assert "+++ b/src/sql/parser/sql_parser_mysql_mode.y" in normalized
    assert patchutil.files_in(normalized) == ["src/sql/parser/sql_parser_mysql_mode.y"]


def test_normalize_keeps_existing_headers_and_metadata():
    patch = (
        "diff --git a/src/query/api/query/parser/ob_item_type.h "
        "b/src/query/api/query/parser/ob_item_type.h\n"
        "index 1234567..89abcde 100644\n"
        "--- a/src/query/api/query/parser/ob_item_type.h\n"
        "+++ b/src/query/api/query/parser/ob_item_type.h\n"
        "@@ -766,3 +766,4 @@\n"
        "   T_FUN_SYS_ARRAY = 1737,\n"
        "+  T_OP_ARRAY_INDEX = 1753,\n"
        "   T_FUN_SYS_L1_DISTANCE = 1738,\n"
    )
    assert patchutil.normalize(patch).count("--- a/") == 1
    assert "index 1234567..89abcde 100644" in patchutil.normalize(patch)


def test_placeholder_path_is_rejected():
    patch = "diff --git a/placeholder b/placeholder\n--- a/placeholder\n+++ b/placeholder\n@@ -0,0 +1 @@\n+x\n"
    ok, offenders = patchutil.validate_paths(patch)
    assert not ok and offenders == ["placeholder"]
    assert "outside the task scope" in patchutil.reject_reason(patch)


def test_allowed_paths_pass_validation():
    patch = (
        "diff --git a/src/sql/parser/sql_parser_mysql_mode.y "
        "b/src/sql/parser/sql_parser_mysql_mode.y\n"
        "--- a/src/sql/parser/sql_parser_mysql_mode.y\n"
        "+++ b/src/sql/parser/sql_parser_mysql_mode.y\n"
        "@@ -1,1 +1,2 @@\n"
        " a\n"
        "+b\n"
    )
    ok, offenders = patchutil.validate_paths(patch)
    assert ok and offenders == []


def test_missing_target_file_is_reported(tmp_path: Path):
    patch = (
        "diff --git a/src/sql/nope.cpp b/src/sql/nope.cpp\n"
        "--- a/src/sql/nope.cpp\n"
        "+++ b/src/sql/nope.cpp\n"
        "@@ -1,1 +1,2 @@\n"
        " a\n"
        "+b\n"
    )
    reason = patchutil.reject_reason(patch, tmp_path)
    assert "do not exist" in reason


def test_excerpts_are_emitted_verbatim(tmp_path: Path):
    """A rendered context must not re-indent code, or the model copies the indentation."""
    from context import build_context

    repo = tmp_path / "repo"
    (repo / "src" / "sql").mkdir(parents=True)
    (repo / "src" / "sql" / "demo.cpp").write_text(
        "namespace x {\n  T_FUNC_SYS_LAMBDA = 1753,\n}\n", encoding="utf-8"
    )
    source = {
        "path": "src/sql/demo.cpp", "symbol": "T_FUNC_SYS_LAMBDA", "line": 2,
        "sha": "x", "exists": True, "quote": "", "note": "",
        "snippet": "namespace x {\n  T_FUNC_SYS_LAMBDA = 1753,\n}",
    }
    context = {
        "task": None,
        "specs": [{"id": "ARR-005", "kind": "must-change", "title": "t", "statement": "s",
                   "evidence": {}, "sources": [source]}],
        "checks": [], "tests": [], "environment": [], "results": [],
    }
    text = build_context.render(context, with_code=True)
    assert "\n  T_FUNC_SYS_LAMBDA = 1753,\n" in text
    assert "    T_FUNC_SYS_LAMBDA" not in text
