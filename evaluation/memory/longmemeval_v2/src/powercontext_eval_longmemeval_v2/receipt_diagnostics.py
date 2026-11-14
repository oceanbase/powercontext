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

"""Validate public Receipt evidence and project bounded, content-free diagnostics."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DIAGNOSTICS_SCHEMA = "powercontext.longmemeval-v2-prepared-context-diagnostics.v1"
RECEIPT_SCHEMA = "powercontext.prepared-context-receipt.v1"
MAX_DIAGNOSTICS_BYTES = 8_192

Family = Literal["atomic-memory", "topic-memory", "experience", "profile", "code"]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
CodeDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Round = Annotated[int, Field(ge=0, le=2)]
Count = Annotated[int, Field(ge=1)]


class _Evidence(BaseModel):
    # Unknown public fields are ignored by Pydantic's default, never copied to the projection.
    model_config = ConfigDict(strict=True, allow_inf_nan=False)


class _Artifact(_Evidence):
    family: Annotated[str, Field(min_length=1, max_length=128)]
    artifact_id: Annotated[str, Field(min_length=1, max_length=128)]
    revision: Count

    @field_validator("family", "artifact_id")
    @classmethod
    def trimmed_identity(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("invalid artifact identity")
        return value


class _Address(_Evidence):
    scope_id: Annotated[str, Field(min_length=1, max_length=256)]
    artifact: _Artifact

    @field_validator("scope_id")
    @classmethod
    def trimmed_scope(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("invalid scope identity")
        return value


class _Code(_Evidence):
    scope_id: Annotated[str, Field(min_length=1, max_length=256, pattern=r".*\S.*")]
    fingerprint: CodeDigest
    path: str
    file_sha256: CodeDigest
    start_line: Count
    end_line: Count
    snippet_sha256: CodeDigest


class _Selected(_Evidence):
    kind: Family
    artifact_address: _Address | None = None
    code_evidence: _Code | None = None
    rendered_bytes: Annotated[int, Field(ge=0)]
    truncated: bool


class _Omitted(_Evidence):
    family: Family
    reason: Literal[
        "duplicate",
        "blank",
        "family_limit",
        "entry_limit",
        "below_min_bytes",
        "no_fitting_truncation",
        "rerank_not_selected",
        "not_retrieved",
    ]
    count: Count


class _Round(_Evidence):
    round: Round
    outcome: Literal["completed", "expansion_failed"]
    retained: bool


class _Search(_Evidence):
    round: Round
    family: Family
    mode: Literal["fts", "vector", "hybrid", "snapshot", "code", "none"]
    outcome: Literal["completed", "not_run", "failed"]
    fallback_reason: Literal["none", "inference_unavailable", "inference_timeout", "reused_fts_fallback"]
    count: Count


class _Prompt(_Evidence):
    scope_id: str
    key: str
    definition_version: str
    builtin_version: str
    selection: Literal["built_in", "artifact"]
    selected_version: str
    compiled_digest: Digest
    artifact_address: _Address | None


class _Config(_Evidence):
    config_id: Digest
    config_digest: Digest
    policy_id: str
    model: str | None
    effective_settings: dict[str, str | int | float | bool]
    timeout_seconds: Annotated[float, Field(gt=0)]
    max_requests: Count
    prompt: _Prompt | None
    non_deterministic: bool


class _Rerank(_Evidence):
    round: Round
    config_id: Digest
    outcome: Literal["selected", "fallback", "failed"]
    fallback_reason: Literal["none", "empty_selection", "inference_unavailable", "inference_timeout", "invalid_output"]
    count: Count


class _Retrieval(_Evidence):
    rounds: Annotated[list[_Round], Field(max_length=3)]
    searches: Annotated[list[_Search], Field(max_length=32)]
    rerank_configs: Annotated[list[_Config], Field(max_length=8)]
    reranks: Annotated[list[_Rerank], Field(max_length=32)]


class _Stage(_Evidence):
    stage: str
    duration_ms: Annotated[float, Field(ge=0)]
    count: Count


class _Receipt(_Evidence):
    receipt_id: str
    policy_id: Literal["powercontext.prepared-context-receipt.v1"]
    query_digest: Digest
    content_digest: Digest | None
    requested_max_bytes: Annotated[int, Field(ge=512, le=32768)]
    used_bytes: Annotated[int, Field(ge=0)]
    truncated: bool
    non_deterministic: bool
    selected: Annotated[list[_Selected], Field(max_length=32)]
    omitted: Annotated[list[_Omitted], Field(max_length=16)]
    retrieval: _Retrieval
    stages: Annotated[list[_Stage], Field(max_length=8)]

    @field_validator("receipt_id")
    @classmethod
    def uuid_identity(cls, value: str) -> str:
        UUID(value)
        return value


def prepared_context_diagnostics(response: Mapping[str, object], *, maximum_bytes: int) -> dict[str, object]:
    """Read only the Receipt accompanying an already validated prepare response.

    Diagnostics failures never invalidate otherwise usable context. No content, query,
    effective settings, code paths, or unknown fields survive the explicit projection.
    """

    raw = response.get("receipt")
    if raw is None:
        return _incomplete("unavailable", None)
    if not isinstance(raw, Mapping):
        return _incomplete("invalid", "invalid_receipt")
    if raw.get("schema") != RECEIPT_SCHEMA:
        return _incomplete("invalid", "unsupported_schema")
    try:
        receipt = _Receipt.model_validate(raw)
    except ValidationError:
        return _incomplete("invalid", "invalid_receipt")
    failure = _integrity_failure(receipt, response, maximum_bytes)
    if failure is not None:
        return _incomplete("invalid", failure)
    projection = {
        "schema": DIAGNOSTICS_SCHEMA,
        "receipt_status": "available",
        "validation_failure": None,
        "completeness": "complete",
        "candidate_count": None,
        "candidate_count_basis": "not_exposed",
        "receipt_schema": RECEIPT_SCHEMA,
        "receipt_id": receipt.receipt_id,
        "policy_id": receipt.policy_id,
        "content_digest": receipt.content_digest,
        "used_bytes": receipt.used_bytes,
        "requested_max_bytes": receipt.requested_max_bytes,
        "truncated": receipt.truncated,
        "non_deterministic": receipt.non_deterministic,
        "selected": [
            item.model_dump(mode="json", exclude_none=True, exclude={"code_evidence": {"path"}})
            for item in receipt.selected
        ],
        "omitted": [item.model_dump(mode="json") for item in receipt.omitted],
        "retrieval": {
            "rounds": [item.model_dump(mode="json") for item in receipt.retrieval.rounds],
            "searches": [item.model_dump(mode="json") for item in receipt.retrieval.searches],
            "reranks": [item.model_dump(mode="json") for item in receipt.retrieval.reranks],
            "rerank_configs": [
                {
                    "config_id": item.config_id,
                    "config_digest": item.config_digest,
                    "prompt_digest": None if item.prompt is None else item.prompt.compiled_digest,
                    "non_deterministic": item.non_deterministic,
                }
                for item in receipt.retrieval.rerank_configs
            ],
        },
        "stages": [
            {
                "stage": item.stage
                if item.stage
                in {
                    "context.build",
                    "memory.search",
                    "memory.rerank",
                    "experience.search",
                    "topic_memory.search",
                }
                else "other",
                "duration_ms": item.duration_ms,
                "count": item.count,
            }
            for item in receipt.stages
        ],
    }
    try:
        size = len(json.dumps(projection, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
    except UnicodeError:
        return _incomplete("invalid", "invalid_receipt")
    if size > MAX_DIAGNOSTICS_BYTES:
        return _incomplete("invalid", "local_size_limit")
    return projection


def _incomplete(status: str, failure: str | None) -> dict[str, object]:
    return {
        "schema": DIAGNOSTICS_SCHEMA,
        "receipt_status": status,
        "validation_failure": failure,
        "completeness": "incomplete",
        "candidate_count": None,
        "candidate_count_basis": "not_exposed",
    }


def _integrity_failure(receipt: _Receipt, response: Mapping[str, object], maximum_bytes: int) -> str | None:
    if receipt.used_bytes != response["content_bytes"]:
        return "used_bytes_mismatch"
    if receipt.requested_max_bytes != maximum_bytes:
        return "budget_mismatch"
    content = response["content"]
    digest = None if content is None else "sha256:" + hashlib.sha256(str(content).encode()).hexdigest()
    if receipt.content_digest != digest:
        return "content_digest_mismatch"
    if receipt.truncated != any(item.truncated for item in receipt.selected):
        return "truncation_mismatch"
    for item in receipt.selected:
        address, code = item.artifact_address, item.code_evidence
        if item.kind == "code":
            if (
                address is not None
                or code is None
                or code.end_line < code.start_line
                or code.path.startswith("/")
                or ".." in code.path.split("/")
            ):
                return "identity_mismatch"
        elif code is not None or address is None or address.artifact.family != item.kind:
            return "identity_mismatch"
    rounds = {item.round for item in receipt.retrieval.rounds}
    configs = {item.config_id for item in receipt.retrieval.rerank_configs}
    if any(item.round not in rounds for item in receipt.retrieval.searches) or any(
        item.round not in rounds or item.config_id not in configs for item in receipt.retrieval.reranks
    ):
        return "evidence_mismatch"
    if any(
        item.config_id != item.config_digest or item.non_deterministic != (item.model is not None)
        for item in receipt.retrieval.rerank_configs
    ):
        return "evidence_mismatch"
    if receipt.non_deterministic != any(item.non_deterministic for item in receipt.retrieval.rerank_configs):
        return "evidence_mismatch"
    return None
