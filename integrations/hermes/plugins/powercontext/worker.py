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

"""Standard-library transport to the installed PowerContext operation worker."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from typing import Any

from .runtime_operations import WRITE_OPERATIONS

MAX_MESSAGE_BYTES = 1_048_576


class WorkerError(RuntimeError):
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__("PowerContext operation failed")


def execute_worker(operation: str, arguments: dict[str, Any], connection: dict[str, Any], deadline: float) -> Any:
    request_id = uuid.uuid4().hex
    remaining = deadline - time.time()
    if remaining <= 0:
        raise WorkerError({"outcome": "failed", "error": "deadline"})
    frame = (
        json.dumps(
            {
                "protocol": 1,
                "id": request_id,
                "operation": operation,
                "arguments": arguments,
                "connection": connection,
                "deadline": deadline,
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    if len(frame.encode()) > MAX_MESSAGE_BYTES:
        raise WorkerError({"outcome": "failed", "error": "invalid_request"})
    unknown = "unknown" if operation in WRITE_OPERATIONS else "failed"
    executable = shutil.which("powercontext-hook")
    if executable is None:
        raise WorkerError({"outcome": "failed", "error": "configuration"})
    try:
        completed = subprocess.run(  # noqa: S603
            [executable],
            input=frame,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=remaining,
            check=False,
        )
    except OSError as error:
        raise WorkerError({"outcome": "failed", "error": "configuration"}) from error
    except subprocess.TimeoutExpired as error:
        raise WorkerError({"outcome": unknown, "error": "deadline"}) from error
    if completed.returncode or len(completed.stdout.encode()) > MAX_MESSAGE_BYTES:
        raise WorkerError({"outcome": unknown, "error": "invalid_response"})
    try:
        result = json.loads(completed.stdout)
    except ValueError as error:
        raise WorkerError({"outcome": unknown, "error": "invalid_response"}) from error
    if (
        not isinstance(result, dict)
        or result.get("protocol") != 1
        or result.get("id") != request_id
        or result.get("outcome") not in {"ok", "empty", "failed", "unknown"}
    ):
        raise WorkerError({"outcome": unknown, "error": "invalid_response"})
    if result.get("outcome") not in {"ok", "empty"}:
        raise WorkerError(result)
    return result.get("value")
