"""Shared commands for every entry point.

``run.py`` (CLI) and ``serve.py`` (HTTP API) both call into ``operations`` so the two
surfaces cannot drift: whichever one an operator or an agent uses, the same database
writes, the same guard and the same task state are involved.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from context import build_context, minimax, patchutil, powercontext
from context.db import connect, ensure_schema

ROOT = Path(__file__).resolve().parents[1]
TASK_ID = os.environ.get("MINIMAX_TASK_ID", "task-arr-element-dml")
TOKEN = re.compile(r"\[(ARR-\d+[a-z]?)\]")


def readiness(spec_ids: list[str]) -> dict[str, Any]:
    """Progress for one task, computed from the database only."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(spec_ids))
            cur.execute(
                f"""
                SELECT spec_id, state, kind, title FROM spec
                WHERE spec_id IN ({placeholders}) ORDER BY spec_id
                """,
                tuple(spec_ids),
            )
            specs = [
                {"spec_id": row[0], "state": row[1], "kind": row[2], "title": row[3]}
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT case_id, spec_id, oracle_key FROM test_case
                WHERE case_id LIKE 'guard:%%' ORDER BY case_id
                """
            )
            checks = [
                {"case_id": row[0], "spec_id": row[1], "oracle": row[2]}
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT action, content FROM session_log WHERE role = 'guard'
                ORDER BY log_id DESC LIMIT 40
                """
            )
            latest: dict[str, dict[str, Any]] = {}
            for action, content in cur.fetchall():
                payload = json.loads(content)
                latest.setdefault(payload["case_id"], payload)
    finally:
        conn.close()

    per_spec: dict[str, dict[str, Any]] = {}
    for spec in specs:
        rows = [item for item in latest.values() if item["spec_id"] == spec["spec_id"]]
        status = "pass" if rows and all(item["status"] == "pass" for item in rows) else "fail"
        per_spec[spec["spec_id"]] = {
            "kind": spec["kind"],
            "state": spec["state"],
            "checks": len(rows),
            "observed": rows[0]["detail"] if rows else "no run yet",
            "status": status if rows else "unknown",
        }
    open_specs = [spec["spec_id"] for spec in specs if spec["state"] != "done"]
    return {
        "task_id": TASK_ID,
        "specs": per_spec,
        "checks": len(checks),
        "guard_runs": len(latest),
        "open_specs": open_specs,
        "ready": not open_specs,
        "at": datetime.now().isoformat(timespec="seconds"),
    }


def mark_specs(spec_ids: list[str], state: str) -> int:
    conn = connect()
    try:
        with conn.cursor() as cur:
            changed = 0
            for spec_id in spec_ids:
                cur.execute(
                    "UPDATE spec SET state = %s, updated_at = NOW(6) WHERE spec_id = %s",
                    (state, spec_id),
                )
                changed += cur.rowcount
            return changed
    finally:
        conn.close()


def context_payload(spec_ids: list[str] | None = None) -> dict[str, Any]:
    return build_context.build(spec_ids)


def task_payload() -> dict[str, Any] | None:
    try:
        state = powercontext.load(TASK_ID)
    except LookupError:
        return None
    return {"task_id": state.task_id, "revision": state.revision,
            "updated_at": str(state.updated_at), "payload": state.payload}


def run_guard(stage: str) -> dict[str, Any]:
    """Run the guard in-process and return its payload (used by the API too)."""
    from context import guard

    return guard.run(stage, save=True)


def agent_prepare(instruction: str, stage: str, workdir: Path) -> dict[str, Any]:
    spec_ids = sorted(set(TOKEN.findall(instruction)))
    if not spec_ids:
        raise ValueError("the instruction does not reference any spec id such as [ARR-001]")
    context_data = context_payload(spec_ids)
    context_text = build_context.render(context_data)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "context.md").write_text(context_text, encoding="utf-8")
    prompt = minimax.prepare_prompt(context_text, instruction)
    (workdir / "prompt.md").write_text(prompt, encoding="utf-8")
    run = minimax.dispatch(prompt, workdir=workdir)
    record = run.to_record()
    powercontext.log(TASK_ID, "minimax", "agent_call", json.dumps(record, ensure_ascii=False))
    if run.output:
        (workdir / "answer.md").write_text(run.output, encoding="utf-8")
    # A new proposal for this stage supersedes the previous one; stale artifacts would
    # otherwise be applied by an ask-then-apply flow that runs later.
    powercontext.clear_artifacts(TASK_ID, f"proposal:{stage}")
    patch, measure, problem = "", {}, ""
    if run.ok:
        try:
            patch = minimax.extract_patch(run.output)
            measure = minimax.measure(patch)
        except ValueError as exc:
            problem = str(exc)
    else:
        problem = run.error
    if patch:
        (workdir / "proposal.patch").write_text(patch, encoding="utf-8")
        powercontext.put_artifact(TASK_ID, f"proposal:{stage}", patch)
    # The task state is written here, in one place, so the saved state always matches
    # what was actually produced (proposal yes/no, last observation, pending work).
    state: dict[str, Any] | None = None
    if patch:
        readiness_report = readiness(spec_ids)
        observation = ""
        for spec in readiness_report["specs"].values():
            if spec.get("observed") and spec["status"] == "fail":
                observation = spec["observed"]
                break
        previous = task_payload()
        carried = previous["payload"] if previous else {}
        state = save_state(
            stage,
            carried.get("results", []),
            spec_ids,
            observation,
            completed=list(carried.get("completed", [])) + [f"agent:{run.backend}"],
            changed_files=measure.get("files", []),
            proposal_sha=minimax._digest(patch),
        )

    return {
        "backend": run.backend,
        "model": run.model,
        "ok": bool(patch),
        "problem": problem,
        "spec_ids": spec_ids,
        "context_chars": len(context_text),
        "prompt_chars": len(prompt),
        "proposal": measure,
        "state": state,
        "prompt_path": str(workdir / "prompt.md"),
        "context_path": str(workdir / "context.md"),
    }


def apply_proposal(worktree: Path, stage: str) -> dict[str, Any]:
    state, summary = powercontext.import_state(TASK_ID, build_context.build)
    kind = f"proposal:{stage}"
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT content FROM artifact WHERE task_id = %s AND kind = %s
                ORDER BY created_at DESC LIMIT 1
                """,
                (TASK_ID, kind),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise FileNotFoundError(f"no {kind} artifact for {TASK_ID}")
    patch = row[0]
    workdir = ROOT / "agent" / "artifacts" / stage
    patch_path = patchutil.write_patch(workdir / "applied.patch", patch)
    check = patchutil.git_apply(worktree, patch_path, check=True)
    if check.returncode != 0:
        return {"applied": False, "error": check.stderr.strip(), "reimport": summary}
    applied = patchutil.git_apply(worktree, patch_path)
    if applied.returncode != 0:
        return {"applied": False, "error": applied.stderr.strip(), "reimport": summary}
    stats = patchutil.diffstat(worktree)
    full_diff = patchutil.working_diff(worktree)
    powercontext.put_artifact(TASK_ID, "code-diff", full_diff)
    payload = dict(state.payload)
    payload["changed_files"] = patchutil.touched_files(worktree)
    payload["completed"] = list(payload.get("completed", [])) + ["code:applied"]
    payload["pending"] = [item for item in payload.get("pending", [])
                          if not item.startswith("apply:")] + [f"verify:{stage}"]
    saved = powercontext.save(TASK_ID, payload, reason="after code apply")
    return {"applied": True, "reimport": summary, "diffstat": stats,
            "changed_files": payload["changed_files"], "revision": saved.revision}


def save_state(stage: str, results: list[dict[str, Any]], spec_ids: list[str],
               last_output: str, completed: list[str] | None = None,
               changed_files: list[str] | None = None,
               proposal_sha: str = "") -> dict[str, Any]:
    context_data = context_payload(spec_ids)
    payload = {
        "task_id": TASK_ID,
        "task_title": "seekdb: element-wise ARRAY access (col[i]) in INSERT/UPDATE/DELETE/SELECT",
        "spec_ids": spec_ids,
        "checks": sorted(
            {item["check"] for item in context_data["checks"] if item["spec_id"] in spec_ids}
            | {check for spec in context_data["specs"] if spec["id"] in spec_ids
               for check in spec["checks"]}
        ),
        "results": results,
        "changed_files": changed_files or [],
        "completed": completed or [f"guard:{stage}"],
        "pending": [] if stage == "after" and results else [f"verify:{stage}"],
        "last_output": last_output,
        "spec_snapshot": context_data["spec_snapshot"],
        "global_spec_snapshot": build_context.snapshot(),
        "stage": stage,
        "proposal_sha": proposal_sha,
    }
    state = powercontext.save(TASK_ID, payload, reason=f"save after {stage}")
    return {"task_id": state.task_id, "revision": state.revision, "summary": state.summary()}


def ensure() -> None:
    ensure_schema()


def main_guard(stage: str) -> int:
    payload = run_guard(stage)
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    sys.exit(main_guard(sys.argv[1] if len(sys.argv) > 1 else "baseline"))
