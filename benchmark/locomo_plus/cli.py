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

"""Command line interface for bounded LoCoMo-Plus smoke and full runs."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .dataset import DEFAULT_DATA_DIR, LoCoMoPlusDataset, ensure_dataset, load_locomo_plus, load_smoke_dataset
from .runner import dry_run_plan, load_settings, normalize_run_id, replay_results, run_benchmark

DEFAULT_RESULTS = Path(__file__).resolve().parent / "results"
ARMS = ("memory", "memory-source", "query-only", "oracle-cue", "full-context")


def main(argv: list[str] | None = None) -> int:
    """Run a benchmark command, keeping offline commands independent of credentials."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "run" and not arguments.dry_run and not arguments.judge_model:
        parser.error("run requires an explicit --judge-model unless --dry-run is selected")
    if arguments.command == "run" and arguments.dataset_file and arguments.profile == "full":
        parser.error("--dataset-file contains only smoke cases; use --data-directory for --profile full")
    try:
        return int(arguments.handler(arguments))
    except (ValueError, OSError) as error:
        parser.exit(2, f"error: {error}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.locomo_plus",
        description="Evaluate pinned LoCoMo-Plus factual and cognitive cases through PowerContext.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="download, validate and audit pinned data without model calls")
    _dataset_arguments(inspect)
    inspect.set_defaults(handler=_inspect)

    run = commands.add_parser("run", help="run or resume a bounded smoke profile or an explicit full profile")
    _dataset_arguments(run)
    run.add_argument("--profile", choices=("smoke", "full"), default="smoke")
    run.add_argument(
        "--limit",
        type=_positive_int,
        help="selected-case limit; smoke limits above 4 extend the fixed anchors evenly across cognitive relations",
    )
    run.add_argument("--arm", choices=ARMS, default="memory")
    run.add_argument("--env-file", type=Path, default=Path(".env"))
    run.add_argument("--judge-model", type=_nonempty_text, help="explicit judge model, for example openai:gpt-4o-mini")
    run.add_argument("--run-id", type=_nonempty_text, help="stable isolated namespace; reuse it to resume")
    run.add_argument("--output-directory", type=Path, help="defaults to benchmark/locomo_plus/results/<run-id>")
    run.add_argument("--top-k", type=_positive_int, default=5)
    run.add_argument(
        "--max-tokens", type=_positive_int, default=512, help="output cap for each answer and judge request"
    )
    run.add_argument(
        "--max-history-sessions",
        type=_positive_int,
        help="optional diagnostic history cap; smoke and full both keep complete histories when omitted",
    )
    run.add_argument("--prices", type=Path, help="versioned JSON model price table; costs are unknown when omitted")
    run.add_argument(
        "--dry-run", action="store_true", help="print case selection and execution plan without model calls"
    )
    run.set_defaults(handler=_run)

    replay = commands.add_parser("replay", help="recompute a saved run summary without model or database calls")
    replay.add_argument("--run-directory", type=Path, required=True)
    replay.set_defaults(handler=_replay)
    return parser


def _dataset_arguments(parser: argparse.ArgumentParser) -> None:
    data = parser.add_mutually_exclusive_group()
    data.add_argument("--data-directory", type=Path, default=DEFAULT_DATA_DIR, help="local pinned dataset cache")
    data.add_argument(
        "--dataset-file", type=Path, help="bundled ten-case smoke JSON with complete histories; no download"
    )
    parser.add_argument("--seed", type=int, default=42, help="deterministic cognitive cue placement seed")


def _load_dataset(arguments: argparse.Namespace) -> LoCoMoPlusDataset:
    if arguments.dataset_file:
        return load_smoke_dataset(arguments.dataset_file, seed=arguments.seed)
    ensure_dataset(arguments.data_directory)
    return load_locomo_plus(arguments.data_directory, seed=arguments.seed)


def _inspect(arguments: argparse.Namespace) -> int:
    dataset = _load_dataset(arguments)
    _print({"dataset": dataset.manifest})
    return 0


def _run(arguments: argparse.Namespace) -> int:
    dataset = _load_dataset(arguments)
    selection: dict[str, Any] = {
        "profile": arguments.profile,
        "limit": arguments.limit,
        "arm": arguments.arm,
        "max_history_sessions": arguments.max_history_sessions,
    }
    plan = dry_run_plan(dataset, **selection)
    if arguments.dry_run:
        _print({"dry_run": True, "plan": plan})
        return 0

    settings = load_settings(arguments.env_file)
    run_id = normalize_run_id(arguments.run_id or datetime.now(UTC).strftime("locomo-plus-%Y%m%dT%H%M%SZ"))
    output_directory = (arguments.output_directory or DEFAULT_RESULTS / run_id).resolve()
    _print({"plan": plan, "run_id": run_id, "output_directory": str(output_directory)})
    summary = asyncio.run(
        run_benchmark(
            dataset,
            settings=settings,
            output_directory=output_directory,
            run_id=run_id,
            judge_model=arguments.judge_model,
            top_k=arguments.top_k,
            max_tokens=arguments.max_tokens,
            prices=arguments.prices,
            **selection,
        )
    )
    _print({"summary": summary, "output_directory": str(output_directory)})
    overall = summary["overall"]
    return int(overall["failure_count"] > 0 or overall["unobserved_count"] > 0)


def _replay(arguments: argparse.Namespace) -> int:
    summary = replay_results(arguments.run_directory.resolve())
    _print({"summary": summary})
    return 0


def _print(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")  # noqa: TRY003
    return parsed


def _nonempty_text(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("value must not be empty")  # noqa: TRY003
    return value.strip()


__all__ = ["main"]
