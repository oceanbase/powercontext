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

"""Run with python -m evaluation.performance.input_encoding."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .dataset import DEFAULT_DATASET, offline
from .judging import judge
from .report import build_report
from .runner import run
from .semantics import run as run_semantic_probe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("offline", help="all 731 exact projections; no credentials, DB, or model calls")
    prepare.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--iterations", type=int, default=3)
    live = commands.add_parser("run", help="real model + configured DB; creates isolated evaluation Scopes")
    live.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    live.add_argument("--output", type=Path, required=True)
    live.add_argument("--env-file", type=Path, required=True)
    live.add_argument("--database-config", type=Path)
    live.add_argument("--protection-config", type=Path)
    live.add_argument("--max-output-tokens", type=int)
    live.add_argument("--case-ids", type=Path)
    live.add_argument("--cohort")
    live.add_argument("--limit", type=int, default=24, choices=range(1, 25))
    live.add_argument("--repeats", type=int, default=2)
    live.add_argument("--seed", type=int, default=2086)
    live.add_argument("--concurrency", type=int, default=1)
    live.add_argument("--timeout", type=float, default=180)
    grading = commands.add_parser("judge", help="blinded semantic quality and Experience-only transfer questions")
    grading.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    grading.add_argument("--output", type=Path, required=True)
    grading.add_argument("--env-file", type=Path, required=True)
    grading.add_argument("--protection-config", type=Path)
    grading.add_argument(
        "--reference-questions", type=Path, help="source-audited questions; skips paid question authoring"
    )
    grading.add_argument(
        "--validate-only", action="store_true", help="check all references and producer coverage offline"
    )
    grading.add_argument("--max-output-tokens", type=int, default=4096)
    grading.add_argument("--concurrency", type=int, default=2)
    grading.add_argument("--timeout", type=float, default=180)
    report = commands.add_parser("report")
    report.add_argument("--output", type=Path, required=True)
    semantic = commands.add_parser(
        "semantic-probe", help="fixed synthetic representation questions; no Runtime or tools"
    )
    semantic.add_argument("--output", type=Path, required=True)
    semantic.add_argument("--schema", choices=("v1", "v2", "both"), default="both")
    semantic.add_argument(
        "--dry-run", action="store_true", help="validate fixtures and selected prompts; no model calls"
    )
    semantic.add_argument("--env-file", type=Path)
    semantic.add_argument("--protection-config", type=Path)
    semantic.add_argument("--max-output-tokens", type=int, default=2048)
    semantic.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    for key in ("iterations", "repeats", "concurrency", "timeout", "max_output_tokens"):
        if getattr(args, key, None) is not None and getattr(args, key) <= 0:
            parser.error(f"{key} must be positive")
    if args.command == "offline":
        print(json.dumps(offline(args.dataset, args.output, iterations=args.iterations), ensure_ascii=False, indent=2))
    elif args.command == "run":
        asyncio.run(run(args))
        build_report(args.output)
    elif args.command == "judge":
        asyncio.run(judge(args))
        if not args.validate_only:
            build_report(args.output)
    elif args.command == "semantic-probe":
        if not args.dry_run and args.env_file is None:
            parser.error("semantic-probe requires --env-file unless --dry-run is selected")
        result = asyncio.run(run_semantic_probe(args))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        complete = result["required_rendering_coverage_complete"] if args.dry_run else result["all_probe_cases_passed"]
        if not complete:
            raise SystemExit(1)
    else:
        build_report(args.output)
        print(args.output / "report.md")


if __name__ == "__main__":
    main()
