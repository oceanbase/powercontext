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

"""Picklable, failure-isolated tracing around real child-process execution."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from threading import Thread
from typing import Any

from opentelemetry import propagate
from opentelemetry.trace import SpanKind

from powercontext._logging import error_category, log_safely
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingBlockedError,
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
    WorkerEntrypoint,
    processing_error_code,
)
from powercontext.builtin.runtime.worker_observability import WorkerObservability, worker_observability
from powercontext.server.settings import TracingConfig
from powercontext.server.tracing import configure_server_tracing


@dataclass(frozen=True)
class WorkerTracing:
    config: TracingConfig

    def wrap(self, entrypoint: WorkerEntrypoint) -> WorkerEntrypoint:
        if not self.config.enabled:
            return entrypoint
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        # Only execution context crosses this boundary, never arbitrary baggage.
        carrier = {key: value for key, value in carrier.items() if key in {"traceparent", "tracestate"}}
        return _TracedWorker(entrypoint, self.config, carrier)


@dataclass(frozen=True)
class _TracedWorker:
    entrypoint: WorkerEntrypoint
    config: TracingConfig
    carrier: dict[str, str]

    def __call__(self, assignment: ArtifactProcessingWorkAssignment) -> ArtifactProcessingWorkerCompletion | None:
        tracing = span = token = None
        try:
            tracing = configure_server_tracing(self.config, role="worker")
            span = tracing.start_span(
                "artifact_processing.worker.execute",
                kind=SpanKind.INTERNAL,
                context=propagate.extract(self.carrier),
                attributes={
                    "powercontext.operation.name": "process_artifact_scope",
                    "powercontext.operation.unit": "background",
                    "powercontext.artifact_processing.family": assignment.artifact_family,
                },
            )
            token = worker_observability.set(WorkerObservability(tracing, tracing.instrumentation))
        except Exception:
            # An unavailable exporter/adapter must not prevent the domain invocation.
            log_safely(
                logging.getLogger(__name__),
                logging.WARNING,
                "Worker tracing initialization failed",
                extra={"event": "observability.worker_tracing_failed"},
            )
        outcome = "success"
        error: BaseException | None = None
        try:
            completion = self.entrypoint(assignment)
            if completion is not None:
                outcome = completion.outcome.value
                if outcome == "succeeded":
                    outcome = "success"
        except BaseException as failure:
            error = failure
            outcome = (
                "cancelled"
                if isinstance(failure, asyncio.CancelledError)
                else "blocked"
                if isinstance(failure, ArtifactProcessingBlockedError)
                else "failure"
            )
            raise
        else:
            return completion
        finally:
            if token is not None:
                worker_observability.reset(token)
            if span is not None:
                _finish_worker_span(span, outcome, error)
            if tracing is not None:
                # BSP/export shutdown is best-effort and cannot hold a child indefinitely.
                with suppress(Exception):
                    flush = Thread(target=tracing.shutdown, daemon=True)
                    flush.start()
                    flush.join(timeout=1.0)


def _finish_worker_span(span: Any, outcome: str, error: BaseException | None) -> None:
    """Classify child failures with the supervisor's bounded vocabulary before ending the span."""

    if error is not None:
        category = (
            "blocked"
            if isinstance(error, ArtifactProcessingBlockedError)
            else error_category(processing_error_code(type(error)), outcome=outcome)
        )
        if category is not None:
            span.set_attributes({"powercontext.error.category": category})
    span.finish(outcome, error=error)
