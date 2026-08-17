"""Command-line entry point for end-to-end workloads and offline scoring."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from loguru import logger

from .settings import HarnessSettings


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, backtrace=False, diagnose=False)

    parser = argparse.ArgumentParser(prog="powercontext-e2e")
    subparsers = parser.add_subparsers(dest="command", required=True)

    acceptance_parser = subparsers.add_parser("acceptance")
    acceptance_parser.add_argument("--manifest", type=Path, default=Path("e2e/bub/tasks"))
    acceptance_parser.add_argument(
        "--id",
        action="append",
        default=[],
        metavar="WORKLOAD_ID",
        help="Select one workload; repeat to select more than one.",
    )
    acceptance_parser.add_argument(
        "--category",
        action="append",
        default=[],
        help="Select one category; repeat to select more than one.",
    )
    acceptance_parser.add_argument("--output", type=Path, required=True)

    scenario_parser = subparsers.add_parser("scenarios")
    scenario_parser.add_argument("--manifest", type=Path, default=Path("e2e/bub/tasks"))
    scenario_parser.add_argument(
        "--id",
        action="append",
        default=[],
        required=True,
        metavar="WORKLOAD_ID",
        help="Select one workload; repeat to select more than one.",
    )
    scenario_parser.add_argument(
        "--scenario",
        action="append",
        choices=("session-handoff", "container-handoff", "completed-reuse"),
        default=[],
        help="Select a scenario; omit to run all scenarios.",
    )
    scenario_parser.add_argument(
        "--handoff-after-step",
        action="append",
        type=int,
        default=[],
        metavar="STEP",
        help="Start a new agent after this Bub loop step; repeat to cover multiple stages.",
    )
    scenario_parser.add_argument("--output", type=Path, required=True)

    rescore_parser = subparsers.add_parser("rescore")
    rescore_parser.add_argument("replay", type=Path)
    rescore_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = HarnessSettings()

    if args.command == "rescore":
        from .rescore import rescore_replay

        passed = rescore_replay(args.replay, args.output, settings)
    elif args.command == "scenarios":
        from .catalog import load_tasks, select_tasks
        from .scenarios import ScenarioSelection, run_scenario_tasks

        tasks = load_tasks(args.manifest)
        selected = select_tasks(tasks, ids=tuple(args.id))
        scenarios = tuple(args.scenario) or ("session-handoff", "container-handoff", "completed-reuse")
        handoff_steps = tuple(args.handoff_after_step) or (5,)
        passed = asyncio.run(
            run_scenario_tasks(
                selected,
                output_dir=args.output,
                settings=settings,
                selection=ScenarioSelection(scenarios=scenarios, handoff_steps=handoff_steps),
            )
        )
    else:
        from .catalog import load_tasks, select_tasks
        from .runner import run_tasks

        tasks = load_tasks(args.manifest)
        selected = select_tasks(tasks, ids=tuple(args.id), categories=tuple(args.category))
        passed = asyncio.run(
            run_tasks(
                selected,
                output_dir=args.output,
                settings=settings,
            )
        )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
