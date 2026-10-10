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
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, ValidationError

from powercontext import http
from powercontext.artifacts import Artifact, ArtifactLineage
from powercontext.artifacts.search import (
    ArtifactSearchExecutionContext,
    ArtifactSearchMatch,
    ArtifactSearchQuery,
    ChannelScore,
)
from powercontext.builtin.inference import InferenceTimeoutError, InferenceUnavailableError
from powercontext.builtin.runtime import BuiltinRuntime, RuntimeCapabilities
from powercontext.builtin.runtime.artifact_search import ArtifactSearchService
from powercontext.server.app import create_app
from powercontext.sources import SourceRef

PATH = "/v1/scopes/scope-a/artifacts/custom-family/search"


class Content(BaseModel):
    title: str
    detail: str


class CustomArtifact(Artifact[Content]):
    family: ClassVar[str] = "custom-family"


class Query(ArtifactSearchQuery):
    filters: dict[str, Any] = Field(default_factory=dict)
    min_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


@dataclass
class Outcome:
    matches: tuple[ArtifactSearchMatch, ...]
    artifacts: tuple[CustomArtifact, ...]


class Searcher:
    family = "custom-family"
    request_type = Query

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[Query] = []
        self.contexts: list[ArtifactSearchExecutionContext | None] = []
        self.error = error

    async def search(
        self, scope_id: str, request: Query, /, *, execution_context: ArtifactSearchExecutionContext | None = None
    ) -> Outcome:
        assert scope_id == "scope-a"
        self.calls.append(request)
        self.contexts.append(execution_context)
        if self.error is not None:
            raise self.error
        artifacts = tuple(
            CustomArtifact(
                artifact_id=key,
                revision=3,
                content=Content(title="中文", detail="complete detail"),
                lineage=ArtifactLineage(sources=(SourceRef(source_type="content", source_id="proof"),)),
            )
            for key in ("z-first", "a-second")
        )
        pairs = [
            (artifact, score)
            for artifact, score in zip(artifacts, (0.8, 0.2), strict=True)
            if request.min_score is None or score >= request.min_score
        ]
        return Outcome(
            matches=tuple(
                ArtifactSearchMatch(artifact.as_ref(), score, {"text": ChannelScore(-2.0, "custom_raw", False)})
                for artifact, score in pairs
            ),
            artifacts=tuple(artifact for artifact, _ in pairs),
        )


class Scopes:
    async def get(self, scope_id: str, /) -> Any:
        return SimpleNamespace(scope_id=scope_id)


class Provider:
    async def get(self, scope_id: str, /) -> Any:
        raise AssertionError(scope_id)


def _client(searcher: Searcher | None = None) -> TestClient:
    service = ArtifactSearchService(known_families={"custom-family", "known-disabled"})
    service.register(searcher or Searcher())
    runtime = BuiltinRuntime(
        provider=Provider(),
        scope_application=cast(Any, Scopes()),
        capabilities=RuntimeCapabilities(memory_extraction=False, memory_search_modes=()),
        artifact_search=service,
    )
    return TestClient(create_app(application=cast(Any, runtime)), raise_server_exceptions=False)


def test_artifact_search_http_returns_arbitrary_family_full_content_and_optional_raw_scores() -> None:
    searcher = Searcher()
    with _client(searcher) as client:
        plain = client.post(PATH, json={"query": "  focused query  ", "filters": {"tags": ["a"]}})
        scored = client.post(PATH, json={"query": "focused query", "include_scores": True})
        threshold = client.post(PATH, json={"query": "focused query", "min_score": 0.8})
    assert plain.status_code == scored.status_code == threshold.status_code == 200
    assert [item["artifact_id"] for item in plain.json()["results"]] == ["z-first", "a-second"]
    assert [item["artifact_id"] for item in scored.json()["results"]] == ["z-first", "a-second"]
    item = plain.json()["results"][0]
    assert item["family"] == "custom-family"
    assert item["content"] == {"title": "中文", "detail": "complete detail"}
    assert item["lineage"]["sources"] == [{"source_type": "content", "source_id": "proof"}]
    assert "scores" not in item
    assert scored.json()["results"][0]["scores"] == {
        "retrieval": 0.8,
        "channels": {"text": {"raw": -2.0, "metric": "custom_raw", "higher_is_better": False}},
    }
    assert [item["artifact_id"] for item in threshold.json()["results"]] == ["z-first"]
    assert searcher.calls[0].model_fields_set == {"query", "filters"}


def test_artifact_search_http_preserves_custom_content_json_aliases() -> None:
    class AliasedContent(Content):
        schema_: str = Field(alias="schema")

    class AliasedSearcher(Searcher):
        async def search(
            self, scope_id: str, request: Query, /, *, execution_context: ArtifactSearchExecutionContext | None = None
        ) -> Outcome:
            outcome = await super().search(scope_id, request, execution_context=execution_context)
            content = AliasedContent.model_validate({
                "title": "中文",
                "detail": "complete detail",
                "schema": "custom-v1",
            })
            return Outcome(
                matches=outcome.matches,
                artifacts=tuple(artifact.model_copy(update={"content": content}) for artifact in outcome.artifacts),
            )

    with _client(AliasedSearcher()) as client:
        response = client.post(PATH, json={"query": "needle"})

    assert response.status_code == 200
    assert response.json()["results"][0]["content"] == {
        "title": "中文",
        "detail": "complete detail",
        "schema": "custom-v1",
    }


@pytest.mark.parametrize(
    "body",
    [
        {"query": " "},
        {"query": "x" * 8193},
        {"limit": True},
        {"limit": "10"},
        {"limit": 201},
        {"include_scores": 1},
        {"min_score": True},
        {"min_score": "0.5"},
        {"min_score": -0.1},
        {"min_score": 1.1},
        {"filters": []},
        {"fusion": {}},
        {"fusion": {"method": "rrf", "unknown": 1}},
        *(
            {field: None}
            for field in (
                "query",
                "limit",
                "include_scores",
                "mode",
                "filters",
                "admission",
                "fusion",
                "min_score",
                "rerank",
            )
        ),
        *(
            {field: "injected"}
            for field in (
                "scope_id",
                "family",
                "principal",
                "access",
                "audit",
                "trusted_local",
                "execution_context",
                "page",
                "offset",
                "cursor",
                "unknown",
            )
        ),
    ],
)
def test_artifact_search_http_rejects_fixed_body_contract_before_read(body: dict[str, object]) -> None:
    searcher = Searcher()
    response = _client(searcher).post(PATH, json={"query": "private-query", **body})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert searcher.calls == []
    assert "private-query" not in str(response.json())


def test_artifact_search_http_distinguishes_unknown_disabled_and_internal_failure() -> None:
    with _client() as client:
        unknown = client.post(PATH.replace("custom-family", "unknown"), json={"query": "needle"})
        disabled = client.post(PATH.replace("custom-family", "known-disabled"), json={"query": "needle"})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "artifact_family_not_found"
    assert disabled.status_code == 422
    assert disabled.json()["error"]["code"] == "artifact_search_not_supported"
    failed = _client(Searcher(RuntimeError("private-failure"))).post(PATH, json={"query": "needle"})
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "internal_error"
    unavailable = _client(Searcher(InferenceUnavailableError("embed"))).post(PATH, json={"query": "needle"})
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "inference_unavailable"
    missing = TestClient(create_app(application=SimpleNamespace()), raise_server_exceptions=False).post(
        PATH, json={"query": "needle"}
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "artifact_search_not_supported"


def test_artifact_search_transport_model_trims_before_bounds_and_rejects_nonfinite() -> None:
    request_type = http.SearchArtifactsRequest
    assert request_type(query="  " + "x" * 8192 + "  ").query == "x" * 8192
    for value in (math.inf, math.nan, -math.inf):
        with pytest.raises(ValidationError):
            request_type(query="needle", min_score=value)
        with pytest.raises(ValidationError) as nested:
            request_type(query="needle", filters={"weights": [value]})
        assert nested.value.errors()[0]["loc"] == ("filters", "weights", 0)


@pytest.mark.parametrize("error", [InferenceTimeoutError("embed", 0.1), InferenceUnavailableError("embed")])
def test_artifact_search_http_retains_typed_inference_unavailable_status(error: Exception) -> None:
    response = _client(Searcher(error)).post(PATH, json={"query": "needle"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] in {"inference_timeout", "inference_unavailable"}


def test_artifact_search_http_disabled_access_supplies_explicit_trusted_execution_context() -> None:
    searcher = Searcher()
    with _client(searcher) as client:
        response = client.post(PATH, json={"query": "needle"})
    assert response.status_code == 200
    context = searcher.contexts[0]
    assert context is not None
    assert context.principal is None
    assert context.access is None
    assert context.trusted_local is True
    assert context.audit.transport == "http"
    assert context.audit.operation == "search_artifacts"
    assert context.audit.request_id == response.headers["x-powercontext-request-id"]
    assert context.audit.subject_groups == ()
    assert "execution_context" not in response.json()["results"][0]


def test_artifact_search_http_reports_family_validation_paths_and_nonfinite_nested_values() -> None:
    searcher = Searcher()
    with _client(searcher) as client:
        mode = client.post(PATH, json={"query": "private-query", "mode": "semantic"})
        nested = client.post(
            PATH,
            content='{"query":"private-query","filters":{"weight":NaN}}',
            headers={"content-type": "application/json"},
        )
    assert mode.status_code == nested.status_code == 422
    assert mode.json()["error"]["code"] == nested.json()["error"]["code"] == "invalid_request"
    assert any("mode" in detail["loc"] for detail in mode.json()["error"]["details"]["errors"])
    assert any("weight" in detail["loc"] for detail in nested.json()["error"]["details"]["errors"])
    assert searcher.calls == []
    assert "private-query" not in str(mode.json()) + str(nested.json())


@pytest.mark.parametrize("family", ["experience", "skill"])
def test_sqlite_artifact_search_http_returns_exact_current_revision_and_lineage(family: str, tmp_path) -> None:
    from powercontext.builtin.artifacts.experience import ExperienceContent
    from powercontext.builtin.artifacts.skill import SkillContent
    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime import (
        ApproveArtifactCandidateRequest,
        CaptureSource,
        GetExperienceRequest,
        GetSkillRequest,
        ProposeExperienceRequest,
        ProposeSkillRequest,
    )
    from powercontext.builtin.scope import ScopeDraft
    from powercontext.server.factory import create_server_app
    from powercontext.server.settings import ServerSettings

    app = create_server_app(
        settings=ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'http.db'}"))
    )

    async def seed():
        runtime = app.state.application
        scope = (
            await runtime.scopes.create(ScopeDraft(title="Search", summary="HTTP", idempotency_key="http"))
        ).scope_id
        source = await runtime.sources.for_scope(scope).capture(
            CaptureSource(source_id="proof", content="needle", metadata={})
        )
        references = []
        for marker in ("oldmarker", "newmarker"):
            target = references[-1] if references else None
            if family == "experience":
                content = ExperienceContent(situation=marker, action="Inspect", outcome="Complete", lesson="Verify")
                candidate = await runtime.experience.for_scope(scope).propose(
                    ProposeExperienceRequest(
                        proposal=content, sources=(source.source_ref,), artifacts=tuple(references), target=target
                    )
                )
            else:
                content = SkillContent(
                    name=marker, description="Inspect", instructions="Complete", validation=("Verify",)
                )
                candidate = await runtime.skill.for_scope(scope).propose(
                    ProposeSkillRequest(
                        proposal=content, sources=(source.source_ref,), artifacts=tuple(references), target=target
                    )
                )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
            )
            assert approved.result_artifact is not None
            references.append(approved.result_artifact)
        exact = (
            await runtime.experience.for_scope(scope).get(GetExperienceRequest(artifact=references[-1]))
            if family == "experience"
            else await runtime.skill.for_scope(scope).get(GetSkillRequest(artifact=references[-1]))
        )
        return scope, references, source.source_ref, exact.content

    with TestClient(app) as client:
        assert client.portal is not None
        scope, refs, source, content = client.portal.call(seed)
        path = f"/v1/scopes/{scope}/artifacts/{family}/search"
        plain = client.post(path, json={"query": "newmarker", "limit": 200})
        scored = client.post(path, json={"query": "newmarker", "limit": 200, "include_scores": True})
        rejected = client.post(path, json={"query": "newmarker", "min_score": 1})
        old = client.post(path, json={"query": "oldmarker"})
        for field, value in (
            ("mode", "semantic"),
            ("filters", {"tags": ["unsupported"]}),
            ("fusion", {"method": "rrf"}),
            ("rerank", {}),
        ):
            invalid = client.post(path, json={"query": "newmarker", field: value})
            assert invalid.status_code == 422
            assert invalid.json()["error"]["code"] == "invalid_request"
            assert any(field in detail["loc"] for detail in invalid.json()["error"]["details"]["errors"])
    assert plain.status_code == scored.status_code == rejected.status_code == old.status_code == 200
    item = plain.json()["results"][0]
    assert (item["family"], item["artifact_id"], item["revision"]) == (family, refs[-1].artifact_id, refs[-1].revision)
    assert item["content"] == content.model_dump(mode="json")
    assert item["lineage"]["sources"] == [source.model_dump(mode="json")]
    assert item["lineage"]["artifacts"] == [refs[0].model_dump(mode="json")]
    assert "scores" not in item
    assert scored.json()["results"][0]["artifact_id"] == item["artifact_id"]
    assert scored.json()["results"][0]["scores"]["channels"]["text"]["raw"] < 0
    assert rejected.json() == old.json() == {"results": []}


def test_skill_library_http_preserves_custom_scoped_protocol_and_combined_window() -> None:
    from powercontext.builtin.artifacts.skill import Skill, SkillContent, SkillSearchHit
    from powercontext.builtin.persistence.artifact_governance import ArtifactGovernance, ArtifactLifecycleState

    skills = tuple(
        Skill(
            artifact_id=key,
            revision=1,
            content=SkillContent(
                name=key,
                description=text,
                instructions="Inspect",
                validation=("Verify",),
            ),
        )
        for key, text in (
            ("a-active", "needle phrase"),
            ("b-deprecated", "needle phrase"),
            ("z-deprecated", "needle phrase"),
        )
    )
    governance = {
        skill.artifact_id: ArtifactGovernance(
            artifact=skill.as_ref(),
            governance_generation=0,
            lifecycle_state=ArtifactLifecycleState.ACTIVE
            if skill.artifact_id.endswith("active")
            else ArtifactLifecycleState.DEPRECATED,
        )
        for skill in skills
    }

    class Scoped:
        def for_scope(self, scope_id):
            assert scope_id == "scope-a"
            return self

        async def search(self, query, limit):
            assert query == "needle phrase"
            return (SkillSearchHit(artifact_ref=skills[0].as_ref(), content=skills[0].content),)

        async def get(self, request):
            return next(skill for skill in skills if skill.as_ref() == request.artifact)

        async def governance(self, artifact_id):
            return governance[artifact_id]

        async def list(self, *, include_deprecated=False, limit=100):
            return tuple(
                (skill, governance[skill.artifact_id])
                for skill in skills
                if include_deprecated or governance[skill.artifact_id].lifecycle_state is ArtifactLifecycleState.ACTIVE
            )[:limit]

    with TestClient(create_app(application=SimpleNamespace(skill=Scoped()))) as client:
        matched = client.post(
            "/v1/skill/library",
            json={"scope_id": "scope-a", "query": "  needle phrase  ", "include_deprecated": True, "limit": 2},
        )
        listed = client.post("/v1/skill/library", json={"scope_id": "scope-a", "include_deprecated": True, "limit": 2})
    assert matched.status_code == listed.status_code == 200
    assert [item["artifact"]["artifact_id"] for item in matched.json()["skills"]] == ["a-active", "b-deprecated"]
    assert matched.json() == listed.json()
