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

"""Content-free, bounded evidence for one PreparedContext selection."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from powercontext.artifacts import ArtifactAddress

RECEIPT_POLICY = "powercontext.prepared-context-receipt.v1"
ReceiptFamily = Literal["atomic-memory", "topic-memory", "experience", "profile", "code"]
OmissionReason = Literal[
    "duplicate",
    "blank",
    "family_limit",
    "entry_limit",
    "below_min_bytes",
    "no_fitting_truncation",
    "rerank_not_selected",
    "not_retrieved",
]


class _ReceiptModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RecallRound(_ReceiptModel):
    round: Annotated[int, Field(ge=0, le=2)]
    outcome: Literal["completed", "expansion_failed"]
    retained: bool


class SearchGroup(_ReceiptModel):
    round: Annotated[int, Field(ge=0, le=2)]
    family: ReceiptFamily
    mode: Literal["fts", "vector", "hybrid", "snapshot", "code", "none"]
    outcome: Literal["completed", "not_run", "failed"]
    fallback_reason: Literal["none", "inference_unavailable", "inference_timeout", "reused_fts_fallback"]
    count: Annotated[int, Field(ge=1)]


class PromptIdentity(_ReceiptModel):
    scope_id: str
    key: str
    definition_version: str
    builtin_version: str
    selection: Literal["built_in", "artifact"]
    selected_version: str
    compiled_digest: str
    artifact_address: ArtifactAddress | None


class RerankConfig(_ReceiptModel):
    config_id: str
    policy_id: str
    model: str | None
    effective_settings: dict[str, str | int | float | bool]
    timeout_seconds: Annotated[float, Field(gt=0)]
    max_requests: Annotated[int, Field(ge=1)]
    config_digest: str
    prompt: PromptIdentity | None
    non_deterministic: bool

    @model_validator(mode="after")
    def check_id(self) -> RerankConfig:
        if self.config_id != self.config_digest or self.non_deterministic != (self.model is not None):
            raise ValueError("invalid rerank configuration identity")  # noqa: TRY003
        return self


class RerankGroup(_ReceiptModel):
    round: Annotated[int, Field(ge=0, le=2)]
    config_id: str
    outcome: Literal["selected", "fallback", "failed"]
    fallback_reason: Literal["none", "empty_selection", "inference_unavailable", "inference_timeout", "invalid_output"]
    count: Annotated[int, Field(ge=1)]


class CodeEvidence(_ReceiptModel):
    scope_id: str
    fingerprint: str
    path: str
    file_sha256: str
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    snippet_sha256: str

    @model_validator(mode="after")
    def check_lines(self) -> CodeEvidence:
        if self.end_line < self.start_line or self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError("invalid code evidence path or range")  # noqa: TRY003
        return self


class SelectedItem(_ReceiptModel):
    kind: ReceiptFamily
    artifact_address: ArtifactAddress | None = None
    code_evidence: CodeEvidence | None = None
    rendered_bytes: Annotated[int, Field(ge=0)]
    truncated: bool

    @model_validator(mode="after")
    def check_identity(self) -> SelectedItem:
        if (self.artifact_address is None) == (self.code_evidence is None):
            raise ValueError("selected item requires exactly one identity")  # noqa: TRY003
        if self.kind == "code":
            if self.code_evidence is None:
                raise ValueError("code item requires code evidence")  # noqa: TRY003
        elif self.artifact_address is None or self.artifact_address.artifact.family != self.kind:
            raise ValueError("artifact item requires a matching artifact address")  # noqa: TRY003
        return self

    @model_serializer(mode="wrap")
    def serialize_identity(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class OmittedGroup(_ReceiptModel):
    family: ReceiptFamily
    reason: OmissionReason
    count: Annotated[int, Field(ge=1)]


class StageTiming(_ReceiptModel):
    stage: str
    duration_ms: Annotated[float, Field(ge=0)]
    count: Annotated[int, Field(ge=1)]


class RetrievalEvidence(_ReceiptModel):
    rounds: Annotated[tuple[RecallRound, ...], Field(max_length=3)]
    searches: Annotated[tuple[SearchGroup, ...], Field(max_length=32)]
    rerank_configs: Annotated[tuple[RerankConfig, ...], Field(max_length=8)]
    reranks: Annotated[tuple[RerankGroup, ...], Field(max_length=32)]


class PreparedContextReceipt(_ReceiptModel):
    schema_version: Literal["powercontext.prepared-context-receipt.v1"] = Field(default=RECEIPT_POLICY, alias="schema")
    receipt_id: str
    policy_id: Literal["powercontext.prepared-context-receipt.v1"] = RECEIPT_POLICY
    query_digest: str
    content_digest: str | None
    requested_max_bytes: Annotated[int, Field(ge=512, le=32768)]
    used_bytes: Annotated[int, Field(ge=0)]
    truncated: bool
    non_deterministic: bool
    retrieval: RetrievalEvidence
    selected: Annotated[tuple[SelectedItem, ...], Field(max_length=32)]
    omitted: Annotated[tuple[OmittedGroup, ...], Field(max_length=16)]
    stages: Annotated[tuple[StageTiming, ...], Field(max_length=8)]

    @model_validator(mode="after")
    def check_integrity(self) -> PreparedContextReceipt:
        configs = {entry.config_id for entry in self.retrieval.rerank_configs}
        rounds = {entry.round for entry in self.retrieval.rounds}
        if any(group.config_id not in configs or group.round not in rounds for group in self.retrieval.reranks):
            raise ValueError("rerank evidence lacks configuration or round")  # noqa: TRY003
        if any(group.round not in rounds for group in self.retrieval.searches):
            raise ValueError("search evidence lacks round")  # noqa: TRY003
        if self.truncated != any(item.truncated for item in self.selected):
            raise ValueError("truncation evidence differs from selected items")  # noqa: TRY003
        return self
