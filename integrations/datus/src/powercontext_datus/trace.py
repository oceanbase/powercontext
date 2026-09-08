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

"""Append-only operation accounting, independent of Datus success-only counters."""

# ruff: noqa: TRY003 - bounded validation errors are part of this bridge's diagnostics.

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

TERMINAL = frozenset({"success", "failure", "cancelled"})


@dataclass(frozen=True)
class OperationEvent:
    """One lifecycle event. Repeated attempts must have different operation IDs.

    A wrapper and its driver span share operation_id. A wrapper spanning several
    operations is not itself an operation: its children each receive their own ID.
    """

    run_id: str
    task_id: str
    attempt_id: str
    operation_id: str
    name: str
    status: Literal["started", "success", "failure", "cancelled"]
    parent_id: str | None = None
    input_digest: str | None = None
    output_digest: str | None = None


class EventJournal:
    """Exclusive, append-only sidecar; store it outside Agent-readable directories."""

    def __init__(self, path: Path) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._stream = os.fdopen(descriptor, "w", encoding="utf-8")
        self._sequence = 0

    def append(self, event: OperationEvent) -> None:
        self._sequence += 1
        self._stream.write(
            json.dumps(
                {"sequence": self._sequence, "monotonic_ns": time.monotonic_ns(), **asdict(event)},
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )
        self._stream.flush()

    def close(self) -> None:
        self._stream.close()


@dataclass(frozen=True)
class StepCount:
    steps: int
    complete: bool
    failures: int


def count_steps(events: list[OperationEvent], *, run_id: str, task_id: str, attempt_id: str) -> StepCount:
    """Count unique attempts, retaining failures; reject inconsistent/missing lifecycle events.

    Empty events alone never certify zero work or a completed answer. Callers must
    also prove capture coverage from question injection through answer submission.
    """
    operations: dict[str, list[OperationEvent]] = {}
    for event in events:
        if (event.run_id, event.task_id, event.attempt_id) != (run_id, task_id, attempt_id):
            raise ValueError("mixed run/task/attempt trace")
        if not event.operation_id or not event.name or event.status not in {"started", *TERMINAL}:
            raise ValueError("invalid operation event")
        operations.setdefault(event.operation_id, []).append(event)
    complete = bool(operations)
    failures = 0
    for lifecycle in operations.values():
        if len({(event.name, event.parent_id, event.input_digest) for event in lifecycle}) != 1:
            raise ValueError("operation identity changed")
        statuses = {event.status for event in lifecycle if event.status in TERMINAL}
        outputs = {event.output_digest for event in lifecycle if event.status in TERMINAL}
        if len(statuses) > 1 or len(outputs) > 1:
            raise ValueError("conflicting terminal event")
        if lifecycle[0].status != "started" or not statuses or lifecycle[-1].status not in TERMINAL:
            complete = False
        failures += int(bool(statuses & {"failure", "cancelled"}))
    return StepCount(steps=len(operations), complete=complete, failures=failures)
