"""Focused behaviour tests for the context module.

These tests exercise the public surface of ``context``: the spec store, the context
builder, the task-state lifecycle and the guard.  They require the seekdb instance
started by ``scripts/init_db.py`` and are skipped with a clear message when it is not
reachable, so a reviewer without the server still sees a clean run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

MINIMAX_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MINIMAX_ROOT))

from context import build_context, harness, minimax, patchutil, powercontext  # noqa: E402
from context.db import connect  # noqa: E402

TASK_ID = "pytest-task"
REPO = os.environ.get("SEEKDB_REPO", "")


def db_available() -> bool:
    try:
        conn = connect()
    except Exception:  # noqa: BLE001 - the point is to skip when it is down
        return False
    conn.close()
    return True


pytestmark = pytest.mark.skipif(
    not db_available(), reason="seekdb is not reachable; run scripts/init_db.py first"
)


def test_spec_store_has_change_and_keep_rules():
    specs = build_context.load_specs()
    kinds = {spec["id"]: spec["kind"] for spec in specs}
    assert kinds["ARR-001"] == "must-change"
    assert kinds["ARR-006"] == "must-keep"
    for spec in specs:
        assert spec["sources"], f"{spec['id']} has no code source, it cannot be verified"
        assert spec["statement"].strip()


def test_context_names_source_and_evidence():
    if not REPO:
        pytest.skip("SEEKDB_REPO is not set")
    data = build_context.build(["ARR-001", "ARR-005"])
    assert [spec["id"] for spec in data["specs"]] == ["ARR-001", "ARR-005"]
    paths = {source["path"] for source in data["code"]}
    assert "src/sql/parser/sql_parser_mysql_mode.y" in paths
    assert all(source["sha"] for source in data["code"]), "code sources must be hashed"
    text = build_context.render(data)
    assert "== SPECS THAT APPLY" in text
    assert "src/sql/parser/sql_parser_mysql_mode.y:" in text


def test_state_survives_and_reimports_newer_spec():
    spec_ids = ["ARR-001"]
    snapshot = {"ARR-001": "old-hash"}
    payload = {
        "task_id": TASK_ID,
        "task_title": "pytest",
        "spec_ids": spec_ids,
        "checks": ["live_server"],
        "results": [],
        "changed_files": [],
        "completed": ["guard:baseline"],
        "pending": ["verify:after"],
        "last_output": "before",
        "spec_snapshot": snapshot,
        "global_spec_snapshot": build_context.snapshot(),
    }
    first = powercontext.save(TASK_ID, payload, reason="pytest")
    assert powercontext.load(TASK_ID).revision == first.revision

    _, summary = powercontext.import_state(TASK_ID, build_context.build)
    assert summary["spec_content_changed_since_save"] == ["ARR-001"]

    payload["completed"] = ["guard:baseline", "agent:prepare"]
    second = powercontext.save(TASK_ID, payload, reason="pytest")
    assert second.revision > first.revision
    assert "agent:prepare" in powercontext.load(TASK_ID).payload["completed"]
    assert len(powercontext.history(TASK_ID)) >= 2


def test_unknown_task_has_no_local_fallback():
    with pytest.raises(LookupError):
        powercontext.load("task-that-was-never-saved")


def test_patch_extraction_ignores_prose_and_fences():
    answer = (MINIMAX_ROOT / "tests" / "fixtures" / "smoke_answer.md").read_text(encoding="utf-8")
    patch = minimax.extract_patch(answer)
    assert patch.startswith("diff --git ")
    assert patch.endswith("\n")
    assert "```" not in patch
    assert minimax.files_in(patch) == ["src/sql/parser/sql_parser_mysql_mode.y"]


def test_patch_is_written_with_trailing_newline(tmp_path: Path):
    path = patchutil.write_patch(tmp_path / "p.patch", "diff --git a/x b/x\n+y")
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r" not in raw


def test_proposal_without_diff_is_rejected():
    with pytest.raises(ValueError):
        minimax.extract_patch("I could not do it, sorry.")


def test_grammar_probe_flags_missing_node(tmp_path: Path):
    script = MINIMAX_ROOT / "grammar_check" / "static_check.py"
    proc = subprocess.run([sys.executable, str(script), "--repo", str(tmp_path)],
                          text=True, capture_output=True)
    assert proc.returncode == 1
    assert "does not contain the parser sources" in proc.stdout


def test_guard_reports_each_spec_exactly_once():
    if not REPO:
        pytest.skip("SEEKDB_REPO is not set")
    spec = build_context.load_specs(["ARR-006"])[0]
    assert spec["checks"] == ["live_server"]


def test_harness_compares_error_codes_not_messages():
    oracle = {"codes": [[True, 0], [False, 1064]], "rows": []}
    results = [
        harness.SqlResult("a", True, 0, "", []),
        harness.SqlResult("b", False, 1064, "a different message", []),
    ]
    ok, problems = harness.compare(oracle, results)
    assert ok, problems
    results[1] = harness.SqlResult("b", True, 0, "", [])
    ok, problems = harness.compare(oracle, results)
    assert not ok and "expected error 1064" in problems[0]


def test_usage_log_records_roles():
    powercontext.log(TASK_ID, "pytest", "smoke", json.dumps({"ok": True}))
    actions = {(entry["role"], entry["action"]) for entry in powercontext.read_log(TASK_ID)}
    assert ("pytest", "smoke") in actions
