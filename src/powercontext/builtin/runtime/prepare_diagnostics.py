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

"""Optional same-execution diagnostic capture, independent of Server composition."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter
from typing import Any
from uuid import uuid4

from powercontext._logging import log_safely
from powercontext.builtin.artifacts.memory.canonical import normalize_query
from powercontext.builtin.inference import (
    InferenceConfigurationError,
    InferencePrivacyError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.receipt_builder import ReceiptEvidence

logger = logging.getLogger(__name__)
DiagnosticSink = Callable[[dict[str, Any]], Awaitable[None]]


class PrepareDiagnosticCapture:
    """Record the observed terminal result; never replay or interpret exception bodies."""

    def __init__(
        self, request: PrepareContextRequest, scope_id: str, sink: DiagnosticSink | None, scope_ids: tuple[str, ...]
    ) -> None:
        self.request = request
        self.scope_id = scope_id
        self.sink = sink
        self.scope_ids = scope_ids
        self.started_at = datetime.now(UTC) if sink is not None else None
        self.started = perf_counter() if sink is not None else 0.0

    async def finish(
        self,
        result: PreparedContext | None,
        evidence: ReceiptEvidence | None,
        *,
        status: str,
        error: Exception | None = None,
    ) -> None:
        if self.sink is None:
            return
        try:
            receipt = None if evidence is None else evidence.prepared_receipt
            request = self.request
            # Domain exception messages and arbitrary `code` values can contain content.
            # Only closed codes selected by exception class cross this boundary.
            safe_error = _failure_code(error) if status == "failed" else None
            if status in {"ready", "empty"} and evidence is not None:
                safe_error = next(
                    (
                        row["error_code"] or "retrieval-failed"
                        for row in evidence.search_results
                        if row["outcome"] == "failed"
                    ),
                    None,
                )
            record = {
                "record_id": "pcd_" + uuid4().hex,
                "scope_id": self.scope_id,
                "started_at": self.started_at,
                "finished_at": datetime.now(UTC),
                "duration_ms": max(0.0, (perf_counter() - self.started) * 1000),
                "status": status,
                "principal_type": None,
                "principal_id": None,
                "request_id": None,
                "trace_id": None,
                "query_text": request.query,
                "query_digest": "sha256:" + sha256(normalize_query(request.query).encode("utf-8")).hexdigest(),
                "requested_max_bytes": request.max_bytes,
                "content_bytes": None if result is None else result.content_bytes,
                "selected_count": None if evidence is None else evidence.selected_count,
                "error_code": safe_error,
                "receipt_payload": None if receipt is None else receipt.model_dump(mode="json", by_alias=True),
                "prepared_content": None if result is None else result.content,
                "diagnostics_payload": {
                    "schema": "powercontext.context-prepare-diagnostics.v1",
                    "scope_ids": list(self.scope_ids),
                    "searches": [] if evidence is None else evidence.search_results,
                    "text_snapshots": [] if evidence is None else evidence.text_snapshots,
                    "text_capture_complete": evidence is not None and evidence.text_capture_complete,
                    "reranks": [] if evidence is None else evidence.rerank_results,
                    "stages": {} if evidence is None else evidence.stages,
                    "receipt_available": receipt is not None,
                    "receipt_error": None if evidence is None else evidence.receipt_error,
                    "evidence_complete": receipt is not None and evidence is not None and evidence.incomplete is None,
                    "failed_stage": evidence.active_stage if result is None and evidence is not None else None,
                    "assembly": None if request.assembly is None else request.assembly.model_dump(mode="json"),
                    "selected_by_family": {} if evidence is None else evidence.selected_by_family,
                    "final_candidates": {} if evidence is None else evidence.final_candidates,
                    "families_requested": (
                        [
                            "atomic-memory" if section.family == "memory" else section.family
                            for section in request.assembly.sections
                        ]
                        if request.assembly is not None
                        else ["atomic-memory", "experience", "topic-memory"]
                    ),
                    "include_code": request.include_code,
                    "include_reuse": request.include_reuse,
                },
            }
            # Bound a broken diagnostic backend, not capture lifetime or retention.
            async with asyncio.timeout(2):
                await self.sink(record)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
        except Exception:
            log_safely(
                logger,
                logging.WARNING,
                "Prepared context diagnostic could not be persisted",
                extra={
                    "event": "context.diagnostic.persist_failed",
                    "operation": "prepare_context",
                    "outcome": "failure",
                    "unit": "context",
                    "error_code": "diagnostic-write-failed",
                },
            )


def _failure_code(error: Exception | None) -> str:
    # Class identity selects a closed code. Messages and attached provider data
    # never become a diagnostic field.
    for kind, code in (
        (InferenceTimeoutError, "inference_timeout"),
        (InferenceUnavailableError, "inference_unavailable"),
        (InvalidInferenceOutputError, "invalid_output"),
        (InferenceConfigurationError, "inference_configuration"),
        (InferencePrivacyError, "inference_privacy"),
    ):
        if isinstance(error, kind):
            return code
    return "prepare-failed"
