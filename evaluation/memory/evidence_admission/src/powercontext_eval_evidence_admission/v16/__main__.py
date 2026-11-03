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

"""Separate offline v16 CLI; deliberately provides no live command."""

import argparse
import asyncio
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--output", type=Path, required=True)
    comparison = commands.add_parser("compare")
    comparison.add_argument("--dataset", choices=("synthetic-v1", "validation-v2", "validation-v3"), required=True)
    comparison.add_argument("--reference-run", type=Path, required=True)
    comparison.add_argument("--output-dir", type=Path, required=True)
    comparison.add_argument("--historical-run", type=Path, required=True)
    replay = commands.add_parser("replay")
    replay.add_argument("--history-root", type=Path, required=True)
    replay.add_argument("--output-dir", type=Path, required=True)
    summary = commands.add_parser("summarize")
    summary.add_argument("--run-root", type=Path, required=True)
    minimal = commands.add_parser("minimal-plan")
    minimal.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    entry = Path(__file__).resolve()
    relative = "evaluation/memory/evidence_admission/src/powercontext_eval_evidence_admission/v16/__main__.py"
    if not any((parent / ".git").exists() and (parent / relative).resolve() == entry for parent in entry.parents):
        parser.error("v16 execution requires a Git source checkout; installed wheels support --help only")

    from .freeze import committed_freeze, identity
    from .replay import replay_history
    from .workflow import compare, minimal_plan, summarize, write_result

    if args.command == "freeze":
        result = {"identity": identity(), "committed_freeze": committed_freeze()}
        write_result(args.output, result)
    elif args.command == "compare":
        result = asyncio.run(compare(args.dataset, args.reference_run, args.output_dir, args.historical_run))
    elif args.command == "replay":
        result = asyncio.run(replay_history(args.history_root, args.output_dir))
    elif args.command == "minimal-plan":
        result = asyncio.run(minimal_plan(args.run_root))
    else:
        result = summarize(args.run_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
