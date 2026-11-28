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

"""Prepare offline; real requests require an explicit command and plan-bound approval."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx
from pydantic import ValidationError

from ..manifests import EvaluationError, read_json
from ..v7.workflow import write_result
from ..v9_preflight.workflow import mock_response
from .contract import identity
from .plan import load_session, prepare_session
from .runner import import_review, run_next, status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--v9-root", type=Path, required=True)
    prepare.add_argument("--sdk-preflight-root", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--mock", action="store_true")
    for name in ("status", "review", "mock", "run"):
        command = commands.add_parser(name)
        command.add_argument("--session", type=Path, required=True)
        if name in {"mock", "run"}:
            command.add_argument("--approval", type=Path, required=True)
        if name == "run":
            command.add_argument("--execute-live", action="store_true", required=True)
        if name == "review":
            command.add_argument("--review-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            result = identity()
            write_result(args.output, result)
        elif args.command == "prepare":
            result = asyncio.run(
                prepare_session(
                    args.v9_root, args.sdk_preflight_root, args.output_dir, mode="offline_mock" if args.mock else "live"
                )
            )
        elif args.command == "status":
            result = status(args.session)
        elif args.command == "review":
            result = import_review(args.session, read_json(args.review_file))
        else:
            public, _, _ = load_session(args.session)
            if public["execution_mode"] != ("live" if args.command == "run" else "offline_mock"):
                raise EvaluationError("command_session_mode_mismatch")
            result = asyncio.run(
                run_next(
                    args.session,
                    read_json(args.approval),
                    transport=httpx.MockTransport(lambda request: httpx.Response(200, json=mock_response()))
                    if args.command == "mock"
                    else None,
                    api_key="offline-placeholder-not-a-real-key" if args.command == "mock" else None,
                )
            )
    except ValidationError:
        parser.exit(2, "invalid_execution_or_review_contract\n")
    except EvaluationError as error:
        parser.exit(2, str(error) + "\n")
    except Exception:  # noqa: BLE001 - this CLI boundary must not print provider or filesystem values.
        # Provider, filesystem and review values belong in private evidence, not terminal diagnostics.
        parser.exit(2, "execution_boundary_failed_inspect_private_session\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
