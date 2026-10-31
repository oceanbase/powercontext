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

"""Bounded, content-free failure diagnostics shared by every processing Family."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

_ErrorT = TypeVar("_ErrorT", bound=BaseException)


class ProcessingStage(StrEnum):
    """The processing stage that owned a failure."""

    GENERATION = "generation"
    EMBEDDING = "embedding"
    OUTPUT_PARSING = "output_parsing"
    BUSINESS_VALIDATION = "business_validation"
    PERSISTENCE = "persistence"
    # Reserved for usage reporting or persistence failures.
    USAGE_ACCOUNTING = "usage_accounting"


class ProcessingFailureReason(StrEnum):
    """A bounded reason, only when the observed evidence supports it."""

    CONTEXT_OVERFLOW = "context_overflow"
    OUTPUT_TRUNCATED = "output_truncated"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    SCHEMA_INVALID = "schema_invalid"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CONFIGURATION = "configuration"
    INVALID_OUTPUT = "invalid_output"
    STORAGE_FAILURE = "storage_failure"
    USAGE_LIMIT = "usage_limit"


_MAX_COUNT = 2**31 - 1
_TEXT_LIMITS = {
    "token_estimator_id": 128,
    "provider_request_id": 128,
    "finish_reason": 64,
    "failure_location": 128,
    "cause_exception_type": 128,
}


def _enum(enum_type: type[StrEnum], value: object) -> Any:
    if value is None:
        return None
    try:
        return enum_type(value)
    except ValueError:
        return None


def _count(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > _MAX_COUNT:
        return None
    return value


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = "".join(character for character in value if character.isprintable()).strip()
    if not cleaned:
        return None
    return cleaned[:limit]


@dataclass(frozen=True, slots=True)
class ProcessingDiagnostics:
    """Safe causal context for one processing failure.

    Every field is bounded: counts are non-negative, provider identifiers and
    reasons are printable and length-limited, and no field carries prompts,
    Source or Artifact bodies, model responses, credentials, or SQL parameters.
    """

    stage: ProcessingStage | None = None
    reason: ProcessingFailureReason | None = None
    input_item_count: int | None = None
    estimated_input_tokens: int | None = None
    token_estimator_id: str | None = None
    effective_budget: int | None = None
    provider_request_id: str | None = None
    finish_reason: str | None = None
    failure_location: str | None = None
    cause_exception_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", _enum(ProcessingStage, self.stage))
        object.__setattr__(self, "reason", _enum(ProcessingFailureReason, self.reason))
        object.__setattr__(self, "input_item_count", _count(self.input_item_count))
        object.__setattr__(self, "estimated_input_tokens", _count(self.estimated_input_tokens))
        object.__setattr__(self, "effective_budget", _count(self.effective_budget))
        for name, limit in _TEXT_LIMITS.items():
            object.__setattr__(self, name, _text(getattr(self, name), limit))

    def log_fields(self) -> dict[str, str | int]:
        """Return only the allowlisted operational fields that are set."""

        fields: dict[str, str | int] = {}
        if self.stage is not None:
            fields["processing_stage"] = self.stage.value
        if self.reason is not None:
            fields["failure_reason"] = self.reason.value
        for name in (
            "input_item_count",
            "estimated_input_tokens",
            "token_estimator_id",
            "effective_budget",
            "provider_request_id",
            "finish_reason",
            "failure_location",
            "cause_exception_type",
        ):
            value = getattr(self, name)
            if value is not None:
                fields[name] = value
        return fields


def attach_diagnostics(error: _ErrorT, diagnostics: ProcessingDiagnostics | None) -> _ErrorT:
    """Attach safe diagnostics to an exception without changing its type or message."""

    if diagnostics is not None:
        setattr(error, "processing_diagnostics", diagnostics)  # noqa: B010 - dynamic exception attribute
    return error


def diagnostics_of(error: BaseException) -> ProcessingDiagnostics | None:
    value = getattr(error, "processing_diagnostics", None)
    return value if isinstance(value, ProcessingDiagnostics) else None


def safe_classify_processing_error(error: BaseException) -> ProcessingDiagnostics | None:
    """Never let classification itself change a processing outcome."""

    try:
        return classify_processing_error(error)
    except Exception:
        return None


def safe_log_fields(diagnostics: ProcessingDiagnostics | None) -> dict[str, str | int]:
    """Never let a diagnostics field failure suppress the failure record."""

    if diagnostics is None:
        return {}
    try:
        return diagnostics.log_fields()
    except Exception:
        return {}


def stage_for_operation(operation: object) -> ProcessingStage | None:
    if not isinstance(operation, str) or not operation:
        return None
    value = operation.lower()
    if "embed" in value or "index" in value:
        return ProcessingStage.EMBEDDING
    if "extract" in value or "generate" in value or "reconcile" in value or "incubate" in value:
        return ProcessingStage.GENERATION
    return None


def classify_processing_error(error: BaseException) -> ProcessingDiagnostics | None:  # noqa: C901 - bounded vocabulary
    """Derive a bounded classification when no raise site attached one."""

    attached = diagnostics_of(error)
    if attached is not None:
        return attached

    names = {base.__name__ for base in type(error).__mro__}
    operation = getattr(error, "operation", None)
    stage = stage_for_operation(operation)
    if "InferenceTimeoutError" in names or isinstance(error, TimeoutError):
        return ProcessingDiagnostics(stage=stage, reason=ProcessingFailureReason.TIMEOUT)
    if "InferenceUnavailableError" in names:
        status_code = getattr(error, "status_code", None)
        reason = (
            ProcessingFailureReason.RATE_LIMIT if status_code == 429 else ProcessingFailureReason.PROVIDER_UNAVAILABLE
        )
        return ProcessingDiagnostics(stage=stage, reason=reason)
    if "InvalidInferenceOutputError" in names:
        detail = str(getattr(error, "detail", "") or "").lower()
        operation_value = operation.lower() if isinstance(operation, str) else ""
        if "token budget" in detail or "budget" in operation_value:
            return ProcessingDiagnostics(
                stage=ProcessingStage.BUSINESS_VALIDATION,
                reason=ProcessingFailureReason.CONTEXT_OVERFLOW,
            )
        if any(term in detail for term in ("schema", "typed", "wrong output", "invalid output tree", "valid result")):
            return ProcessingDiagnostics(
                stage=ProcessingStage.OUTPUT_PARSING,
                reason=ProcessingFailureReason.SCHEMA_INVALID,
            )
        if "vector" in detail:
            return ProcessingDiagnostics(
                stage=ProcessingStage.EMBEDDING,
                reason=ProcessingFailureReason.INVALID_OUTPUT,
            )
        return ProcessingDiagnostics(
            stage=stage or ProcessingStage.OUTPUT_PARSING,
            reason=ProcessingFailureReason.INVALID_OUTPUT,
        )
    if "InferenceConfigurationError" in names:
        return ProcessingDiagnostics(stage=stage, reason=ProcessingFailureReason.CONFIGURATION)
    if "AtomicMemoryIndexError" in names:
        code = str(getattr(error, "code", "") or "").lower()
        if code == "embedding-profile":
            return ProcessingDiagnostics(
                stage=ProcessingStage.EMBEDDING,
                reason=ProcessingFailureReason.CONFIGURATION,
            )
        if code in {"embedding-input", "embedding-result", "embedding-values"}:
            return ProcessingDiagnostics(
                stage=ProcessingStage.EMBEDDING,
                reason=ProcessingFailureReason.INVALID_OUTPUT,
            )
        if code in {
            "content",
            "prepared-content",
            "limit",
            "mode",
            "admission",
            "threshold",
            "batch-size",
            "vector-search",
        }:
            return ProcessingDiagnostics(
                stage=ProcessingStage.BUSINESS_VALIDATION,
                reason=ProcessingFailureReason.INVALID_OUTPUT,
            )
        if code == "state":
            return ProcessingDiagnostics(stage=ProcessingStage.BUSINESS_VALIDATION)
        if code in {
            "family",
            "dialect",
            "oceanbase",
            "sqlite",
            "sqlite-fts",
            "sqlite-vector",
            "oceanbase-fts",
            "oceanbase-vector",
            "vector-schema",
            "current-schema",
            "authority-schema",
            "migration-pending",
            "maintenance-required",
        }:
            return ProcessingDiagnostics(reason=ProcessingFailureReason.CONFIGURATION)
        if code in {"authority-orphan", "authority-head", "authority-content", "incomplete-vector"}:
            return ProcessingDiagnostics(
                stage=ProcessingStage.PERSISTENCE,
                reason=ProcessingFailureReason.STORAGE_FAILURE,
            )
        if code == "publication":
            return ProcessingDiagnostics(reason=ProcessingFailureReason.CONFIGURATION)
        return None
    return None


__all__ = [
    "ProcessingDiagnostics",
    "ProcessingFailureReason",
    "ProcessingStage",
    "attach_diagnostics",
    "classify_processing_error",
    "diagnostics_of",
    "safe_classify_processing_error",
    "safe_log_fields",
    "stage_for_operation",
]
