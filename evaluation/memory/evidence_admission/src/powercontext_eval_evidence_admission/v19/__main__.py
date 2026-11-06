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

"""Opt-in continuation of exactly the remaining 467 native comparison requests."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ..manifests import EvaluationError, read_json
from ..v18.core import committed_freeze, dump
from ..v18.workflow import locked
from . import workflow


def show(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


async def execute(args):
    if args.command == "freeze":
        return committed_freeze()
    if args.command == "prepare":
        return workflow.prepare(
            args.source, args.session, approved_total=args.approved_total_cny, authorization=args.authorization
        )
    if args.command == "preflight":
        return await workflow.preflight(args.session, progress=show)
    if args.command == "initialize":
        return workflow.initialize(args.session)
    if args.command == "status":
        return workflow.status(args.session)
    if args.command == "report":
        return workflow.export(args.session, args.output)
    if not args.execute_live:
        raise EvaluationError("v19_explicit_execute_live_required")
    try:
        while True:
            result = await workflow.run_next(args.session, execute_live=True)
            show(result)
            if result["state"] != "ready":
                return result
    except asyncio.CancelledError:
        with locked(args.session):
            state = read_json(args.session / "state.json")
            if state["state"] == "ready":
                state.update(state="cancelled", stop_reason="cancelled_before_next_request")
                dump(args.session / "state.json", state)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("freeze")
    for name in ("prepare", "preflight", "initialize", "run", "status", "report"):
        command = sub.add_parser(name)
        command.add_argument("--session", type=Path, required=True)
        if name == "prepare":
            command.add_argument("--source", type=Path, required=True)
            command.add_argument("--approved-total-cny", default="5.00")
            command.add_argument("--authorization", default="existing_user_approved_cumulative_5_CNY")
        if name == "report":
            command.add_argument("--output", type=Path, required=True)
        if name == "run":
            command.add_argument("--execute-live", action="store_true")
    try:
        show(asyncio.run(execute(parser.parse_args())))
    except EvaluationError as error:
        show({"error": str(error)})
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        show({"state": "cancelled", "automatic_resend": False})
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
