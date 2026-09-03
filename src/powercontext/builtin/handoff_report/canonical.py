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

"""Canonical digests for Handoff Report snapshots."""

# ruff: noqa: TRY003

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast
from unicodedata import normalize

import rfc8785
from pydantic import BaseModel, JsonValue

from powercontext.builtin.handoff_report.report import HandoffReport


class ReportCanonicalizationError(ValueError):
    pass


def canonical_json_bytes(value: object, /) -> bytes:
    return rfc8785.dumps(cast(Any, _normalize_json(value)))


def selection_envelope(report: HandoffReport, /) -> dict[str, object]:
    """Describe the exact resolved observation independently of rendering time."""

    return {
        "schema": "powercontext.handoff-report-selection.v2",
        "selection": report.selection,
        "scope_ids": report.scope_ids,
        "handoffs": tuple(entry.handoff for entry in report.scopes),
    }


def selection_digest(report: HandoffReport, /) -> str:
    return _digest(selection_envelope(report))


def report_digest(report: HandoffReport, /) -> str:
    payload = report.model_dump(mode="python", by_alias=True, exclude_none=False)
    payload.pop("report_digest", None)
    return _digest(payload)


def finalize_digests(report: HandoffReport, /) -> HandoffReport:
    selected = report.model_copy(update={"selection_digest": selection_digest(report)})
    return selected.model_copy(update={"report_digest": report_digest(selected)})


def _digest(value: object) -> str:
    return f"sha256:{sha256(canonical_json_bytes(value)).hexdigest()}"


def _normalize_json(value: object) -> JsonValue:  # noqa: C901
    if isinstance(value, BaseModel):
        return _normalize_json(value.model_dump(mode="python", by_alias=True, exclude_none=False))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ReportCanonicalizationError("digest timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, str):
        return normalize("NFC", value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        raise ReportCanonicalizationError("digest inputs must not contain floating-point values")
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, item in cast(Mapping[object, object], value).items():
            if not isinstance(key, str):
                raise ReportCanonicalizationError("digest object keys must be strings")
            normalized_key = normalize("NFC", key)
            if normalized_key in normalized:
                raise ReportCanonicalizationError("digest object keys collide after normalization")
            normalized[normalized_key] = _normalize_json(item)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview)):
        return [_normalize_json(item) for item in value]
    raise ReportCanonicalizationError(f"unsupported digest value: {type(value).__name__}")


__all__ = [
    "ReportCanonicalizationError",
    "canonical_json_bytes",
    "finalize_digests",
    "report_digest",
    "selection_digest",
    "selection_envelope",
]
