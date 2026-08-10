"""Command-line entry point for session replay and offline scoring."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from loguru import logger

from .models import load_scenario
from .runner import evaluate_scenario, rescore_replay


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, backtrace=False, diagnose=False)

    parser = argparse.ArgumentParser(prog="powercontext-e2e")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("acceptance", "live"):
        run_parser = subparsers.add_parser(command)
        run_parser.add_argument("scenario", type=Path, nargs="+")
        run_parser.add_argument("--output", type=Path, required=True)

    rescore_parser = subparsers.add_parser("rescore")
    rescore_parser.add_argument("replay", type=Path)
    rescore_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "rescore":
        passed = asyncio.run(rescore_replay(args.replay, args.output))
    else:
        passed = True
        for scenario_path in args.scenario:
            scenario = load_scenario(scenario_path)
            output_dir = args.output if len(args.scenario) == 1 else args.output / scenario.id
            passed = asyncio.run(evaluate_scenario(scenario, mode=args.command, output_dir=output_dir)) and passed
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
