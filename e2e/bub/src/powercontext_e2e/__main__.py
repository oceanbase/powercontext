# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Command-line entry point for end-to-end workloads and offline scoring."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from .settings import HarnessSettings

if TYPE_CHECKING:
    from .catalog import E2ETask


async def _run_default_acceptance(
    tasks: tuple[E2ETask, ...],
    *,
    output_dir: Path,
    settings: HarnessSettings,
) -> bool:
    from .catalog import select_tasks
    from .runner import run_tasks

    collect_all_passed = await run_tasks(
        select_tasks(tasks, categories=("acceptance",)),
        output_dir=output_dir,
        settings=settings,
        failure_policy="collect-all",
    )
    fail_fast_passed = await run_tasks(
        select_tasks(tasks, categories=("batch:acceptance",)),
        output_dir=output_dir / "fail-fast",
        settings=settings,
        failure_policy="fail-fast",
    )
    return collect_all_passed and fail_fast_passed


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
    acceptance_parser.add_argument(
        "--failure-policy",
        choices=("collect-all", "fail-fast"),
        default="collect-all",
        help="Continue through case failures or stop the Harbor trial at the first failed step.",
    )

    rescore_parser = subparsers.add_parser("rescore")
    rescore_parser.add_argument("replay", type=Path)
    rescore_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = HarnessSettings()

    if args.command == "rescore":
        from .rescore import rescore_replay

        passed = rescore_replay(args.replay, args.output, settings)
    else:
        from .catalog import load_tasks, select_tasks
        from .runner import run_tasks

        tasks = load_tasks(args.manifest)
        selected = select_tasks(tasks, ids=tuple(args.id), categories=tuple(args.category))
        passed = (
            asyncio.run(
                run_tasks(
                    selected,
                    output_dir=args.output,
                    settings=settings,
                    failure_policy=args.failure_policy,
                )
            )
            if args.id or args.category
            else asyncio.run(
                _run_default_acceptance(
                    tasks,
                    output_dir=args.output,
                    settings=settings,
                )
            )
        )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
