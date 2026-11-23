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

from powercontext.builtin.processing_status import ArtifactProcessingStatus
from powercontext.builtin.statistics.models import ProcessingObservation
from powercontext.http import ArtifactProcessingStatusResponse

EXECUTOR_TTL_SECONDS = 30


def _observation(value: ProcessingObservation | None):
    if value is None:
        return None
    return value.model_dump(
        include={
            "binding",
            "family",
            "state",
            "reason",
            "observed_at",
            "scheduler_observed",
            "requested_generation",
            "handled_generation",
            "dirty_generation",
            "clean_generation",
            "running_seconds",
            "next_check_seconds",
            "retry_failures",
        }
    )


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
            "binding": (executor.binding if executor else None)
            if dimension == "executor"
            else (value.invocation.binding if value.invocation else None),
            "observed_at": observation,
            "read_at": value.observed_at,
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
        "invocation": _observation(value.invocation),
        "executor": _observation(executor),
        "executor_expires_at": expires,
        "block": None
        if block is None
        else {
            "reason": None if block.diagnostics is None else block.diagnostics.reason,
            "stage": None if block.diagnostics is None else block.diagnostics.stage,
            "source_after": block.source_after,
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
                "reason": None if run.diagnostics is None else run.diagnostics.reason,
                "stage": None if run.diagnostics is None else run.diagnostics.stage,
            }
            for run in value.dream_operations
        ],
        "dream_pending": value.dream_pending,
        "unavailable_reason": value.unavailable_reason,
        "availability": availability,
        "evidence": evidence,
    })
