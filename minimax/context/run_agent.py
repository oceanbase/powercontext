"""MiniMax agent driver: context in, patch proposal out, everything recorded.

This is the interface MiniMax uses.  It runs in two steps so the competition's
"state survives a restart" requirement can be observed on the same task:

    step 1  prepare   build the developer context, walk MiniMax (api/cli/manual),
                      validate the answer, and save the task state
    step 2  (restart the process)
    step 3  apply     read the state from seekdb, re-import it, apply the saved patch
                      to the target tree and record the code change

Usage:
    python context/run_agent.py --step prepare --stage baseline --instruction @instruction.md
    python context/run_agent.py --step apply --worktree /tmp/seekdb-patch
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from context import build_context, minimax, powercontext  # noqa: E402
from context.db import connect, ensure_schema  # noqa: E402

TASK_ID = os.environ.get("MINIMAX_TASK_ID", "task-arr-element-dml")
AGENT_DIR = ROOT / "agent"
TOKEN = re.compile(r"\[(ARR-\d+[a-z]?)\]")


def discover_specs(instruction: str) -> list[str]:
    """The specs this task is about, taken verbatim from the instruction."""
    ids = sorted(set(TOKEN.findall(instruction)))
    if not ids:
        raise SystemExit("the instruction does not reference any spec id such as [ARR-001]")
    return ids


def artifacts_dir(stage: str) -> Path:
    path = AGENT_DIR / "artifacts" / stage
    path.mkdir(parents=True, exist_ok=True)
    return path


def observed_results() -> tuple[list[dict], str]:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT action, content FROM session_log WHERE role = 'guard'
                ORDER BY log_id DESC LIMIT 12
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    newest: dict[str, dict] = {}
    for _, content in rows:
        payload = json.loads(content)
        newest.setdefault(payload["case_id"], payload)
    results = [
        {
            "case_id": payload["case_id"],
            "kind": payload["kind"],
            "status": payload["status"],
            "basis": payload["basis"],
            "detail": payload["detail"][:400],
            "result_digest": payload.get("result_digest", ""),
        }
        for payload in sorted(newest.values(), key=lambda item: item["case_id"])
    ]
    stage = next(iter(newest.values()))["stage"] if newest else ""
    return results, stage


def step_prepare(args) -> int:
    ensure_schema()
    instruction_path = ROOT / args.instruction if not Path(args.instruction).is_absolute() else Path(args.instruction)
    instruction = instruction_path.read_text(encoding="utf-8")
    spec_ids = discover_specs(instruction)
    print(f"task {TASK_ID}: specs {spec_ids}")

    context_data = build_context.build(spec_ids)
    context_text = build_context.render(context_data)
    context_path = artifacts_dir(args.stage) / "context.md"
    context_path.write_text(context_text, encoding="utf-8")
    print(f"context written: {context_path} ({len(context_text)} chars)")

    prompt = minimax.prepare_prompt(context_text, instruction)
    prompt_path = artifacts_dir(args.stage) / "prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")

    run = minimax.dispatch(prompt, workdir=artifacts_dir(args.stage))
    record = run.to_record()
    record.update({"task_id": TASK_ID, "at": datetime.now().isoformat(timespec="seconds"),
                   "stage": args.stage, "spec_ids": spec_ids,
                   "prompt_sha": minimax._digest(prompt)})
    powercontext.log(TASK_ID, "minimax", "agent_call", json.dumps(record, ensure_ascii=False))
    print(f"MiniMax backend={run.backend} model={run.model} ok={run.ok} output={len(run.output)} chars")
    if not run.ok:
        print(f"MiniMax did not answer: {run.error}", file=sys.stderr)

    patch = ""
    measure: dict = {}
    if run.ok:
        try:
            patch = minimax.extract_patch(run.output)
            measure = minimax.measure(patch)
        except ValueError as exc:
            record["ok"] = False
            record["error"] = str(exc)
    if patch:
        patch_path = artifacts_dir(args.stage) / "proposal.patch"
        patch_path.write_text(patch, encoding="utf-8")
        powercontext.put_artifact(TASK_ID, f"proposal:{args.stage}", patch)
        print(f"proposal patch: {patch_path} files={measure['files']} "
              f"+{measure['added_lines']}/-{measure['removed_lines']}")
    else:
        print("no patch proposal in this stage's answer")

    results, stage_seen = observed_results()
    payload = {
        "task_id": TASK_ID,
        "task_title": "seekdb: element-wise ARRAY DML (col[i])",
        "spec_ids": spec_ids,
        "checks": sorted({check["check"] for check in context_data["checks"]
                          if check["spec_id"] in spec_ids}),
        "results": results,
        "changed_files": measure.get("files", []),
        "completed": [f"context:{stage_seen}", f"agent:{run.backend}"],
        "pending": [f"apply:{args.stage}-proposal", f"verify:{args.stage}"],
        "last_output": results[0]["detail"] if results else "",
        "spec_snapshot": context_data["spec_snapshot"],
        "stage": args.stage,
        "proposal_patch_sha": minimax._digest(patch) if patch else "",
    }
    state = powercontext.save(TASK_ID, payload, reason=f"after {args.stage} prepare")
    print(f"saved task state {state.task_id}@{state.revision} ({state.summary()})")
    return 0 if patch else 2


def latest_proposal(payload: dict) -> tuple[str, str]:
    """Return (kind, patch) of the newest proposal artifact for the saved stage."""
    kind = f"proposal:{payload.get('stage', '')}"
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
        raise SystemExit(f"no proposal artifact of kind {kind}; run --step prepare first")
    return kind, row[0]


def step_apply(args) -> int:
    state, summary = powercontext.import_state(TASK_ID, build_context.build)
    print("re-imported state:", json.dumps(summary, ensure_ascii=False))
    payload = state.payload
    kind, patch = latest_proposal(payload)
    print(f"proposal artifact {kind}: {len(patch)} chars")

    target = Path(args.worktree).resolve()
    if not (target / ".git").exists():
        raise SystemExit(f"{target} is not a git checkout")
    patch_path = artifacts_dir(payload.get("stage", "after")) / "applied.patch"
    patch_path.write_text(patch, encoding="utf-8")

    check = subprocess.run(["git", "apply", "--check", str(patch_path)], cwd=str(target),
                           text=True, capture_output=True)
    if check.returncode != 0:
        raise SystemExit(f"patch does not apply cleanly: {check.stderr.strip()}")
    applied = subprocess.run(["git", "apply", str(patch_path)], cwd=str(target),
                             text=True, capture_output=True)
    if applied.returncode != 0:
        raise SystemExit(f"git apply failed: {applied.stderr.strip()}")

    diffstat = subprocess.run(["git", "diff", "--stat"], cwd=str(target), text=True,
                              capture_output=True).stdout
    full_diff = subprocess.run(["git", "diff"], cwd=str(target), text=True,
                               capture_output=True).stdout
    powercontext.put_artifact(TASK_ID, "code-diff", full_diff)
    (ROOT / "agent" / "artifacts" / "code-diff.patch").write_text(full_diff, encoding="utf-8")
    print("applied to", target)
    print(diffstat)

    payload = dict(payload)
    payload["changed_files"] = minimax.files_in(full_diff)
    payload["completed"] = list(payload.get("completed", [])) + ["code:applied"]
    payload["pending"] = [item for item in payload.get("pending", [])
                          if not item.startswith("apply:")] + [f"verify:{payload.get('stage')}"]
    saved = powercontext.save(TASK_ID, payload, reason="after code apply")
    print(f"saved task state {saved.task_id}@{saved.revision}")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", choices=["prepare", "apply"], required=True)
    parser.add_argument("--stage", default="after", help="baseline | after")
    parser.add_argument("--instruction", default="agent/instruction.md")
    parser.add_argument("--worktree", default="", help="checkout the patch is applied to")
    args = parser.parse_args()
    if args.step == "prepare":
        return step_prepare(args)
    if not args.worktree:
        parser.error("--worktree is required for --step apply")
    return step_apply(args)


if __name__ == "__main__":
    sys.exit(main())
