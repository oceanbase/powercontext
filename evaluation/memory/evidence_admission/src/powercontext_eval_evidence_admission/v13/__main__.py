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

"""Freeze, prepare, preflight and explicitly advance an isolated Pipeline diagnostic session."""

import argparse
import asyncio
import json
from pathlib import Path

from ..manifests import EvaluationError, read_json
from .freeze import committed_freeze
from .preflight import preflight
from .replay import replay
from .report import report
from .runner import import_review, run_next, status
from .session import prepare_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("freeze")
    mock = commands.add_parser("preflight")
    mock.add_argument("--baseline-dir", type=Path, required=True)
    mock.add_argument("--output-dir", type=Path, required=True)
    recorded = commands.add_parser("replay")
    recorded.add_argument("--baseline-dir", type=Path, required=True)
    recorded.add_argument("--historical-session", type=Path, required=True)
    recorded.add_argument("--output-dir", type=Path, required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--baseline-dir", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--mode", choices=("live", "offline_mock"), default="offline_mock")
    prepare.add_argument("--preflight-dir", type=Path)
    publish = commands.add_parser("report")
    publish.add_argument("--session", type=Path, required=True)
    publish.add_argument("--output-dir", type=Path, required=True)
    for command in ("run", "review", "status"):
        sub = commands.add_parser(command)
        sub.add_argument("--session", type=Path, required=True)
        if command == "run":
            sub.add_argument("--approval", type=Path, required=True)
            sub.add_argument("--execute-live", action="store_true", required=True)
        if command == "review":
            sub.add_argument("--review-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            result = committed_freeze()
        elif args.command == "preflight":
            result = asyncio.run(preflight(args.baseline_dir, args.output_dir))
        elif args.command == "replay":
            result = asyncio.run(replay(args.baseline_dir, args.historical_session, args.output_dir))
        elif args.command == "prepare":
            plan = asyncio.run(
                prepare_session(args.baseline_dir, args.output_dir, mode=args.mode, preflight_dir=args.preflight_dir)
            )
            from ..models import digest

            result = {"plan_hash": digest(plan), "mode": plan["execution_mode"], "provider_requests": 0}
        elif args.command == "report":
            result = report(args.session, args.output_dir)
        elif args.command == "run":
            result = asyncio.run(run_next(args.session, read_json(args.approval)))
        elif args.command == "review":
            result = import_review(args.session, read_json(args.review_file))
        else:
            result = status(args.session)
    except EvaluationError as error:
        parser.exit(2, str(error) + "\n")
    except Exception as error:
        # Provider and filesystem exception values are private audit data, never terminal output.
        raise SystemExit("v13_execution_failed_inspect_private_audit") from error
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
