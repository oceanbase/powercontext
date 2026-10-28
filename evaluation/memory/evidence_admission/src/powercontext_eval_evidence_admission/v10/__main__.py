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

"""Offline commands only; no live command or provider credential access."""

import argparse
import asyncio
import json
from pathlib import Path

from .workflow import compare, replay_historical


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("replay")
    replay.add_argument("--canary-dir", type=Path, required=True)
    replay.add_argument("--output-dir", type=Path, required=True)
    comparison = commands.add_parser("compare")
    comparison.add_argument("--v9-root", type=Path, required=True)
    comparison.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    work = (
        replay_historical(args.canary_dir, args.output_dir)
        if args.command == "replay"
        else compare(args.v9_root, args.output_dir)
    )
    print(json.dumps(asyncio.run(work), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
