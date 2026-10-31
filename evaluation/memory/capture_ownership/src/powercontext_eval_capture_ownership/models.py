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

"""Frozen, content-labeled experiment inputs and private review records."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal, Self, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

SafeId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")]
Owner = Literal["current", "history", "prepared_context", "scaffold", "skill", "quoted", "summary"]
Arm = Literal["joined-source-v1", "host-turn-candidate-v1"]
ARMS: tuple[Arm, ...] = ("joined-source-v1", "host-turn-candidate-v1")


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def contains_literal(text: str, literal: str) -> bool:
    """Match frozen values without accepting identifier, path or numeric extensions."""

    if not literal:
        return False
    if literal.isdecimal():
        pattern = rf"(?<![\dA-Za-z_.+−-]){re.escape(literal)}(?![\dA-Za-z_]|[.,/]\d)"
    else:
        # ASCII values may directly adjoin CJK prose, but not identifier/path characters.
        identifier = r"\w" if not literal.isascii() else r"\dA-Za-z_"
        pattern = rf"(?<![{identifier}./-]){re.escape(literal)}(?![{identifier}/-]|\.[{identifier}])"
    return re.search(pattern, text) is not None


class Span(Frozen):
    span_id: SafeId
    owner: Owner
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    byte_start: int = Field(ge=0)
    byte_end: int = Field(ge=0)


class Claim(Frozen):
    claim_id: SafeId
    text: str = Field(min_length=1)
    span_ids: tuple[SafeId, ...] = Field(min_length=1)
    required_current: bool = False
    exact_literals: tuple[str, ...] = ()


class Case(Frozen):
    case_id: SafeId
    prompt: str = Field(min_length=1, max_length=200_000)
    spans: tuple[Span, ...] = Field(min_length=1)
    claims: tuple[Claim, ...] = ()
    expected_turn: Literal["eligible", "empty", "unknown"]

    @model_validator(mode="after")
    def validate_ground_truth(self) -> Self:
        position = 0
        span_ids: dict[str, Span] = {}
        for span in self.spans:
            if span.span_id in span_ids or span.start != position or span.end <= span.start:
                raise ValueError("ownership spans must uniquely partition the complete prompt")
            if span.end > len(self.prompt):
                raise ValueError("ownership span exceeds prompt")
            if (span.byte_start, span.byte_end) != (
                len(self.prompt[: span.start].encode()),
                len(self.prompt[: span.end].encode()),
            ):
                raise ValueError("ownership byte and character offsets disagree")
            position = span.end
            span_ids[span.span_id] = span
        if position != len(self.prompt):
            raise ValueError("ownership spans must cover the complete prompt")
        if len({claim.claim_id for claim in self.claims}) != len(self.claims):
            raise ValueError("claim identifiers must be unique within a case")
        for claim in self.claims:
            if not set(claim.span_ids) <= span_ids.keys():
                raise ValueError("claim references an unknown span")
            supporting = "".join(self.prompt[span_ids[key].start : span_ids[key].end] for key in claim.span_ids)
            if any(not contains_literal(supporting, value) for value in claim.exact_literals):
                raise ValueError("required literal is absent from its labeled support")
            if claim.required_current and (
                self.expected_turn != "eligible" or any(span_ids[key].owner != "current" for key in claim.span_ids)
            ):
                raise ValueError("required current facts need unambiguous current-user support")
        return self


class Manifest(Frozen):
    schema_version: Literal["capture-ownership.manifest.v1"]
    corpus: Literal["synthetic", "private"]
    cases: tuple[Case, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("case identifiers must be unique")
        return self


class RunConfig(Frozen):
    schema_version: Literal["capture-ownership.config.v1"]
    profile: Literal["coding", "conversation"] = "coding"
    model: str | None = None
    base_url: str | None = None
    model_settings: dict[str, JsonValue] = Field(
        default_factory=lambda: cast(dict[str, JsonValue], {"temperature": 0, "max_tokens": 1024})
    )
    repetitions: int = Field(default=2, ge=2, le=10)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    max_requests: int = Field(default=1, ge=1, le=4)
    max_input_tokens: int = Field(default=24000, ge=1)
    request_interval_seconds: float = Field(default=0, ge=0, le=60)
    max_cost_cny: float = Field(default=1, gt=0)
    input_cny_per_million: float = Field(default=0, ge=0)
    output_cny_per_million: float = Field(default=0, ge=0)
    policy_version: Literal["capture-ownership.decision.v1"] = "capture-ownership.decision.v1"

    @model_validator(mode="after")
    def nonsecret_configuration(self) -> Self:
        if not set(self.model_settings) <= {"temperature", "max_tokens", "top_p", "seed", "extra_body"}:
            raise ValueError("only content-free generation settings may be frozen")
        if "extra_body" in self.model_settings and self.model_settings["extra_body"] != {"enable_thinking": False}:
            raise ValueError("only disabling provider thinking is supported as an extra setting")
        for name, value in self.model_settings.items():
            if name != "extra_body" and (isinstance(value, bool) or not isinstance(value, (float, int))):
                raise ValueError("generation settings must be numeric")
        limit = self.model_settings.get("max_tokens")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("a positive integer output token cap is required")
        if self.base_url is not None:
            url = urlsplit(self.base_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("provider URL must not contain credentials, query parameters, or fragments")
        return self


class CandidateReview(Frozen):
    candidate_index: int = Field(ge=0)
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    claim_ids: tuple[SafeId, ...] | None = None
    support_span_ids: tuple[SafeId, ...] = ()
    support_complete: bool | None = None
    unsupported_claim: bool | None = None
    current_user_attribution: bool | None = None
    duplicate_group: SafeId | None = None


class CaseReview(Frozen):
    case_id: SafeId
    arm: Arm
    repetition: int = Field(ge=0)
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str | None = None
    candidates: tuple[CandidateReview, ...]
    # Explicit confirmation also covers empty outputs and missing required facts.
    complete: bool = False


class ReviewFile(Frozen):
    schema_version: Literal["capture-ownership.review.v1"]
    run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: tuple[CaseReview, ...]
