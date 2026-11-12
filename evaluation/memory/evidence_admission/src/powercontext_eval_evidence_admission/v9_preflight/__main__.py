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

"""Offline SDK preflight CLI, deliberately exposing no real-provider run command."""

import argparse
import asyncio
import json
from pathlib import Path

from ..v7.workflow import write_result
from .contract import identity
from .workflow import preflight


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("mock")
    run.add_argument("--v9-root", type=Path, required=True)
    run.add_argument("--session", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        result = identity()
        write_result(args.output, result)
    else:
        result = asyncio.run(preflight(args.v9_root, args.session, args.output_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
