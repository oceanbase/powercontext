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
from powercontext.builtin.artifacts.handoff.errors import (
    HandoffGenerationUnavailableError,
    InvalidHandoffGenerationError,
)
from powercontext.builtin.artifacts.memory.extraction import MemoryExtractionCandidate, _selected_evidence
from powercontext.builtin.artifacts.topic_memory.errors import (
    TopicMemoryCapabilityError,
    TopicMemoryProjectionError,
    TopicMemoryStorageInvariantError,
)
from powercontext.builtin.artifacts.topic_memory.generation import TopicMemoryGenerationError
from powercontext.builtin.dream.models import DreamError
from powercontext.builtin.evidence.models import EvidenceResolutionError
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
    classify_block_code,
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
        input_renderer=None,
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


@pytest.mark.parametrize(
    ("code", "stage", "reason"),
    [
        ("embedding_unavailable", ProcessingStage.EMBEDDING, ProcessingFailureReason.CONFIGURATION),
        ("input_budget_exceeded", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.CONTEXT_OVERFLOW),
        (
            "reduction_input_budget_exceeded",
            ProcessingStage.BUSINESS_VALIDATION,
            ProcessingFailureReason.CONTEXT_OVERFLOW,
        ),
        ("window_provider_budget_exceeded", ProcessingStage.GENERATION, ProcessingFailureReason.USAGE_LIMIT),
        ("window_attempt_limit", ProcessingStage.GENERATION, ProcessingFailureReason.USAGE_LIMIT),
        ("reduction_output_budget_exceeded", ProcessingStage.GENERATION, ProcessingFailureReason.USAGE_LIMIT),
        ("invalid_plan", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("invalid_evidence", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("invalid_proposal", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("invalid_reconciliation", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("invalid_reduction", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("invalid_target", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("invalid_temporary", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("invalid_work_item", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("unsupported_evidence", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("reduction_did_not_progress", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("duplicate_target", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("identity_merge", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("proposal_limit", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("incomplete_target", ProcessingStage.PERSISTENCE, ProcessingFailureReason.STORAGE_FAILURE),
        (
            "source_complexity_limit",
            ProcessingStage.BUSINESS_VALIDATION,
            None,
        ),
        ("related_history_limit", ProcessingStage.BUSINESS_VALIDATION, None),
        ("source_window_changed", None, None),
        ("invalid_window", None, None),
        ("duplicate_operation", None, None),
        ("window_attempt_superseded", None, None),
        ("target_did_not_converge", None, None),
        ("unaccepted_invocation", None, None),
        ("invalid_invocation", None, None),
        ("invalid_work_reservation", None, None),
        ("invalid_stage_budget", None, ProcessingFailureReason.CONFIGURATION),
        ("invalid_max_tokens", None, ProcessingFailureReason.CONFIGURATION),
        ("output_budget_exceeded", None, ProcessingFailureReason.CONFIGURATION),
    ],
)
def test_topic_memory_generation_errors_classify_by_code(code, stage, reason) -> None:
    diagnostics = classify_processing_error(TopicMemoryGenerationError(code))

    assert diagnostics is not None
    assert diagnostics.stage is stage
    assert diagnostics.reason is reason
    assert diagnostics.failure_location == code


def test_unknown_topic_memory_code_keeps_unknown_classification() -> None:
    assert classify_processing_error(TopicMemoryGenerationError("unknown_future_code")) is None


@pytest.mark.parametrize(
    ("code", "stage", "reason"),
    [
        ("content", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("chunks", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("lexical", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("vector-incomplete", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("fts", ProcessingStage.PERSISTENCE, ProcessingFailureReason.CONFIGURATION),
        ("vector-unconfigured", ProcessingStage.PERSISTENCE, ProcessingFailureReason.CONFIGURATION),
        ("embedding-profile", ProcessingStage.PERSISTENCE, ProcessingFailureReason.CONFIGURATION),
        ("embedding-values", ProcessingStage.EMBEDDING, ProcessingFailureReason.INVALID_OUTPUT),
    ],
)
def test_topic_memory_projection_errors_classify_by_code(code, stage, reason) -> None:
    diagnostics = classify_processing_error(TopicMemoryProjectionError(code))

    assert diagnostics is not None
    assert diagnostics.stage is stage
    assert diagnostics.reason is reason
    assert diagnostics.failure_location == code


def test_unknown_topic_memory_projection_code_keeps_reason_unknown() -> None:
    diagnostics = classify_processing_error(TopicMemoryProjectionError("future-code"))

    assert diagnostics is not None
    assert diagnostics.stage is ProcessingStage.PERSISTENCE
    assert diagnostics.reason is None
    assert diagnostics.failure_location == "future-code"


def test_topic_memory_storage_invariant_errors_classify_as_persistence() -> None:
    diagnostics = classify_processing_error(TopicMemoryStorageInvariantError("missing-publication", ("scope",)))

    assert diagnostics is not None
    assert diagnostics.stage is ProcessingStage.PERSISTENCE
    assert diagnostics.reason is ProcessingFailureReason.STORAGE_FAILURE
    assert diagnostics.failure_location == "missing-publication"


def test_topic_memory_capability_errors_classify_as_configuration() -> None:
    diagnostics = classify_processing_error(TopicMemoryCapabilityError("vector"))

    assert diagnostics is not None
    assert diagnostics.stage is ProcessingStage.PERSISTENCE
    assert diagnostics.reason is ProcessingFailureReason.CONFIGURATION
    assert diagnostics.failure_location == "vector"


def test_durable_blocks_classify_only_for_topic_memory() -> None:
    diagnostics = classify_block_code("topic_memory", "window_attempt_limit")

    assert diagnostics is not None
    assert diagnostics.stage is ProcessingStage.GENERATION
    assert diagnostics.reason is ProcessingFailureReason.USAGE_LIMIT
    assert diagnostics.failure_location == "window_attempt_limit"
    assert classify_block_code("memory", "window_attempt_limit") is None
    assert classify_block_code("topic_memory", "") is None


# -- Dream, Handoff, and Evidence error classification --


@pytest.mark.parametrize(
    ("code", "stage", "reason"),
    [
        ("capability_unavailable", ProcessingStage.GENERATION, ProcessingFailureReason.CONFIGURATION),
        ("unsupported_target", ProcessingStage.GENERATION, ProcessingFailureReason.CONFIGURATION),
        ("budget_exceeded", ProcessingStage.GENERATION, ProcessingFailureReason.USAGE_LIMIT),
        ("capacity_exceeded", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.USAGE_LIMIT),
        ("invalid_generation_output", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("invalid_target", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("invalid_dream_operation", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("invalid_artifact_family", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("invalid_idempotency_key", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("evidence_unavailable", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("reference_not_found", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("invalid_memory_citation", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("policy_changed", ProcessingStage.BUSINESS_VALIDATION, None),
        ("artifact_conflict", None, None),
        ("attempt_conflict", None, None),
        ("tag_precondition_failed", None, None),
        ("scope_not_found", ProcessingStage.PERSISTENCE, ProcessingFailureReason.STORAGE_FAILURE),
        ("evidence_limit_exceeded", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.CONTEXT_OVERFLOW),
        ("memory_entry_inactive", ProcessingStage.PERSISTENCE, ProcessingFailureReason.STORAGE_FAILURE),
        ("source_not_eligible", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("access_revoked", None, None),
        ("access_unavailable", None, None),
        ("dream_request_unavailable", None, None),
        ("generation_failed", None, None),
        ("idempotency_conflict", None, None),
        ("needs_evidence", None, None),
    ],
)
def test_dream_errors_classify_by_code(code, stage, reason) -> None:
    diagnostics = classify_processing_error(DreamError(code))

    assert diagnostics is not None
    assert diagnostics.stage is stage
    assert diagnostics.reason is reason
    assert diagnostics.failure_location == code


def test_unknown_dream_code_keeps_unknown_classification() -> None:
    assert classify_processing_error(DreamError("future_dream_code")) is None


@pytest.mark.parametrize(
    ("code", "stage", "reason"),
    [
        ("output", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("evidence", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("budget", ProcessingStage.GENERATION, ProcessingFailureReason.USAGE_LIMIT),
        ("objective", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("rollover_next_action", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
        ("rollover_quality", ProcessingStage.BUSINESS_VALIDATION, ProcessingFailureReason.INVALID_OUTPUT),
    ],
)
def test_handoff_generation_errors_classify_by_code(code, stage, reason) -> None:
    diagnostics = classify_processing_error(InvalidHandoffGenerationError(code))

    assert diagnostics is not None
    assert diagnostics.stage is stage
    assert diagnostics.reason is reason
    assert diagnostics.failure_location == code


def test_unknown_handoff_code_keeps_unknown_classification() -> None:
    assert classify_processing_error(InvalidHandoffGenerationError("future_handoff_code")) is None


def test_handoff_generation_unavailable_classifies_as_configuration() -> None:
    diagnostics = classify_processing_error(HandoffGenerationUnavailableError())

    assert diagnostics is not None
    assert diagnostics.stage is ProcessingStage.GENERATION
    assert diagnostics.reason is ProcessingFailureReason.CONFIGURATION


def test_evidence_resolution_error_classifies_by_code() -> None:
    diagnostics = classify_processing_error(EvidenceResolutionError("reference_not_found"))

    assert diagnostics is not None
    assert diagnostics.stage is ProcessingStage.BUSINESS_VALIDATION
    assert diagnostics.reason is ProcessingFailureReason.INVALID_OUTPUT
    assert diagnostics.failure_location == "reference_not_found"


@pytest.mark.parametrize(
    "code",
    [
        "invalid_generation_output",
        "needs_evidence",
        "evidence_limit_exceeded",
        "reference_not_found",
        "evidence_unavailable",
        "memory_entry_inactive",
        "source_not_eligible",
    ],
)
def test_evidence_resolution_error_codes_classify(code) -> None:
    diagnostics = classify_processing_error(EvidenceResolutionError(code))

    assert diagnostics is not None
    assert diagnostics.failure_location == code


def test_dream_blocks_classify_for_dream_stage() -> None:
    diagnostics = classify_block_code("dream", "budget_exceeded")

    assert diagnostics is not None
    assert diagnostics.stage is ProcessingStage.GENERATION
    assert diagnostics.reason is ProcessingFailureReason.USAGE_LIMIT
    assert diagnostics.failure_location == "budget_exceeded"

    assert classify_block_code("dream", "") is None
    assert classify_block_code("experience", "budget_exceeded") is None
    assert classify_block_code("dream", "unknown_future_code") is None


def test_unknown_evidence_resolution_error_code_is_unclassified() -> None:
    assert classify_processing_error(EvidenceResolutionError("completely_unknown")) is None
