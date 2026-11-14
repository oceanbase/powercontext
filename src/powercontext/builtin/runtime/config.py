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

"""Validated configuration for one built-in runtime instance."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    PrivateAttr,
    SecretStr,
    field_validator,
    model_validator,
)

from powercontext.builtin.artifacts.memory.prompts import MemoryExtractionProfile
from powercontext.builtin.artifacts.search import RecallChannelWeights
from powercontext.builtin.artifacts.skill import AgentSkillTarget, CodexSkillRoot
from powercontext.builtin.artifacts.topic_memory import MAX_TOPIC_MEMORY_SEARCH_LIMIT
from powercontext.builtin.artifacts.topic_memory.generation import (
    TopicMemoryGenerationError,
    narrow_stage_budget,
    topic_memory_stage_budget,
    validate_topic_memory_stage_capacity,
)
from powercontext.builtin.code.models import CodeConfig
from powercontext.builtin.dream.models import DreamBudget
from powercontext.builtin.inference import character_token_estimator
from powercontext.builtin.inference.budget import (
    GENERATION_OPERATIONS,
    TOPIC_MEMORY_STAGES,
    BudgetProvenance,
    EffectiveRequestBudget,
    EmbeddingBudget,
    resolve_request_budget,
)
from powercontext.builtin.inference.input_encoding import InputEncodingMode
from powercontext.builtin.inference.privacy import InferenceProtectionConfig
from powercontext.builtin.inference.tokens import TokenEstimatorProfile, resolve_token_estimator
from powercontext.builtin.persistence.atomic_memory_index import DEFAULT_ATOMIC_MEMORY_RELATED_PAGE_SIZE
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime._scope_cache import DEFAULT_SCOPE_CACHE_SIZE
from powercontext.builtin.runtime.decision_policy import DecisionPolicyMode, DecisionPrivacyBoundary

_HTTP_FIELD_NAME_PATTERN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
InputEncodingOperation = Literal[
    "atomic_memory.extract", "atomic_memory.reconcile", "experience.generate", "experience.incubate"
]
_RECALL_GATE_BASE_MIN_SEMANTIC_SIMILARITY = 0.3
_RECALL_GATE_ROUND1_ORDER_ERROR = (
    "recall_gate_round1_min_semantic_similarity must be less than or equal to the round-zero floor"
)
_RECALL_GATE_ROUND2_ORDER_ERROR = (
    "recall_gate_round2_min_semantic_similarity must be less than or equal to "
    "recall_gate_round1_min_semantic_similarity"
)


def _equal_numeric_aliases(left: Any, right: Any) -> bool:
    if left is None or right is None or isinstance(left, bool) or isinstance(right, bool):
        return False
    try:
        return float(left) == float(right)
    except (ValueError, TypeError, OverflowError):
        return False


if TYPE_CHECKING:
    from powercontext.builtin.inference.pydantic_ai import InferenceLimits


class RuntimeConfig(BaseModel):
    """Built-in runtime policy and scheduler configuration."""

    model_config = ConfigDict(extra="forbid")
    _processing_aliases_used: tuple[str, ...] = PrivateAttr(default=())

    @model_validator(mode="wrap")
    @classmethod
    def record_processing_aliases(cls, value: Any, handler: Any) -> Any:
        result = handler(value)
        if isinstance(value, Mapping):
            result._processing_aliases_used = tuple(
                name
                for name in (
                    "schedule_seconds",
                    "profile_max_concurrency",
                    "artifact_processing_max_workers",
                    "artifact_processing_worker_timeout_seconds",
                )
                if name in value
            )
        return result

    @model_validator(mode="before")
    @classmethod
    def normalize_processing_aliases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        for old, new in (
            ("schedule_seconds", "memory_schedule_seconds"),
            ("profile_max_concurrency", "profile_max_workers"),
            ("artifact_processing_max_workers", "topic_memory_max_workers"),
            ("artifact_processing_worker_timeout_seconds", "topic_memory_worker_timeout_seconds"),
        ):
            if (
                old in normalized
                and new in normalized
                and (
                    normalized[old] != normalized[new]
                    and str(normalized[old]) != str(normalized[new])
                    and not _equal_numeric_aliases(normalized[old], normalized[new])
                )
            ):
                raise ValueError(f"conflicting artifact processing settings: {old} and {new}")  # noqa: TRY003
            if old in normalized:
                normalized[new] = normalized[old]
            elif new in normalized:
                normalized[old] = normalized[new]
        return normalized

    @field_validator(
        "memory_max_workers",
        "topic_memory_max_workers",
        "experience_max_workers",
        "skill_max_workers",
        "handoff_max_workers",
        "prompt_max_workers",
        "profile_max_workers",
        "profile_max_concurrency",
        "artifact_processing_max_workers",
        mode="before",
    )
    @classmethod
    def reject_boolean_worker_quota(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("Worker quota must be a positive integer")  # noqa: TRY003, TRY004 - Pydantic validation
        return value

    scope_cache_size: int = Field(default=DEFAULT_SCOPE_CACHE_SIZE, ge=1)
    source_window_limit: int = Field(default=100, ge=1)
    context_assembly_max_entries: int = Field(default=8, ge=1)
    memory_extraction_profile: MemoryExtractionProfile = MemoryExtractionProfile.CODING
    memory_rerank_enabled: bool = False
    memory_rerank_candidate_limit: int = Field(default=30, ge=1, le=100)
    decision_assistance_enabled: bool = False
    memory_write_gate_enabled: bool = False
    memory_write_gate_mode: DecisionPolicyMode = DecisionPolicyMode.SHADOW
    memory_write_gate_privacy_boundary: DecisionPrivacyBoundary = DecisionPrivacyBoundary.NO_EXTERNAL_CALL
    memory_write_gate_sanitizer: Literal["none", "known_secrets"] = "none"
    # Direction only: which verdict means "evidence is insufficient". The strength threshold
    # stays unset until a calibration probe establishes it, so a hold never depends on a made-up
    # number.
    memory_write_gate_hold_on: Literal["yes", "no"] = "yes"
    memory_write_gate_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    decision_observation_retention_days: int | None = Field(default=None, ge=1, le=3650)
    memory_max_active_entries: int = Field(default=5_000, ge=1, le=100_000)
    memory_max_manifest_entries: int = Field(default=10_000, ge=1, le=200_000)
    memory_max_manifest_bytes: int = Field(default=4_194_304, ge=1_024, le=67_108_864)
    memory_compaction_enabled: bool = False
    memory_compaction_min_tombstone_revisions: int = Field(default=10, ge=0)
    memory_max_history_revisions: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def validate_memory_capacity_order(self) -> RuntimeConfig:
        if self.memory_max_active_entries > self.memory_max_manifest_entries:
            raise ValueError("memory_max_active_entries cannot exceed memory_max_manifest_entries")  # noqa: TRY003
        return self

    recall_gate_enabled: bool = False
    recall_gate_max_rounds: int = Field(default=2, ge=0, le=2)
    recall_gate_min_candidates: int = Field(default=2, ge=1)
    recall_gate_min_top_score: float = Field(default=0.35, ge=0.0, le=1.0)
    recall_gate_min_top_gap: float = Field(default=0.02, ge=0.0, le=1.0)
    recall_gate_min_lexical_overlap: float = Field(default=0.5, ge=0.0, le=1.0)
    recall_gate_round1_min_semantic_similarity: float = Field(default=0.15, ge=0.0, le=1.0)
    recall_gate_round2_min_semantic_similarity: float = Field(default=0.10, ge=0.0, le=1.0)
    recall_gate_allow_with_rerank: bool = False
    recall_fts_weight: float = Field(default=1.0, ge=0.0, allow_inf_nan=False)
    recall_vector_weight: float = Field(default=1.0, ge=0.0, allow_inf_nan=False)
    # Budget for waiting on query embeddings in one context preparation. The Atomic Memory searches of the Scope and
    # each context reference and the Topic Memory search share it, and only their waits are charged, so database reads
    # and reranking in between do not take a later search's share; a search that finds it spent continues with lexical
    # matching only. The default covers a typical remote embedding round trip and leaves the rest of a one-second Hook
    # request to the other work.
    recall_embedding_timeout_seconds: float = Field(default=0.5, gt=0.0, le=60.0, allow_inf_nan=False)
    profile_schedule_enabled: bool = False
    profile_cron: str = "0 2 * * *"
    profile_timezone: str = "Asia/Shanghai"
    profile_max_concurrency: int = Field(default=4, ge=1)
    profile_max_sources_per_window: int = Field(default=32, ge=1, le=32)

    @model_validator(mode="after")
    def validate_profile_schedule(self):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        from apscheduler.triggers.cron import CronTrigger

        try:
            timezone = ZoneInfo(self.profile_timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("invalid Profile schedule timezone") from error  # noqa: TRY003
        CronTrigger.from_crontab(self.profile_cron, timezone=timezone)
        return self

    @model_validator(mode="after")
    def validate_recall_gate_threshold_order(self):
        if not self.recall_gate_enabled:
            return self
        if self.recall_gate_round1_min_semantic_similarity > _RECALL_GATE_BASE_MIN_SEMANTIC_SIMILARITY:
            raise ValueError(_RECALL_GATE_ROUND1_ORDER_ERROR)
        if self.recall_gate_round2_min_semantic_similarity > self.recall_gate_round1_min_semantic_similarity:
            raise ValueError(_RECALL_GATE_ROUND2_ORDER_ERROR)
        return self

    @field_validator("recall_embedding_timeout_seconds", mode="before")
    @classmethod
    def reject_boolean_recall_embedding_timeout(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("recall embedding timeout must be a number")  # noqa: TRY003, TRY004 - Pydantic validation
        return value

    @field_validator("atomic_memory_related_page_size", mode="before")
    @classmethod
    def reject_boolean_related_page_size(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("atomic memory related page sizes must be integers")  # noqa: TRY003, TRY004 - Pydantic validation
        return value

    @field_validator("recall_fts_weight", "recall_vector_weight", mode="before")
    @classmethod
    def reject_boolean_recall_weight(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("recall channel weights must be numbers")  # noqa: TRY003, TRY004 - Pydantic validation
        return value

    @model_validator(mode="after")
    def validate_recall_channel_weights(self) -> RuntimeConfig:
        RecallChannelWeights(fts=self.recall_fts_weight, vector=self.recall_vector_weight)
        return self

    @property
    def recall_channel_weights(self) -> RecallChannelWeights:
        """Return the deployment's normalized FTS/vector recall ratio."""

        return RecallChannelWeights(fts=self.recall_fts_weight, vector=self.recall_vector_weight)

    schedule_seconds: float | None = Field(default=None, gt=0)
    memory_schedule_seconds: float | None = Field(default=None, gt=0)
    experience_schedule_seconds: float | None = Field(default=None, gt=0)
    dream_enabled: bool = True
    dream_max_pending_per_scope: int = Field(default=32, ge=1, le=1000)
    atomic_memory_related_mode: Literal["auto", "fts", "vector", "hybrid"] = "auto"
    atomic_memory_related_max_distance: float = Field(default=1.0, ge=0)
    atomic_memory_related_page_size: int = Field(default=DEFAULT_ATOMIC_MEMORY_RELATED_PAGE_SIZE, ge=1, le=1000)
    atomic_memory_related_fts_fallback: bool = False
    atomic_memory_comparison_batch_size: int = Field(default=20, ge=1)
    atomic_memory_input_tokens_limit: int = Field(default=24000, ge=1)
    atomic_memory_restore_retry_budget: int = Field(default=3, ge=0, le=10)
    atomic_memory_preview_signing_secret: SecretStr | None = Field(default=None, repr=False, min_length=32)
    atomic_memory_preview_signing_key_id: str = Field(default="atomic-memory-v1", min_length=1, max_length=128)
    atomic_memory_preview_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    generation_concurrency: int = Field(default=4, ge=1, le=64)
    dream_budget: DreamBudget = Field(default_factory=DreamBudget)
    topic_memory_schedule_seconds: float | None = Field(default=None, gt=0)
    topic_memory_source_window_limit: int = Field(default=10, ge=1)
    topic_memory_history_max_candidates: int = Field(default=20, ge=1, le=MAX_TOPIC_MEMORY_SEARCH_LIMIT)
    topic_memory_history_rrf_threshold: int = Field(default=70, ge=0, le=100)
    topic_memory_history_min_candidates: int = Field(default=5, ge=1, le=MAX_TOPIC_MEMORY_SEARCH_LIMIT)
    artifact_processing_max_workers: int = Field(default=10, ge=1)
    artifact_processing_worker_timeout_seconds: float = Field(default=600, gt=0)
    artifact_processing_role: Literal["all", "api", "background"] = "all"
    artifact_processing_supervisor_mode: Literal["global", "dedicated"] = "global"
    artifact_processing_families: tuple[str, ...] | None = None
    memory_max_workers: int = Field(default=1, ge=1)
    topic_memory_max_workers: int = Field(default=10, ge=1)
    experience_max_workers: int = Field(default=1, ge=1)
    skill_max_workers: int = Field(default=1, ge=1)
    handoff_max_workers: int = Field(default=1, ge=1)
    prompt_max_workers: int = Field(default=1, ge=1)
    profile_max_workers: int = Field(default=4, ge=1)
    memory_worker_timeout_seconds: float = Field(default=600, gt=0)
    topic_memory_worker_timeout_seconds: float = Field(default=600, gt=0)
    experience_worker_timeout_seconds: float = Field(default=600, gt=0)
    skill_worker_timeout_seconds: float = Field(default=600, gt=0)
    handoff_worker_timeout_seconds: float = Field(default=600, gt=0)
    prompt_worker_timeout_seconds: float = Field(default=600, gt=0)
    profile_worker_timeout_seconds: float = Field(default=600, gt=0)
    model_usage_queue_capacity: int = Field(default=256, ge=1, le=16_384)
    model_usage_write_timeout_seconds: float = Field(default=1.0, gt=0, le=30)
    model_usage_flush_timeout_seconds: float = Field(default=0.5, gt=0, le=30)

    @model_validator(mode="after")
    def validate_topic_memory_history_candidates(self) -> RuntimeConfig:
        if self.topic_memory_history_min_candidates > self.topic_memory_history_max_candidates:
            raise ValueError(  # noqa: TRY003
                "topic_memory_history_min_candidates must not exceed topic_memory_history_max_candidates"
            )
        families = self.artifact_processing_families
        if families is not None and (
            len(set(families)) != len(families) or any(not family or family != family.strip() for family in families)
        ):
            raise ValueError("artifact_processing_families must contain unique, nonempty Family names")  # noqa: TRY003
        return self


class HandoffReportConfig(BaseModel):
    """Optional Handoff Report feature registration."""

    enabled: bool = True


def _reject_boolean_count(value: Any) -> Any:
    """Reject booleans, which Pydantic would otherwise coerce to ``1``."""

    if isinstance(value, bool):
        # Pydantic only converts ValueError into a validation error here.
        raise ValueError("a count or token limit must be an integer, not a boolean")  # noqa: TRY003, TRY004
    return value


_PositiveCount = Annotated[int, BeforeValidator(_reject_boolean_count), Field(ge=1)]
_OptionalPositiveCount = Annotated[int, BeforeValidator(_reject_boolean_count), Field(ge=1)] | None
_NonNegativeCount = Annotated[int, BeforeValidator(_reject_boolean_count), Field(ge=0)]


def _narrowest(*values: int | None) -> int | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _budget_source(operation: str | None, deployment_value: Any, operation_value: Any) -> str:
    if operation_value is not None:
        return f"generation_operation_budgets.{operation}"
    if deployment_value is not None:
        return "generation_request_budget"
    return "default"


class GenerationRequestBudgetConfig(BaseModel):
    """Deployment-declared capacity for the generation endpoint.

    ``context_semantics`` says whether the endpoint shares one context window
    between input and output or limits them separately. ``input_tokens_limit``
    and ``output_tokens_limit`` describe the endpoint itself; per-operation
    allowances live in ``generation_operation_budgets``.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    context_semantics: Literal["joint_window", "separate_limits"] = "joint_window"
    input_tokens_limit: _OptionalPositiveCount = None
    output_tokens_limit: _OptionalPositiveCount = None
    output_reserve_tokens: _OptionalPositiveCount = None
    safety_margin_ratio: float = Field(default=0.05, ge=0, lt=1)
    safety_margin_tokens: _NonNegativeCount = 0
    estimator_id: str = Field(default="character:weighted", min_length=1, max_length=128)
    estimator_version: str = Field(default="1", min_length=1, max_length=64)

    @model_validator(mode="after")
    def _validate_estimator(self) -> Self:
        resolve_token_estimator(self.token_estimator)
        return self

    @property
    def token_estimator(self) -> TokenEstimatorProfile:
        """Return the declared estimator identity this build can run."""

        return TokenEstimatorProfile(estimator_id=self.estimator_id, version=self.estimator_version)


class GenerationOperationBudgetConfig(BaseModel):
    """Allowance one registered generation operation may use.

    Every field is optional: an unset field simply does not narrow the resolved
    deployment budget.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    input_tokens_limit: _OptionalPositiveCount = None
    output_tokens_limit: _OptionalPositiveCount = None
    max_requests: _OptionalPositiveCount = None
    timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)


def _validate_endpoint_capacity(
    label: str,
    budget: GenerationRequestBudgetConfig | None,
    context_window_tokens: int | None,
    model: str | None,
) -> None:
    """Require both halves of a separate endpoint's declared capacity."""

    if (budget is None) != (context_window_tokens is None):
        raise ValueError(  # noqa: TRY003
            f"{label}_request_budget and {label}_context_window_tokens must be declared together"
        )
    if budget is not None and model is None:
        raise ValueError(f"{label}_request_budget requires {label}_model")  # noqa: TRY003


class InferenceConfig(BaseModel):
    """Optional generation, embedding, and LLM reranking configuration."""

    model_config = ConfigDict(hide_input_in_errors=True)

    protection: InferenceProtectionConfig = Field(default_factory=InferenceProtectionConfig)
    atomic_memory_input_encoding: InputEncodingMode = "off"
    input_encoding: dict[InputEncodingOperation, InputEncodingMode] = Field(default_factory=dict)

    generation_model: str | None = None
    generation_base_url: AnyHttpUrl | None = None
    generation_headers: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    generation_model_settings: dict[str, JsonValue] = Field(default_factory=dict)
    generation_timeout_seconds: float = Field(default=30.0, gt=0)
    generation_max_requests: int = Field(default=2, ge=1)
    generation_streaming: Literal["auto", "disabled"] = "disabled"
    generation_stream_idle_timeout_seconds: float = Field(default=60.0, gt=0)
    generation_model_context_window_tokens: int = Field(default=125_000, ge=1)
    generation_request_budget: GenerationRequestBudgetConfig = Field(default_factory=GenerationRequestBudgetConfig)
    generation_operation_budgets: dict[str, GenerationOperationBudgetConfig] = Field(default_factory=dict)
    embedding_model: str | None = None
    embedding_base_url: AnyHttpUrl | None = None
    embedding_headers: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    embedding_model_settings: dict[str, JsonValue] = Field(default_factory=dict)
    embedding_profile_id: str | None = None
    embedding_dimension: int | None = Field(default=None, ge=1)
    embedding_send_dimensions: bool = True
    embedding_normalization: Literal["none", "unit"] = "unit"
    embedding_timeout_seconds: float = Field(default=30.0, gt=0)
    embedding_batch_size: int = Field(default=10, ge=1)
    embedding_input_tokens_per_text_limit: _OptionalPositiveCount = None
    embedding_input_tokens_per_batch_limit: _OptionalPositiveCount = None
    embedding_batch_items_limit: _OptionalPositiveCount = None
    embedding_estimator: str = "character:weighted"
    rerank_model: str | None = None
    rerank_base_url: AnyHttpUrl | None = None
    rerank_headers: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    rerank_model_settings: dict[str, JsonValue] = Field(default_factory=dict)
    rerank_timeout_seconds: float | None = Field(default=None, gt=0)
    rerank_max_requests: int | None = Field(default=None, ge=1)
    decision_model: str | None = None
    decision_base_url: AnyHttpUrl | None = None
    decision_headers: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    decision_model_settings: dict[str, JsonValue] = Field(default_factory=dict)
    decision_timeout_seconds: float | None = Field(default=None, gt=0)
    decision_context_window_tokens: int | None = Field(default=None, ge=1)
    decision_request_budget: GenerationRequestBudgetConfig | None = None
    rerank_context_window_tokens: int | None = Field(default=None, ge=1)
    rerank_request_budget: GenerationRequestBudgetConfig | None = None
    decision_max_requests: int | None = Field(default=None, ge=1)

    def input_encoding_mode(self, operation: InputEncodingOperation) -> InputEncodingMode:
        """Explicit stage settings override the legacy Atomic Memory default."""
        if operation in self.input_encoding:
            return self.input_encoding[operation]
        if operation in {"atomic_memory.extract", "atomic_memory.reconcile"}:
            return self.atomic_memory_input_encoding
        return "off"

    @model_validator(mode="after")
    def _validate_generation_budgets(self) -> Self:
        budget = self.generation_request_budget
        for operation, allowance in self.generation_operation_budgets.items():
            if operation not in GENERATION_OPERATIONS:
                raise ValueError(  # noqa: TRY003
                    f"generation_operation_budgets has an unknown generation operation: {operation}"
                )
            self._reject_widening(operation, allowance)
        settings_max_tokens = self.generation_model_settings.get("max_tokens")
        if isinstance(settings_max_tokens, int) and not isinstance(settings_max_tokens, bool):
            declared = budget.output_tokens_limit
            if declared is not None and settings_max_tokens != declared:
                raise ValueError(  # noqa: TRY003
                    "generation_model_settings.max_tokens must match generation_request_budget.output_tokens_limit"
                )
        # Resolve the deployment capacity once so an unexecutable configuration
        # fails at startup rather than on the first request.
        self._resolve_generation_budget(None, None)
        return self

    def _reject_widening(self, label: str, allowance: GenerationOperationBudgetConfig) -> None:
        budget = self.generation_request_budget
        if (
            allowance.input_tokens_limit is not None
            and budget.input_tokens_limit is not None
            and allowance.input_tokens_limit > budget.input_tokens_limit
        ):
            raise ValueError(  # noqa: TRY003
                f"{label}.input_tokens_limit must not exceed generation_request_budget.input_tokens_limit"
            )
        if (
            allowance.output_tokens_limit is not None
            and budget.output_tokens_limit is not None
            and allowance.output_tokens_limit > budget.output_tokens_limit
        ):
            raise ValueError(  # noqa: TRY003
                f"{label}.output_tokens_limit must not exceed generation_request_budget.output_tokens_limit"
            )
        if allowance.max_requests is not None and allowance.max_requests > self.generation_max_requests:
            raise ValueError(f"{label}.max_requests must not exceed generation_max_requests")  # noqa: TRY003
        if allowance.timeout_seconds is not None and allowance.timeout_seconds > self.generation_timeout_seconds:
            raise ValueError(f"{label}.timeout_seconds must not exceed generation_timeout_seconds")  # noqa: TRY003

    def generation_budget(self, operation: str) -> EffectiveRequestBudget:
        """Resolve deployment capacity and one operation allowance into a budget."""

        if operation not in GENERATION_OPERATIONS:
            raise ValueError(f"unknown generation operation: {operation}")  # noqa: TRY003
        return self._resolve_generation_budget(operation, self.generation_operation_budgets.get(operation))

    def generation_limits(self, operation: str) -> InferenceLimits:
        """Resolve execution limits in the units used by one Agent run."""

        from powercontext.builtin.inference.pydantic_ai import InferenceLimits

        if operation not in GENERATION_OPERATIONS:
            raise ValueError(f"unknown generation operation: {operation}")  # noqa: TRY003
        allowance = self.generation_operation_budgets.get(operation)
        requests = self.generation_max_requests
        timeout = self.generation_timeout_seconds
        if operation == "memory.rerank":
            requests = self.rerank_max_requests or requests
            timeout = self.rerank_timeout_seconds or timeout
        elif operation == "memory.write-gate":
            requests = self.decision_max_requests or requests
            timeout = self.decision_timeout_seconds or timeout
        return InferenceLimits(
            max_requests=min(requests, allowance.max_requests or requests) if allowance is not None else requests,
            timeout_seconds=(min(timeout, allowance.timeout_seconds or timeout) if allowance is not None else timeout),
        )

    @property
    def decision_budget(self) -> EffectiveRequestBudget | None:
        """Resolve admission capacity for the memory write-gate workload.

        The gate can run on its own endpoint. When the deployment declares that
        endpoint's capacity, the gate admits against it; otherwise the workload keeps
        no budget instead of borrowing the generation window, whose size may differ.
        """

        if self.decision_request_budget is None:
            return None
        return self._resolve_generation_budget(
            "memory.write-gate",
            self.generation_operation_budgets.get("memory.write-gate"),
            declared=self.decision_request_budget,
            context_window_tokens=self.decision_context_window_tokens,
            context_window_source="decision_context_window_tokens",
        )

    @property
    def rerank_budget(self) -> EffectiveRequestBudget | None:
        """Resolve admission capacity for the rerank workload's demonstration prompt.

        A separate rerank endpoint can have a different window, so its capacity is
        declared independently; without it the demonstrations keep no budget.
        """

        if self.rerank_request_budget is None:
            return None
        return self._resolve_generation_budget(
            "prompt_demonstrations",
            self.generation_operation_budgets.get("prompt_demonstrations"),
            declared=self.rerank_request_budget,
            context_window_tokens=self.rerank_context_window_tokens,
            context_window_source="rerank_context_window_tokens",
        )

    @property
    def generation_deployment_budget(self) -> EffectiveRequestBudget:
        """Resolve deployment capacity for workloads bounded per run, not per operation.

        Dream runs are metered by ``DreamBudget`` (calls, output tokens, deadline) rather
        than by a per-operation allowance, so they admit against the deployment capacity
        only.
        """

        return self._resolve_generation_budget(None, None)

    @field_validator("embedding_estimator")
    @classmethod
    def validate_embedding_estimator(cls, value: str) -> str:
        """Reject an estimator identity this build cannot run before any request."""

        resolve_token_estimator(TokenEstimatorProfile(estimator_id=value, version="1"))
        return value

    @property
    def embedding_budget(self) -> EmbeddingBudget:
        """Resolve the capacity one embedding batch must fit into.

        An unset dimension stays unset: the deployment declares only the limits it
        knows, and the adapter keeps its item-count batching as the baseline.
        """

        estimator = resolve_token_estimator(TokenEstimatorProfile(estimator_id=self.embedding_estimator, version="1"))
        return EmbeddingBudget(
            max_input_tokens_per_text=self.embedding_input_tokens_per_text_limit,
            max_input_tokens_per_batch=self.embedding_input_tokens_per_batch_limit,
            max_batch_items=self.embedding_batch_items_limit,
            estimator=estimator.profile,
        )

    def _resolve_generation_budget(
        self,
        operation: str | None,
        allowance: GenerationOperationBudgetConfig | None,
        *,
        declared: GenerationRequestBudgetConfig | None = None,
        context_window_tokens: int | None = None,
        context_window_source: str = "generation_model_context_window_tokens",
    ) -> EffectiveRequestBudget:
        budget = self.generation_request_budget if declared is None else declared
        operation_input = allowance.input_tokens_limit if allowance is not None else None
        operation_output = allowance.output_tokens_limit if allowance is not None else None
        explicit = budget.model_fields_set
        provenance = BudgetProvenance(
            context_window=context_window_source,
            input_limit=_budget_source(operation, budget.input_tokens_limit, operation_input),
            output_limit=_budget_source(operation, budget.output_tokens_limit, operation_output),
            output_reserve="generation_request_budget" if "output_reserve_tokens" in explicit else "default",
            safety_margin=self._safety_margin_source(budget, explicit),
            estimator=f"{budget.estimator_id}@{budget.estimator_version}",
        )
        return resolve_request_budget(
            estimator=budget.token_estimator,
            provenance=provenance,
            context_semantics=budget.context_semantics,
            context_window_tokens=(
                self.generation_model_context_window_tokens if context_window_tokens is None else context_window_tokens
            ),
            input_tokens_limit=_narrowest(budget.input_tokens_limit, operation_input),
            output_tokens_limit=_narrowest(budget.output_tokens_limit, operation_output),
            output_reserve_tokens=budget.output_reserve_tokens,
            safety_margin_ratio=budget.safety_margin_ratio,
            safety_margin_tokens=budget.safety_margin_tokens,
        )

    def _safety_margin_source(self, budget: GenerationRequestBudgetConfig, explicit: set[str]) -> str:
        if "safety_margin_tokens" in explicit and budget.safety_margin_tokens > 0:
            return "generation_request_budget.absolute"
        if "safety_margin_ratio" in explicit:
            return "generation_request_budget.ratio"
        return "default"

    @field_validator("generation_model", "embedding_model", "embedding_profile_id", "rerank_model", "decision_model")
    @classmethod
    def validate_optional_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("inference identifiers must not be empty")  # noqa: TRY003
        return normalized

    @field_validator("embedding_normalization", mode="before")
    @classmethod
    def validate_normalization(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if normalized not in {"none", "unit"}:
            raise ValueError("embedding normalization must be 'none' or 'unit'")  # noqa: TRY003
        return normalized

    @field_validator("generation_headers", "embedding_headers", "rerank_headers", "decision_headers")
    @classmethod
    def validate_headers(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        normalized_names: set[str] = set()
        for name, secret in value.items():
            normalized_name = name.casefold()
            if _HTTP_FIELD_NAME_PATTERN.fullmatch(name) is None:
                raise ValueError("inference header names must be non-empty HTTP field names")  # noqa: TRY003
            if normalized_name in normalized_names:
                raise ValueError("inference header names must be unique ignoring case")  # noqa: TRY003
            if not secret.get_secret_value():
                raise ValueError("inference header values must not be empty")  # noqa: TRY003
            normalized_names.add(normalized_name)
        return value

    @field_validator(
        "generation_model_settings",
        "embedding_model_settings",
        "rerank_model_settings",
        "decision_model_settings",
    )
    @classmethod
    def reserve_headers_field(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if "extra_headers" in value:
            raise ValueError(  # noqa: TRY003
                "configure credentials and static headers through the dedicated headers field"
            )
        return value

    @model_validator(mode="after")
    def validate_embedding_profile(self) -> Self:
        values = (self.embedding_model, self.embedding_profile_id, self.embedding_dimension)
        if any(value is not None for value in values) and not all(value is not None for value in values):
            raise ValueError(  # noqa: TRY003
                "embedding_model, embedding_profile_id, and embedding_dimension must be configured together"
            )
        return self

    @model_validator(mode="after")
    def validate_workload_overrides(self) -> Self:
        if self.generation_model is None and self.generation_model_settings:
            raise ValueError("generation_model_settings requires generation_model")  # noqa: TRY003
        if self.generation_model is None and (self.generation_base_url is not None or self.generation_headers):
            raise ValueError("generation overrides require generation_model")  # noqa: TRY003
        if self.embedding_model is None and (
            self.embedding_base_url is not None or self.embedding_headers or self.embedding_model_settings
        ):
            raise ValueError("embedding overrides require a complete embedding profile")  # noqa: TRY003
        if self.rerank_base_url is not None and self.rerank_model is None:
            raise ValueError("rerank_base_url requires rerank_model")  # noqa: TRY003
        if (
            self.rerank_model is None
            and self.generation_model is None
            and (self.rerank_headers or self.rerank_model_settings)
        ):
            raise ValueError("rerank overrides require rerank_model or generation_model")  # noqa: TRY003
        self._validate_decision_overrides()
        max_tokens = self.generation_model_settings.get("max_tokens")
        if max_tokens is not None and (
            not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1
        ):
            raise ValueError("generation_model_settings.max_tokens must be a positive integer")  # noqa: TRY003
        if self.generation_model is not None:
            try:
                budget = topic_memory_stage_budget(
                    context_window_tokens=self.generation_model_context_window_tokens,
                    max_requests=self.generation_max_requests,
                    model_settings=self.generation_model_settings,
                )
                validate_topic_memory_stage_capacity(budget, character_token_estimator())
                for stage in TOPIC_MEMORY_STAGES:
                    narrow_stage_budget(budget, self.generation_budget(f"topic_memory.{stage}"))
            except TopicMemoryGenerationError as error:
                raise ValueError(  # noqa: TRY003
                    f"Topic Memory generation budget is invalid: {error.error_code}"
                ) from error
        return self

    def _validate_decision_overrides(self) -> None:
        """Keep the decision workload's endpoint overrides consistent with its model."""

        _validate_endpoint_capacity(
            "decision",
            self.decision_request_budget,
            self.decision_context_window_tokens,
            self.decision_model,
        )
        _validate_endpoint_capacity(
            "rerank",
            self.rerank_request_budget,
            self.rerank_context_window_tokens,
            self.rerank_model,
        )

        if self.decision_base_url is not None and self.decision_model is None:
            raise ValueError("decision_base_url requires decision_model")  # noqa: TRY003
        if (
            self.decision_model is None
            and self.generation_model is None
            and (self.decision_headers or self.decision_model_settings)
        ):
            raise ValueError("decision overrides require decision_model or generation_model")  # noqa: TRY003


class ExternalSkillsConfig(BaseModel):
    """Explicit host-local targets used by Agent-native Skill providers."""

    host_id: str | None = Field(default=None, min_length=1, max_length=128)
    targets: tuple[AgentSkillTarget, ...] = ()
    codex_roots: tuple[CodexSkillRoot, ...] = ()

    @field_validator("host_id")
    @classmethod
    def validate_host_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or value != value.strip():
            raise ValueError("external Skill host_id must be non-empty and trimmed")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def require_host_for_roots(self) -> ExternalSkillsConfig:
        targets = self.agent_targets
        if targets and self.host_id is None:
            raise ValueError("external Skill host_id is required when Agent targets are configured")  # noqa: TRY003
        target_ids = [target.target_id for target in targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("external Skill Agent target IDs must be unique")  # noqa: TRY003
        return self

    @property
    def agent_targets(self) -> tuple[AgentSkillTarget, ...]:
        """Return unified targets, including legacy Codex root configuration."""

        return (*self.targets, *(root.as_agent_target() for root in self.codex_roots))


DatabaseConfig = SQLiteConfig | OceanBaseConfig | SeekDBConfig


def normalize_database_discriminator(value: Any) -> Any:
    """Use SQLite when a partial database mapping omits its kind."""

    if not isinstance(value, Mapping):
        return value
    database = value.get("database")
    if not isinstance(database, Mapping) or "kind" in database:
        return value
    normalized = dict(value)
    normalized["database"] = {"kind": "sqlite", **database}
    return normalized


class BuiltinConfig(BaseModel):
    """Configuration for one built-in runtime and its database."""

    code: CodeConfig = Field(default_factory=CodeConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    database: DatabaseConfig = Field(default_factory=SQLiteConfig, discriminator="kind")
    handoff_report: HandoffReportConfig = Field(default_factory=HandoffReportConfig)
    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    external_skills: ExternalSkillsConfig = Field(default_factory=ExternalSkillsConfig)

    @model_validator(mode="before")
    @classmethod
    def default_database_to_sqlite(cls, value: Any) -> Any:
        return normalize_database_discriminator(value)

    @model_validator(mode="after")
    def validate_artifact_processing_role(self) -> BuiltinConfig:
        if not isinstance(self.database, OceanBaseConfig) and self.runtime.artifact_processing_role != "all":
            raise ValueError(  # noqa: TRY003
                "runtime.artifact_processing_role must be 'all' for SQLite and embedded seekdb"
            )
        return self


__all__ = [
    "BuiltinConfig",
    "DatabaseConfig",
    "ExternalSkillsConfig",
    "HandoffReportConfig",
    "InferenceConfig",
    "RuntimeConfig",
]
