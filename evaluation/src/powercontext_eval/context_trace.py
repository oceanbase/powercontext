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

"""Merge audited Codex, PowerContext, and official events into one context timeline."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from powercontext_eval.artifacts import ArtifactStore
from powercontext_eval.benchmarks.swebench_pro.evaluator import OfficialEvaluation, TestGroupResult
from powercontext_eval.models import Arm


class ContextTraceError(ValueError):
    """A private trace artifact does not satisfy the evaluation contract."""


@dataclass(frozen=True)
class _PendingEvent:
    observed_at: datetime
    priority: int
    source_sequence: int
    actor: str
    event_type: str
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    source_artifact: str


def write_context_trace(
    store: ArtifactStore,
    *,
    arm: Arm,
    prompt: bytes,
    codex_sidecar: Path,
    injection_sidecar: Path | None,
    official: OfficialEvaluation,
    official_observed_at: datetime,
) -> Path:
    """Write the complete ordered timeline through the arm's secret-scanning store."""

    if not isinstance(prompt, bytes):
        raise TypeError("prompt must be exact bytes")
    try:
        prompt_text = prompt.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContextTraceError("benchmark prompt is not valid UTF-8") from error
    official_time = _require_utc(official_observed_at)
    pending = [
        *_codex_events(codex_sidecar),
        *_injection_events(injection_sidecar),
        _official_event(official, official_time),
    ]
    first_observed = min((event.observed_at for event in pending), default=official_time)
    pending.append(
        _PendingEvent(
            observed_at=first_observed,
            priority=0,
            source_sequence=0,
            actor="benchmark",
            event_type="benchmark_prompt",
            input={"prompt": prompt_text},
            output=None,
            source_artifact="instance.jsonl",
        )
    )
    ordered = sorted(
        pending,
        key=lambda event: (event.observed_at, event.priority, event.source_sequence),
    )
    origin = ordered[0].observed_at
    lines: list[str] = []
    for sequence, event in enumerate(ordered, start=1):
        elapsed_ms = round((event.observed_at - origin).total_seconds() * 1_000)
        value = {
            "sequence": sequence,
            "observed_at": _format_utc(event.observed_at),
            "elapsed_ms": elapsed_ms,
            "arm": arm.value,
            "actor": event.actor,
            "event_type": event.event_type,
            "input": event.input,
            "output": event.output,
            "source_artifact": event.source_artifact,
            "source_sequence": event.source_sequence,
        }
        lines.append(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
    return store.write_text("context/timeline.jsonl", "".join(lines))


def _codex_events(path: Path) -> list[_PendingEvent]:
    events: list[_PendingEvent] = []
    for line_number, value in _read_jsonl(path):
        if set(value) != {"sequence", "observed_at", "event"}:
            raise ContextTraceError(f"Codex trace line {line_number} has unexpected fields")
        sequence = value["sequence"]
        event = value["event"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ContextTraceError(f"Codex trace line {line_number} has an invalid sequence")
        if not isinstance(event, dict):
            raise ContextTraceError(f"Codex trace line {line_number} has a non-object event")
        event_type = event.get("type")
        if not isinstance(event_type, str) or not event_type:
            event_type = "codex_event"
        events.append(
            _PendingEvent(
                observed_at=_parse_utc(value["observed_at"], f"Codex trace line {line_number}"),
                priority=2,
                source_sequence=sequence,
                actor="codex",
                event_type=event_type,
                input=None,
                output={"event": event},
                source_artifact="context/codex-observed.jsonl",
            )
        )
    if not events:
        raise ContextTraceError("Codex trace is empty")
    if [event.source_sequence for event in events] != list(range(1, len(events) + 1)):
        raise ContextTraceError("Codex trace sequences are not contiguous")
    return events


def _injection_events(path: Path | None) -> list[_PendingEvent]:
    if path is None:
        return []
    events: list[_PendingEvent] = []
    for line_number, value in _read_jsonl(path):
        if value.get("event_type") != "powercontext_injection":
            raise ContextTraceError(f"PowerContext trace line {line_number} has an invalid event type")
        query = value.get("query")
        injected_text = value.get("injected_text")
        hits = value.get("hits")
        scope_id = value.get("scope_id")
        if (
            not isinstance(query, str)
            or not isinstance(injected_text, str)
            or not isinstance(hits, list)
            or not isinstance(scope_id, str)
        ):
            raise ContextTraceError(f"PowerContext trace line {line_number} is malformed")
        event_input: dict[str, Any] = {"query": query, "scope_id": scope_id}
        for key in ("session_id", "turn_id"):
            identifier = value.get(key)
            if identifier is not None:
                if not isinstance(identifier, str):
                    raise ContextTraceError(f"PowerContext trace line {line_number} has an invalid {key}")
                event_input[key] = identifier
        events.append(
            _PendingEvent(
                observed_at=_parse_utc(value.get("observed_at"), f"PowerContext trace line {line_number}"),
                priority=1,
                source_sequence=line_number,
                actor="powercontext",
                event_type="powercontext_injection",
                input=event_input,
                output={"hits": hits, "injected_text": injected_text},
                source_artifact="context/powercontext-injections.jsonl",
            )
        )
    return events


def _official_event(evaluation: OfficialEvaluation, observed_at: datetime) -> _PendingEvent:
    return _PendingEvent(
        observed_at=observed_at,
        priority=3,
        source_sequence=1,
        actor="official_evaluator",
        event_type="official_evaluation",
        input={"instance_id": evaluation.instance_id},
        output={
            "resolved": evaluation.resolved,
            "patch_applied": evaluation.patch_applied,
            "fail_to_pass": _group_json(evaluation.fail_to_pass),
            "pass_to_pass": _group_json(evaluation.pass_to_pass),
            "log_excerpt": evaluation.log_excerpt,
        },
        source_artifact="official",
    )


def _group_json(group: TestGroupResult) -> dict[str, Any]:
    return {"passed": group.passed, "total": group.total, "failed": list(group.failed)}


def _read_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ContextTraceError("Trace source is not a regular file")
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as stream:
            values: list[tuple[int, dict[str, Any]]] = []
            for line_number, line in enumerate(stream, start=1):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ContextTraceError(f"Trace line {line_number} is not valid JSON") from error
                if not isinstance(value, dict):
                    raise ContextTraceError(f"Trace line {line_number} is not an object")
                values.append((line_number, value))
            return values
    except OSError as error:
        raise ContextTraceError("Trace source cannot be read safely") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _parse_utc(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ContextTraceError(f"{field} timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContextTraceError(f"{field} timestamp is malformed") from error
    try:
        return _require_utc(parsed)
    except ContextTraceError as error:
        raise ContextTraceError(f"{field} timestamp is not UTC") from error


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ContextTraceError("timestamp must use UTC")
    return value


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
