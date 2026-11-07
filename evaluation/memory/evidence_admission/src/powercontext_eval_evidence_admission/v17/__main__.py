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

"""Opt-in preparation, SDK preflight, one live step and bound diagnostic review."""

import argparse
import asyncio
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("freeze")
    for name in ("preflight", "prepare"):
        sub = commands.add_parser(name)
        sub.add_argument("--baseline-root", type=Path, required=True)
        sub.add_argument("--session", type=Path, required=True)
        if name == "prepare":
            sub.add_argument("--preflight-session", type=Path, required=True)
    for name in ("run", "review", "status", "report"):
        sub = commands.add_parser(name)
        sub.add_argument("--session", type=Path, required=True)
        if name == "run":
            sub.add_argument("--execute-live", action="store_true", required=True)
        if name == "review":
            sub.add_argument("--review-file", type=Path, required=True)
        if name == "report":
            sub.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from ..manifests import EvaluationError, read_json
    from ..models import digest
    from .freeze import committed_freeze
    from .workflow import preflight, prepare, review, run_next, save_json, status

    try:
        if args.command == "freeze":
            result = committed_freeze()
        elif args.command == "preflight":
            result = asyncio.run(preflight(args.baseline_root, args.session))
        elif args.command == "prepare":
            checked = read_json(args.preflight_session / "preflight.json")
            result = asyncio.run(prepare(args.baseline_root, args.session, "live"))
            if not checked["passed"] or checked["plan_hash"] != digest(result):
                raise EvaluationError("v17_preflight_plan_mismatch")
            save_json(args.session / "preflight.json", checked, new=True)
        elif args.command == "run":
            result = asyncio.run(run_next(args.session, execute_live=args.execute_live))
        elif args.command == "review":
            result = review(args.session, read_json(args.review_file))
        else:
            result = status(args.session)
            if args.command == "report":
                save_json(args.output, result, new=True)
    except EvaluationError as error:
        parser.exit(2, str(error) + "\n")
    except Exception:  # noqa: BLE001 -- CLI must not expose provider errors or sensitive paths.
        parser.exit(2, "v17_execution_failed_inspect_private_audit\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
