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

"""Versioned, type-preserving fixture encoding that never combines surrogates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA = "powercontext.unicode-contract.fixtures.v1"


class StrSubclass(str):
    """Exercise the public entry's exact handling of a Python str subclass."""


def codepoints(value: str) -> list[int]:
    return [ord(character) for character in value]


def decode_string(data: dict[str, Any]) -> str:
    if data["type"] not in {"str", "str_subclass"}:
        message = f"not a string fixture: {data['type']!r}"
        raise TypeError(message)
    points = data["codepoints"]
    if not isinstance(points, list):
        message = "codepoints must be a list"
        raise TypeError(message)
    for point in points:
        if type(point) is not int or not 0 <= point <= 0x10FFFF:
            message = f"invalid codepoint: {point!r}"
            raise ValueError(message)
    # chr() deliberately admits surrogate codepoints. Joining cannot merge a pair.
    value = "".join(chr(point) for point in points)
    if codepoints(value) != points:
        message = "string fixture did not round-trip exactly"
        raise AssertionError(message)
    return StrSubclass(value) if data["type"] == "str_subclass" else value


def encode_string(value: str) -> dict[str, Any]:
    return {
        "type": "str" if type(value) is str else "str_subclass",
        "codepoints": codepoints(value),
        "python_type": f"{type(value).__module__}.{type(value).__qualname__}",
    }


def decode_input(data: dict[str, Any]) -> object:
    kind = data["type"]
    if kind in {"str", "str_subclass"}:
        return decode_string(data)
    if kind == "none":
        return None
    if kind == "bool":
        if type(data["value"]) is not bool:
            message = "bool fixture requires a JSON boolean"
            raise TypeError(message)
        return data["value"]
    if kind == "int":
        if type(data["value"]) is not int:
            message = "int fixture requires a JSON integer"
            raise TypeError(message)
        return data["value"]
    message = f"unknown input type: {kind!r}"
    raise ValueError(message)


def typed(value: object) -> Any:
    """Encode result containers without discarding tuple/string/bool distinctions."""
    if value is None:
        return {"type": "none"}
    if type(value) is bool:
        return {"type": "bool", "value": value}
    if type(value) is int:
        return {"type": "int", "value": value}
    if type(value) is float:
        # The investigated entries do not return floats. Refuse lossy/non-finite JSON extensions.
        if value != value or value in {float("inf"), float("-inf")}:
            message = "non-finite float requires an explicit fixture encoding"
            raise ValueError(message)
        return {"type": "float", "value": value, "hex": value.hex()}
    if isinstance(value, str):
        return encode_string(value)
    if isinstance(value, bytes):
        return {"type": "bytes", "hex": value.hex()}
    if isinstance(value, tuple):
        return {"type": "tuple", "items": [typed(item) for item in value]}
    if isinstance(value, list):
        return {"type": "list", "items": [typed(item) for item in value]}
    message = f"unsupported result type: {type(value).__module__}.{type(value).__qualname__}"
    raise TypeError(message)


def load_cases(path: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    if document.get("schema") != SCHEMA:
        message = "unsupported fixture schema"
        raise ValueError(message)
    cases = document["cases"]
    identifiers = [case["id"] for case in cases]
    if len(identifiers) != len(set(identifiers)):
        message = "duplicate case id"
        raise ValueError(message)
    for case in cases:
        decode_input(case["input"])
    import hashlib

    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "lf_sha256": hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
    }, cases


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
