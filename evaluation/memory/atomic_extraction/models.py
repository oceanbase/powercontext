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

"""Versioned fixture, private observation, and content-free score models."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate
from powercontext.builtin.artifacts.memory import MemoryExtractionProfile
from powercontext.builtin.inference import InferenceUsage

Disposition = Literal["atomic_memory", "experience_or_skill", "reject"]
CandidateKind = Literal["fact", "preference", "decision", "constraint", "working_note"]

MANIFEST_SCHEMA = "powercontext.atomic-memory-extraction-cases.v1"
OBSERVATION_SCHEMA = "powercontext.atomic-memory-extraction-observation.v1"
REPORT_SCHEMA = "powercontext.atomic-memory-extraction-report.v1"
RUN_MANIFEST_SCHEMA = "powercontext.atomic-memory-extraction-run.v1"

_PUBLIC_INTEGER_SETTINGS = frozenset({"max_tokens", "seed", "top_k"})
_PUBLIC_NUMBER_SETTINGS = frozenset({"frequency_penalty", "presence_penalty", "temperature", "top_p"})
_PUBLIC_BOOLEAN_SETTINGS = frozenset({"parallel_tool_calls"})
_PUBLIC_ENUM_SETTINGS = {
    "service_tier": frozenset({"auto", "default", "flex", "priority"}),
    "thinking": frozenset({"minimal", "low", "medium", "high", "xhigh"}),
}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def safe_model_settings(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Return the content-free model settings safe for public artifacts."""

    return {
        key: item
        if _is_public_setting(key, item) or _is_redacted_value(item)
        else {"redacted_sha256": _json_value_sha256(item)}
        for key, item in value.items()
    }


def _is_public_setting(key: str, value: JsonValue) -> bool:
    if key in _PUBLIC_INTEGER_SETTINGS:
        return isinstance(value, int) and not isinstance(value, bool)
    if key in _PUBLIC_NUMBER_SETTINGS:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if key in _PUBLIC_BOOLEAN_SETTINGS:
        return isinstance(value, bool)
    if key == "thinking" and isinstance(value, bool):
        return True
    allowed = _PUBLIC_ENUM_SETTINGS.get(key)
    return isinstance(value, str) and allowed is not None and value in allowed


def _is_redacted_value(value: JsonValue) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"redacted_sha256"}
        and isinstance(value["redacted_sha256"], str)
        and _SHA256_PATTERN.fullmatch(value["redacted_sha256"]) is not None
    )


def _json_value_sha256(value: JsonValue) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


class EvaluationEvidence(BaseModel):
    """One public synthetic Source-evidence item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    content: JsonValue
    source_metadata: JsonValue = Field(default_factory=dict)


class ExpectedDisposition(BaseModel):
    """Observable requirements used by the deterministic scorer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: Disposition
    required_subjects: tuple[str, ...]
    required_literals: tuple[str, ...]
    forbidden_output_fragments: tuple[str, ...]
    required_kinds: tuple[CandidateKind, ...] = ()
    required_evidence_ids: tuple[str, ...] = ()
    max_candidates: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_atomic_expectations(self) -> Self:
        values = (
            self.required_subjects,
            self.required_literals,
            self.forbidden_output_fragments,
            self.required_evidence_ids,
        )
        if any(any(not item.strip() or item != item.strip() for item in items) for items in values):
            raise ValueError("expectation strings must be nonempty and trimmed")  # noqa: TRY003
        if self.disposition == "atomic_memory" and (not self.required_subjects or not self.required_evidence_ids):
            raise ValueError("Atomic Memory cases require subjects and evidence IDs")  # noqa: TRY003
        if self.disposition != "atomic_memory" and (
            self.required_subjects
            or self.required_literals
            or self.required_kinds
            or self.required_evidence_ids
            or self.max_candidates is not None
        ):
            raise ValueError("non-Memory dispositions cannot require candidate content")  # noqa: TRY003
        return self


class EvaluationCase(BaseModel):
    """One bilingual or boundary-focused extraction case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    profile: MemoryExtractionProfile
    evidence: tuple[EvaluationEvidence, ...] = Field(min_length=1)
    expected: ExpectedDisposition

    @model_validator(mode="after")
    def validate_case_references(self) -> Self:
        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("case evidence IDs must be unique")  # noqa: TRY003
        if not set(self.expected.required_evidence_ids) <= set(evidence_ids):
            raise ValueError("expected evidence IDs must be supplied by the case")  # noqa: TRY003
        return self


class EvaluationManifest(BaseModel):
    """Committed synthetic cases with stable scoring metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["powercontext.atomic-memory-extraction-cases.v1"] = MANIFEST_SCHEMA
    dataset_version: str = Field(min_length=1)
    cases: tuple[EvaluationCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_ids(self) -> Self:
        case_ids = tuple(item.case_id for item in self.cases)
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("case IDs must be unique")  # noqa: TRY003
        dispositions = {item.expected.disposition for item in self.cases}
        profiles = {item.profile for item in self.cases}
        if dispositions != {"atomic_memory", "experience_or_skill", "reject"}:
            raise ValueError("manifest must cover every disposition")  # noqa: TRY003
        if profiles != set(MemoryExtractionProfile):
            raise ValueError("manifest must cover every extraction profile")  # noqa: TRY003
        return self


class CandidateObservation(BaseModel):
    """Private generated content retained outside Git."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: CandidateKind
    text: str
    evidence_ids: tuple[str, ...]

    @classmethod
    def from_candidate(cls, candidate: AtomicMemoryCandidate) -> CandidateObservation:
        # The observation schema retains the fields consumed by the scorer.
        return cls(kind=candidate.kind, text=candidate.text, evidence_ids=candidate.evidence_ids)


class CaseObservation(BaseModel):
    """One private live-run result; candidate text must not enter public reports."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["powercontext.atomic-memory-extraction-observation.v1"] = OBSERVATION_SCHEMA
    case_id: str
    status: Literal["succeeded", "failed"]
    candidates: tuple[CandidateObservation, ...] = ()
    usage: InferenceUsage = Field(default_factory=lambda: InferenceUsage(requests=0))
    error_class: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if (self.status == "succeeded") != (self.error_class is None):
            raise ValueError("successful observations cannot have errors and failed observations must have one")  # noqa: TRY003
        if self.status == "failed" and self.candidates:
            raise ValueError("failed observations cannot contain partial candidates")  # noqa: TRY003
        return self


class CaseScore(BaseModel):
    """Content-free score for one case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(exclude=True)
    disposition: Disposition
    passed: bool
    factual_retention_applicable: bool
    factual_retention_passed: bool
    instruction_leakage_passed: bool
    failure_classes: tuple[str, ...]


class PromptRunIdentity(BaseModel):
    """Exact built-in Prompt identity used by one live run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: Literal["atomic_memory.extract"]
    definition_version: str = Field(min_length=1)
    builtin_version: str = Field(min_length=1)
    instructions_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class DatasetRunIdentity(BaseModel):
    """Exact committed fixture identity used by one live run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["powercontext.atomic-memory-extraction-cases.v1"]
    dataset_version: str = Field(min_length=1)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=1)


class ProviderRunIdentity(BaseModel):
    """Credential-free generation configuration used by one live run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url_configured: bool
    base_url_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    header_names: tuple[str, ...]
    model_settings: dict[str, JsonValue]
    model_settings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timeout_seconds: float = Field(gt=0)
    max_requests: int = Field(ge=1)

    @field_validator("model_settings", mode="before")
    @classmethod
    def sanitize_model_settings(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return safe_model_settings(cast(Mapping[str, JsonValue], value))

    @model_validator(mode="after")
    def validate_endpoint_identity(self) -> Self:
        if self.base_url_configured != (self.base_url_sha256 is not None):
            raise ValueError("configured base URL requires its SHA-256 identity")  # noqa: TRY003
        if any(not name.strip() or name != name.strip() for name in self.header_names) or len(
            set(self.header_names)
        ) != len(self.header_names):
            raise ValueError("provider header names must be unique, nonempty, and trimmed")  # noqa: TRY003
        return self


class LiveRunManifest(BaseModel):
    """Strict safe manifest that may be pinned in Git."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["powercontext.atomic-memory-extraction-run.v1"] = RUN_MANIFEST_SCHEMA
    run_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    started_at: datetime
    dataset: DatasetRunIdentity
    prompts: dict[MemoryExtractionProfile, PromptRunIdentity]
    provider: ProviderRunIdentity

    @model_validator(mode="after")
    def validate_profiles_and_dataset(self) -> Self:
        if set(self.prompts) != set(MemoryExtractionProfile):
            raise ValueError("run manifest must identify every extraction profile")  # noqa: TRY003
        if self.dataset.case_count < 1:
            raise ValueError("run manifest dataset must not be empty")  # noqa: TRY003
        return self


__all__ = [
    "MANIFEST_SCHEMA",
    "OBSERVATION_SCHEMA",
    "REPORT_SCHEMA",
    "RUN_MANIFEST_SCHEMA",
    "CandidateObservation",
    "CaseObservation",
    "CaseScore",
    "DatasetRunIdentity",
    "Disposition",
    "EvaluationCase",
    "EvaluationEvidence",
    "EvaluationManifest",
    "ExpectedDisposition",
    "LiveRunManifest",
    "PromptRunIdentity",
    "ProviderRunIdentity",
    "safe_model_settings",
]
