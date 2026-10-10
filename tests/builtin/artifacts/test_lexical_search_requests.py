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

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.experience import search as experience_search
from powercontext.builtin.artifacts.skill import search as skill_search


@pytest.mark.parametrize(
    "module,name", [(experience_search, "ExperienceSearchRequest"), (skill_search, "SkillSearchRequest")]
)
def test_lexical_search_request_defaults_and_json_numbers(module, name: str) -> None:
    request_type = getattr(module, name)
    request = request_type.model_validate({"query": "  contract client  ", "filters": {}, "admission": {}})

    assert request.query == "contract client"
    assert request.limit == 10
    assert request.include_scores is False
    assert request.mode is None
    assert request.min_score is None
    assert request.admission.lexical_coverage == 0.25
    assert request.admission.lexical_min_matched_terms == 2
    assert request.model_fields_set == {"query", "filters", "admission"}
    configured = request_type.model_validate_json(
        '{"query":"client","mode":"text","admission":{"lexical_coverage":0,"lexical_min_matched_terms":1},"min_score":1}'
    )
    assert configured.admission.lexical_coverage == 0
    assert configured.min_score == 1


@pytest.mark.parametrize(
    "module,name", [(experience_search, "ExperienceSearchRequest"), (skill_search, "SkillSearchRequest")]
)
@pytest.mark.parametrize(
    "fields",
    [
        {"query": "   "},
        {"limit": True},
        {"limit": "10"},
        {"limit": 201},
        {"include_scores": 1},
        {"filters": {"scope": "other"}},
        {"filters": []},
        {"admission": {"min_semantic_similarity": 0.2}},
        {"admission": {"lexical_coverage": True}},
        {"admission": {"lexical_coverage": "0.5"}},
        {"admission": {"lexical_coverage": math.inf}},
        {"admission": {"lexical_min_matched_terms": True}},
        {"admission": {"lexical_min_matched_terms": 0}},
        {"mode": "vector"},
        {"fusion": {}},
        {"rerank": {}},
        {"min_score": True},
        {"min_score": "0.5"},
        {"min_score": math.nan},
        {"min_score": -0.1},
        {"min_score": 1.1},
        {"unknown": 1},
        *({field: None} for field in ("query", "limit", "include_scores", "mode", "filters", "admission", "min_score")),
        {"admission": {"lexical_coverage": None}},
    ],
)
def test_lexical_search_request_rejects_invalid_fields(module, name: str, fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        getattr(module, name).model_validate({"query": "client", **fields})


@pytest.mark.parametrize(
    "module,name,maximum",
    [(experience_search, "ExperienceSearchRequest", 8192), (skill_search, "SkillSearchRequest", 2000)],
)
def test_lexical_search_request_owns_query_length(module, name: str, maximum: int) -> None:
    request_type = getattr(module, name)
    assert request_type(query="x" * maximum).query == "x" * maximum
    with pytest.raises(ValidationError):
        request_type(query="x" * (maximum + 1))
