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

"""Runtime-independent contracts for bounded decision-policy sidecars."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from powercontext.builtin.inference import InferenceUsage


class DecisionPolicyMode(StrEnum):
    """Runtime mode for one versioned decision policy."""

    DISABLED = "disabled"
    SHADOW = "shadow"
    ADVISORY = "advisory"
    ENFORCING = "enforcing"


class DecisionFailurePolicy(StrEnum):
    """How a consumer treats backend failure when it owns a domain action."""

    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"


class DecisionPrivacyBoundary(StrEnum):
    """Content boundary declared by a policy before backend calls."""

    LOCAL_ONLY = "local_only"
    HOSTED_REDACTED = "hosted_redacted"
    REFERENCES_ONLY = "references_only"
    NO_EXTERNAL_CALL = "no_external_call"


class DecisionCoverage(StrEnum):
    """Whether a policy evaluation actually judged the supplied content."""

    ADJUDICATED = "adjudicated"
    UNADJUDICATED = "unadjudicated"


class DecisionVerdict(StrEnum):
    """Domain-neutral verdict before an owning service maps it to an action."""

    ALLOW = "allow"
    DENY = "deny"
    REVIEW = "review"
    UNKNOWN = "unknown"


class DecisionAssessmentSource(StrEnum):
    """The source that produced the assessment's substantive judgement."""

    LOCAL_RULE = "local_rule"
    DECISION_MODEL = "decision_model"
    NONE = "none"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DecisionPolicy(_StrictModel):
    """Versioned, reviewable policy manifest for one bounded runtime question."""

    policy_id: Annotated[str, Field(min_length=1, max_length=256)]
    decision_kind: Annotated[str, Field(min_length=1, max_length=128)]
    version: Annotated[str, Field(min_length=1, max_length=64)]
    consumer: Annotated[str, Field(min_length=1, max_length=128)]
    mode: DecisionPolicyMode
    failure_policy: DecisionFailurePolicy
    privacy_boundary: DecisionPrivacyBoundary
    local_rules: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = ()
    question: Annotated[str, Field(min_length=1, max_length=8192)]
    subject_selector: Annotated[str, Field(min_length=1, max_length=512)]
    evidence_selector: Annotated[str, Field(min_length=1, max_length=512)]
    outcome_mapping: Mapping[str, Annotated[str, Field(min_length=1, max_length=128)]] = Field(default_factory=dict)
    promotion_criteria: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = ()


class DecisionAssessment(_StrictModel):
    """Policy-level assessment before domain-specific action mapping."""

    policy_id: Annotated[str, Field(min_length=1, max_length=256)]
    policy_version: Annotated[str, Field(min_length=1, max_length=64)]
    mode: DecisionPolicyMode
    coverage: DecisionCoverage
    verdict: DecisionVerdict
    source: DecisionAssessmentSource
    reason: Annotated[str | None, Field(min_length=1, max_length=4096)] = None
    confidence: Annotated[float | None, Field(ge=0.0, le=1.0)] = None
    used_fallback: bool = False
    usage: InferenceUsage = Field(default_factory=lambda: InferenceUsage(requests=0))
    latency_ms: Annotated[float | None, Field(ge=0.0, allow_inf_nan=False)] = None

    @model_validator(mode="after")
    def validate_fallback_coverage(self) -> DecisionAssessment:
        if self.used_fallback and self.coverage is not DecisionCoverage.UNADJUDICATED:
            raise ValueError("fallback assessments must be unadjudicated")  # noqa: TRY003
        return self


class DecisionObservation(_StrictModel):
    """Audit/replay sidecar for one policy evaluation attempt."""

    observation_id: Annotated[str, Field(min_length=1, max_length=128)] = Field(
        default_factory=lambda: f"decision-observation-{uuid4().hex}"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    operation_id: Annotated[str, Field(min_length=1, max_length=256)]
    scope_id: Annotated[str, Field(min_length=1, max_length=256)]
    consumer: Annotated[str, Field(min_length=1, max_length=128)]
    policy_id: Annotated[str, Field(min_length=1, max_length=256)]
    policy_version: Annotated[str, Field(min_length=1, max_length=64)]
    mode: DecisionPolicyMode
    subject_refs: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = ()
    evidence_refs: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = ()
    privacy_boundary: DecisionPrivacyBoundary
    privacy_outcome: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    provider_id: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    backend_model_id: Annotated[str | None, Field(min_length=1, max_length=256)] = None
    model_policy_id: Annotated[str | None, Field(min_length=1, max_length=256)] = None
    assessment: DecisionAssessment
    final_action: Annotated[str, Field(min_length=1, max_length=128)]
    fallback_reason: Annotated[str | None, Field(min_length=1, max_length=256)] = None
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict)


__all__ = [
    "DecisionAssessment",
    "DecisionAssessmentSource",
    "DecisionCoverage",
    "DecisionFailurePolicy",
    "DecisionObservation",
    "DecisionPolicy",
    "DecisionPolicyMode",
    "DecisionPrivacyBoundary",
    "DecisionVerdict",
]
