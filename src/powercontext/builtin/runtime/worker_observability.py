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

"""Optional child-process observations supplied by an embedding application."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingModelUsageDelta
from powercontext.builtin.runtime.protocols import RuntimeTracing

if TYPE_CHECKING:
    from pydantic_ai.models.instrumented import InstrumentationSettings


@dataclass(frozen=True)
class WorkerObservability:
    tracing: RuntimeTracing
    instrumentation: InstrumentationSettings | None


worker_observability: ContextVar[WorkerObservability | None] = ContextVar("worker_observability", default=None)


class WorkerModelUsageCollector:
    """Accumulate fixed recorder loss deltas without retaining usage records."""

    def __init__(self) -> None:
        self._dropped_records = 0
        self._write_failures = 0
        self._reported = False

    def observer(self) -> Callable[[int, int, int, int], None]:
        """Return one observer for one recorder; its counters start at zero."""

        dropped_records = 0
        write_failures = 0

        def observe(_queued: int, _capacity: int, dropped: int, failed: int) -> None:
            nonlocal dropped_records, write_failures
            if (
                type(dropped) is not int
                or type(failed) is not int
                or dropped < dropped_records
                or failed < write_failures
            ):
                return
            self._reported = True
            self._dropped_records += dropped - dropped_records
            self._write_failures += failed - write_failures
            dropped_records = dropped
            write_failures = failed

        return observe

    def snapshot(self) -> ArtifactProcessingModelUsageDelta | None:
        if not self._reported:
            return None
        return ArtifactProcessingModelUsageDelta(self._dropped_records, self._write_failures)


worker_model_usage_collector: ContextVar[WorkerModelUsageCollector | None] = ContextVar(
    "worker_model_usage_collector", default=None
)
