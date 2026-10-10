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

import asyncio
from dataclasses import dataclass
from typing import Any, ClassVar, cast

import pytest
from pydantic import BaseModel, ValidationError

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.artifacts.search import (
    ArtifactSearchContractError,
    ArtifactSearchExecutionContext,
    ArtifactSearchMatch,
    ArtifactSearchQuery,
    ArtifactSearchUnsupported,
    ChannelScore,
)
from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceSearchHit, ExperienceSearchOutcome
from powercontext.builtin.artifacts.search import InvalidSearchScore
from powercontext.builtin.artifacts.skill import Skill, SkillContent, SkillSearchHit
from powercontext.builtin.persistence.artifact_governance import ArtifactGovernance, ArtifactLifecycleState
from powercontext.builtin.persistence.experience_index import NoExperienceIndex
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.sqlite.experience_index import SQLiteExperienceFTSIndex
from powercontext.builtin.runtime import (
    ApproveCandidateRequest,
    BuiltinConfig,
    BuiltinRuntime,
    CaptureSource,
    GetExperienceRequest,
    GetSkillRequest,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    RuntimeCapabilities,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.artifact_search import ArtifactSearchService
from powercontext.builtin.scope import ScopeDraft, ScopeNotFoundError


async def _scope(runtime: BuiltinRuntime) -> str:
    assert runtime.scopes is not None
    return (
        await runtime.scopes.create(ScopeDraft(title="Search", summary="Artifact tests", idempotency_key="search"))
    ).scope_id


def _content(family: str, marker: str) -> ExperienceContent | SkillContent:
    if family == "experience":
        return ExperienceContent(
            situation=marker, action="Inspect generated client", outcome="Correct contract", lesson="Validate"
        )
    return SkillContent(
        name="generated-client",
        description=marker,
        instructions="Inspect generated contract",
        validation=("Validate contract",),
    )


async def _approve(runtime: BuiltinRuntime, scope: str, family: str, marker: str, target: ArtifactRef | None = None):
    source = await runtime.sources.for_scope(scope).capture(
        CaptureSource(source_id=marker, content=marker, metadata={})
    )
    proposal = _content(family, marker)
    lineage = () if target is None else (target,)
    if isinstance(proposal, ExperienceContent):
        candidate = await runtime.experience.for_scope(scope).propose(
            ProposeExperienceRequest(proposal=proposal, sources=(source.source_ref,), artifacts=lineage, target=target)
        )
    else:
        candidate = await runtime.skill.for_scope(scope).propose(
            ProposeSkillRequest(proposal=proposal, sources=(source.source_ref,), artifacts=lineage, target=target)
        )
    approved = await runtime.review.for_scope(scope).approve(
        ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
    )
    assert approved.result_artifact is not None
    return approved.result_artifact, source.source_ref


@pytest.mark.parametrize("family", ["experience", "skill"])
def test_public_lexical_search_returns_current_exact_artifacts_and_true_scores(family: str) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope = await _scope(runtime)
            first, _ = await _approve(runtime, scope, family, "oldmarker")
            current, source = await _approve(runtime, scope, family, "currentmarker", first)
            application = runtime.artifacts.for_scope(scope)
            outcome = await application.search(family, {"query": "currentmarker", "limit": 200, "include_scores": True})
            assert outcome.artifacts is not None
            assert tuple(match.artifact_ref for match in outcome.matches) == (current,)
            assert tuple(artifact.as_ref() for artifact in outcome.artifacts) == (current,)
            artifact = outcome.artifacts[0]
            exact = (
                await runtime.experience.for_scope(scope).get(GetExperienceRequest(artifact=current))
                if family == "experience"
                else await runtime.skill.for_scope(scope).get(GetSkillRequest(artifact=current))
            )
            assert artifact == exact
            assert artifact.lineage.sources == (source,)
            assert artifact.lineage.artifacts == (first,)
            channel = outcome.matches[0].channel_scores
            assert channel is not None
            assert channel["text"].metric == "sqlite_bm25"
            assert channel["text"].raw < 0
            assert 0 < outcome.matches[0].retrieval_score < 1
            plain = await application.search(family, {"query": "currentmarker", "limit": 200})
            assert tuple(match.artifact_ref for match in plain.matches) == (current,)
            assert plain.artifacts == outcome.artifacts
            assert (await application.search(family, {"query": "oldmarker"})).matches == ()
            excluded = await application.search(family, {"query": "currentmarker", "min_score": 1})
            assert excluded.matches == ()
            assert excluded.artifacts == ()
            with pytest.raises(ValidationError):
                await application.search(family, {"query": "currentmarker", "filters": {"unknown": True}})
            with pytest.raises(ValidationError):
                await application.search(family, {"query": "currentmarker", "limit": 201})
            with pytest.raises(ScopeNotFoundError):
                await runtime.artifacts.for_scope("missing-scope").search(family, {"query": "currentmarker"})

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["experience", "skill"])
def test_public_lexical_search_reports_disabled_index_while_legacy_stays_empty(family: str) -> None:
    async def scenario() -> None:
        from powercontext.builtin.runtime.experience_search import ExperienceArtifactSearcher
        from powercontext.builtin.runtime.skill_search import SkillArtifactSearcher

        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            index = NoExperienceIndex()
            service = ArtifactSearchService(known_families={family})
            if family == "experience":
                service.register(ExperienceArtifactSearcher(contexts.database, contexts.repositories.artifacts, index))
            else:
                service.register(SkillArtifactSearcher(contexts.database, contexts.repositories.artifacts, index))
            with pytest.raises(ArtifactSearchUnsupported):
                await service.search("project", family, {"query": "client"})
            async with contexts.database.transaction() as connection:
                assert (await index.search(connection, "project", "", 500)).hits == ()
                assert await index.search_skills(connection, "project", "", 500) == ()

    asyncio.run(scenario())


class _UnusedProvider:
    async def get(self, scope_id: str, /) -> Any:
        raise AssertionError(scope_id)


class _Scopes:
    async def get(self, _scope_id: str, /) -> Any:
        return object()


def test_runtime_facade_preserves_custom_family_outcome_and_optional_injection() -> None:
    class CustomContent(BaseModel):
        title: str

    class CustomArtifact(Artifact[CustomContent]):
        family: ClassVar[str] = "custom"

    artifact = CustomArtifact(artifact_id="custom", revision=1, content=CustomContent(title="value"))
    supplied_context = ArtifactSearchExecutionContext(principal=object())
    seen_contexts: list[ArtifactSearchExecutionContext | None] = []

    @dataclass
    class Outcome:
        matches: tuple[ArtifactSearchMatch, ...] = (ArtifactSearchMatch(artifact.as_ref(), 0.5),)
        artifacts: tuple[CustomArtifact, ...] = (artifact,)
        extra: str = "preserved"

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
            assert scope_id == "project" and request.query == "value"
            seen_contexts.append(execution_context)
            return Outcome()

    async def scenario() -> None:
        service = ArtifactSearchService(known_families={"custom"})
        service.register(Searcher())
        async with BuiltinRuntime(
            provider=_UnusedProvider(),
            capabilities=RuntimeCapabilities(memory_extraction=False, memory_search_modes=()),
            scope_application=cast(Any, _Scopes()),
            artifact_search=service,
        ) as runtime:
            result = await runtime.artifacts.for_scope("project").search("custom", {"query": "value"})
            assert isinstance(result, Outcome)
            assert result.extra == "preserved"
            await runtime.artifacts.for_scope("project").search(
                "custom", {"query": "value"}, execution_context=supplied_context
            )
            assert seen_contexts[0] is None
            assert seen_contexts[1] is supplied_context
        async with BuiltinRuntime(
            provider=_UnusedProvider(),
            capabilities=RuntimeCapabilities(memory_extraction=False, memory_search_modes=()),
            scope_application=cast(Any, _Scopes()),
        ) as runtime:
            assert await runtime.skill.for_scope("project").search("", 500) == ()
            with pytest.raises(ArtifactSearchUnsupported):
                await runtime.artifacts.for_scope("project").search("custom", {"query": "value"})

    asyncio.run(scenario())


@pytest.mark.parametrize("include_scores", [False, True])
def test_public_experience_requires_raw_metadata_even_when_scores_are_hidden(include_scores: bool) -> None:
    from powercontext.builtin.runtime.experience_search import ExperienceArtifactSearcher

    class Index(SQLiteExperienceFTSIndex):
        async def search(self, *_args: Any, **_kwargs: Any) -> ExperienceSearchOutcome:
            return ExperienceSearchOutcome(
                hits=(
                    ExperienceSearchHit(
                        artifact_ref=ArtifactRef(family="experience", artifact_id="missing-raw", revision=1),
                        content=cast(ExperienceContent, _content("experience", "needle")),
                        retrieval_score=0.5,
                    ),
                )
            )

    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            service = ArtifactSearchService(known_families={"experience"})
            service.register(ExperienceArtifactSearcher(contexts.database, contexts.repositories.artifacts, Index()))
            with pytest.raises(InvalidSearchScore):
                await service.search("project", "experience", {"query": "needle", "include_scores": include_scores})

    asyncio.run(scenario())


def test_skill_library_preserves_combined_window_whole_query_and_active_order() -> None:
    skills = tuple(
        Skill(
            artifact_id=key,
            revision=1,
            content=SkillContent(name=key, description=text, instructions="check", validation=("Validate",)),
        )
        for key, text in (
            ("a-active", "needle phrase"),
            ("b-deprecated", "needle phrase"),
            ("c-deprecated", "needle and phrase"),
            ("y-active", "needle phrase"),
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

    async def recall(_scope: str, query: str, limit: int):
        assert query == "needle phrase"
        return tuple(
            SkillSearchHit(artifact_ref=skill.as_ref(), content=skill.content) for skill in (skills[3], skills[0])
        )

    async def lister(_scope: str, include_deprecated: bool, limit: int):
        return tuple(
            (skill, governance[skill.artifact_id])
            for skill in skills
            if include_deprecated or governance[skill.artifact_id].lifecycle_state is ArtifactLifecycleState.ACTIVE
        )[:limit]

    async def reader(_scope: str, key: str):
        return governance[key]

    class Review:
        async def get_skill(self, ref: ArtifactRef):
            return next(skill for skill in skills if skill.as_ref() == ref)

    async def scenario() -> None:
        async with BuiltinRuntime(
            provider=_UnusedProvider(),
            capabilities=RuntimeCapabilities(memory_extraction=False, memory_search_modes=()),
            scope_application=cast(Any, _Scopes()),
            skill_recall=recall,
            skill_lister=lister,
            skill_governance_reader=reader,
            review_service=cast(Any, lambda _scope: Review()),
        ) as runtime:
            scoped = runtime.skill.for_scope("project")
            values = await scoped.search_library("  needle phrase  ", 3, include_deprecated=True)
            assert tuple(skill.artifact_id for skill, _ in values) == ("y-active", "a-active", "b-deprecated")
            assert tuple(
                skill.artifact_id for skill, _ in await scoped.search_library("", 2, include_deprecated=True)
            ) == ("a-active", "b-deprecated")
            assert tuple(
                skill.artifact_id
                for skill, _ in await scoped.search_library("needle phrase", 3, include_deprecated=False)
            ) == ("y-active", "a-active")

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["experience", "skill"])
def test_public_search_missing_selected_revision_is_an_internal_contract_failure(family: str) -> None:
    from powercontext.builtin.runtime.experience_search import ExperienceArtifactSearcher
    from powercontext.builtin.runtime.skill_search import SkillArtifactSearcher

    ref = ArtifactRef(family=family, artifact_id="missing-selected-revision", revision=1)
    scores = {"text": ChannelScore(-1.0, "sqlite_bm25", False)}

    class Index(SQLiteExperienceFTSIndex):
        async def search(self, *_args: Any, **_kwargs: Any) -> ExperienceSearchOutcome:
            return ExperienceSearchOutcome(
                hits=(
                    ExperienceSearchHit(
                        artifact_ref=ref,
                        content=cast(ExperienceContent, _content(family, "needle")),
                        retrieval_score=0.5,
                        channel_scores=scores,
                    ),
                )
            )

        async def search_skills(self, *_args: Any, **_kwargs: Any) -> tuple[SkillSearchHit, ...]:
            return (
                SkillSearchHit(
                    artifact_ref=ref,
                    content=cast(SkillContent, _content(family, "needle")),
                    retrieval_score=0.5,
                    channel_scores=scores,
                ),
            )

    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            service = ArtifactSearchService(known_families={family})
            searcher = ExperienceArtifactSearcher if family == "experience" else SkillArtifactSearcher
            service.register(searcher(contexts.database, contexts.repositories.artifacts, Index()))
            with pytest.raises(ArtifactSearchContractError):
                await service.search("project", family, {"query": "needle"})

    asyncio.run(scenario())
