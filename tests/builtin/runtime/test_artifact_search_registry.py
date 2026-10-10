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

"""Public search dispatch accepts arbitrary Artifact Families and checks outcomes."""

import asyncio
import json
import math
from dataclasses import FrozenInstanceError, dataclass
from typing import Any, ClassVar, cast

import pytest
from pydantic import BaseModel, Field, StrictInt, ValidationError

from powercontext.artifacts import Artifact, ArtifactRef, ArtifactSearchExecutionContext


class CustomContent(BaseModel):
    title: str


class CustomArtifact(Artifact[CustomContent]):
    family: ClassVar[str] = "custom"


@pytest.mark.parametrize("local_default", [False, True])
def test_registry_passes_the_same_trusted_execution_context_to_a_custom_family(local_default):
    from powercontext.artifacts.search import ArtifactSearchMatch, ArtifactSearchQuery
    from powercontext.builtin.runtime.artifact_search import ArtifactSearchService

    context = (
        None if local_default else ArtifactSearchExecutionContext(principal=object(), access=object(), audit=object())
    )
    if context is not None:
        assert context.trusted_local is False
        with pytest.raises(FrozenInstanceError):
            cast(Any, context).principal = object()
    artifact = CustomArtifact(artifact_id="item", revision=1, content=CustomContent(title="value"))

    @dataclass
    class Outcome:
        matches: tuple[ArtifactSearchMatch, ...] = (ArtifactSearchMatch(artifact.as_ref(), 0.5),)
        artifacts: tuple[CustomArtifact, ...] = (artifact,)

    class Searcher:
        family = "custom"
        request_type = ArtifactSearchQuery

        async def search(self, scope, request, /, *, execution_context=None) -> Outcome:
            assert scope == "scope"
            assert request.model_fields_set == {"query"}
            assert execution_context is context
            return Outcome()

    service = ArtifactSearchService(known_families={"custom"})
    service.register(Searcher())
    result = asyncio.run(service.search("scope", "custom", {"query": "value"}, execution_context=context))
    assert isinstance(result, Outcome)


def test_query_trims_and_validates_inherited_fields_with_json_semantics():
    from powercontext.artifacts.search import ArtifactSearchQuery

    class CustomQuery(ArtifactSearchQuery):
        tags: tuple[str, ...] = ()
        optional: str | None = None
        limit: StrictInt = Field(default=1, ge=1, le=2)

    request = CustomQuery.model_validate_json(json.dumps({"query": " 你好 ", "tags": ["a"]}), strict=True)
    assert request.query == "你好"
    assert request.tags == ("a",)
    assert request.limit == 1
    assert request.optional is None
    assert request.model_fields_set == {"query", "tags"}
    with pytest.raises(ValidationError):
        CustomQuery.model_validate_json('{"query":"valid","optional":null}', strict=True)


@pytest.mark.parametrize(
    "payload",
    [
        {"query": " "},
        {"query": 1},
        {"query": "x" * 8193},
        {"query": "x", "limit": True},
        {"query": "x", "limit": "1"},
        {"query": "x", "limit": 1.0},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": 201},
        {"query": "x", "include_scores": 1},
        {"query": "x", "include_scores": "true"},
        {"query": "x", "include_scores": None},
    ],
)
def test_query_rejects_invalid_public_parameters(payload):
    from powercontext.artifacts.search import ArtifactSearchQuery

    with pytest.raises(ValidationError):
        ArtifactSearchQuery.model_validate_json(json.dumps(payload), strict=True)


def test_registry_dispatches_custom_request_and_preserves_outcome():
    from powercontext.artifacts.search import ArtifactSearchMatch, ArtifactSearchQuery, ChannelScore
    from powercontext.builtin.runtime.artifact_search import ArtifactSearchService
    from powercontext.builtin.statistics import ModelUsagePurpose

    class CustomQuery(ArtifactSearchQuery):
        tags: tuple[str, ...] = ()
        limit: StrictInt = Field(default=1, ge=1, le=2)

    artifact = CustomArtifact(artifact_id="item", revision=3, content=CustomContent(title="你好"))

    @dataclass
    class CustomOutcome:
        matches: tuple[ArtifactSearchMatch, ...]
        artifacts: tuple[CustomArtifact, ...]
        additional_field: str = "preserved"

    class CustomSearcher:
        family = "custom"
        request_type = CustomQuery

        async def search(
            self,
            scope_id: str,
            request: CustomQuery,
            /,
            *,
            execution_context: ArtifactSearchExecutionContext | None = None,
        ) -> CustomOutcome:
            assert scope_id == "scope"
            assert request.tags == ("a",)
            assert request.query == "你好"
            assert request.model_fields_set == {"query", "tags", "include_scores"}
            return CustomOutcome(
                matches=(ArtifactSearchMatch(artifact.as_ref(), 0.8, {"text": ChannelScore(-2.5, "distance", False)}),),
                artifacts=(artifact,),
            )

    service = ArtifactSearchService(known_families={"custom", "unsupported"})
    service.register(CustomSearcher(), embedding_purpose=ModelUsagePurpose.MEMORY_RECALL)
    result = asyncio.run(service.search("scope", "custom", {"query": " 你好 ", "tags": ["a"], "include_scores": True}))
    assert isinstance(result, CustomOutcome)
    assert result.additional_field == "preserved"
    assert result.artifacts[0] is artifact
    assert result.artifacts[0].content.title == "你好"
    assert service.embedding_purpose("custom") == ModelUsagePurpose.MEMORY_RECALL


def test_registry_distinguishes_unknown_family_and_unsupported_search():
    from powercontext.artifacts.search import ArtifactSearchFamilyNotFound, ArtifactSearchUnsupported
    from powercontext.builtin.runtime.artifact_search import ArtifactSearchService

    service = ArtifactSearchService(known_families={"custom"})
    with pytest.raises(ArtifactSearchFamilyNotFound) as missing:
        asyncio.run(service.search("scope", "unknown", {"query": "x"}))
    assert missing.value.family == "unknown"
    with pytest.raises(ArtifactSearchUnsupported) as unsupported:
        service.embedding_purpose("custom")
    assert unsupported.value.family == "custom"
    assert unsupported.value.field == "family"


def test_registry_refuses_unknown_and_duplicate_registration():
    from powercontext.artifacts.search import ArtifactSearchFamilyNotFound, ArtifactSearchQuery
    from powercontext.builtin.runtime.artifact_search import ArtifactSearchService

    class Searcher:
        family = "custom"
        request_type = ArtifactSearchQuery

        async def search(
            self,
            scope_id: str,
            request: ArtifactSearchQuery,
            /,
            *,
            execution_context: ArtifactSearchExecutionContext | None = None,
        ) -> Any:
            raise AssertionError("not called")  # noqa: TRY003

    with pytest.raises(ArtifactSearchFamilyNotFound):
        ArtifactSearchService(known_families=()).register(Searcher())
    service = ArtifactSearchService(known_families={"custom"})
    service.register(Searcher())
    with pytest.raises(ValueError, match="already registered"):
        service.register(Searcher())


@pytest.mark.parametrize(
    "defect",
    [
        "missing-artifacts",
        "count",
        "order",
        "wrong-family",
        "duplicates",
        "limit",
        "missing-scores",
        "nonfinite-score",
        "nonfinite-raw",
        "invalid-reference",
    ],
)
def test_registry_reports_malformed_public_outcomes_as_implementation_failure(defect):  # noqa: C901
    from powercontext.artifacts.search import (
        ArtifactSearchContractError,
        ArtifactSearchMatch,
        ArtifactSearchQuery,
        ChannelScore,
    )
    from powercontext.builtin.runtime.artifact_search import ArtifactSearchService

    class Query(ArtifactSearchQuery):
        limit: StrictInt = Field(default=1, ge=1, le=200)

    first = CustomArtifact(artifact_id="first", revision=1, content=CustomContent(title="first"))
    second = CustomArtifact(artifact_id="second", revision=2, content=CustomContent(title="second"))
    score = ChannelScore(1.0, "relevance", True)
    match = ArtifactSearchMatch(first.as_ref(), 1.0, {"text": score})
    matches = (match,)
    artifacts = (first,)
    if defect == "missing-artifacts":
        artifacts = None
    elif defect == "count":
        artifacts = ()
    elif defect == "order":
        artifacts = (second,)
    elif defect == "wrong-family":
        object.__setattr__(match, "artifact_ref", ArtifactRef(family="other", artifact_id="first", revision=1))
    elif defect == "duplicates":
        matches = (match, match)
        artifacts = (first, first)
    elif defect == "limit":
        matches = (match, ArtifactSearchMatch(second.as_ref(), 0.5, {"text": score}))
        artifacts = (first, second)
    elif defect == "missing-scores":
        object.__setattr__(match, "channel_scores", None)
    elif defect == "nonfinite-score":
        object.__setattr__(match, "retrieval_score", math.inf)
    elif defect == "nonfinite-raw":
        object.__setattr__(score, "raw", math.nan)
    else:
        object.__setattr__(match, "artifact_ref", {"family": "custom", "artifact_id": "first", "revision": 1})

    @dataclass
    class Outcome:
        matches: Any
        artifacts: Any

    class Searcher:
        family = "custom"
        request_type = Query

        async def search(
            self, scope_id: str, request: Query, /, *, execution_context: ArtifactSearchExecutionContext | None = None
        ) -> Outcome:
            return Outcome(matches, artifacts)

    service = ArtifactSearchService(known_families={"custom"})
    service.register(Searcher())
    with pytest.raises(ArtifactSearchContractError):
        asyncio.run(service.search("scope", "custom", {"query": "x", "include_scores": True}))


def test_registry_preserves_runtime_failures():
    from powercontext.artifacts.search import ArtifactSearchQuery
    from powercontext.builtin.runtime.artifact_search import ArtifactSearchService

    class Searcher:
        family = "custom"
        request_type = ArtifactSearchQuery

        async def search(
            self,
            scope_id: str,
            request: ArtifactSearchQuery,
            /,
            *,
            execution_context: ArtifactSearchExecutionContext | None = None,
        ) -> Any:
            raise RuntimeError("retrieval failed")  # noqa: TRY003

    service = ArtifactSearchService(known_families={"custom"})
    service.register(Searcher())
    with pytest.raises(RuntimeError, match="retrieval failed"):
        asyncio.run(service.search("scope", "custom", {"query": "x"}))


@pytest.mark.parametrize("channel_scores", [None, {}])
def test_registry_accepts_unrequested_or_collected_empty_metadata(channel_scores):
    from powercontext.artifacts.search import ArtifactSearchMatch, ArtifactSearchQuery
    from powercontext.builtin.runtime.artifact_search import ArtifactSearchService

    artifact = CustomArtifact(artifact_id="item", revision=1, content=CustomContent(title="item"))

    @dataclass
    class Outcome:
        matches: tuple[ArtifactSearchMatch, ...]
        artifacts: tuple[CustomArtifact, ...]

    outcome = Outcome((ArtifactSearchMatch(artifact.as_ref(), 0.5, channel_scores),), (artifact,))

    class Searcher:
        family = "custom"
        request_type = ArtifactSearchQuery

        async def search(
            self,
            scope_id: str,
            request: ArtifactSearchQuery,
            /,
            *,
            execution_context: ArtifactSearchExecutionContext | None = None,
        ) -> Outcome:
            return outcome

    service = ArtifactSearchService(known_families={"custom"})
    service.register(Searcher())
    assert asyncio.run(service.search("scope", "custom", {"query": "x"})) is outcome
    if channel_scores is not None:
        assert asyncio.run(service.search("scope", "custom", {"query": "x", "include_scores": True})) is outcome


@pytest.mark.parametrize(
    "kwargs",
    [
        {"raw": math.nan, "metric": "relevance", "higher_is_better": True},
        {"raw": True, "metric": "relevance", "higher_is_better": True},
        {"raw": "1", "metric": "relevance", "higher_is_better": True},
        {"raw": 0, "metric": " ", "higher_is_better": True},
        {"raw": 0, "metric": "relevance", "higher_is_better": 1},
    ],
)
def test_channel_scores_require_meaningful_finite_values(kwargs):
    from powercontext.artifacts.search import ChannelScore

    with pytest.raises(ValueError):
        ChannelScore(**kwargs)


@pytest.mark.parametrize("score", [math.inf, math.nan, True, "1", -0.1, 1.1])
def test_match_scores_require_finite_normalized_values(score):
    from powercontext.artifacts.search import ArtifactSearchMatch

    with pytest.raises(ValueError):
        ArtifactSearchMatch(ArtifactRef(family="custom", artifact_id="item", revision=1), score)


@pytest.mark.parametrize("value", ["", b"", bytearray()])
def test_registry_rejects_string_like_result_sequences(value):
    from powercontext.artifacts.search import ArtifactSearchContractError, ArtifactSearchQuery
    from powercontext.builtin.runtime.artifact_search import ArtifactSearchService

    @dataclass
    class BadOutcome:
        matches: Any
        artifacts: Any

    class Searcher:
        family = "custom"
        request_type = ArtifactSearchQuery

        async def search(
            self,
            scope_id: str,
            request: ArtifactSearchQuery,
            /,
            *,
            execution_context: ArtifactSearchExecutionContext | None = None,
        ) -> BadOutcome:
            return BadOutcome(value, value)

    service = ArtifactSearchService(known_families={"custom"})
    service.register(Searcher())
    with pytest.raises(ArtifactSearchContractError):
        asyncio.run(service.search("scope", "custom", {"query": "x"}))


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_registry_reports_nonfinite_json_input_with_a_located_validation_error(value):
    from powercontext.artifacts.search import ArtifactSearchQuery
    from powercontext.builtin.runtime.artifact_search import ArtifactSearchService

    class Query(ArtifactSearchQuery):
        filters: dict[str, float] = Field(default_factory=dict)

    class Searcher:
        family = "custom"
        request_type = Query

        async def search(
            self, scope_id: str, request: Query, /, *, execution_context: ArtifactSearchExecutionContext | None = None
        ) -> Any:
            raise AssertionError("invalid input must not be dispatched")  # noqa: TRY003

    service = ArtifactSearchService(known_families={"custom"})
    service.register(Searcher())
    with pytest.raises(ValidationError) as error:
        asyncio.run(service.search("scope", "custom", {"query": "private query", "filters": {"value": value}}))
    assert error.value.errors()[0]["loc"] == ("filters", "value")
    assert "private query" not in str(error.value)
