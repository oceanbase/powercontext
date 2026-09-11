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
from typing import Any, Literal, Self

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PrivateAttr,
    SecretStr,
    field_validator,
    model_validator,
)

from powercontext.builtin.artifacts.memory.prompts import MemoryExtractionProfile
from powercontext.builtin.artifacts.skill import AgentSkillTarget, CodexSkillRoot
from powercontext.builtin.artifacts.topic_memory import MAX_TOPIC_MEMORY_SEARCH_LIMIT
from powercontext.builtin.artifacts.topic_memory.generation import (
    TopicMemoryGenerationError,
    topic_memory_stage_budget,
    validate_topic_memory_stage_capacity,
)
from powercontext.builtin.dream.models import DreamBudget
from powercontext.builtin.inference import character_token_estimator
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime._scope_cache import DEFAULT_SCOPE_CACHE_SIZE

_HTTP_FIELD_NAME_PATTERN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


def _equal_numeric_aliases(left: Any, right: Any) -> bool:
    if left is None or right is None or isinstance(left, bool) or isinstance(right, bool):
        return False
    try:
        return float(left) == float(right)
    except (ValueError, TypeError, OverflowError):
        return False


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

    schedule_seconds: float | None = Field(default=None, gt=0)
    memory_schedule_seconds: float | None = Field(default=None, gt=0)
    experience_schedule_seconds: float | None = Field(default=None, gt=0)
    dream_enabled: bool = True
    dream_max_pending_per_scope: int = Field(default=32, ge=1, le=1000)
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
    profile_max_workers: int = Field(default=4, ge=1)
    memory_worker_timeout_seconds: float = Field(default=600, gt=0)
    topic_memory_worker_timeout_seconds: float = Field(default=600, gt=0)
    experience_worker_timeout_seconds: float = Field(default=600, gt=0)
    skill_worker_timeout_seconds: float = Field(default=600, gt=0)
    profile_worker_timeout_seconds: float = Field(default=600, gt=0)

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


class InferenceConfig(BaseModel):
    """Optional generation, embedding, and LLM reranking configuration."""

    model_config = ConfigDict(hide_input_in_errors=True)

    generation_model: str | None = None
    generation_base_url: AnyHttpUrl | None = None
    generation_headers: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    generation_model_settings: dict[str, JsonValue] = Field(default_factory=dict)
    generation_timeout_seconds: float = Field(default=30.0, gt=0)
    generation_max_requests: int = Field(default=2, ge=1)
    generation_model_context_window_tokens: int = Field(default=125_000, ge=1)
    embedding_model: str | None = None
    embedding_base_url: AnyHttpUrl | None = None
    embedding_headers: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    embedding_model_settings: dict[str, JsonValue] = Field(default_factory=dict)
    embedding_profile_id: str | None = None
    embedding_dimension: int | None = Field(default=None, ge=1)
    embedding_normalization: Literal["none", "unit"] = "unit"
    embedding_timeout_seconds: float = Field(default=30.0, gt=0)
    embedding_batch_size: int = Field(default=10, ge=1)
    rerank_model: str | None = None
    rerank_base_url: AnyHttpUrl | None = None
    rerank_headers: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    rerank_model_settings: dict[str, JsonValue] = Field(default_factory=dict)
    rerank_timeout_seconds: float | None = Field(default=None, gt=0)
    rerank_max_requests: int | None = Field(default=None, ge=1)

    @field_validator("generation_model", "embedding_model", "embedding_profile_id", "rerank_model")
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

    @field_validator("generation_headers", "embedding_headers", "rerank_headers")
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

    @field_validator("generation_model_settings", "embedding_model_settings", "rerank_model_settings")
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
            except TopicMemoryGenerationError as error:
                raise ValueError(  # noqa: TRY003
                    f"Topic Memory generation budget is invalid: {error.error_code}"
                ) from error
        return self


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
