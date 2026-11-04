"""Developer context for the seekdb task 'element-wise ARRAY access (col[i])'.

Reads specs and task state from seekdb, regenerates the developer context and
renders it.  This is the module the CLI, the tests and MiniMax all consume.

Usage:
    python -m context.main show                 # render the context for a saved task
    python -m context.main show --json          # same, machine readable
    python -m context.main specs                # list every spec in the store
    python -m context.main save --stage after   # snapshot the task state
    python -m context.main history              # state revisions
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from context import build_context, config, powercontext  # noqa: E402
from context.db import ensure_schema  # noqa: E402

TASK_ID = os.environ.get("MINIMAX_TASK_ID", config.TASK_ID)


def cmd_show(args: argparse.Namespace) -> int:
    context_data = build_context.build(args.spec or None)
    if args.json:
        print(json.dumps(context_data, ensure_ascii=False, indent=2))
        return 0
    print(build_context.render(context_data, compact=args.compact))
    return 0


def cmd_specs(args: argparse.Namespace) -> int:
    specs = build_context.load_specs()
    for spec in specs:
        print(f"{spec['id']:<8} {spec['kind']:<10} {spec['title']}")
    return 0


def cmd_save(args: argparse.Namespace) -> int:
    ensure_schema()
    task = build_context._load_task(TASK_ID)
    state = task["payload"] if task else {}
    payload = {
        "task_id": TASK_ID,
        "task_title": state.get("task_title", config.TASK_TITLE),
        "spec_ids": state.get("spec_ids", config.SPEC_IDS),
        "checks": state.get("checks", []),
        "results": state.get("results", []),
        "changed_files": state.get("changed_files", []),
        "completed": state.get("completed", []),
        "pending": state.get("pending", []),
        "last_output": state.get("last_output", ""),
        "spec_snapshot": state.get("spec_snapshot") or build_context.build(
            config.SPEC_IDS)["spec_snapshot"],
        "stage": args.stage or state.get("stage", ""),
    }
    saved = powercontext.save(TASK_ID, payload, reason=args.reason)
    print(f"saved {saved.task_id}@{saved.revision}: {saved.summary()}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    for revision, reason in powercontext.history(TASK_ID):
        print(f"{TASK_ID}@{revision:<3} {reason}")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    for entry in powercontext.read_log(TASK_ID, args.limit):
        content = entry["content"].replace("\n", " ")
        print(f"{entry['at']} {entry['role']:<11} {entry['action']:<24} {content[:120]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="render the developer context")
    show.add_argument("--json", action="store_true")
    show.add_argument("--compact", action="store_true")
    show.add_argument("--spec", action="append", default=[], help="limit to these spec ids")
    show.set_defaults(handler=cmd_show)

    specs = sub.add_parser("specs", help="list the spec store")
    specs.set_defaults(handler=cmd_specs)

    save = sub.add_parser("save", help="snapshot the task state")
    save.add_argument("--stage", default="")
    save.add_argument("--reason", default="manual")
    save.set_defaults(handler=cmd_save)

    history = sub.add_parser("history", help="list state revisions")
    history.set_defaults(handler=cmd_history)

    log = sub.add_parser("log", help="show the recorded usage log")
    log.add_argument("--limit", type=int, default=50)
    log.set_defaults(handler=cmd_log)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
