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

"""Strict public Atomic controls and deployment-aware planning."""

import json

import pytest
from pydantic import ValidationError


def test_atomic_request_defaults_and_json_tag_filter() -> None:
    from powercontext.builtin.artifacts.atomic_memory.search import AtomicArtifactSearchRequest

    request = AtomicArtifactSearchRequest.model_validate_json(
        json.dumps({
            "query": "  focused query  ",
            "filters": {"kind": "fact", "tags": ["数据库", "API"], "tag_match": "any"},
        }),
        strict=True,
    )
    assert request.query == "focused query"
    assert request.limit == 10
    assert request.mode == "text"
    assert request.include_scores is False
    assert request.filters.tags == ("数据库", "API")
    tag_filter = request.filters.as_tag_filter()
    assert tag_filter is not None
    assert tag_filter.keys == ("api", "数据库")
    assert tag_filter.match == "any"
    assert request.admission.as_floor().min_semantic_similarity == 0.3


@pytest.mark.parametrize(
    "fields",
    [
        {"limit": 101},
        {"limit": True},
        {"limit": "10"},
        {"mode": "auto"},
        {"query": " "},
        {"query": "x" * 8193},
        {"include_scores": 1},
        {"min_score": True},
        {"min_score": "0.5"},
        {"filters": {"principal": "injected"}},
        {"filters": {"states": ["forgotten"]}},
        {"filters": {"kind": " "}},
        {"filters": {"tags": []}},
        {"filters": {"tags": ["API", "api"]}},
        {"filters": {"tags": [" api"]}},
        {"filters": {"tag_match": "all"}},
        {"admission": {"lexical_coverage": True}},
        {"admission": {"min_semantic_similarity": "0.3"}},
        {"admission": {"lexical_min_matched_terms": 1.0}},
        {"rerank": {}},
        *(
            {field: None}
            for field in ("query", "mode", "limit", "filters", "admission", "fusion", "min_score", "include_scores")
        ),
        {"filters": {"kind": None}},
        {"filters": {"tags": None}},
        {"admission": {"min_semantic_similarity": None}},
    ],
)
def test_atomic_request_rejects_invalid_or_injected_controls(fields) -> None:
    from powercontext.builtin.artifacts.atomic_memory.search import AtomicArtifactSearchRequest

    with pytest.raises(ValidationError):
        AtomicArtifactSearchRequest.model_validate_json(json.dumps({"query": "needle", **fields}), strict=True)


@pytest.mark.parametrize(
    "fields",
    [
        {"admission": {"min_semantic_similarity": 0.3}},
        {"fusion": {"method": "rrf", "params": {"weights": {"vector": 0}}}},
        {"mode": "vector", "admission": {"lexical_coverage": 0.2}},
        {"mode": "vector", "fusion": {"method": "rrf", "params": {"weights": {"text": 0}}}},
        {"fusion": {"method": "unknown"}},
        {"fusion": {"method": "rrf", "params": {"rank_constant": True}}},
        {"fusion": {"method": "rrf", "params": {"weights": {"text": 0}}}},
        {"fusion": {"method": "rrf", "params": {"weights": {"text": "1"}}}},
    ],
)
def test_atomic_plan_rejects_channel_and_algorithm_combinations(fields) -> None:
    from powercontext.builtin.artifacts.atomic_memory.search import AtomicArtifactSearchRequest, plan_atomic_search
    from powercontext.builtin.artifacts.memory import EmbeddingProfile
    from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexCapabilities

    profile = EmbeddingProfile(profile_id="test", model="test", dimension=2, normalization="unit")
    capabilities = AtomicMemoryIndexCapabilities(vector=True, hybrid=True, embedding_profile=profile)
    request = AtomicArtifactSearchRequest.model_validate_json(json.dumps({"query": "needle", **fields}), strict=True)
    with pytest.raises(ValidationError):
        plan_atomic_search(request, capabilities, embedding_profile=profile)


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_atomic_plan_requires_actual_complete_vector_deployment(mode) -> None:
    from powercontext.artifacts import ArtifactSearchUnsupported
    from powercontext.builtin.artifacts.atomic_memory.search import AtomicArtifactSearchRequest, plan_atomic_search
    from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexCapabilities

    with pytest.raises(ArtifactSearchUnsupported):
        plan_atomic_search(
            AtomicArtifactSearchRequest(query="needle", mode=mode),
            AtomicMemoryIndexCapabilities(),
            embedding_profile=None,
        )


@pytest.mark.parametrize("deployment", ["missing-model", "missing-index-profile", "profile-mismatch", "not-unit"])
def test_atomic_plan_checks_vector_profile_contract_before_retrieval(deployment) -> None:
    from powercontext.artifacts import ArtifactSearchUnsupported
    from powercontext.builtin.artifacts.atomic_memory.search import AtomicArtifactSearchRequest, plan_atomic_search
    from powercontext.builtin.artifacts.memory import EmbeddingProfile
    from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexCapabilities

    profile = EmbeddingProfile(profile_id="index", model="test", dimension=2, normalization="unit")
    model_profile = profile
    if deployment == "missing-model":
        model_profile = None
    elif deployment == "missing-index-profile":
        profile = None
    elif deployment == "profile-mismatch":
        model_profile = EmbeddingProfile(profile_id="other", model="test", dimension=2, normalization="unit")
    elif deployment == "not-unit":
        profile = model_profile = EmbeddingProfile(profile_id="index", model="test", dimension=2, normalization="none")
    with pytest.raises(ArtifactSearchUnsupported):
        plan_atomic_search(
            AtomicArtifactSearchRequest(query="needle", mode="vector"),
            AtomicMemoryIndexCapabilities(vector=True, hybrid=True, embedding_profile=profile),
            embedding_profile=model_profile,
        )
