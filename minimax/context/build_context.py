"""Developer-context assembly.

The context a developer (or an agent) needs for one task is built from evidence, and
every piece of it names where it came from:

* ``specs``          -- the normative statements, read from the seekdb spec store
* ``code``           -- the cited locations, re-read from the checkout with a hash
* ``tests``          -- the existing regression files that must keep passing
* ``environment``    -- what the *running* server does, captured by the harness
* ``state``          -- completed work, pending items and the last observed output

Nothing here is a cached answer: ``build()`` always re-reads and re-hashes, so a
context produced after a spec update carries the new text.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from context.db import connect

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden_baseline.json"
DEFAULT_TESTS = [
    "tools/deploy/mysql_test/test_suite/array/t/array_arith_op_mysql.test",
    "tools/deploy/mysql_test/test_suite/array/t/array_ddl_mysql.test",
]


def repo_root() -> Path:
    value = os.environ.get("SEEKDB_REPO") or os.environ.get("MINIMAX_SEEKDB_REPO", "")
    if not value:
        raise RuntimeError(
            "SEEKDB_REPO is not set; point it at the seekdb checkout the specs cite"
        )
    return Path(value).resolve()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _spec_snapshot(cur, spec_ids: list[str]) -> dict[str, str]:
    if not spec_ids:
        return {}
    placeholders = ",".join(["%s"] * len(spec_ids))
    cur.execute(
        f"""
        SELECT spec_id, title, kind, state, statement, updated_at
        FROM spec WHERE spec_id IN ({placeholders})
        """,
        tuple(spec_ids),
    )
    return {
        row[0]: _sha(f"{row[1]}|{row[2]}|{row[3]}|{row[4]}")
        for row in cur.fetchall()
    }


def load_specs(spec_ids: list[str] | None = None) -> list[dict[str, Any]]:
    repo = repo_root()
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT spec_id, title, category, kind, state, statement, evidence, repro,
                       tests, depends_on
                FROM spec
                """
            )
            rows = cur.fetchall()
            specs = []
            for (spec_id, title, category, kind, state, statement, evidence, repro,
                 tests, depends_on) in rows:
                if spec_ids is not None and spec_id not in spec_ids:
                    continue
                cur.execute(
                    """
                    SELECT repo_path, symbol, line_no, quote, note
                    FROM spec_source WHERE spec_id = %s ORDER BY source_id
                    """,
                    (spec_id,),
                )
                sources = []
                for path, symbol, line_no, quote, note in cur.fetchall():
                    file_path = repo / path
                    exists = file_path.is_file()
                    current = ""
                    lines = []
                    if exists:
                        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    anchor = (symbol or "").split("(")[0]
                    if exists and (not line_no or line_no <= 0) and anchor \
                            and not anchor.startswith("ob_expr_*"):
                        for index, line in enumerate(lines, start=1):
                            if anchor in line:
                                line_no = index
                                break
                    if exists and line_no:
                        start = max(0, int(line_no) - 1)
                        current = "\n".join(lines[start:start + 4])
                    sources.append(
                        {
                            "path": path,
                            "symbol": symbol,
                            "line": int(line_no or 0),
                            "quote": quote,
                            "note": note,
                            "exists": exists,
                            "sha": _sha(current) if current else "",
                        }
                    )
                specs.append(
                    {
                        "id": spec_id,
                        "title": title,
                        "category": category,
                        "kind": kind,
                        "state": state,
                        "statement": statement,
                        "evidence": json.loads(evidence) if evidence else {},
                        "repro": repro,
                        "checks": [item for item in (tests or "").split(",") if item],
                        "depends_on": [item for item in (depends_on or "").split(",") if item],
                        "sources": sources,
                    }
                )
            specs.sort(key=lambda item: item["id"])
            return specs
    finally:
        conn.close()


def load_checks() -> list[dict[str, Any]]:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT case_id, spec_id, check_name, title, oracle_key
                FROM test_case ORDER BY spec_id, check_name
                """
            )
            return [
                {"case_id": row[0], "spec_id": row[1], "check": row[2], "title": row[3],
                 "oracle": row[4]}
                for row in cur.fetchall()
            ]
    finally:
        conn.close()


def load_results(limit: int = 12) -> list[dict[str, Any]]:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT action, content, created_at FROM session_log
                WHERE role = 'guard' ORDER BY log_id DESC LIMIT %s
                """,
                (limit,),
            )
            return [
                {"action": action, "content": json.loads(content), "at": str(at)}
                for action, content, at in cur.fetchall()
            ]
    finally:
        conn.close()


def existing_tests(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repo = repo_root()
    wanted = set(DEFAULT_TESTS)
    for spec in specs:
        for source in spec["sources"]:
            if source["path"].endswith((".test", ".result")):
                wanted.add(source["path"])
    tests = []
    for relative in sorted(wanted):
        path = repo / relative
        digest = _sha(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else ""
        tests.append({"path": relative, "exists": path.is_file(), "sha": digest})
    return tests


def environment_observed(limit: int = 6) -> list[dict[str, Any]]:
    """What the running server does today, taken from the newest guard observation."""
    results = load_results(limit=200)
    seen: dict[str, dict[str, Any]] = {}
    for entry in results:
        payload = entry["content"]
        case_id = payload.get("case_id")
        if case_id and case_id not in seen:
            seen[case_id] = {
                "case_id": case_id,
                "stage": payload.get("stage"),
                "status": payload.get("status"),
                "detail": payload.get("detail"),
                "at": entry["at"],
                "statements": [
                    {"statement": item["statement"], "ok": item["ok"], "code": item["code"],
                     "message": item["message"][:160]}
                    for item in payload.get("statements", [])
                ],
            }
    return sorted(seen.values(), key=lambda item: item["case_id"])[:limit]


def build(spec_ids: list[str] | None = None) -> dict[str, Any]:
    specs = load_specs(spec_ids)
    conn = connect()
    try:
        with conn.cursor() as cur:
            snapshot = _spec_snapshot(cur, [spec["id"] for spec in specs])
    finally:
        conn.close()
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "spec_snapshot": snapshot,
        "checks": load_checks(),
        "results": load_results(),
        "specs": specs,
        "code": [source for spec in specs for source in spec["sources"]],
        "tests": existing_tests(specs),
        "environment": environment_observed(),
        "task": _load_task(),
        "golden": json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.is_file() else None,
    }


def _load_task(task_id: str | None = None) -> dict[str, Any] | None:
    task_id = task_id or os.environ.get("MINIMAX_TASK_ID", "task-arr-element-dml")
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT revision, payload, updated_at FROM task_state WHERE task_id = %s",
                (task_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    payload = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    return {"task_id": task_id, "revision": int(row[0]), "updated_at": str(row[2]),
            "payload": payload}


def snapshot() -> dict[str, str]:
    """Spec ids and content hashes for the whole store (used for state snapshots)."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT spec_id, kind, statement FROM spec")
            return {
                spec_id: _sha(f"{kind}|{statement}")
                for spec_id, kind, statement in cur.fetchall()
            }
    finally:
        conn.close()


def render(context: dict[str, Any], compact: bool = False) -> str:
    """Render the context as text for a developer or an agent prompt."""
    lines: list[str] = []
    task = context.get("task")
    lines.append("== TASK ==")
    if task:
        payload = task["payload"]
        lines.append(f"{payload.get('task_title')}  [state revision {task['revision']}]")
        if payload.get("last_output"):
            lines.append(f"last observed output: {payload['last_output']}")
    else:
        lines.append("no saved task state (run 'state save' first)")

    lines.append("")
    lines.append("== SPECS THAT APPLY (read from seekdb) ==")
    for spec in context["specs"]:
        lines.append(f"[{spec['id']}] ({spec['kind']}) {spec['title']}")
        lines.append(f"  {spec['statement']}")
        if not compact and spec["evidence"].get("observed"):
            for item in spec["evidence"]["observed"].splitlines()[:2]:
                lines.append(f"  observed: {item.strip()}")
        for source in spec["sources"]:
            lines.append(
                f"  source: {source['path']}:{source['line']}"
                f"{' (' + source['symbol'] + ')' if source['symbol'] else ''}"
                f" sha={source['sha'] or 'missing'}"
            )

    lines.append("")
    lines.append("== CHECKS ==")
    for check in context["checks"]:
        lines.append(f"  {check['spec_id']} <- {check['check']}")

    lines.append("")
    lines.append("== EXISTING TESTS THAT MUST KEEP PASSING ==")
    for test in context["tests"]:
        lines.append(f"  {test['path']} sha={test['sha'] or 'missing'}")

    lines.append("")
    lines.append("== ENVIRONMENT: what seekdb does today ==")
    for item in context["environment"]:
        lines.append(f"  {item['case_id']} ({item['status']}) {item['detail']}")
        if not compact:
            for statement in item["statements"]:
                state = "ok" if statement["ok"] else f"error {statement['code']}"
                lines.append(f"    {state:>12}  {statement['statement'][:110]}")

    lines.append("")
    lines.append("== RESULTS OF THE LAST STAGES ==")
    for result in context["results"][:6]:
        payload = result["content"]
        lines.append(
            f"  {payload.get('stage')}/{payload.get('case_id')}: {payload.get('status')} "
            f"@ {result['at']}"
        )
    return "\n".join(lines)
