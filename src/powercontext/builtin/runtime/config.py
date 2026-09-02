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

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, JsonValue, SecretStr, field_validator, model_validator

from powercontext.builtin.artifacts.memory.prompts import MemoryExtractionProfile
from powercontext.builtin.artifacts.skill import AgentSkillTarget, CodexSkillRoot
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime._scope_cache import DEFAULT_SCOPE_CACHE_SIZE

_HTTP_FIELD_NAME_PATTERN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


class RuntimeConfig(BaseModel):
    """Built-in runtime policy and scheduler configuration."""

    scope_cache_size: int = Field(default=DEFAULT_SCOPE_CACHE_SIZE, ge=1)
    source_window_limit: int = Field(default=100, ge=1)
    memory_extraction_profile: MemoryExtractionProfile = MemoryExtractionProfile.CODING
    memory_rerank_enabled: bool = False
    memory_rerank_candidate_limit: int = Field(default=30, ge=1, le=100)
    schedule_seconds: float | None = Field(default=None, gt=0)
    experience_schedule_seconds: float | None = Field(default=None, gt=0)


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


__all__ = [
    "BuiltinConfig",
    "DatabaseConfig",
    "ExternalSkillsConfig",
    "HandoffReportConfig",
    "InferenceConfig",
    "RuntimeConfig",
]
