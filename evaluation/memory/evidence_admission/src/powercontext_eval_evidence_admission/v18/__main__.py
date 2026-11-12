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

"""Explicit offline preparation, opt-in live execution, review, replay and report commands."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ..manifests import EvaluationError, read_json
from .core import committed_freeze, dump, identity, prepare
from .report import export, summarize
from .workflow import apply_review, initialize_live, locked, preflight, replay_recorded, run_next


def show(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


async def execute(args):
    session = args.session
    if args.command == "prepare":
        return await prepare(session, progress=show)
    if args.command == "preflight":
        return await preflight(session, progress=show)
    if args.command == "freeze":
        return committed_freeze()
    if args.command == "identity":
        return identity()
    if args.command == "initialize":
        return initialize_live(session)
    if args.command == "run":
        if not args.execute_live:
            raise EvaluationError("v18_explicit_execute_live_required")
        try:
            while True:
                result = await run_next(session, execute_live=True)
                show(result)
                if result["state"] != "ready":
                    return result
        except asyncio.CancelledError:
            # Cancellation can arrive during Source reconstruction, before run_next enters
            # its HTTP cancellation boundary. A ready session must not silently survive it.
            with locked(session):
                state = read_json(session / "state.json")
                if state["state"] in {"ready", "prepared"}:
                    state.update(state="cancelled", stop_reason="cancelled_before_next_request")
                    dump(session / "state.json", state)
            raise
    if args.command == "replay":
        return await replay_recorded(session, args.output)
    if args.command == "review":
        return apply_review(session, args.call_id, args.review_file)
    if args.command == "report":
        return export(session, args.output)
    if args.command == "status":
        state = read_json(session / "state.json")
        return {
            "state": state["state"],
            "completed_calls": len(state["records"]),
            "known_charge_cny": state["known_charge_cny"],
        }
    if args.command == "offline-report":
        result = summarize(session)["preparation"]
        dump(args.output, result, new=True)
        return result
    raise EvaluationError("v18_unknown_command")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "preflight", "initialize", "run", "status", "report", "offline-report", "replay", "review"):
        command = sub.add_parser(name)
        command.add_argument("--session", type=Path, required=True)
        if name in {"report", "offline-report", "replay"}:
            command.add_argument("--output", type=Path, required=True)
        if name == "run":
            command.add_argument("--execute-live", action="store_true")
        if name == "review":
            command.add_argument("--call-id", required=True)
            command.add_argument("--review-file", type=Path, required=True)
    sub.add_parser("freeze")
    sub.add_parser("identity")
    args = parser.parse_args()
    if not hasattr(args, "session"):
        args.session = None
    try:
        show(asyncio.run(execute(args)))
    except EvaluationError as error:
        show({"error": str(error)})
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        show({"state": "cancelled", "automatic_resend": False})
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
