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

"""Topic-owned public mode, capability, and fusion parameter policy."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError
from pydantic_core import InitErrorDetails

from powercontext.artifacts.fusion import FusionSelection, RrfParameters, parse_fusion_parameters
from powercontext.builtin.artifacts.topic_memory.models import (
    TopicArtifactSearchRequest,
    TopicMemoryCapabilities,
    TopicMemoryMatchedBy,
    TopicMemoryUsedSearchMode,
)

TOPIC_MODE_CHANNELS: dict[TopicMemoryUsedSearchMode, tuple[TopicMemoryMatchedBy, ...]] = {
    "fts": ("topic_fts", "detail_fts"),
    "vector": ("topic_vector", "detail_vector"),
    "hybrid": ("topic_fts", "topic_vector", "detail_fts", "detail_vector"),
}


def topic_search_error(path: tuple[str | int, ...], message: str) -> ValidationError:
    return ValidationError.from_exception_data(
        "TopicArtifactSearchRequest",
        [{"type": "value_error", "loc": path, "input": None, "ctx": {"error": ValueError(message)}}],
    )


def topic_fusion_parameters(selection: FusionSelection | None) -> RrfParameters:
    if selection is None:
        return RrfParameters()
    if selection.method != "rrf":
        raise topic_search_error(("fusion", "method"), "supported fusion method is 'rrf'")
    try:
        return parse_fusion_parameters(selection)
    except ValidationError as exc:
        errors: list[InitErrorDetails] = []
        for error in exc.errors(include_url=False):
            detail: InitErrorDetails = {
                "type": error["type"],
                "loc": ("fusion", "params", *error["loc"]),
                "input": error["input"],
            }
            if "ctx" in error:
                detail["ctx"] = error["ctx"]
            errors.append(detail)
        raise ValidationError.from_exception_data("TopicArtifactSearchRequest", errors) from None


def validate_topic_weights(mode: TopicMemoryUsedSearchMode, params: RrfParameters) -> None:
    enabled = TOPIC_MODE_CHANNELS[mode]
    for name in params.weights:
        if name not in enabled:
            raise topic_search_error(
                ("fusion", "params", "weights", name), f"enabled channels are {', '.join(enabled)}"
            )
    if not any(params.weights.get(name, 1.0) > 0 for name in enabled):
        raise topic_search_error(("fusion", "params", "weights"), "enabled channels must have positive total weight")


@dataclass(frozen=True)
class TopicSearchPlan:
    mode: TopicMemoryUsedSearchMode
    fusion: RrfParameters
    requires_vector: bool
    fallback_allowed: bool


def plan_topic_search(
    request: TopicArtifactSearchRequest,
    capabilities: TopicMemoryCapabilities,
    *,
    embedding_available: bool,
) -> TopicSearchPlan:
    params = topic_fusion_parameters(request.fusion)
    for name in params.weights:
        if name not in TOPIC_MODE_CHANNELS["hybrid"]:
            raise topic_search_error(
                ("fusion", "params", "weights", name),
                "supported channels are topic_fts, topic_vector, detail_fts, detail_vector",
            )
    semantic_explicit = "min_semantic_similarity" in request.admission.model_fields_set
    vector_weight_explicit = any(name.endswith("_vector") for name in params.weights)
    lexical_explicit = bool(request.admission.model_fields_set & {"lexical_coverage", "lexical_min_matched_terms"})
    if request.mode == "text" and semantic_explicit:
        raise topic_search_error(
            ("admission", "min_semantic_similarity"), "semantic admission requires vector or hybrid mode"
        )
    if request.mode == "vector" and lexical_explicit:
        field = next(
            name
            for name in ("lexical_coverage", "lexical_min_matched_terms")
            if name in request.admission.model_fields_set
        )
        raise topic_search_error(("admission", field), "lexical admission requires text or hybrid mode")
    requires_vector = request.mode in {"vector", "hybrid"} or semantic_explicit or vector_weight_explicit
    mode: TopicMemoryUsedSearchMode = (
        "fts" if request.mode == "text" else request.mode or ("hybrid" if embedding_available else "fts")
    )
    if request.mode is not None:
        validate_topic_weights(mode, params)
    if requires_vector and (not capabilities.vector or not embedding_available):
        raise topic_search_error(
            ("mode",), "vector and hybrid searches require configured vector indexes and an embedding model"
        )
    if mode in {"fts", "hybrid"} and not capabilities.fts:
        raise topic_search_error(("mode",), "text search is unavailable in this deployment")
    if mode == "hybrid" and not capabilities.hybrid:
        raise topic_search_error(("mode",), "hybrid search is unavailable in this deployment")
    validate_topic_weights(mode, params)
    fallback_allowed = (
        request.mode is None
        and not requires_vector
        and capabilities.fts
        and any(params.weights.get(name, 1.0) > 0 for name in TOPIC_MODE_CHANNELS["fts"])
    )
    return TopicSearchPlan(mode, params, requires_vector, fallback_allowed)
