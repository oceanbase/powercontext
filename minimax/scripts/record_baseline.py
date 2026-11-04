#!/usr/bin/env python3
"""Record a baseline oracle: observations of the *unmodified* tree.

Runs the live-server suite against a baseline seekdb build, prints every statement
with its result or error code, and writes the oracle file the guard compares against.
This is the command that produces the "before the fix" evidence.

Usage:
    python scripts/record_baseline.py --server-user minimax_ctx --out tests/golden_baseline.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from context import harness  # noqa: E402
from context.db import connect  # noqa: E402


def print_observations(case_id: str, result: dict) -> None:
    print(f"== {case_id} digest={result['result_digest']}")
    for item in result["statements"]:
        state = "ok" if item["ok"] else f"error {item['code']}"
        detail = item["rows"] if item["ok"] else item["message"][:80]
        print(f"   {state:>9}  {item['statement'][:96]}  ->  {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "tests" / "golden_baseline.json"))
    parser.add_argument("--server-user", default="minimax_ctx")
    parser.add_argument("--server-password", default="minimax_ctx_dev")
    parser.add_argument("--save", action="store_true", help="also append to session_log")
    args = parser.parse_args()

    os.environ["MINIMAX_DB_USER"] = args.server_user
    os.environ["MINIMAX_DB_PASSWORD"] = args.server_password

    cases = harness.load_suite(harness.default_tests_path())
    recorded = []
    for case in cases:
        if case.get("check") != "live_server":
            continue
        result = harness.run_case(dict(case, stop_on_error=False))
        print_observations(case["id"], result)
        recorded.append(
            {
                "case_id": case["id"],
                "spec_id": case["spec_id"],
                "title": case["title"],
                "kind": case["kind"],
                "codes": [[item["ok"], item["code"]] for item in result["statements"]],
                "rows": [
                    [None if value is None else str(value) for value in row]
                    for item in result["statements"]
                    for row in item["rows"]
                ],
                "messages": [item["message"] for item in result["statements"]],
                "result_digest": result["result_digest"],
                "server": result["server"],
            }
        )

    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "oracle for the guard; produced by the unmodified tree",
        "server": recorded[0]["server"] if recorded else "",
        "cases": recorded,
    }
    path = Path(args.out)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")

    if args.save:
        conn = connect()
        try:
            with conn.cursor() as cur:
                for entry in recorded:
                    cur.execute(
                        """
                        UPDATE test_case SET oracle_key = %s, title = %s
                        WHERE case_id = %s
                        """,
                        (entry["result_digest"], entry["title"],
                         f"{entry['case_id']}:{entry['spec_id']}"),
                    )
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
