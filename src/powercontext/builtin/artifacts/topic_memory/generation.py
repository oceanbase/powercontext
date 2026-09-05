# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""Schema-bound Topic Memory generation stages and private resource budgets."""

from __future__ import annotations

import json
from collections.abc import Mapping
from math import floor
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, field_validator, model_validator

from powercontext.builtin.artifacts.topic_memory.models import TopicMemoryContent
from powercontext.builtin.inference import GenerationResult, StructuredGenerator, TokenEstimator

MAX_TOPIC_MEMORY_STAGE_ITEMS = 20
_OUTPUT_RESERVE_RATIO = 0.20
_RETRY_FEEDBACK_TOKENS = 128


class TopicMemoryGenerationError(RuntimeError):
    """Reject unsafe or internally inconsistent model output without publishing."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.stage = "topic_memory"
        self.error_code = code
        super().__init__(f"Topic Memory generation failed: {code}")


class _TopicMemoryStageModel(BaseModel):
    """Reject fields outside the server-issued stage contract."""

    model_config = ConfigDict(extra="forbid")


class TopicMemoryStageBudget(_TopicMemoryStageModel):
    """Fixed input/output budget for one stage including all structured retries."""

    context_window_tokens: StrictInt = Field(ge=1)
    max_requests: StrictInt = Field(ge=1)
    input_tokens_limit: StrictInt = Field(ge=1)
    transcript_reserve: StrictInt = Field(ge=1)
    output_tokens_limit: StrictInt = Field(ge=1)
    max_output_tokens_per_request: StrictInt = Field(ge=1)


def topic_memory_stage_budget(
    *,
    context_window_tokens: int,
    max_requests: int,
    model_settings: Mapping[str, JsonValue],
) -> TopicMemoryStageBudget:
    """Derive the private 80/20 budget and both provider-output guards."""

    if context_window_tokens < 1 or max_requests < 1:
        raise TopicMemoryGenerationError("invalid_stage_budget")
    configured_max = model_settings.get("max_tokens")
    if configured_max is not None and (
        not isinstance(configured_max, int) or isinstance(configured_max, bool) or configured_max < 1
    ):
        raise TopicMemoryGenerationError("invalid_max_tokens")
    reserve = floor(context_window_tokens * _OUTPUT_RESERVE_RATIO)
    fixed_feedback = _RETRY_FEEDBACK_TOKENS * (max_requests - 1)
    per_request = (reserve - fixed_feedback) // (2 * max_requests - 1)
    feedback = (per_request + _RETRY_FEEDBACK_TOKENS) * (max_requests - 1)
    output_limit = reserve - feedback
    if (
        reserve < 1
        or context_window_tokens - reserve < 1
        or per_request < 1
        or output_limit < max_requests * per_request
    ):
        raise TopicMemoryGenerationError("output_budget_exceeded")
    if configured_max is not None:
        per_request = min(per_request, configured_max)
    if per_request < 1:
        raise TopicMemoryGenerationError("output_budget_exceeded")
    return TopicMemoryStageBudget(
        context_window_tokens=context_window_tokens,
        max_requests=max_requests,
        input_tokens_limit=context_window_tokens - reserve,
        transcript_reserve=reserve,
        output_tokens_limit=output_limit,
        max_output_tokens_per_request=per_request,
    )


def topic_memory_stage_fixed_prompt(
    instructions: str,
    input_type: type[BaseModel],
    output_type: type[BaseModel],
    /,
) -> str:
    """Return the canonical fixed instructions and schemas used for accounting."""

    schemas = {
        "input_schema": input_type.model_json_schema(),
        "output_schema": output_type.model_json_schema(),
    }
    return f"{instructions}\n{json.dumps(schemas, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}"


InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT")


class BudgetedTopicMemoryGenerator(Generic[InputT, OutputT]):
    """Fail before provider I/O when a canonical stage request exceeds its budget."""

    def __init__(
        self,
        delegate: StructuredGenerator[InputT, OutputT],
        *,
        estimator: TokenEstimator,
        budget: TopicMemoryStageBudget,
        fixed_prompt: str,
    ) -> None:
        self._delegate = delegate
        self._estimator = estimator
        self._budget = budget
        self._fixed_prompt = fixed_prompt

    async def generate(self, value: InputT, /) -> GenerationResult[OutputT]:
        request = f"{self._fixed_prompt}\n{value.model_dump_json(exclude_none=False)}"
        if self._estimator.estimate(request) > self._budget.input_tokens_limit:
            raise TopicMemoryGenerationError("input_budget_exceeded")
        return await self._delegate.generate(value)


class TopicMemoryEvidence(_TopicMemoryStageModel):
    """One Window Source exposed under a run-local opaque identity."""

    evidence_id: str = Field(min_length=1, max_length=64)
    source_type: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class TopicMemoryProbe(_TopicMemoryStageModel):
    """One retrieval probe grounded in current Window evidence."""

    query: str = Field(min_length=1, max_length=8_192)
    keywords: tuple[str, ...] = Field(default=(), max_length=64)
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("probe evidence ids must be unique")  # noqa: TRY003
        return value


class TopicMemoryProbeInput(_TopicMemoryStageModel):
    evidence: tuple[TopicMemoryEvidence, ...] = Field(min_length=1)


class TopicMemoryProbeOutput(_TopicMemoryStageModel):
    probes: tuple[TopicMemoryProbe, ...] = Field(default=(), max_length=MAX_TOPIC_MEMORY_STAGE_ITEMS)


class TopicMemoryHistoricalSlot(_TopicMemoryStageModel):
    """Exact historical content exposed only through an opaque candidate id."""

    candidate_id: str = Field(min_length=1, max_length=64)
    title: str
    summary: str
    detail: str


class TopicMemoryProbeCandidates(_TopicMemoryStageModel):
    probe_id: str = Field(min_length=1, max_length=64)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    candidates: tuple[TopicMemoryHistoricalSlot, ...] = Field(default=(), max_length=MAX_TOPIC_MEMORY_STAGE_ITEMS)


class TopicMemoryProposal(_TopicMemoryStageModel):
    """Generated content plus opaque evidence and optional historical target."""

    proposal_id: str | None = Field(default=None, min_length=1, max_length=64)
    candidate_id: str | None = Field(default=None, min_length=1, max_length=64)
    content: TopicMemoryContent
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def reject_content_identity_fields(cls, value: object) -> object:
        if isinstance(value, Mapping):
            content = value.get("content")
            if isinstance(content, Mapping) and set(content) - {"title", "summary", "detail"}:
                raise ValueError("generated Topic content contains unknown fields")  # noqa: TRY003
        return value

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("proposal evidence ids must be unique")  # noqa: TRY003
        return value


class TopicMemoryGlobalInput(_TopicMemoryStageModel):
    evidence: tuple[TopicMemoryEvidence, ...] = Field(min_length=1)
    probes: tuple[TopicMemoryProbeCandidates, ...] = Field(max_length=MAX_TOPIC_MEMORY_STAGE_ITEMS)
    historical: tuple[TopicMemoryHistoricalSlot, ...] = Field(default=(), max_length=MAX_TOPIC_MEMORY_STAGE_ITEMS)


class TopicMemoryGlobalOutput(_TopicMemoryStageModel):
    ambiguous: bool = False
    proposals: tuple[TopicMemoryProposal, ...] = Field(default=(), max_length=MAX_TOPIC_MEMORY_STAGE_ITEMS)


class TopicMemoryPlanItem(_TopicMemoryStageModel):
    probe_ids: tuple[str, ...] = Field(min_length=1, max_length=MAX_TOPIC_MEMORY_STAGE_ITEMS)
    candidate_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("probe_ids")
    @classmethod
    def unique_probes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("plan probe ids must be unique")  # noqa: TRY003
        return value


class TopicMemoryPlannerInput(_TopicMemoryStageModel):
    probes: tuple[TopicMemoryProbeCandidates, ...] = Field(min_length=1, max_length=MAX_TOPIC_MEMORY_STAGE_ITEMS)


class TopicMemoryPlannerOutput(_TopicMemoryStageModel):
    items: tuple[TopicMemoryPlanItem, ...] = Field(min_length=1, max_length=MAX_TOPIC_MEMORY_STAGE_ITEMS)


class TopicMemoryEvolveInput(_TopicMemoryStageModel):
    work_id: str = Field(min_length=1, max_length=64)
    evidence: tuple[TopicMemoryEvidence, ...] = ()
    temporary: tuple[TopicMemoryProposal, ...] = Field(default=(), max_length=MAX_TOPIC_MEMORY_STAGE_ITEMS)
    historical: TopicMemoryHistoricalSlot | None = None

    @model_validator(mode="after")
    def require_new_material(self):
        if not self.evidence and not self.temporary:
            raise ValueError("evolve input requires evidence or temporary Topics")  # noqa: TRY003
        return self


class TopicMemoryEvolveOutput(_TopicMemoryStageModel):
    proposal: TopicMemoryProposal | None = None


class TopicMemoryTemporaryInput(_TopicMemoryStageModel):
    work_id: str = Field(min_length=1, max_length=64)
    evidence: tuple[TopicMemoryEvidence, ...] = Field(min_length=1)


class TopicMemoryTemporaryOutput(_TopicMemoryStageModel):
    proposals: tuple[TopicMemoryProposal, ...] = Field(min_length=1, max_length=MAX_TOPIC_MEMORY_STAGE_ITEMS)


class TopicMemoryReconcileInput(_TopicMemoryStageModel):
    component_id: str = Field(min_length=1, max_length=64)
    proposals: tuple[TopicMemoryProposal, ...] = Field(min_length=1, max_length=MAX_TOPIC_MEMORY_STAGE_ITEMS)
    historical: tuple[TopicMemoryHistoricalSlot, ...] = Field(default=(), max_length=MAX_TOPIC_MEMORY_STAGE_ITEMS)


class TopicMemoryReconcileOutput(_TopicMemoryStageModel):
    proposals: tuple[TopicMemoryProposal, ...] = Field(default=(), max_length=MAX_TOPIC_MEMORY_STAGE_ITEMS)


TOPIC_MEMORY_PROBE_INSTRUCTIONS = """Identify up to 20 durable topic probes. Cite only supplied evidence_id values."""
TOPIC_MEMORY_GLOBAL_INSTRUCTIONS = """Evolve the whole Window into at most 20 topics using only opaque ids."""
TOPIC_MEMORY_PLANNER_INSTRUCTIONS = """Partition every probe exactly once into at most 20 bounded work items."""
TOPIC_MEMORY_EVOLVE_INSTRUCTIONS = """Create or revise one topic. Return content and cited opaque evidence only."""
TOPIC_MEMORY_TEMPORARY_INSTRUCTIONS = """Summarize an oversized work item into at most 20 temporary topics."""
TOPIC_MEMORY_RECONCILE_INSTRUCTIONS = """Coordinate related proposals without merging two historical identities."""


__all__ = [
    "MAX_TOPIC_MEMORY_STAGE_ITEMS",
    "TOPIC_MEMORY_EVOLVE_INSTRUCTIONS",
    "TOPIC_MEMORY_GLOBAL_INSTRUCTIONS",
    "TOPIC_MEMORY_PLANNER_INSTRUCTIONS",
    "TOPIC_MEMORY_PROBE_INSTRUCTIONS",
    "TOPIC_MEMORY_RECONCILE_INSTRUCTIONS",
    "TOPIC_MEMORY_TEMPORARY_INSTRUCTIONS",
    "BudgetedTopicMemoryGenerator",
    "TopicMemoryEvidence",
    "TopicMemoryEvolveInput",
    "TopicMemoryEvolveOutput",
    "TopicMemoryGenerationError",
    "TopicMemoryGlobalInput",
    "TopicMemoryGlobalOutput",
    "TopicMemoryHistoricalSlot",
    "TopicMemoryPlanItem",
    "TopicMemoryPlannerInput",
    "TopicMemoryPlannerOutput",
    "TopicMemoryProbe",
    "TopicMemoryProbeCandidates",
    "TopicMemoryProbeInput",
    "TopicMemoryProbeOutput",
    "TopicMemoryProposal",
    "TopicMemoryReconcileInput",
    "TopicMemoryReconcileOutput",
    "TopicMemoryStageBudget",
    "TopicMemoryTemporaryInput",
    "TopicMemoryTemporaryOutput",
    "topic_memory_stage_budget",
    "topic_memory_stage_fixed_prompt",
]
