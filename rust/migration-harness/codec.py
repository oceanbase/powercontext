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

"""Lossless, versioned codec for the migration harness record format (LF-delimited JSON).

Supported types are explicit and closed: ``str``, ``bool``, ``int`` (arbitrary precision),
``float`` (including ``-0.0``, ``nan``, ``inf``), ``bytes``, ``tuple``, ``list``, ``None`` and a
structured exception record. Anything else raises :class:`UnsupportedType` instead of falling back
to ``repr``.

Records are written one JSON object per line, and lines are split **only** on ``\n`` so strings
containing U+0085 / U+2028 / U+2029 stay in one record. Strings are stored as codepoint arrays so an
explicit surrogate pair (``"a\\ud85d\\udf41b"``) stays distinct from the real scalar
(``"a\\U00027741b"``), which JSON text alone cannot guarantee once a reader round-trips it.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA_ID = "powercontext.migration-harness.v1"

# Marker keys are prefixed so a plain mapping can never be confused with a tagged value.
_TAG = "$t"
_VALUE = "$v"
_CANONICAL_FIELDS = frozenset({_TAG, _VALUE})


class UnsupportedType(TypeError):
    """Raised for values outside the closed, explicitly supported type set."""


class MalformedRecord(ValueError):
    """Raised for a record that does not match the declared schema."""


def _encode_float(value: float) -> str:
    if math.isnan(value):
        return "nan"
    if value == math.inf:
        return "inf"
    if value == -math.inf:
        return "-inf"
    if value == 0.0:
        # Keep the sign of zero: `-0.0` and `0.0` are distinct behavioural inputs.
        return "-0" if math.copysign(1.0, value) < 0 else "0"
    return repr(value)


def _decode_float(text: str) -> float:
    if text == "nan":
        return math.nan
    if text == "inf":
        return math.inf
    if text == "-inf":
        return -math.inf
    if text == "-0":
        return -0.0
    return float(text)


def encode_value(value: Any) -> Any:
    """Encode one supported value into its tagged JSON representation."""

    # bool must be tested before int: `isinstance(True, int)` is True.
    if isinstance(value, bool):
        return {_TAG: "bool", _VALUE: value}
    if isinstance(value, str):
        return {_TAG: "str", _VALUE: [ord(character) for character in value]}
    if isinstance(value, int):
        return {_TAG: "int", _VALUE: str(value)}
    if isinstance(value, float):
        return {_TAG: "float", _VALUE: _encode_float(value)}
    if isinstance(value, bytes):
        return {_TAG: "bytes", _VALUE: value.hex()}
    if value is None:
        return {_TAG: "none"}
    if isinstance(value, (tuple, list)):
        return {
            _TAG: "tuple" if isinstance(value, tuple) else "list",
            _VALUE: [encode_value(item) for item in value],
        }
    message = f"unsupported value type: {type(value).__module__}.{type(value).__qualname__}"
    raise UnsupportedType(message)


def encode_exception(error: BaseException) -> dict[str, Any]:
    """Encode a structured exception: type identity plus a lossless argument list."""

    return {
        _TAG: "exception",
        _VALUE: {
            "module": type(error).__module__,
            "qualname": type(error).__qualname__,
            "args": [encode_value(argument) for argument in error.args],
        },
    }


def _payload(document: dict[str, Any]) -> Any:
    if _VALUE not in document:
        message = f"{document.get(_TAG)!r} value is missing $v"
        raise MalformedRecord(message)
    return document[_VALUE]


def _decode_none(document: dict[str, Any]) -> None:
    return None


def _decode_bool(document: dict[str, Any]) -> bool:
    value = _payload(document)
    if not isinstance(value, bool):
        message = "bool payload must be a JSON boolean"
        raise MalformedRecord(message)
    return value


def _is_codepoint(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 0x10FFFF


def _decode_str(document: dict[str, Any]) -> str:
    payload = _payload(document)
    if not isinstance(payload, list) or not all(_is_codepoint(item) for item in payload):
        message = "str payload must be a list of codepoints"
        raise MalformedRecord(message)
    return "".join(chr(item) for item in payload)


def _decode_int(document: dict[str, Any]) -> int:
    payload = _payload(document)
    if not isinstance(payload, str):
        message = "int payload must be a decimal string"
        raise MalformedRecord(message)
    try:
        return int(payload)
    except ValueError as error:
        message = f"int payload is not a decimal integer: {error}"
        raise MalformedRecord(message) from error


def _decode_float_value(document: dict[str, Any]) -> float:
    payload = _payload(document)
    if not isinstance(payload, str):
        message = "float payload must be a string"
        raise MalformedRecord(message)
    try:
        return _decode_float(payload)
    except ValueError as error:
        message = f"float payload is not a supported float: {error}"
        raise MalformedRecord(message) from error


def _decode_bytes(document: dict[str, Any]) -> bytes:
    payload = _payload(document)
    if not isinstance(payload, str):
        message = "bytes payload must be a hex string"
        raise MalformedRecord(message)
    try:
        return bytes.fromhex(payload)
    except ValueError as error:
        message = f"bytes payload is not valid hex: {error}"
        raise MalformedRecord(message) from error


def _decode_sequence(document: dict[str, Any]) -> tuple[Any, ...] | list[Any]:
    payload = _payload(document)
    if not isinstance(payload, list):
        message = "tuple/list payload must be an array"
        raise MalformedRecord(message)
    items = [decode_value(item) for item in payload]
    return tuple(items) if document[_TAG] == "tuple" else items


def _decode_exception(document: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(document)
    if not isinstance(payload, dict):
        message = "exception payload must be an object"
        raise MalformedRecord(message)
    module = payload.get("module")
    qualname = payload.get("qualname")
    arguments = payload.get("args")
    if not isinstance(module, str) or not isinstance(qualname, str) or not isinstance(arguments, list):
        message = "exception payload needs module, qualname and args"
        raise MalformedRecord(message)
    return {
        "module": module,
        "qualname": qualname,
        "args": [decode_value(argument) for argument in arguments],
    }


_DECODERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "none": _decode_none,
    "bool": _decode_bool,
    "str": _decode_str,
    "int": _decode_int,
    "float": _decode_float_value,
    "bytes": _decode_bytes,
    "tuple": _decode_sequence,
    "list": _decode_sequence,
    "exception": _decode_exception,
}


def decode_value(document: Any) -> Any:
    """Decode one tagged JSON value, rejecting unknown or ambiguous shapes."""

    if not isinstance(document, dict):
        message = f"tagged value must be an object, got {type(document).__name__}"
        raise MalformedRecord(message)
    tag = document.get(_TAG)
    if tag is None:
        message = "tagged value is missing $t"
        raise MalformedRecord(message)
    if not set(document) <= _CANONICAL_FIELDS:
        extra = sorted(set(document) - _CANONICAL_FIELDS)
        message = f"unexpected fields {extra} beside $t"
        raise MalformedRecord(message)
    decoder = _DECODERS.get(tag)
    if decoder is None:
        message = f"unknown value tag: {tag!r}"
        raise MalformedRecord(message)
    return decoder(document)


def dump_record(record: dict[str, Any]) -> str:
    """Serialise one record without newlines, and only as an object with a schema id."""

    if record.get("schema") != SCHEMA_ID:
        message = f"record schema must be {SCHEMA_ID!r}"
        raise MalformedRecord(message)
    text = json.dumps(record, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if "\n" in text:  # defensive: string payloads are codepoint arrays, never raw text
        message = "serialised record must not contain a newline"
        raise MalformedRecord(message)
    return text


def parse_record(line: str) -> dict[str, Any]:
    """Parse one record line and validate its declared schema."""

    try:
        record = json.loads(line)
    except json.JSONDecodeError as error:
        message = f"record is not valid JSON at offset {error.pos}"
        raise MalformedRecord(message) from error
    if not isinstance(record, dict):
        message = "record must be a JSON object"
        raise MalformedRecord(message)
    if record.get("schema") != SCHEMA_ID:
        message = f"record schema must be {SCHEMA_ID!r}, got {record.get('schema')!r}"
        raise MalformedRecord(message)
    return record


def write_records(path: Path, records: list[dict[str, Any]]) -> None:
    """Write LF-delimited records; never use ``splitlines``-compatible separators."""

    text = "\n".join(dump_record(record) for record in records) + "\n"
    path.write_bytes(text.encode("utf-8"))


def read_records(path: Path) -> list[dict[str, Any]]:
    """Read records by splitting on LF only, so U+0085/U+2028/U+2029 never split a record."""

    text = path.read_bytes().decode("utf-8")
    return [parse_record(line) for line in text.split("\n") if line]
