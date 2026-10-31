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

"""Shared processing failure diagnostics stay bounded, safe, and classifiable."""

from __future__ import annotations

import asyncio
import json
import logging
import pickle
from types import SimpleNamespace
from typing import Any, cast

import pytest

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryEvidence,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.memory.extraction import MemoryExtractionCandidate, _selected_evidence
from powercontext.builtin.inference.errors import (
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.inference.models import GenerationResult
from powercontext.builtin.inference.pydantic_ai import PydanticAIConfigurationError
from powercontext.builtin.inference.tokens import character_token_estimator
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexFilter,
    AtomicMemoryProjectionPublisher,
    AtomicMemorySearchRequest,
    RelationalAtomicMemoryIndex,
)
from powercontext.builtin.processing_diagnostics import (
    ProcessingDiagnostics,
    ProcessingFailureReason,
    ProcessingStage,
    attach_diagnostics,
    classify_processing_error,
    safe_classify_processing_error,
)
from powercontext.builtin.runtime.atomic_memory_processing import AtomicMemorySourceWindowProcessor
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkerFailure
from powercontext.server.logging import JsonFormatter
from powercontext.sources import SourceRef


def test_diagnostics_bound_and_sanitize_every_field() -> None:
    diagnostics = ProcessingDiagnostics(
        stage=cast(Any, "generation"),
        reason=cast(Any, "context_overflow"),
        input_item_count=3,
        estimated_input_tokens=120,
        token_estimator_id="character\x00weighted",  # noqa: S106 - estimator identity, not a secret
        effective_budget=100,
        provider_request_id=" req\nid ",
        finish_reason="length\x00",
        failure_location="location" * 40,
    )

    assert diagnostics.stage is ProcessingStage.GENERATION
    assert diagnostics.reason is ProcessingFailureReason.CONTEXT_OVERFLOW
    assert diagnostics.token_estimator_id == "characterweighted"  # noqa: S105 - estimator identity
    assert diagnostics.provider_request_id == "reqid"
    assert diagnostics.finish_reason == "length"
    assert diagnostics.failure_location is not None
    assert len(diagnostics.failure_location) == 128


def test_diagnostics_reject_invalid_enums_and_counts() -> None:
    diagnostics = ProcessingDiagnostics(
        stage=cast(Any, "unknown-stage"),
        reason=cast(Any, "unknown-reason"),
        input_item_count=-1,
        estimated_input_tokens=True,
        effective_budget=2**40,
    )

    assert diagnostics.stage is None
    assert diagnostics.reason is None
    assert diagnostics.input_item_count is None
    assert diagnostics.estimated_input_tokens is None
    assert diagnostics.effective_budget is None


def test_log_fields_only_include_set_values() -> None:
    fields = ProcessingDiagnostics(
        stage=ProcessingStage.OUTPUT_PARSING,
        reason=ProcessingFailureReason.SCHEMA_INVALID,
        provider_request_id="req-1",
    ).log_fields()

    assert fields == {
        "processing_stage": "output_parsing",
        "failure_reason": "schema_invalid",
        "provider_request_id": "req-1",
    }


def test_attached_diagnostics_take_precedence_over_derived_classification() -> None:
    error = InvalidInferenceOutputError("atomic-memory-extract", "provider did not return a valid result")
    attached = ProcessingDiagnostics(
        stage=ProcessingStage.BUSINESS_VALIDATION,
        reason=ProcessingFailureReason.INVALID_OUTPUT,
    )
    attach_diagnostics(error, attached)

    assert classify_processing_error(error) is attached


@pytest.mark.parametrize(
    ("error", "stage", "reason"),
    [
        (InferenceTimeoutError("generate", 5.0), ProcessingStage.GENERATION, ProcessingFailureReason.TIMEOUT),
        (InferenceTimeoutError("embed", 5.0), ProcessingStage.EMBEDDING, ProcessingFailureReason.TIMEOUT),
        (
            InferenceUnavailableError("embed"),
            ProcessingStage.EMBEDDING,
            ProcessingFailureReason.PROVIDER_UNAVAILABLE,
        ),
        (
            InvalidInferenceOutputError(
                "atomic-memory-input-budget", "A complete comparison input exceeds its token budget"
            ),
            ProcessingStage.BUSINESS_VALIDATION,
            ProcessingFailureReason.CONTEXT_OVERFLOW,
        ),
        (
            InvalidInferenceOutputError("generate", "provider did not return a valid result"),
            ProcessingStage.OUTPUT_PARSING,
            ProcessingFailureReason.SCHEMA_INVALID,
        ),
        (
            InvalidInferenceOutputError("embed", "provider returned the wrong vector count"),
            ProcessingStage.EMBEDDING,
            ProcessingFailureReason.INVALID_OUTPUT,
        ),
        (
            PydanticAIConfigurationError("provider-rejected"),
            None,
            ProcessingFailureReason.CONFIGURATION,
        ),
    ],
)
def test_classifies_known_failures(error, stage, reason) -> None:
    diagnostics = classify_processing_error(error)

    assert diagnostics is not None
    assert diagnostics.stage is stage
    assert diagnostics.reason is reason


def test_rate_limited_unavailable_error_is_distinguished() -> None:
    error = InferenceUnavailableError("generate")
    setattr(error, "status_code", 429)  # noqa: B010 - provider status attached by the adapter

    diagnostics = classify_processing_error(error)

    assert diagnostics is not None
    assert diagnostics.reason is ProcessingFailureReason.RATE_LIMIT


def test_worker_failure_carries_diagnostics_across_a_process_boundary() -> None:
    diagnostics = ProcessingDiagnostics(
        stage=ProcessingStage.GENERATION,
        reason=ProcessingFailureReason.OUTPUT_TRUNCATED,
        provider_request_id="req-1",
        finish_reason="length",
    )
    failure = ArtifactProcessingWorkerFailure(
        stage="worker",
        error_code="invalid_model_output",
        exception_type="InvalidInferenceOutputError",
        traceback="",
        diagnostics=diagnostics,
    )

    restored = pickle.loads(pickle.dumps(failure))  # noqa: S301 - round-trip of a trusted local object

    assert restored.diagnostics == diagnostics


def test_budget_failure_reports_safe_accounting_fields() -> None:
    processor = object.__new__(AtomicMemorySourceWindowProcessor)
    processor.config = SimpleNamespace(input_tokens_limit=10)
    pipeline = SimpleNamespace(
        estimator=character_token_estimator(),
        extraction_instructions="extract",
    )
    value = SimpleNamespace(
        evidence=(object(), object(), object()),
        model_dump_json=lambda: "a long body that exceeds the configured budget",
    )

    with pytest.raises(InvalidInferenceOutputError) as raised:
        processor._require_budget(pipeline, value, "atomic_memory.extract")

    diagnostics = classify_processing_error(raised.value)
    assert diagnostics is not None
    assert diagnostics.stage is ProcessingStage.BUSINESS_VALIDATION
    assert diagnostics.reason is ProcessingFailureReason.CONTEXT_OVERFLOW
    assert diagnostics.input_item_count == 3
    assert diagnostics.estimated_input_tokens is not None
    assert diagnostics.estimated_input_tokens > 10
    assert diagnostics.token_estimator_id == "character:weighted"  # noqa: S105 - estimator identity
    assert diagnostics.effective_budget == 10
    assert diagnostics.failure_location == "atomic_memory.extract"


def test_json_formatter_serializes_the_new_processing_fields() -> None:
    record = logging.makeLogRecord({
        "msg": "Artifact processing failed",
        "event": "artifact_processing.failed",
        "outcome": "failure",
        "processing_stage": "generation",
        "failure_reason": "context_overflow",
        "input_item_count": 3,
        "estimated_input_tokens": 120,
        "token_estimator_id": "character:weighted",
        "effective_budget": 100,
        "provider_request_id": "req-1",
        "finish_reason": "length",
        "failure_location": "atomic_memory.extract",
        "unexpected_extra": "must not be serialized",
    })

    payload = json.loads(JsonFormatter().format(record))

    assert payload["processing_stage"] == "generation"
    assert payload["failure_reason"] == "context_overflow"
    assert payload["input_item_count"] == 3
    assert payload["provider_request_id"] == "req-1"
    assert "unexpected_extra" not in payload


@pytest.mark.parametrize(
    ("code", "stage", "reason"),
    [
        ("embedding-result", ProcessingStage.EMBEDDING, ProcessingFailureReason.INVALID_OUTPUT),
        ("embedding-input", ProcessingStage.EMBEDDING, ProcessingFailureReason.INVALID_OUTPUT),
        ("embedding-values", ProcessingStage.EMBEDDING, ProcessingFailureReason.INVALID_OUTPUT),
        ("embedding-profile", ProcessingStage.EMBEDDING, ProcessingFailureReason.CONFIGURATION),
        ("content", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("prepared-content", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("limit", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("mode", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("admission", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("threshold", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("batch-size", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("vector-search", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("state", ProcessingStage.BUSINESS_VALIDATION, None),
        ("family", None, ProcessingFailureReason.CONFIGURATION),
        ("dialect", None, ProcessingFailureReason.CONFIGURATION),
        ("sqlite", None, ProcessingFailureReason.CONFIGURATION),
        ("sqlite-fts", None, ProcessingFailureReason.CONFIGURATION),
        ("sqlite-vector", None, ProcessingFailureReason.CONFIGURATION),
        ("oceanbase", None, ProcessingFailureReason.CONFIGURATION),
        ("oceanbase-fts", None, ProcessingFailureReason.CONFIGURATION),
        ("oceanbase-vector", None, ProcessingFailureReason.CONFIGURATION),
        ("vector-schema", None, ProcessingFailureReason.CONFIGURATION),
        ("current-schema", None, ProcessingFailureReason.CONFIGURATION),
        ("authority-schema", None, ProcessingFailureReason.CONFIGURATION),
        ("migration-pending", None, ProcessingFailureReason.CONFIGURATION),
        ("maintenance-required", None, ProcessingFailureReason.CONFIGURATION),
        ("publication", None, ProcessingFailureReason.CONFIGURATION),
        ("authority-orphan", ProcessingStage.PERSISTENCE, ProcessingFailureReason.STORAGE_FAILURE),
        ("authority-head", ProcessingStage.PERSISTENCE, ProcessingFailureReason.STORAGE_FAILURE),
        ("authority-content", ProcessingStage.PERSISTENCE, ProcessingFailureReason.STORAGE_FAILURE),
        ("incomplete-vector", ProcessingStage.PERSISTENCE, ProcessingFailureReason.STORAGE_FAILURE),
    ],
)
def test_atomic_memory_index_errors_classify_by_code(code, stage, reason) -> None:
    diagnostics = classify_processing_error(AtomicMemoryIndexError(code, "bounded detail"))

    assert diagnostics is not None
    assert diagnostics.stage is stage
    assert diagnostics.reason is reason


@pytest.mark.parametrize("code", ["stale-projection", "stale-recall", "rebuild-conflict", "future-code"])
def test_unrecognized_atomic_memory_index_codes_stay_unclassified(code) -> None:
    assert classify_processing_error(AtomicMemoryIndexError(code, "bounded detail")) is None


def test_index_request_validation_is_not_classified_as_a_storage_failure() -> None:
    request = AtomicMemorySearchRequest(query="bounded", filters=AtomicMemoryIndexFilter(), limit=0)

    with pytest.raises(AtomicMemoryIndexError, match="limit") as raised:
        RelationalAtomicMemoryIndex.validate_request(request)

    diagnostics = classify_processing_error(raised.value)
    assert diagnostics is not None
    assert diagnostics.stage is ProcessingStage.BUSINESS_VALIDATION
    assert diagnostics.reason is ProcessingFailureReason.INVALID_OUTPUT


def test_index_without_replace_many_is_a_capability_not_a_storage_failure() -> None:
    with pytest.raises(AtomicMemoryIndexError, match="complete projection sets") as raised:
        AtomicMemoryProjectionPublisher(cast(Any, object()))

    diagnostics = classify_processing_error(raised.value)
    assert diagnostics is not None
    assert diagnostics.reason is ProcessingFailureReason.CONFIGURATION
    assert diagnostics.stage is None


def test_safe_classifier_never_escapes() -> None:
    class ExplodingError(RuntimeError):
        @property
        def operation(self) -> str:
            raise RuntimeError("exploded")

    assert safe_classify_processing_error(ExplodingError()) is None


def test_memory_extraction_rules_attach_business_validation_diagnostics() -> None:
    candidate = MemoryExtractionCandidate(intent="add", kind="fact", text="A fact", evidence_ids=())

    with pytest.raises(InvalidInferenceOutputError) as raised:
        _selected_evidence(candidate, {})

    diagnostics = classify_processing_error(raised.value)
    assert diagnostics is not None
    assert diagnostics.stage is ProcessingStage.BUSINESS_VALIDATION
    assert diagnostics.reason is ProcessingFailureReason.INVALID_OUTPUT
    assert diagnostics.failure_location == "memory-extract"


@pytest.mark.parametrize(
    ("text", "description"),
    [("", "A summary"), ("A fact", "   "), ("A fact", "First\nsecond")],
)
def test_atomic_memory_content_validation_attaches_business_validation_diagnostics(text: str, description: str) -> None:
    class InvalidCandidateGenerator:
        async def generate(self, value):
            del value
            return GenerationResult(
                output=AtomicMemoryExtractionOutput(
                    candidates=(
                        AtomicMemoryCandidate(
                            kind="fact", text=text, description=description, evidence_ids=("evidence-1",)
                        ),
                    )
                )
            )

    pipeline = AtomicMemoryGenerationPipeline(
        extractor=InvalidCandidateGenerator(),
        reconciler=cast(Any, None),
        estimator=character_token_estimator(),
    )
    request = AtomicMemoryExtractionInput(
        evidence=(
            AtomicMemoryEvidence(
                evidence_id="evidence-1",
                source_ref=SourceRef(source_type="content", source_id="source-1"),
                journal_position=1,
                content={"text": "bounded"},
                source_metadata={},
            ),
        )
    )

    with pytest.raises(InvalidInferenceOutputError) as raised:
        asyncio.run(pipeline.extract(request))

    diagnostics = classify_processing_error(raised.value)
    assert diagnostics is not None
    assert diagnostics.stage is ProcessingStage.BUSINESS_VALIDATION
    assert diagnostics.reason is ProcessingFailureReason.INVALID_OUTPUT
    assert diagnostics.failure_location == "atomic-memory-extract"


def test_console_filter_renders_the_new_processing_fields() -> None:
    from powercontext.server.logging import _HumanContextFilter

    record = logging.makeLogRecord({
        "msg": "Artifact processing failed",
        "event": "artifact_processing.failed",
        "outcome": "failure",
        "processing_stage": "generation",
        "failure_reason": "rate_limit",
        "provider_request_id": "req-1",
        "cause_exception_type": "ModelHTTPError",
    })

    _HumanContextFilter().filter(record)

    processing_context = getattr(record, "processing_context", "")
    assert 'processing_stage="generation"' in processing_context
    assert 'failure_reason="rate_limit"' in processing_context
    assert 'cause_exception_type="ModelHTTPError"' in processing_context
