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

"""Separate offline v15 CLI; deliberately provides no live command."""

import argparse
import asyncio
import json
from pathlib import Path

from .freeze import identity
from .replay import replay_history
from .workflow import compare, summarize, write_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--output", type=Path, required=True)
    comparison = commands.add_parser("compare")
    comparison.add_argument("--dataset", choices=("synthetic-v1", "validation-v2", "validation-v3"), required=True)
    comparison.add_argument("--reference-run", type=Path, required=True)
    comparison.add_argument("--output-dir", type=Path, required=True)
    replay = commands.add_parser("replay")
    replay.add_argument("--history-root", type=Path, required=True)
    replay.add_argument("--output-dir", type=Path, required=True)
    summary = commands.add_parser("summarize")
    summary.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        result = identity()
        write_result(args.output, result)
    elif args.command == "compare":
        result = asyncio.run(compare(args.dataset, args.reference_run, args.output_dir))
    elif args.command == "replay":
        result = asyncio.run(replay_history(args.history_root, args.output_dir))
    else:
        result = summarize(args.run_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
