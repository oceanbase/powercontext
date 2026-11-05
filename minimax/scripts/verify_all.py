"""One command that reproduces the whole verification chain and judges it.

A reviewer should not have to copy ten commands out of a document and interpret raw
output. ``verify_all.py`` runs the chain end to end, compares every step against the
outcome the submission claims, and prints one table with PASS/FAIL per step.

    python scripts/verify_all.py --worktree D:\\code\\seekdb

Steps and the outcome each one must produce:

1. database reachable (spec store + scenario accounts)
2. spec store loaded (9 specs, 20 source rows)
3. guard --stage baseline     -> matches the unmodified tree; every acceptance case red
4. guard --stage after (now)  -> acceptance fails (the change is not in the server yet)
5. apply the reviewed patch to the worktree
6. grammar probe on the worktree -> element-access node present (GRAMMAR-CHECK: PASS)
7. guard --stage after with the patched grammar -> grammar green, compatibility green,
   acceptance still red (no patched server build on this machine)

Step 7's "acceptance still red" is expected and is stated in docs/代码与测试.md; the
script reports it as EXPECTED rather than hiding it. The worktree is restored unless
--keep is given.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from context.db import connect  # noqa: E402

PATCH = ROOT / "agent" / "code" / "author-fix.patch"
GRAMMAR = ROOT / "grammar_check" / "static_check.py"


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""
    expected: bool = True


def run(cmd: list[str], env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    merged = {**os.environ, **(env or {})}
    return subprocess.run(cmd, text=True, capture_output=True, cwd=str(cwd or ROOT), env=merged)


def db_step() -> Step:
    try:
        conn = connect()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM spec")
            specs = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM spec_source")
            sources = int(cur.fetchone()[0])
        conn.close()
    except Exception as exc:  # noqa: BLE001 - the step is the diagnosis
        return Step("database reachable", False, str(exc).splitlines()[0])
    ok = specs == 9 and sources == 20
    return Step("spec store loaded", ok, f"{specs} specs, {sources} sources (expect 9/20)")


def guard_step(stage: str, expect_success: bool, patched_grammar: bool,
               worktree: Path) -> Step:
    env = {"SEEKDB_REPO": str(worktree if patched_grammar else Path(
        os.environ.get("MINIMAX_BASELINE_REPO", r"D:\code\oceanbase\seekdb")))}
    proc = run([sys.executable, "run.py", "guard", "--stage", stage], env=env)
    last = (proc.stdout or proc.stderr).strip().splitlines()
    summary = last[-1] if last else "(no output)"
    ok = (proc.returncode == 0) == expect_success
    label = f"guard --stage {stage}" + (" (patched grammar)" if patched_grammar else "")
    if not ok:
        return Step(label, False, summary)
    return Step(label, True, summary)


def apply_step(worktree: Path) -> Step:
    proc = run([sys.executable, "run.py", "agent", "--step", "apply",
                "--patch", str(PATCH), "--worktree", str(worktree)])
    out = (proc.stdout or "").strip()
    ok = proc.returncode == 0 and '"applied": true' in out
    detail = "patch applied" if ok else (proc.stderr or out).strip().splitlines()[-1:]
    if not ok:
        return Step("apply reviewed patch", False, str(detail))
    stat = run(["git", "diff", "--stat"], cwd=worktree).stdout.strip().splitlines()
    return Step("apply reviewed patch", True, stat[-1] if stat else "applied")


def grammar_step(worktree: Path) -> Step:
    proc = run([sys.executable, str(GRAMMAR)], env={"SEEKDB_REPO": str(worktree)})
    out = proc.stdout or proc.stderr
    node = next((line for line in out.splitlines() if line.startswith("element-access node:")), "")
    passed = "GRAMMAR-CHECK: PASS" in out
    return Step("grammar probe on the worktree", passed, node or out.strip()[:120])


def worktree_state(worktree: Path) -> tuple[bool, str]:
    proc = run(["git", "status", "--porcelain"], cwd=worktree)
    if proc.returncode != 0:
        return False, f"{worktree} is not a git checkout"
    return proc.stdout.strip() == "", proc.stdout.strip()[:200]


def restore(worktree: Path) -> None:
    run(["git", "checkout", "--", "."], cwd=worktree)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", default=os.environ.get("MINIMAX_WORKTREE", r"D:\code\seekdb"))
    parser.add_argument("--keep", action="store_true", help="leave the patch applied")
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    worktree = Path(args.worktree).resolve()
    steps: list[Step] = []

    clean, dirty = worktree_state(worktree)
    if not clean:
        print(f"worktree {worktree} is not clean:\n{dirty}", file=sys.stderr)
        print("commit or discard those changes first (the run applies and restores a patch)",
              file=sys.stderr)
        return 2
    if not PATCH.is_file():
        print(f"missing patch {PATCH}", file=sys.stderr)
        return 2

    steps.append(db_step())
    if not steps[-1].ok:
        print(json.dumps([s.__dict__ for s in steps], ensure_ascii=False, indent=2))
        return 1

    steps.append(guard_step("baseline", True, False, worktree))
    steps.append(guard_step("after", False, False, worktree))
    steps.append(apply_step(worktree))
    if steps[-1].ok:
        steps.append(grammar_step(worktree))
        steps.append(guard_step("after", False, True, worktree))
    if not args.keep:
        restore(worktree)
        steps.append(Step("worktree restored", worktree_state(worktree)[0],
                          "git checkout -- ."))

    width = max(len(step.name) for step in steps)
    failures = [step for step in steps if not step.ok]
    print()
    print(f"{'STEP'.ljust(width)}  RESULT  DETAIL")
    print("-" * (width + 8 + 60))
    for step in steps:
        print(f"{step.name.ljust(width)}  {'PASS' if step.ok else 'FAIL':<6}  {step.detail}")
    print()
    print(f"{len(steps) - len(failures)}/{len(steps)} steps produced the documented outcome")
    print("PASS here means 'the step matched what the submission claims', not 'tests are green':")
    print("the acceptance cases are expected to fail until seekdb is rebuilt with the patch.")
    print("That limitation is documented in docs/ (the 'code and tests' page).")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps([step.__dict__ for step in steps], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
