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

"""Atomic-owned public search controls and deployment policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator, model_validator
from pydantic_core import InitErrorDetails
from typing_extensions import override

from powercontext.artifacts import ArtifactSearchQuery, ArtifactSearchUnsupported
from powercontext.artifacts.fusion import FusionSelection, RrfParameters, parse_fusion_parameters
from powercontext.builtin.artifacts.search import AdmissionFloor, LexicalSearchAdmission
from powercontext.builtin.records import InvalidBaseAccessRequestError
from powercontext.builtin.tags import TagFilter, normalize_tags

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory import EmbeddingProfile
    from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexCapabilities

AtomicSearchMode = Literal["text", "vector", "hybrid"]
ATOMIC_MODE_CHANNELS: dict[AtomicSearchMode, tuple[str, ...]] = {
    "text": ("text",),
    "vector": ("vector",),
    "hybrid": ("text", "vector"),
}


class AtomicSearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: str | None = Field(default=None, min_length=1, max_length=128)
    tags: tuple[str, ...] | None = None
    tag_match: Literal["all", "any"] | None = None

    @field_validator("*", mode="after")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("omit the field instead of sending null")  # noqa: TRY003
        return value

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("kind must be nonblank")  # noqa: TRY003
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        try:
            normalize_tags(value, maximum=16, allow_empty=False)
        except InvalidBaseAccessRequestError:
            raise ValueError("tags must contain distinct valid labels") from None  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def validate_tag_match(self) -> AtomicSearchFilters:
        if self.tag_match is not None and self.tags is None:
            raise atomic_search_error(("tag_match",), "tag_match requires tags")
        return self

    def as_tag_filter(self) -> TagFilter | None:
        return None if self.tags is None else TagFilter(tags=self.tags, match=self.tag_match or "all")


class AtomicSearchAdmission(LexicalSearchAdmission):
    min_semantic_similarity: float = Field(default=0.3, ge=-1, le=1, allow_inf_nan=False)

    @override
    def as_floor(self) -> AdmissionFloor:
        return AdmissionFloor(self.lexical_coverage, self.lexical_min_matched_terms, self.min_semantic_similarity)


class AtomicArtifactSearchRequest(ArtifactSearchQuery):
    """Bounded Atomic search; reranking follows the configured deployment policy."""

    limit: StrictInt = Field(default=10, ge=1, le=100)
    mode: AtomicSearchMode = "text"
    filters: AtomicSearchFilters = Field(default_factory=AtomicSearchFilters)
    admission: AtomicSearchAdmission = Field(default_factory=AtomicSearchAdmission)
    fusion: FusionSelection | None = None
    min_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


def atomic_search_error(path: tuple[str | int, ...], message: str) -> ValidationError:
    return ValidationError.from_exception_data(
        "AtomicArtifactSearchRequest",
        [{"type": "value_error", "loc": path, "input": None, "ctx": {"error": ValueError(message)}}],
    )


@dataclass(frozen=True)
class AtomicSearchPlan:
    mode: AtomicSearchMode
    fusion: RrfParameters


def atomic_fusion_parameters(selection: FusionSelection | None) -> RrfParameters:
    if selection is not None and selection.method != "rrf":
        raise atomic_search_error(("fusion", "method"), "supported fusion method is 'rrf'")
    try:
        params = RrfParameters() if selection is None else parse_fusion_parameters(selection)
    except ValidationError as exc:
        errors: list[InitErrorDetails] = []
        for error in exc.errors(include_url=False):
            detail: InitErrorDetails = {
                "type": error["type"],
                "loc": ("fusion", "params", *error["loc"]),
                "input": None,
            }
            if "ctx" in error:
                detail["ctx"] = error["ctx"]
            errors.append(detail)
        raise ValidationError.from_exception_data("AtomicArtifactSearchRequest", errors) from None
    return params


def plan_atomic_search(  # noqa: C901
    request: AtomicArtifactSearchRequest,
    capabilities: AtomicMemoryIndexCapabilities,
    *,
    embedding_profile: EmbeddingProfile | None,
) -> AtomicSearchPlan:
    params = atomic_fusion_parameters(request.fusion)
    if request.mode == "text" and "min_semantic_similarity" in request.admission.model_fields_set:
        raise atomic_search_error(
            ("admission", "min_semantic_similarity"), "semantic admission requires vector or hybrid mode"
        )
    if request.mode == "vector":
        for field in ("lexical_coverage", "lexical_min_matched_terms"):
            if field in request.admission.model_fields_set:
                raise atomic_search_error(("admission", field), "lexical admission requires text or hybrid mode")
    enabled = ATOMIC_MODE_CHANNELS[request.mode]
    for name in params.weights:
        if name not in enabled:
            raise atomic_search_error(
                ("fusion", "params", "weights", name), f"enabled channels are {', '.join(enabled)}"
            )
    if not any(params.weights.get(name, 1.0) > 0 for name in enabled):
        raise atomic_search_error(("fusion", "params", "weights"), "enabled channels must have positive total weight")
    if request.mode in {"text", "hybrid"} and not capabilities.fts:
        raise ArtifactSearchUnsupported("atomic-memory", field="mode")
    if request.mode == "hybrid" and not capabilities.hybrid:
        raise ArtifactSearchUnsupported("atomic-memory", field="mode")
    if request.mode in {"vector", "hybrid"}:
        profile = capabilities.embedding_profile
        if (
            not capabilities.vector
            or profile is None
            or embedding_profile != profile
            or profile.normalization != "unit"
            or profile.distance != "l2"
        ):
            raise ArtifactSearchUnsupported("atomic-memory", field="mode")
    if request.filters.tags is not None and not capabilities.tag_filter:
        raise ArtifactSearchUnsupported("atomic-memory", field="filters.tags")
    return AtomicSearchPlan(request.mode, params)
