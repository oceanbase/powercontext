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

"""Resolve deployment capacity and admit one complete model request.

The shared layer decides whether a complete request fits; it never decides what
goes into the request. Artifact families keep their own selection strategies and
call :func:`admit_request` with the estimate of the payload they assembled.

Admission is deliberately split from estimation. Selection-time guesses help a
family choose a smaller window, but only the estimate of the real payload may
admit or reject a send.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from powercontext.builtin.inference.errors import InferenceBudgetExceededError
from powercontext.builtin.inference.tokens import TokenEstimatorProfile, resolve_token_estimator

ContextSemantics = Literal["joint_window", "separate_limits"]
AdmissionPhase = Literal["selection", "initial_dispatch", "retry_dispatch", "continuation_dispatch"]
BudgetUnit = Literal["tokens", "requests", "seconds", "items", "bytes", "characters"]
BudgetDimension = Literal[
    "input_tokens",
    "context_tokens",
    "output_tokens",
    "request_count",
    "work_tokens",
    "deadline",
    "embedding_item_tokens",
    "embedding_batch_tokens",
    "embedding_batch_items",
    "input_bytes",
]

TOPIC_MEMORY_STAGES = ("probe", "global", "planner", "evolve", "temporary", "reduce", "reconcile")

#: Generation operations that may carry their own allowance.
#:
#: This is the registry the runtime configuration validates against, so a
#: misspelled operation fails at startup instead of silently doing nothing.
#: Dream operations are absent on purpose: a Dream run is bounded by its own
#: per-run budget rather than a per-operation generation allowance.
GENERATION_OPERATIONS: frozenset[str] = frozenset(
    {
        "atomic_memory.extract",
        "atomic_memory.reconcile",
        "experience.incubate",
        "experience.generate",
        "handoff.generate",
        "memory.rerank",
        "memory.write-gate",
        "profile.generate",
        "prompt_demonstrations",
        "skill.generate",
    }
    | {f"topic_memory.{stage}" for stage in TOPIC_MEMORY_STAGES}
)

DEFAULT_OUTPUT_RESERVE_TOKENS = 4096
DEFAULT_OUTPUT_RESERVE_RATIO = 0.20


class BudgetProvenance(BaseModel):
    """Record where every resolved budget value came from."""

    model_config = ConfigDict(frozen=True)

    context_window: str
    input_limit: str
    output_limit: str
    output_reserve: str
    safety_margin: str
    estimator: str


class EffectiveRequestBudget(BaseModel):
    """Immutable capacity for one request after every applicable limit intersects.

    ``safety_margin_tokens`` is an absolute quantity. Ratios only exist in the
    deployment configuration where a human can reason about them.
    """

    model_config = ConfigDict(frozen=True)

    context_semantics: ContextSemantics = "joint_window"
    context_window_tokens: int | None = Field(default=None, ge=1)
    input_tokens_limit: int | None = Field(default=None, ge=1)
    output_tokens_limit: int | None = Field(default=None, ge=1)
    output_reserve_tokens: int = Field(default=0, ge=0)
    safety_margin_tokens: int = Field(default=0, ge=0)
    estimator: TokenEstimatorProfile
    provenance: BudgetProvenance

    @model_validator(mode="after")
    def _require_executable_capacity(self) -> EffectiveRequestBudget:
        if self.context_semantics == "joint_window":
            if self.context_window_tokens is None:
                raise ValueError("joint_window requires context_window_tokens")  # noqa: TRY003
            if self.context_window_tokens - self.output_reserve_tokens - self.safety_margin_tokens < 1:
                raise ValueError("output reserve and safety margin leave no executable context")  # noqa: TRY003
        elif self.input_tokens_limit is None:
            raise ValueError("separate_limits requires input_tokens_limit")  # noqa: TRY003
        if self.output_tokens_limit is not None and self.output_reserve_tokens > self.output_tokens_limit:
            raise ValueError("output reserve exceeds the declared output limit")  # noqa: TRY003
        return self

    def with_output_limit(self, requested: int | None) -> EffectiveRequestBudget:
        """Bind capacity to the response limit that will actually be sent."""

        output = self.output_reserve_tokens if requested is None else requested
        if not isinstance(output, int) or isinstance(output, bool) or output < 1:
            raise ValueError("invalid-output-budget")
        if self.output_tokens_limit is not None:
            output = min(output, self.output_tokens_limit)
        if self.provenance.output_reserve != "default":
            output = min(output, self.output_reserve_tokens)
        return EffectiveRequestBudget.model_validate({**self.model_dump(), "output_reserve_tokens": output})

    @property
    def context_limit_tokens(self) -> int | None:
        """Return the input ceiling implied by the shared context window."""

        if self.context_window_tokens is None:
            return None
        return self.context_window_tokens - self.output_reserve_tokens - self.safety_margin_tokens

    @property
    def joint_allowed_total_tokens(self) -> int | None:
        """Return the input-plus-output ceiling for separately limited providers."""

        if self.context_semantics != "separate_limits" or self.context_window_tokens is None:
            return None
        return self.context_window_tokens - self.safety_margin_tokens

    @property
    def binding_dimension(self) -> BudgetDimension:
        """Name the dimension that limits input when the request is rejected."""

        context = self.context_limit_tokens
        if self.context_semantics == "separate_limits" or context is None:
            return "input_tokens"
        if self.input_tokens_limit is None or context <= self.input_tokens_limit:
            return "context_tokens"
        return "input_tokens"

    @property
    def allowed_input_tokens(self) -> int:
        """Return how many estimated input tokens one request may carry."""

        if self.context_semantics == "joint_window":
            ceiling = min(limit for limit in (self.input_tokens_limit, self.context_limit_tokens) if limit is not None)
            return max(0, ceiling)
        return max(0, (self.input_tokens_limit or 0) - self.safety_margin_tokens)


def hard_limit_decision(
    *,
    operation: str,
    dimension: BudgetDimension,
    estimated_value: int,
    limit_value: int,
    unit: BudgetUnit = "bytes",
    stage: str | None = None,
) -> AdmissionDecision:
    """Reject a request that cannot be shrunk to fit a fixed contract limit."""

    return AdmissionDecision(
        allowed=False,
        operation=operation,
        stage=stage,
        dimension=dimension,
        estimated_value=estimated_value,
        limit_value=limit_value,
        unit=unit,
        output_reserve_tokens=0,
        safety_margin_tokens=0,
        estimator_id="contract",
        estimator_version="1",
        phase="selection",
    )


class RequestEstimate(BaseModel):
    """One estimate of a payload, together with the estimator that produced it."""

    model_config = ConfigDict(frozen=True)

    estimated_input_tokens: int = Field(ge=0)
    estimator: TokenEstimatorProfile
    scope: Literal["complete_request", "selection"] = "complete_request"


class AdmissionDecision(BaseModel):
    """The outcome of admitting one request, with only safe budget facts."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    operation: str = Field(min_length=1)
    stage: str | None = None
    dimension: BudgetDimension
    estimated_value: int = Field(ge=0)
    limit_value: int = Field(ge=0)
    unit: BudgetUnit = "tokens"
    output_reserve_tokens: int = Field(default=0, ge=0)
    safety_margin_tokens: int = Field(default=0, ge=0)
    estimator_id: str = Field(min_length=1)
    estimator_version: str = Field(min_length=1)
    phase: AdmissionPhase = "initial_dispatch"

    def details(self) -> dict[str, object]:
        """Return JSON-safe budget facts that never include content."""

        facts: dict[str, object] = {
            "operation": self.operation,
            "dimension": self.dimension,
            "estimated_value": self.estimated_value,
            "limit_value": self.limit_value,
            "unit": self.unit,
            "output_reserve_tokens": self.output_reserve_tokens,
            "safety_margin_tokens": self.safety_margin_tokens,
            "estimator_id": self.estimator_id,
            "estimator_version": self.estimator_version,
            "phase": self.phase,
        }
        if self.stage is not None:
            facts["stage"] = self.stage
        return facts


def resolve_request_budget(
    *,
    estimator: TokenEstimatorProfile,
    provenance: BudgetProvenance,
    context_semantics: ContextSemantics = "joint_window",
    context_window_tokens: int | None = None,
    input_tokens_limit: int | None = None,
    output_tokens_limit: int | None = None,
    output_reserve_tokens: int | None = None,
    safety_margin_ratio: float = 0.05,
    safety_margin_tokens: int = 0,
) -> EffectiveRequestBudget:
    """Resolve declared capacity into one effective budget.

    An unset output reserve falls back to the smaller of the historical default,
    the declared output limit, and a fraction of the context window. An explicit
    absolute safety margin overrides the ratio so the two never stack.
    """

    reserve = output_reserve_tokens
    if reserve is None:
        candidates = [DEFAULT_OUTPUT_RESERVE_TOKENS]
        if output_tokens_limit is not None:
            candidates.append(output_tokens_limit)
        if context_window_tokens is not None:
            candidates.append(int(context_window_tokens * DEFAULT_OUTPUT_RESERVE_RATIO))
        reserve = max(0, min(candidates))
    margin = safety_margin_tokens
    if margin <= 0 and context_window_tokens is not None:
        margin = int(context_window_tokens * safety_margin_ratio)
    return EffectiveRequestBudget(
        context_semantics=context_semantics,
        context_window_tokens=context_window_tokens,
        input_tokens_limit=input_tokens_limit,
        output_tokens_limit=output_tokens_limit,
        output_reserve_tokens=reserve,
        safety_margin_tokens=margin,
        estimator=estimator,
        provenance=provenance,
    )


def _decide(
    *,
    allowed: bool,
    dimension: BudgetDimension,
    estimate: RequestEstimate,
    limit_value: int,
    budget: EffectiveRequestBudget,
    operation: str,
    stage: str | None,
    phase: AdmissionPhase,
) -> AdmissionDecision:
    return AdmissionDecision(
        allowed=allowed,
        operation=operation,
        stage=stage,
        dimension=dimension,
        estimated_value=estimate.estimated_input_tokens,
        limit_value=limit_value,
        unit="tokens",
        output_reserve_tokens=budget.output_reserve_tokens,
        safety_margin_tokens=budget.safety_margin_tokens,
        estimator_id=budget.estimator.estimator_id,
        estimator_version=budget.estimator.version,
        phase=phase,
    )


def admit_request(
    budget: EffectiveRequestBudget,
    estimate: RequestEstimate,
    *,
    operation: str,
    stage: str | None = None,
    phase: AdmissionPhase = "initial_dispatch",
) -> AdmissionDecision:
    """Admit one complete request, reporting the binding dimension either way."""

    if estimate.scope != "complete_request":
        raise ValueError("only a complete-request estimate can be admitted")  # noqa: TRY003
    if estimate.estimator != budget.estimator:
        raise ValueError("estimate estimator does not match the resolved budget")  # noqa: TRY003

    allowed_input = budget.allowed_input_tokens
    if estimate.estimated_input_tokens > allowed_input:
        return _decide(
            allowed=False,
            dimension=budget.binding_dimension,
            estimate=estimate,
            limit_value=allowed_input,
            budget=budget,
            operation=operation,
            stage=stage,
            phase=phase,
        )
    joint = budget.joint_allowed_total_tokens
    if joint is not None and estimate.estimated_input_tokens + budget.output_reserve_tokens > joint:
        decision = _decide(
            allowed=False,
            dimension="context_tokens",
            estimate=estimate,
            limit_value=joint,
            budget=budget,
            operation=operation,
            stage=stage,
            phase=phase,
        )
        return decision.model_copy(
            update={"estimated_value": estimate.estimated_input_tokens + budget.output_reserve_tokens}
        )
    return _decide(
        allowed=True,
        dimension=budget.binding_dimension,
        estimate=estimate,
        limit_value=allowed_input,
        budget=budget,
        operation=operation,
        stage=stage,
        phase=phase,
    )


class EmbeddingBudget(BaseModel):
    """Deployment capacity for one embedding call.

    Embeddings have no output budget and no cumulative stage ledger. The adapter
    admits the batch it is about to send, so a single oversized text and an
    oversized batch stay separate dimensions instead of one shared total.
    """

    model_config = ConfigDict(frozen=True)

    max_input_tokens_per_text: int | None = Field(default=None, ge=1)
    max_input_tokens_per_batch: int | None = Field(default=None, ge=1)
    max_batch_items: int | None = Field(default=None, ge=1)
    estimator: TokenEstimatorProfile = Field(
        default_factory=lambda: TokenEstimatorProfile(estimator_id="character:weighted", version="1")
    )

    @property
    def has_limits(self) -> bool:
        """Report whether this deployment declared any limit to admit against."""

        return (
            self.max_input_tokens_per_text is not None
            or self.max_input_tokens_per_batch is not None
            or self.max_batch_items is not None
        )


def _embedding_decision(
    *,
    allowed: bool,
    dimension: BudgetDimension,
    estimated_value: int,
    limit_value: int,
    budget: EmbeddingBudget,
    operation: str,
    phase: AdmissionPhase,
) -> AdmissionDecision:
    return AdmissionDecision(
        allowed=allowed,
        operation=operation,
        dimension=dimension,
        estimated_value=estimated_value,
        limit_value=limit_value,
        unit="items" if dimension == "embedding_batch_items" else "tokens",
        estimator_id=budget.estimator.estimator_id,
        estimator_version=budget.estimator.version,
        phase=phase,
    )


def admit_embedding_request(
    budget: EmbeddingBudget,
    texts: tuple[str, ...],
    *,
    operation: str = "embedding",
    phase: AdmissionPhase = "initial_dispatch",
) -> AdmissionDecision:
    """Admit one embedding batch, reporting the binding dimension either way.

    The estimate runs on exactly the texts the adapter is about to send, so a
    text that was not selected never affects the decision.
    """

    estimator = resolve_token_estimator(budget.estimator)
    counted = tuple(estimator.estimate(text) for text in texts)

    if budget.max_batch_items is not None and len(texts) > budget.max_batch_items:
        return _embedding_decision(
            allowed=False,
            dimension="embedding_batch_items",
            estimated_value=len(texts),
            limit_value=budget.max_batch_items,
            budget=budget,
            operation=operation,
            phase=phase,
        )
    if budget.max_input_tokens_per_text is not None:
        largest = max(counted, default=0)
        if largest > budget.max_input_tokens_per_text:
            return _embedding_decision(
                allowed=False,
                dimension="embedding_item_tokens",
                estimated_value=largest,
                limit_value=budget.max_input_tokens_per_text,
                budget=budget,
                operation=operation,
                phase=phase,
            )
    total = sum(counted)
    if budget.max_input_tokens_per_batch is not None and total > budget.max_input_tokens_per_batch:
        return _embedding_decision(
            allowed=False,
            dimension="embedding_batch_tokens",
            estimated_value=total,
            limit_value=budget.max_input_tokens_per_batch,
            budget=budget,
            operation=operation,
            phase=phase,
        )

    dimension: BudgetDimension
    estimated: int
    limit: int
    if budget.max_input_tokens_per_batch is not None:
        dimension, estimated, limit = "embedding_batch_tokens", total, budget.max_input_tokens_per_batch
    elif budget.max_input_tokens_per_text is not None:
        dimension = "embedding_item_tokens"
        estimated = max(counted, default=0)
        limit = budget.max_input_tokens_per_text
    else:
        dimension, estimated, limit = "embedding_batch_items", len(texts), budget.max_batch_items or 0
    return _embedding_decision(
        allowed=True,
        dimension=dimension,
        estimated_value=estimated,
        limit_value=limit,
        budget=budget,
        operation=operation,
        phase=phase,
    )


def validate_embedding_batches(budget: EmbeddingBudget, texts: tuple[str, ...], batch_size: int) -> None:
    """Reject every locally predictable batch violation before the first send."""

    for start in range(0, len(texts), batch_size):
        raise_if_rejected(admit_embedding_request(budget, texts[start : start + batch_size], operation="embed"))


def raise_if_rejected(decision: AdmissionDecision) -> AdmissionDecision:
    """Raise the typed budget failure so the owning boundary can classify it."""

    if not decision.allowed:
        raise InferenceBudgetExceededError(decision)
    return decision


__all__ = [
    "GENERATION_OPERATIONS",
    "TOPIC_MEMORY_STAGES",
    "AdmissionDecision",
    "AdmissionPhase",
    "BudgetDimension",
    "BudgetProvenance",
    "BudgetUnit",
    "ContextSemantics",
    "EffectiveRequestBudget",
    "EmbeddingBudget",
    "RequestEstimate",
    "admit_embedding_request",
    "admit_request",
    "raise_if_rejected",
    "resolve_request_budget",
    "validate_embedding_batches",
]
