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

"""Run native hooks or serve bounded JSON Lines using the installed client."""

from __future__ import annotations

import argparse
import asyncio
import json
import runpy
import sys
from pathlib import Path

from pydantic import ValidationError

from powercontext.client.integration.core import MAX_MESSAGE_BYTES, WRITE_OPERATIONS, execute
from powercontext.client.integration.models import HookRequest


def main() -> int:
    """Run a native adapter or serve JSON Lines using the installed client's Python."""

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--doctor", action="store_true", help="diagnose the supplied connection")
    mode.add_argument("--script", type=Path, help="run a native hook adapter in the client environment")
    parser.add_argument("script_args", nargs=argparse.REMAINDER, help="arguments after -- are passed to the adapter")
    arguments = parser.parse_args()
    if arguments.doctor:
        if arguments.script_args:
            parser.error("adapter arguments require --script")
        from powercontext.client.integration.doctor import DoctorRequest, diagnose_server

        try:
            raw = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
            if len(raw) > MAX_MESSAGE_BYTES:
                sys.stderr.write("Invalid diagnostic frame.\n")
                return 2
            request = DoctorRequest.model_validate_json(raw)
            report = asyncio.run(diagnose_server(request.connection, deadline=request.deadline))
        except ValueError:
            sys.stderr.write("Invalid diagnostic request.\n")
            return 2
        sys.stdout.write(json.dumps(report) + "\n")
        return 0 if report["ok"] else 1
    if arguments.script is not None:
        script = arguments.script.resolve()
        if not script.is_file():
            parser.error("hook adapter must be an existing file")
        script_args = arguments.script_args
        if script_args[:1] == ["--"]:
            script_args = script_args[1:]
        sys.argv = [str(script), *script_args]
        sys.path.insert(0, str(script.parent))
        sys.dont_write_bytecode = True
        runpy.run_path(str(script), run_name="__main__")
        return 0
    if arguments.script_args:
        parser.error("adapter arguments require --script")
    return run_worker()


def run_worker() -> int:
    """Read one operation per line; malformed framing terminates the worker."""

    with asyncio.Runner() as runner:
        while raw := sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1):
            if len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
                sys.stderr.write("Invalid hook frame size or terminator.\n")
                return 2
            try:
                request = HookRequest.model_validate_json(raw)
            except ValidationError:
                # Validation errors include payload values; never print them.
                sys.stderr.write("Invalid hook request.\n")
                return 2

            def on_headers(status: int, request_id: str | None, *, frame_id: str = request.id) -> None:
                sys.stdout.write(
                    json.dumps({
                        "protocol": 1,
                        "id": frame_id,
                        "event": "response_headers",
                        "status_code": status,
                        "request_id": request_id,
                    })
                    + "\n"
                )
                sys.stdout.flush()

            result = runner.run(execute(request, on_headers=on_headers if request.observe_headers else None))
            encoded = result.model_dump_json() + "\n"
            if len(encoded.encode()) > MAX_MESSAGE_BYTES:
                encoded = (
                    json.dumps({
                        "protocol": 1,
                        "id": request.id,
                        "outcome": "unknown" if request.operation in WRITE_OPERATIONS else "failed",
                        "error": "invalid_response",
                    })
                    + "\n"
                )
            sys.stdout.write(encoded)
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
