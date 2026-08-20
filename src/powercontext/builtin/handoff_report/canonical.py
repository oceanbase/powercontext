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

"""Canonical JSON and digest helpers for Handoff Reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast
from unicodedata import normalize

import rfc8785
from pydantic import BaseModel, JsonValue

if TYPE_CHECKING:
    from powercontext.builtin.handoff_report.report import HandoffReport


class ReportCanonicalizationError(ValueError):
    """Raised when a report digest input cannot be represented canonically."""

    def __init__(self, code: str, detail: object | None = None) -> None:
        messages = {
            "unknown-event": f"activity selection references unknown event {detail!r}",
            "timestamp": "digest timestamps must be timezone-aware",
            "float": "digest inputs must not contain floating-point values",
            "key-type": "digest object keys must be strings",
            "key-collision": "digest object keys collide after NFC normalization",
            "unsupported-type": f"digest input contains unsupported value type {detail}",
        }
        super().__init__(messages[code])


def canonical_json_bytes(value: object, /) -> bytes:
    """Return RFC 8785 JSON bytes after applying the report NFC rules."""

    return rfc8785.dumps(cast(Any, _normalize_json(value)))


def selection_envelope(report: HandoffReport, /) -> dict[str, object]:
    """Build the locale-independent exact selection envelope for one report."""

    events = {event.event_id: event for item in report.workstreams for event in item.activities}
    events.update({event.event_id: event for event in report.unassigned_activity})
    activity_selection = []
    for event_id in report.activity_selection:
        event = events.get(event_id)
        if event is None:
            raise ReportCanonicalizationError("unknown-event", event_id)
        activity_selection.append({
            "event_id": event.event_id,
            "source": event.source,
            "source_event_id": event.source_event_id,
            "occurred_at": event.occurred_at,
            "observed_at": event.observed_at,
            "time_basis": event.time_basis,
        })
    return {
        "schema": "powercontext.handoff-report-selection.v1",
        "project_id": report.project.project_id,
        "project_revision": report.project_revision,
        "normalized_filters": report.normalized_filters,
        "normalized_period": report.normalized_period,
        "selection_consistency": report.selection_consistency,
        "activity_cursor": report.activity_cursor,
        "baseline_selection": report.baseline_selection,
        "end_selection": report.end_selection,
        "activity_selection": activity_selection,
    }


def selection_digest(report: HandoffReport, /) -> str:
    """Hash the exact selection independently of locale and renderer."""

    return _digest(selection_envelope(report))


def report_digest(report: HandoffReport, /) -> str:
    """Hash the complete report payload, excluding its own digest field."""

    payload = report.model_dump(mode="python", by_alias=True, exclude_none=False)
    payload.pop("report_digest", None)
    return _digest(payload)


def finalize_digests(report: HandoffReport, /) -> HandoffReport:
    """Return a report with selection and output-specific digests populated."""

    selected = report.model_copy(update={"selection_digest": selection_digest(report)})
    return selected.model_copy(update={"report_digest": report_digest(selected)})


def _digest(value: object) -> str:
    return f"sha256:{sha256(canonical_json_bytes(value)).hexdigest()}"


def _normalize_json(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        return _normalize_json(value.model_dump(mode="python", by_alias=True, exclude_none=False))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ReportCanonicalizationError("timestamp")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, str):
        return normalize("NFC", value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        raise ReportCanonicalizationError("float")
    if isinstance(value, Mapping):
        return _normalize_mapping(cast(Mapping[object, object], value))
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview)):
        return _normalize_sequence(value)
    raise ReportCanonicalizationError("unsupported-type", type(value).__name__)


def _normalize_mapping(value: Mapping[object, object]) -> dict[str, JsonValue]:
    normalized: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ReportCanonicalizationError("key-type")
        normalized_key = normalize("NFC", key)
        if normalized_key in normalized:
            raise ReportCanonicalizationError("key-collision")
        normalized[normalized_key] = _normalize_json(item)
    return normalized


def _normalize_sequence(value: Sequence[object]) -> list[JsonValue]:
    return [_normalize_json(item) for item in value]


__all__ = [
    "ReportCanonicalizationError",
    "canonical_json_bytes",
    "finalize_digests",
    "report_digest",
    "selection_digest",
    "selection_envelope",
]
