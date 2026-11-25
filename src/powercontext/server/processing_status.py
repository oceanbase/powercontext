# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Publish only safe, passive processing evidence at the HTTP boundary."""

from datetime import UTC, datetime, timedelta

from starlette.datastructures import MutableHeaders
from starlette.routing import compile_path, get_route_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from powercontext.builtin.processing_diagnostics import ProcessingDiagnostics, processing_error_code
from powercontext.builtin.processing_status import ArtifactProcessingStatus
from powercontext.builtin.statistics.models import ProcessingObservation
from powercontext.http import (
    ArtifactProcessingExecutor,
    ArtifactProcessingObservation,
    ArtifactProcessingStatusResponse,
)
from powercontext.http._generated.operations import GET_ARTIFACT_PROCESSING_STATUS
from powercontext.server.routing import encoded_routing_scope

EXECUTOR_TTL_SECONDS = 30
_STATUS_PATH = compile_path(GET_ARTIFACT_PROCESSING_STATUS.path)[0]


class ProcessingStatusCacheControl:
    """Set the cache policy at the final send, including early rejections and error responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = get_route_path(scope) if scope["type"] == "http" and scope["method"] == "GET" else ""
        if path != "/dashboard/artifact-processing-status" and not (
            path.endswith("/processing-status") and _STATUS_PATH.fullmatch(get_route_path(encoded_routing_scope(scope)))
        ):
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_no_store)


def _public_executor(observation: ProcessingObservation | None) -> ArtifactProcessingExecutor | None:
    if observation is None:
        return None
    code = observation.last_error_code
    when = observation.last_error_at
    known_failure = (
        observation.scheduler_observed
        and when is not None
        and when <= observation.observed_at
        and code is not None
        and (processing_error_code("", code) == code or code == "worker_cancelled")
    )
    stage = observation.last_error_stage
    return ArtifactProcessingExecutor.model_validate({
        "binding": observation.binding,
        "family": observation.family,
        "state": observation.state,
        "reason": observation.reason,
        "observed_at": observation.observed_at,
        "scheduler_observed": observation.scheduler_observed,
        "running_seconds": observation.running_seconds,
        "next_check_seconds": observation.next_check_seconds,
        "retry_failures": observation.retry_failures,
        "last_error_code": code if known_failure else None,
        "last_error_stage": stage
        if known_failure and stage in {"authorization", "inference", "completion", "worker", "work_admission"}
        else None,
        "last_error_at": when if known_failure else None,
    })


def _block_reason(diagnostics: ProcessingDiagnostics | None) -> str | None:
    if diagnostics is None:
        return None
    # These structural limits have a known cause but no general failure category.
    if diagnostics.failure_location in {"source_complexity_limit", "related_history_limit"}:
        return diagnostics.failure_location
    return diagnostics.reason


def processing_status_response(value: ArtifactProcessingStatus) -> ArtifactProcessingStatusResponse:
    now = datetime.now(UTC)
    supported = value.unavailable_reason != "family_not_supported"
    progress = value.progress
    block = value.block
    executor = value.executor
    expires = None if executor is None else executor.observed_at + timedelta(seconds=EXECUTOR_TTL_SECONDS)
    fresh = (
        executor is not None
        and expires is not None
        and executor.scheduler_observed
        and executor.observed_at <= now < expires
    )
    availability = {
        "progress": "available" if progress else "not_applicable" if supported else "unavailable",
        "invocation": "available" if value.invocation else "unavailable",
        "executor": "available" if executor else "unavailable",
        "block": "available" if value.family == "topic-memory" else "not_applicable" if supported else "unavailable",
        "dream": "available" if supported and value.dream_pending is not None else "unavailable",
    }
    evidence = {}
    for dimension, origin, observation in (
        ("progress", "source_cursor_and_journal_head", None),
        ("invocation", "processing_intent", None),
        (
            "block",
            "retained_block" if block and block.persisted else "budget_projection",
            None if block is None else block.blocked_at,
        ),
        ("dream", "dream_records", None),
        ("executor", "local_supervisor", None if executor is None else executor.observed_at),
    ):
        if availability[dimension] != "available" or (dimension == "block" and block is None):
            continue
        evidence[dimension] = {
            "origin": origin,
            "durability": "process_local" if dimension == "executor" else "durable",
            "scope_id": value.scope_id,
            "binding": executor.binding if dimension == "executor" and executor is not None else value.binding,
            "observed_at": observation,
            "read_at": now if dimension == "executor" else value.observed_at,
            "freshness": ("fresh" if fresh else "stale") if dimension == "executor" else "not_applicable",
        }
    return ArtifactProcessingStatusResponse.model_validate({
        "scope_id": value.scope_id,
        "family": value.family,
        "state": value.state,
        "observed_at": value.observed_at,
        "queried_at": now,
        "source_consumption_applicable": value.source_consumption_applicable,
        "progress": None
        if progress is None
        else {
            "source_cursor": progress.source_cursor,
            "source_head": progress.source_head,
            "target_source_through": progress.target_source_through,
            "pending_inputs": progress.pending_inputs,
        },
        "invocation": ArtifactProcessingObservation.model_validate(value.invocation, from_attributes=True)
        if value.invocation
        else None,
        "executor": _public_executor(executor),
        "executor_expires_at": expires,
        "block": None
        if block is None
        else {
            "reason": _block_reason(block.diagnostics),
            "stage": None if block.diagnostics is None else block.diagnostics.stage,
            "source_after": block.source_after,
            "source_through": block.source_through,
            "budget_source_through": block.budget_source_through,
            "target_source_through": block.target_source_through,
            "attempts": block.attempts,
            "requests": block.requests,
            "tokens": block.tokens,
            "block_id": block.block_id,
            "blocked_at": block.blocked_at,
            "persisted": block.persisted,
        },
        "dream_operations": [
            {
                "run_id": run.run_id,
                "operation": run.operation,
                "status": run.status,
                "outcome": run.outcome,
                "accepted_at": run.accepted_at,
                "completed_at": run.completed_at,
                "candidate": None if run.candidate is None else run.candidate.model_dump(),
                # classify_dream_code only retains locations from its public rejection vocabulary.
                "reason": (run.diagnostics.reason or run.diagnostics.failure_location) if run.diagnostics else None,
                "stage": None if run.diagnostics is None else run.diagnostics.stage,
            }
            for run in value.dream_operations
        ],
        "dream_pending": value.dream_pending,
        "unavailable_reason": value.unavailable_reason,
        "availability": availability,
        "evidence": evidence,
    })
