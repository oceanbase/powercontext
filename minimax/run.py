#!/usr/bin/env python3
"""Single entry point for the MiniMax SDD context module.

Every subcommand is one step of the documented workflow; ``README.md`` walks through
the same sequence, so the commands here and the run book cannot drift apart.

    python run.py init                 # schema + spec store + oracle checks
    python run.py guard --stage baseline
    python run.py agent prepare        # build context, call MiniMax, save state
    python run.py state history        # ... restart the process here ...
    python run.py state show
    python run.py agent apply --worktree <checkout>
    python run.py guard --stage after
    python run.py state save --stage after
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MINIMAX_ROOT = Path(__file__).resolve().parent
if str(MINIMAX_ROOT) not in sys.path:
    sys.path.insert(0, str(MINIMAX_ROOT))

from context import build_context, config, operations, powercontext  # noqa: E402


def cmd_init(args: argparse.Namespace) -> int:
    operations.ensure()
    print("schema ready; next: scripts/build_specs.py then scripts/record_baseline.py")
    return 0


def cmd_specs(args: argparse.Namespace) -> int:
    for spec in build_context.load_specs():
        print(f"{spec['id']:<8} {spec['kind']:<10} {spec['state']:<5} {spec['title']}")
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    data = operations.context_payload(args.spec or None)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(build_context.render(data, compact=args.compact))
    if args.out:
        Path(args.out).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) if args.json
            else build_context.render(data, compact=args.compact),
            encoding="utf-8",
        )
    return 0


def cmd_guard(args: argparse.Namespace) -> int:
    payload = operations.run_guard(args.stage)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0 if payload["success"] else 1


def cmd_agent(args: argparse.Namespace) -> int:
    instruction = Path(args.instruction).read_text(encoding="utf-8")
    if args.step == "prepare":
        workdir = MINIMAX_ROOT / "agent" / "artifacts" / args.stage
        result = operations.agent_prepare(instruction, args.stage, workdir,
                                          spec_override=args.spec or None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["ok"]:
            state = operations.save_state(args.stage, [], result["spec_ids"], "")
            print(f"saved task state {state['summary']}")
        return 0 if result["ok"] else 2
    if not args.worktree:
        raise SystemExit("--worktree is required for --step apply")
    result = operations.apply_proposal(Path(args.worktree), args.stage)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["applied"] else 1


def cmd_state(args: argparse.Namespace) -> int:
    if args.step == "show":
        payload = operations.task_payload()
        print(json.dumps(payload, ensure_ascii=False, indent=2) if payload else "no saved state")
        return 0
    if args.step == "history":
        for revision, reason in powercontext.history(config.TASK_ID):
            print(f"{config.TASK_ID}@{revision:<3} {reason}")
        return 0
    if args.step == "log":
        for entry in powercontext.read_log(config.TASK_ID, args.limit):
            print(f"{entry['at']} {entry['role']:<11} {entry['action']:<26} "
                  f"{entry['content'].replace(chr(10), ' ')[:110]}")
        return 0
    if args.step == "import":
        state, summary = powercontext.import_state(config.TASK_ID, build_context.build)
        print("re-imported", state.summary())
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.step == "save":
        payload = operations.task_payload()
        spec_ids = payload["payload"]["spec_ids"] if payload else config.SPEC_IDS
        result = operations.save_state(args.stage, [], spec_ids, "")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.step == "mark":
        changed = operations.mark_specs(args.spec, args.state)
        print(f"{changed} spec rows set to {args.state}")
        return 0
    raise SystemExit(f"unknown state step {args.step}")


def cmd_ready(args: argparse.Namespace) -> int:
    payload = operations.readiness(args.spec or config.SPEC_IDS)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ready"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the schema if it is missing").set_defaults(handler=cmd_init)
    sub.add_parser("specs", help="list the spec store").set_defaults(handler=cmd_specs)

    ctx = sub.add_parser("context", help="build the developer context")
    ctx.add_argument("--json", action="store_true")
    ctx.add_argument("--compact", action="store_true")
    ctx.add_argument("--spec", action="append", default=[])
    ctx.add_argument("--out", default="")
    ctx.set_defaults(handler=cmd_context)

    guard = sub.add_parser("guard", help="run the acceptance/compatibility guard")
    guard.add_argument("--stage", choices=["baseline", "after"], required=True)
    guard.add_argument("--json-out", default="")
    guard.set_defaults(handler=cmd_guard)

    agent = sub.add_parser("agent", help="call MiniMax with the assembled context")
    agent.add_argument("--step", choices=["prepare", "apply"], required=True)
    agent.add_argument("--stage", default="after")
    agent.add_argument("--instruction", default=str(MINIMAX_ROOT / "agent" / "instruction.md"))
    agent.add_argument("--worktree", default="")
    agent.add_argument("--spec", action="append", default=[],
                       help="narrow the request to these spec ids (repeatable)")
    agent.set_defaults(handler=cmd_agent)

    state = sub.add_parser("state", help="inspect or change the saved task state")
    state.add_argument("step", choices=["show", "history", "log", "import", "save", "mark"])
    state.add_argument("--stage", default="")
    state.add_argument("--spec", action="append", default=[])
    state.add_argument("--state", default="done")
    state.add_argument("--limit", type=int, default=60)
    state.set_defaults(handler=cmd_state)

    ready = sub.add_parser("ready", help="is the task ready according to the database?")
    ready.add_argument("--spec", action="append", default=[])
    ready.set_defaults(handler=cmd_ready)

    args = parser.parse_args(argv)
    if args.command == "guard":
        # The guard talks to the scenario database, and context building (needed by the
        # agent) needs the spec store; each operation switches account where it must.
        os.environ.setdefault("MINIMAX_DB_USER", os.environ.get("MINIMAX_SCENARIO_USER", "minimax_test"))
        os.environ.setdefault("MINIMAX_DB_PASSWORD",
                              os.environ.get("MINIMAX_SCENARIO_PASSWORD", "minimax_test_dev"))
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
