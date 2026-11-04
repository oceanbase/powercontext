"""Guard: prove the change is real on a live server.

``guard.py`` answers three questions with data from a running seekdb:

1. Does the suite fail on the unmodified tree?  (the acceptance cases must be red)
2. Does it pass after the patch?  (every acceptance case green)
3. Do the compatibility cases stay green?  (nothing that worked may break)

The oracles in ``tests/golden_baseline.json`` were recorded before any code changed.
Acceptance cases invert the expectation: whatever the baseline rejected must now be
accepted and produce the value the spec states.  Compatibility cases keep it.

Usage:
    python context/guard.py --stage baseline   # current tree must still be red
    python context/guard.py --stage after      # patched tree must be green
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from context import harness  # noqa: E402
from context.db import connect, ensure_schema  # noqa: E402

GOLDEN = ROOT / "tests" / "golden_baseline.json"

# What each acceptance case must produce once the feature exists.  The baseline
# oracle shows the statement was rejected with 1064; the expectation is the value
# the spec promises, which is also what the regression test asserts.
AFTER_EXPECTATIONS = {
    "TC-INSERT-01": [
        (True, 0), (True, 0), (True, 0), (True, 0), (True, 0), (True, 0),
    ],
    "TC-UPDATE-01": [
        (True, 0), (True, 0), (True, 0), (True, 0), (True, 0), (True, 0),
    ],
    "TC-DELETE-01": [
        (True, 0), (True, 0), (True, 0), (True, 0), (True, 0),
    ],
    "TC-READ-01": [
        (True, 0), (True, 0), (True, 0), (True, 0), (True, 0), (True, 0), (True, 0),
    ],
}

AFTER_ROWS = {
    # (case_id, statement_number) -> rows the feature must return
    "TC-INSERT-01": {4: [["1", "[0,0,0,0]", "widen"], ["2", "[0,42,0,0]", "target"]],
                     5: [["2"]]},
    "TC-UPDATE-01": {3: [["1", "[4,20,3,1]", "after"]], 4: [["20"]]},
    "TC-DELETE-01": {3: [["2", "[9,3,2,1]"]]},
    "TC-READ-01": {0: [["30"]], 1: [["null"]], 5: [["5"]], 6: [["null"]]},
}


def evaluate_baseline(oracle: dict[str, Any], observed: dict[str, Any]) -> tuple[bool, str]:
    """On the unmodified tree the observed result must equal the recorded oracle."""
    ok, problems = harness.compare(oracle, [
        harness.SqlResult(statement=item["statement"], ok=item["ok"], code=item["code"],
                          message=item["message"], rows=item["rows"])
        for item in observed["statements"]
    ])
    if observed["result_digest"] != oracle["result_digest"]:
        problems.append(
            f"digest drift: recorded {oracle['result_digest']} vs now {observed['result_digest']}"
        )
        ok = False
    return ok, "; ".join(problems) if problems else "matches the unmodified tree"


def evaluate_after(oracle: dict[str, Any], observed: dict[str, Any]) -> tuple[bool, str]:
    case_id = observed["case_id"]
    results = [
        harness.SqlResult(statement=item["statement"], ok=item["ok"], code=item["code"],
                          message=item["message"], rows=item["rows"])
        for item in observed["statements"]
    ]
    if oracle["kind"] == "compat":
        return evaluate_baseline(oracle, observed)
    want = AFTER_EXPECTATIONS[case_id]
    problems = []
    for index, (expected, got) in enumerate(zip(want, results)):
        if (got.ok, got.code if not got.ok else 0) != (expected[0], expected[1]):
            state = "ok" if got.ok else f"error {got.code}: {got.message[:90]}"
            problems.append(f"#{index}: expected success, got {state}")
    for statement, rows in AFTER_ROWS.get(case_id, {}).items():
        if statement >= len(results):
            problems.append(f"#{statement}: statement missing")
            continue
        got_rows = [[None if value is None else str(value) for value in row]
                    for row in results[statement].rows]
        want_rows = [["null" if value == "null" else value for value in row] for row in rows]
        got_rows = [["null" if value is None else value for value in row] for row in got_rows]
        if got_rows != want_rows:
            problems.append(f"#{statement}: rows expected {want_rows}, got {got_rows}")
    return (not problems), "; ".join(problems) if problems else "behaves as the spec states"


def run_grammar_stage(stage: str) -> dict[str, Any]:
    """Run the out-of-tree grammar probe and report whether a node now exists.

    On the baseline the probe must *not* find an element-access node (that is the
    defect); after the patch it must.  The probe is a separate program so it can be
    pointed at any checkout without touching this tree.
    """
    check = os.environ.get("MINIMAX_GRAMMAR_CHECK") or str(ROOT / "grammar_check" / "static_check.py")
    command = f'"{sys.executable}" "{check}"'
    started = time.time()
    proc = subprocess.run(command, text=True, capture_output=True, shell=True)
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    verdict = next((line.split(":", 1)[1].strip() for line in output.splitlines()
                    if line.startswith("GRAMMAR-CHECK:")), "")
    found_node = "element-access node: none" not in output
    if stage == "baseline":
        passed = proc.returncode != 0 and not found_node
        detail = "no element-access node in the grammar (the defect reproduces)"
    else:
        passed = verdict == "PASS"
        node_line = next((line for line in output.splitlines()
                          if line.startswith("element-access node:")), "")
        detail = node_line or "grammar probe produced no verdict"
    return {
        "check": "parser_only",
        "status": "pass" if passed else "fail",
        "elapsed_ms": int((time.time() - started) * 1000),
        "detail": detail,
        "output": output[-4000:],
    }


def register_oracle_checks(cur, oracles: dict[str, dict[str, Any]]) -> None:
    """One row per guard check, so the readiness query can see which checks are red."""
    for case_id, oracle in oracles.items():
        cur.execute(
            """
            INSERT INTO test_case(case_id, spec_id, check_name, title, oracle_key, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW(6))
            ON DUPLICATE KEY UPDATE title = VALUES(title), oracle_key = VALUES(oracle_key)
            """,
            (
                f"guard:{case_id}",
                oracle["spec_id"],
                "live_server",
                oracle["title"],
                oracle["result_digest"],
            ),
        )


def run(stage: str, save: bool) -> dict[str, Any]:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    oracles = {case["case_id"]: case for case in golden["cases"]}
    cases = harness.load_suite(harness.default_tests_path())

    os.environ.setdefault("MINIMAX_DB_USER", "minimax_test")
    os.environ.setdefault("MINIMAX_DB_PASSWORD", "minimax_test_dev")

    results = []
    for case in cases:
        if case.get("check") != "live_server":
            continue
        observed = harness.run_case(dict(case, stop_on_error=False))
        oracle = oracles[case["id"]]
        if stage == "after":
            ok, detail = evaluate_after(oracle, observed)
        else:
            ok, detail = evaluate_baseline(oracle, observed)
        results.append(
            {
                "case_id": case["id"],
                "spec_id": case["spec_id"],
                "kind": oracle["kind"],
                "check": "live_server",
                "stage": stage,
                "status": "pass" if ok else "fail",
                "basis": "baseline" if stage == "baseline" else "feature",
                "detail": detail,
                "result_digest": observed["result_digest"],
                "statements": observed["statements"],
            }
        )
        print(f"[{'pass' if ok else 'FAIL':4}] {case['id']:<14} ({oracle['kind']}) {detail}")

    grammar = run_grammar_stage(stage)
    grammar.update({"case_id": "TC-PARSER-01", "kind": "acceptance", "spec_id": "ARR-005",
                    "stage": stage, "basis": "baseline" if stage == "baseline" else "feature"})
    results.append(grammar)
    print(f"[{grammar['status']:4}] {'grammar-check':<14} {grammar['detail']}")

    acceptance = [r for r in results if r["kind"] == "acceptance"]
    compat = [r for r in results if r["kind"] == "compat"]
    if stage == "baseline":
        summary_ok = all(r["status"] == "pass" for r in results)
        verdict = (
            "baseline reproduced: every case matches the tree as it is, and every "
            f"acceptance case is still red ({len(acceptance)} cases)"
        )
    else:
        acceptance_ok = all(r["status"] == "pass" for r in acceptance)
        compat_ok = all(r["status"] == "pass" for r in compat)
        summary_ok = acceptance_ok and compat_ok
        verdict = (
            f"acceptance {sum(1 for r in acceptance if r['status'] == 'pass')}/{len(acceptance)} "
            f"pass, compatibility {sum(1 for r in compat if r['status'] == 'pass')}/{len(compat)} pass"
        )

    payload = {
        "stage": stage,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "success": summary_ok,
        "verdict": verdict,
        "results": results,
    }

    # Progress rows are always written; the raw per-statement observations are only
    # appended when --save is given.  Results carry processed SQL only (no credentials).
    ensure_schema()
    _register_checks(oracles)
    if save:
        _append_session_log(results)
    print(verdict)
    return payload


def _with_ctx_account(fn):
    """Run a database write with the spec-store account, never the scenario account."""
    saved = (os.environ.get("MINIMAX_DB_USER"), os.environ.get("MINIMAX_DB_PASSWORD"))
    os.environ["MINIMAX_DB_USER"] = os.environ.get("MINIMAX_CTX_USER", "minimax_ctx")
    os.environ["MINIMAX_DB_PASSWORD"] = os.environ.get("MINIMAX_CTX_PASSWORD", "minimax_ctx_dev")
    try:
        return fn()
    finally:
        os.environ["MINIMAX_DB_USER"], os.environ["MINIMAX_DB_PASSWORD"] = saved


def _register_checks(oracles: dict[str, dict[str, Any]]) -> None:
    def write() -> None:
        ensure_schema()
        conn = connect()
        try:
            with conn.cursor() as cur:
                register_oracle_checks(cur, oracles)
        finally:
            conn.close()

    _with_ctx_account(write)


def _append_session_log(results: list[dict[str, Any]]) -> None:
    def write() -> None:
        conn = connect()
        try:
            with conn.cursor() as cur:
                for item in results:
                    cur.execute(
                        """
                        INSERT INTO session_log(task_id, role, action, content, created_at)
                        VALUES (%s, %s, %s, %s, NOW(6))
                        """,
                        (
                            os.environ.get("MINIMAX_TASK_ID", "task-arr-element-dml"),
                            "guard",
                            f"{item['stage']}:{item['case_id']}",
                            json.dumps(item, ensure_ascii=False),
                        ),
                    )
        finally:
            conn.close()

    _with_ctx_account(write)
    print(f"saved {len(results)} check results to session_log")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["baseline", "after"], required=True)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--save", action="store_true", help="append results to session_log")
    args = parser.parse_args()
    payload = run(args.stage, args.save)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
