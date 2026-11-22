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

"""Lossless test protocol; the frozen task harness remains the value codec."""

from __future__ import annotations

import hashlib
import json
import os
import runpy
from functools import lru_cache
from pathlib import Path
from typing import Any


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode_value(value: dict[str, Any]) -> Any:
    kind = value["type"]
    if kind in ("str", "int", "bool", "NoneType"):
        result = value["value"]
        expected = {"str": str, "int": int, "bool": bool, "NoneType": type(None)}[kind]
        if type(result) is not expected:
            message = "Request value has an incorrect Python type"
            raise ValueError(message)
        return result
    if kind == "str32":
        return bytes.fromhex(value["value"]).decode("utf-32-le", "surrogatepass")
    if kind == "str-repeat" and type(value["count"]) is int and value["count"] >= 0:
        char = value["char"]
        if type(char) is str and len(char) == 1:
            return char * value["count"]
    message = "Unsupported request value codec"
    raise ValueError(message)


def decode_request(request: str) -> tuple[str, dict[str, Any]]:
    value = json.loads(request)
    if type(value) is not dict or value.get("schema") != 1 or type(value.get("id")) is not str:
        message = "Invalid tool request"
        raise ValueError(message)
    if set(value) == {"schema", "id", "missing"} and value["missing"] is True:
        return value["id"], {"missing": True}
    if set(value) != {"schema", "id", "value"}:
        message = "Invalid call shape"
        raise ValueError(message)
    return value["id"], {"invalid": decode_value(value["value"])}


@lru_cache(maxsize=1)
def context() -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(os.environ["PC_TOPIC_TOOL_CONTEXT"])
    if sha(path) != os.environ["PC_TOPIC_TOOL_CONTEXT_SHA256"]:
        message = "Tool context identity mismatch"
        raise RuntimeError(message)
    data = json.loads(path.read_text())
    root = Path(data["root"])
    for relative, expected in data["source_hashes"].items():
        if sha(root / relative) != expected:
            message = f"Tool source drift: {relative}"
            raise RuntimeError(message)
    harness = runpy.run_path(str(root / "python/powercontext_topic_chunk_rs/_harness.py"))
    harness["check_reference"]()
    return data, harness


def capture(function: Any, request: str) -> str:
    _, harness = context()
    _, case = decode_request(request)
    # Only the actual business call is caught by outcome(); codec/monitor errors propagate.
    return canonical(harness["outcome"](function, case))
