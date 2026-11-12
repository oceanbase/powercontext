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

"""Frozen experiment inputs and content-free, validated public result records."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

ARMS = ("complete-projection-v1", "window-reduction-v1", "bounded-fragments-v1")
Arm = Literal[
    "complete-projection-v1",
    "window-reduction-v1",
    "bounded-fragments-v1",
    "bounded-fragments-v2",
    "bounded-fragments-v3",
]


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Piece(Record):
    text: str
    repeat: int = Field(default=1, ge=1, le=200_000)


class SourceFixture(Record):
    source_id: str = Field(pattern=r"^[a-z0-9-]+$")
    pieces: tuple[Piece, ...]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    metadata_pieces: dict[str, tuple[Piece, ...]] = Field(default_factory=dict)
    route: Literal["native", "observation"] = "native"

    def capture(self) -> tuple[str, dict[str, JsonValue]]:
        body = "".join(piece.text * piece.repeat for piece in self.pieces)
        metadata = {**self.metadata}
        metadata.update(
            {key: "".join(p.text * p.repeat for p in pieces) for key, pieces in self.metadata_pieces.items()}
        )
        return body, metadata


class Claim(Record):
    claim_id: str = Field(pattern=r"^[a-z0-9-]+$")
    source_id: str
    field: str = "/content/content"
    text: str = Field(min_length=1)
    exact_literals: tuple[str, ...] = ()
    accepted_candidates: tuple[str, ...] = ()
    current_user: bool = True


class Case(Record):
    case_id: str = Field(pattern=r"^[a-z0-9-]+$")
    categories: tuple[str, ...]
    sources: tuple[SourceFixture, ...]
    required: tuple[Claim, ...] = ()
    forbidden: tuple[Claim, ...] = ()
    structural_provenance: Literal["none"] = "none"
    completeness_ceiling: Literal["complete", "budget_dependent", "impossible", "unavailable", "cancelled"]
    reducibility: Literal["source_count", "fragments", "both", "neither"]
    fault: Literal["none", "missing_source", "invalid_projection"] = "none"
    cancel_phase: Literal["none", "before_read", "projection", "generation"] = "none"


class V2Policy(Record):
    version: Literal["bounded-fragments-v2"]
    boundaries: Literal["newline-record-utf8-v2"]
    selection: Literal["source-field-index-gap-round-robin-v2"]
    encoding: Literal["source-range-bundle-v2", "source-range-bundle-citation-v2"]
    max_calls_per_sample: int = Field(ge=1)
    total_input_budget_multiplier: int = Field(ge=1)
    reference_encoding: Literal["v1-claim-fragments"]
    require_no_preparation_regression: Literal[True]
    validation_fixture_sha256: str
    validation_catalog_sha256: str


class V3Policy(Record):
    version: Literal["bounded-fragments-v3"]
    boundaries: Literal["newline-record-utf8-v2"]
    selection: Literal["source-family-distinct-first-v3"]
    encoding: Literal["source-range-bundle-v3", "source-range-bundle-citation-v2"]
    family_turns: dict[str, int]
    duplicate_scope: Literal["source-materialization-projection-field-exact-json-value"]
    duplicate_disposition: Literal["defer-only-no-support-alias"]
    max_calls_per_sample: int = Field(ge=1)
    total_input_budget_multiplier: int = Field(ge=1)
    reference_encoding: Literal["v1-claim-fragments"]
    require_no_preparation_regression: Literal[True]
    required_datasets: tuple[str, ...]
    validation_fixture_sha256: str
    validation_catalog_sha256: str


class Experiment(Record):
    schema_version: Literal[
        "evidence-admission.experiment.v1",
        "evidence-admission.experiment.v2",
        "evidence-admission.experiment.v3",
        "evidence-admission.experiment.v4",
        "evidence-admission.experiment.v5",
    ]
    arms: tuple[Arm, ...]
    budgets: tuple[int, ...]
    fragment_bytes: int = Field(ge=4)
    ordering: Literal["source-field-original-order-v1"]
    selection: Literal["field-fair-head-tail-mid-largest-gap-v1"]
    estimator: Literal["character:weighted@1"]
    repetitions: int = Field(ge=2)
    live_repetitions: int = Field(ge=2)
    model_settings: dict[str, JsonValue]
    max_requests_per_call: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0)
    concurrency: Literal[1]
    normalization: Literal["none"]
    fixture_sha256: str
    catalog_sha256: str
    decision_sha256: str
    v2: V2Policy | None = None
    v3: V3Policy | None = None
    citation_protocol: Literal["evidence-id-only-v2", "evidence-id-only-v3"] | None = None
    selection_cost_protocol: Literal["frozen-v3-envelope-v1"] | None = None
    citation_encoding: Literal["source-range-bundle-citation-v2"] | None = None

    @property
    def candidate_arm(self) -> Arm:
        if self.v3:
            return "bounded-fragments-v3"
        return "bounded-fragments-v2" if self.v2 else "bounded-fragments-v1"

    def policy_for(self, arm: Arm) -> V2Policy | V3Policy | None:
        if arm == "bounded-fragments-v3":
            return self.v3
        return self.v2 if arm == "bounded-fragments-v2" else None


class Omission(Record):
    source_hash: str
    field_hash: str
    byte_start: int
    byte_end: int
    reason: Literal["input_budget", "source_window", "unavailable", "resource_budget"]


class Support(Record):
    evidence_id: str
    source_hash: str
    materialization_hash: str
    projection_hash: str
    field_hash: str
    byte_start: int
    byte_end: int
    char_start: int
    char_end: int


class InputMetrics(Record):
    projected_bytes: int = 0
    admitted_bytes: int = 0
    omitted_bytes: int = 0
    projected_tokens: int = 0
    prepared_tokens: int = 0
    prepared_tokens_total: int = 0
    selection_prepared_tokens: int | None = None
    selection_prepared_tokens_total: int | None = None
    prepared_requests: int = 0
    omitted_tokens: int = 0
    evidence_items: int = 0
    fragments: int = 0
    projected_fragments: int = 0
    omitted_fragments: int = 0
    categories: dict[str, int] = Field(default_factory=dict)
    serialized_input_bytes: int = 0
    value_bytes_by_family: dict[str, int] = Field(default_factory=dict)
    input_estimator_units_by_family: dict[str, int] = Field(default_factory=dict)


class PreparationScore(Record):
    required_count: int
    retained_claim_hashes: tuple[str, ...]
    missing_claim_hashes: tuple[str, ...]
    exact_literals_retained: int
    exact_literals_total: int
    retained_literal_hashes: tuple[str, ...] = ()
    required_content_fits: bool


class GenerationScore(Record):
    candidates: int
    retained_claim_hashes: tuple[str, ...]
    retained_literal_hashes: tuple[str, ...] = ()
    unsupported: int
    false_attribution: int
    invalid_support: int
    unresolved: int
    exact_duplicates: int
    near_duplicates: int
    candidate_hashes: tuple[str, ...]
    support: tuple[tuple[Support, ...], ...]


class Sample(Record):
    schema_version: Literal["evidence-admission.sample.v1", "evidence-admission.sample.v2"] = (
        "evidence-admission.sample.v1"
    )
    sample_id: str
    case_hash: str
    arm: Arm
    budget: int
    repetition: int
    completeness: Literal["complete", "bounded", "unavailable", "cancelled"]
    status: Literal["prepared", "budget_rejected", "generated", "failed", "cancelled", "unavailable"]
    input_hashes: tuple[str, ...] = ()
    output_hashes: tuple[str, ...] = ()
    materializations: tuple[str, ...] = ()
    projections: tuple[str, ...] = ()
    source_hashes: tuple[str, ...] = ()
    definitions: tuple[str, ...] = ()
    projection_versions: tuple[str, ...] = ()
    metrics: InputMetrics = Field(default_factory=InputMetrics)
    omissions: tuple[Omission, ...] = ()
    supports: tuple[Support, ...] = ()
    preparation: PreparationScore | None = None
    generation: GenerationScore | None = None
    window_attempts: tuple[tuple[int, int, int, bool], ...] = ()
    materialization_seconds: float = 0
    admission_seconds: float = 0
    generation_seconds: float = 0
    cancellation_seconds: float | None = None
    requests: int = 0
    generation_calls: int = 0
    provider_input_tokens: int | None = None
    provider_output_tokens: int | None = None
    usage_complete: bool = True
    failure_phase: Literal["none", "generation", "output_validation"] = "none"
    failure_kind: Literal["none", "inference_failed", "invalid_output", "invalid_evidence_reference"] = "none"
    memory_mutations: int = 0
    published_cursor: int = 0
    reason: Literal[
        "none", "input_budget", "materialization_unavailable", "cancelled", "generation_failed", "resource_budget"
    ] = "none"
