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

"""Operational diagnostics for model usage reporting and recording."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import suppress

from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from powercontext._logging import log_safely
from powercontext.builtin.processing_diagnostics import (
    ProcessingDiagnostics,
    ProcessingFailureReason,
    ProcessingStage,
    allow_processing_failure_log,
    safe_log_fields,
)
from powercontext.builtin.statistics.models import ModelUsageOperation, ModelUsagePurpose

_PURPOSE_FAMILY = {
    ModelUsagePurpose.MEMORY_EXTRACTION: "memory",
    ModelUsagePurpose.MEMORY_INDEXING: "memory",
    ModelUsagePurpose.MEMORY_RECALL: "memory",
    ModelUsagePurpose.EXPERIENCE_GENERATION: "experience",
    ModelUsagePurpose.SKILL_GENERATION: "skill",
    ModelUsagePurpose.HANDOFF_GENERATION: "handoff",
    ModelUsagePurpose.PROFILE_GENERATION: "profile",
    ModelUsagePurpose.PROMPT_GENERATION: "prompt",
    ModelUsagePurpose.TOPIC_MEMORY_GENERATION: "topic-memory",
    ModelUsagePurpose.TOPIC_MEMORY_RECALL: "topic-memory",
    ModelUsagePurpose.TOPIC_MEMORY_INDEXING: "topic-memory",
}


def model_usage_family(purpose: ModelUsagePurpose) -> str | None:
    return _PURPOSE_FAMILY.get(purpose) if isinstance(purpose, ModelUsagePurpose) else None


def log_usage_accounting_failure(
    logger: logging.Logger,
    message: str,
    location: str,
    *,
    family: str | None = None,
    capability: ModelUsageOperation | None = None,
    record_count: int | None = None,
    error: Exception | None = None,
    worker_fields: Mapping[str, str | int] | None = None,
) -> None:
    """Emit only bounded accounting metadata for an observed reporting failure."""
    with suppress(Exception):
        driver_error = error.orig if isinstance(error, DBAPIError) else None
        reason = None
        if location.endswith("_timeout") or isinstance(error, (TimeoutError, PoolTimeoutError)):
            reason = ProcessingFailureReason.TIMEOUT
        elif location == "write" and isinstance(error, DBAPIError):
            reason = ProcessingFailureReason.STORAGE_FAILURE
        code = f"usage_accounting_{location}"
        if reason is ProcessingFailureReason.TIMEOUT and not location.endswith("_timeout"):
            code += "_timeout"
        if not allow_processing_failure_log(family or "", "model_usage", code, reason):
            return
        driver_type = None if driver_error is None else type(driver_error).__name__
        diagnostics = ProcessingDiagnostics(
            stage=ProcessingStage.USAGE_ACCOUNTING,
            reason=reason,
            failure_location=location,
            input_item_count=record_count,
            cause_exception_type=driver_type if driver_type and driver_type.isidentifier() else None,
        )
        exception_type = None if error is None else type(error).__name__
        log_safely(
            logger,
            logging.WARNING,
            message,
            extra={
                "event": "model_usage.accounting.failed",
                "operation": "model_usage",
                "unit": "statistics",
                "outcome": "failure",
                "error_code": code,
                "family": family,
                "model_capability": capability.value if isinstance(capability, ModelUsageOperation) else None,
                "exception_type": exception_type[:128] if exception_type and exception_type.isidentifier() else None,
                **(worker_fields or {}),
                **safe_log_fields(diagnostics),
            },
        )
