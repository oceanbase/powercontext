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

"""Mandatory-native entry for the official tool; never a production selector."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import _native
from ._monitor import NativeMonitor
from ._tool_protocol import capture, context, decode_request, sha


@lru_cache(maxsize=1)
def session() -> dict[str, Any]:
    if os.environ["PC_TOPIC_TOOL_PHASE"] not in ("candidate", "bench"):
        message = "Candidate wrapper is not a reference recorder"
        raise RuntimeError(message)
    data, _ = context()
    package = Path(__file__).parent
    for name, expected in data["candidate_sources"].items():
        if sha(package / name) != expected:
            message = f"Tool candidate source drift: {name}"
            raise RuntimeError(message)
    binary = Path(_native.__file__).resolve()
    if binary.parent != package.resolve() or sha(binary) != os.environ["PC_TOPIC_TOOL_BINARY_SHA256"]:
        message = "Tool candidate binary identity mismatch"
        raise RuntimeError(message)
    run_id = os.environ["PC_TOPIC_TOOL_RUN_ID"]
    if len(run_id) != 32 or any(char not in "0123456789abcdef" for char in run_id):
        message = "Invalid tool run identity"
        raise RuntimeError(message)
    output = Path(os.environ["PC_TOPIC_TOOL_STATS"]) / f"{run_id}.{os.getpid()}.json"
    if output.exists():
        message = "Tool evidence already exists"
        raise RuntimeError(message)
    output.parent.mkdir(parents=True, exist_ok=True)
    monitor = NativeMonitor()
    monitor.install()
    from powercontext.builtin.artifacts.topic_memory.chunking import chunk_topic_memory_detail

    return {
        "data": data,
        "monitor": monitor,
        "function": chunk_topic_memory_detail,
        "output": output,
        "requests": [],
        "calls": 0,
        "protocol_rejections": [],
        "binary": str(binary),
        "binary_sha256": sha(binary),
        "run_id": run_id,
    }


def write_report(state: dict[str, Any]) -> None:
    report = {
        **state["monitor"].report(),
        "run_id": state["run_id"],
        "pid": os.getpid(),
        "calls": state["calls"],
        "requests": state["requests"],
        "binary_sha256": state["binary_sha256"],
        "context_sha256": os.environ["PC_TOPIC_TOOL_CONTEXT_SHA256"],
        "phase": os.environ["PC_TOPIC_TOOL_PHASE"],
        "python": sys.version,
        "protocol_rejections": state["protocol_rejections"],
        "complete": state["calls"] == len(state["data"]["requests"]) and len(state["protocol_rejections"]) == 1,
    }
    state["output"].write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")


def evaluate(request: str) -> str:
    state = session()
    data, monitor = state["data"], state["monitor"]
    monitor.check_health()
    before = monitor.report()
    request_sha256 = hashlib.sha256(request.encode("ascii")).hexdigest()
    try:
        identity, case = decode_request(request)
    except ValueError as error:
        monitor.check_health()
        rejection = {
            "sha256": request_sha256,
            "class": type(error).__module__ + "." + type(error).__qualname__,
            "text": str(error),
            "native_delta": monitor.report()["native_completions"] - before["native_completions"],
        }
        if (
            rejection != data["protocol_rejection"]
            or state["calls"] % len(data["requests"])
            or not state["calls"]
            or (state["protocol_rejections"] and os.environ["PC_TOPIC_TOOL_PHASE"] != "bench")
        ):
            message = "Unexpected protocol rejection or native execution"
            raise RuntimeError(message) from error
        state["protocol_rejections"].append(rejection)
        write_report(state)
        raise
    index = state["calls"]
    expected = data["requests"][index % len(data["requests"])]
    if expected != {"id": identity, "sha256": request_sha256}:
        message = "Tool request order or identity mismatch"
        raise RuntimeError(message)
    if os.environ["PC_TOPIC_TOOL_PHASE"] != "bench" and index >= len(data["requests"]):
        message = "Tool replay duplicated a request"
        raise RuntimeError(message)
    monitor.check_health()
    result = capture(state["function"], request)
    monitor.check_health()
    after = monitor.report()
    native_call = not case.get("missing") and type(case.get("invalid")) is str
    increment = int(native_call)
    if (
        after["N"] - before["N"] != increment
        or after["R"] - before["R"] != increment
        or after["F"]
        or after["E"]
        or after["native_completions"] - before["native_completions"] != increment
    ):
        message = "Tool call did not satisfy native path accounting"
        raise RuntimeError(message)
    state["calls"] += 1
    if index < len(data["requests"]):
        state["requests"].append(expected)
    write_report(state)
    return result
