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

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceContent, ExperienceSearchHit
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.skill import Skill, SkillContent, SkillPackageSnapshot, SkillSearchHit
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE, BUILTIN_TABLES
from powercontext.builtin.review import (
    ArtifactTargetConflictError,
    CandidateConflictError,
    CandidateStatus,
    CandidateTerminalError,
    InvalidCandidateError,
)
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest,
    BuiltinConfig,
    CaptureSource,
    GetArtifactCandidateRequest,
    GetExperienceRequest,
    GetSkillRequest,
    ListArtifactCandidatesRequest,
    PrepareContextRequest,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    RejectArtifactCandidateRequest,
    RememberMemoryRequest,
    ReviseArtifactCandidateRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.sources import ContentCapture


class _InjectedCandidateStatusError(RuntimeError):
    pass


class _InjectedExperienceProjectionError(RuntimeError):
    pass


class _FailingExperienceIndex:
    async def initialize(self, _connection: AsyncConnection, /) -> None:
        pass

    async def replace(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _experience: Experience,
        /,
    ) -> None:
        raise _InjectedExperienceProjectionError

    async def search(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _query: str,
        _limit: int,
        /,
    ) -> tuple[ExperienceSearchHit, ...]:
        return ()

    async def replace_skill(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _skill: Skill,
        _package: SkillPackageSnapshot,
        /,
    ) -> None:
        pass

    async def search_skills(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _query: str,
        _limit: int,
        /,
    ) -> tuple[SkillSearchHit, ...]:
        return ()


def _proposal(lesson: str = "Regenerate the Client before contract tests.") -> ExperienceContent:
    return ExperienceContent(
        situation="The public OpenAPI contract changes.",
        action="Regenerate the checked-in Client and run contract tests.",
        outcome="The generated transport and contract remain aligned.",
        lesson=lesson,
    )


def _skill_proposal(
    instructions: str = "Regenerate clients, inspect the diff, and run contract tests.",
) -> SkillContent:
    return SkillContent(
        name="powercontext-openapi-change",
        description="Use when changing PowerContext's public HTTP contract.",
        instructions=instructions,
        validation=("make api-generate-check passes", "make contract-test passes"),
    )


def test_memory_write_remains_direct_and_does_not_create_a_candidate() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            remembered = await runtime.memory.for_scope("project").remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="decision", text="Keep Memory direct."),))
            )
            inbox = await runtime.review.for_scope("project").list(ListArtifactCandidatesRequest())

            assert remembered.memory_ref.family == "memory"
            assert inbox.candidates == ()

    asyncio.run(scenario())


def test_experience_projection_failure_rolls_back_approval_artifact_and_status() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(
                database=profile.database,
                experience_index=_FailingExperienceIndex(),
            )
            context = await contexts.get("project")
            source, _ = await context.sources.capture(
                ContentCapture(source_id="task-1", content="A reviewed task outcome.")
            )
            review = contexts.review("project")
            candidate = await review.propose_experience(
                _proposal(),
                sources=(context.sources.catalog.as_ref(source),),
                artifacts=(),
                target=None,
                reason=None,
            )

            with pytest.raises(_InjectedExperienceProjectionError):
                await review.approve(candidate.candidate_id, candidate.version)

            current = await review.get_candidate(candidate.candidate_id)
            async with profile.database.transaction() as connection:
                artifacts = await connection.scalar(
                    select(func.count())
                    .select_from(ARTIFACTS_TABLE)
                    .where(
                        ARTIFACTS_TABLE.c.scope_id == "project",
                        ARTIFACTS_TABLE.c.family == Experience.family,
                    )
                )
            assert current.status == "pending"
            assert current.result_artifact is None
            assert artifacts == 0

    asyncio.run(scenario())


def test_experience_candidate_revise_approve_and_retrieval_gate() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            captured = await runtime.sources.for_scope("project").capture(
                CaptureSource(source_id="task-1", content="api-generate then contract-test passed", metadata={})
            )
            candidate = await runtime.experience.for_scope("project").propose(
                ProposeExperienceRequest(
                    proposal=_proposal(),
                    sources=(captured.source_ref,),
                )
            )
            inbox = await runtime.review.for_scope("project").list(ListArtifactCandidatesRequest())
            prepared = await runtime.context.for_scope("project").prepare(
                PrepareContextRequest(query="Regenerate the Client before contract tests.")
            )
            with pytest.raises(InvalidCandidateError):
                await runtime.review.for_scope("project").revise(
                    ReviseArtifactCandidateRequest(
                        candidate_id=candidate.candidate_id,
                        expected_version=1,
                        proposal=_skill_proposal(),
                        sources=(captured.source_ref,),
                    )
                )
            revised = await runtime.review.for_scope("project").revise(
                ReviseArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=1,
                    proposal=_proposal("Regenerate and inspect the Client before contract tests."),
                    sources=(captured.source_ref,),
                )
            )
            with pytest.raises(CandidateConflictError):
                await runtime.review.for_scope("project").approve(
                    ApproveArtifactCandidateRequest(candidate_id=candidate.candidate_id, expected_version=1)
                )
            approved = await runtime.review.for_scope("project").approve(
                ApproveArtifactCandidateRequest(candidate_id=candidate.candidate_id, expected_version=2)
            )
            assert approved.result_artifact is not None
            experience = await runtime.experience.for_scope("project").get(
                GetExperienceRequest(artifact=approved.result_artifact)
            )
            approved_context = await runtime.context.for_scope("project").prepare(
                PrepareContextRequest(query="Regenerate and inspect the Client before contract tests.")
            )
            isolated_context = await runtime.context.for_scope("other-project").prepare(
                PrepareContextRequest(query="Regenerate and inspect the Client before contract tests.")
            )
            pending = await runtime.review.for_scope("project").list(ListArtifactCandidatesRequest())
            approved_page = await runtime.review.for_scope("project").list(
                ListArtifactCandidatesRequest(status=CandidateStatus.APPROVED)
            )

            assert candidate.status == "pending"
            assert inbox.candidates == (candidate,)
            assert prepared.status == "empty"
            assert revised.version == 2
            assert experience.content == revised.proposal
            assert experience.lineage.sources == (captured.source_ref,)
            assert approved_context.status == "ready"
            assert approved_context.content is not None
            assert '"kind":"experience"' in approved_context.content
            assert approved.result_artifact.artifact_id in approved_context.content
            assert isolated_context.status == "empty"
            assert pending.candidates == ()
            assert approved_page.candidates == (approved,)
            with pytest.raises(CandidateTerminalError):
                await runtime.review.for_scope("project").approve(
                    ApproveArtifactCandidateRequest(candidate_id=candidate.candidate_id, expected_version=2)
                )

    asyncio.run(scenario())


def test_rejected_candidate_is_terminal_and_scope_evidence_isolated() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            captured = await runtime.sources.for_scope("scope-a").capture(
                CaptureSource(source_id="task-1", content="bounded evidence", metadata={})
            )
            with pytest.raises(InvalidCandidateError):
                await runtime.experience.for_scope("scope-b").propose(
                    ProposeExperienceRequest(proposal=_proposal(), sources=(captured.source_ref,))
                )
            candidate = await runtime.experience.for_scope("scope-a").propose(
                ProposeExperienceRequest(proposal=_proposal(), sources=(captured.source_ref,))
            )
            rejected = await runtime.review.for_scope("scope-a").reject(
                RejectArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=1,
                    reason="The outcome does not support the lesson.",
                )
            )
            prepared = await runtime.context.for_scope("scope-a").prepare(
                PrepareContextRequest(query="Regenerate the Client before contract tests.")
            )

            assert rejected.status == "rejected"
            assert rejected.result_artifact is None
            assert rejected.decision_reason == "The outcome does not support the lesson."
            assert prepared.status == "empty"
            with pytest.raises(CandidateTerminalError):
                await runtime.review.for_scope("scope-a").revise(
                    ReviseArtifactCandidateRequest(
                        candidate_id=candidate.candidate_id,
                        expected_version=1,
                        proposal=_proposal("replacement"),
                        sources=(captured.source_ref,),
                    )
                )

    asyncio.run(scenario())


def test_managed_skill_uses_review_gate_and_exact_replacement_lineage() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            task = await runtime.sources.for_scope("project").capture(
                CaptureSource(source_id="task-1", content="api generation and contract validation passed", metadata={})
            )
            experience_candidate = await runtime.experience.for_scope("project").propose(
                ProposeExperienceRequest(proposal=_proposal(), sources=(task.source_ref,))
            )
            experience_approval = await runtime.review.for_scope("project").approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=experience_candidate.candidate_id,
                    expected_version=1,
                )
            )
            assert experience_approval.result_artifact is not None

            candidate = await runtime.skill.for_scope("project").propose(
                ProposeSkillRequest(
                    proposal=_skill_proposal(),
                    artifacts=(experience_approval.result_artifact,),
                    reason="Incubated from reviewed task evidence.",
                )
            )
            pending_skills = await runtime.review.for_scope("project").list(
                ListArtifactCandidatesRequest(family="skill")
            )
            pending_context = await runtime.context.for_scope("project").prepare(
                PrepareContextRequest(query="Regenerate clients and run contract tests")
            )

            assert candidate.family == "skill"
            assert candidate.result_artifact is None
            assert pending_skills.candidates == (candidate,)
            assert pending_context.status == "ready"
            assert pending_context.content is not None
            assert '"kind":"experience"' in pending_context.content
            assert '"family":"skill"' not in pending_context.content

            approved = await runtime.review.for_scope("project").approve(
                ApproveArtifactCandidateRequest(candidate_id=candidate.candidate_id, expected_version=1)
            )
            assert approved.result_artifact is not None
            first = await runtime.skill.for_scope("project").get(GetSkillRequest(artifact=approved.result_artifact))

            usage = await runtime.sources.for_scope("project").capture(
                CaptureSource(
                    source_id="task-2",
                    content="The managed Skill was used and both validation commands passed.",
                    metadata={},
                )
            )
            replacement = await runtime.skill.for_scope("project").propose(
                ProposeSkillRequest(
                    proposal=_skill_proposal(
                        "Regenerate clients, inspect the diff, run generation checks, and then run contract tests."
                    ),
                    sources=(usage.source_ref,),
                    artifacts=(first.as_ref(),),
                    target=first.as_ref(),
                    reason="Usage evidence showed that the generation check must be explicit.",
                )
            )
            replacement_approval = await runtime.review.for_scope("project").approve(
                ApproveArtifactCandidateRequest(candidate_id=replacement.candidate_id, expected_version=1)
            )
            assert replacement_approval.result_artifact is not None
            second = await runtime.skill.for_scope("project").get(
                GetSkillRequest(artifact=replacement_approval.result_artifact)
            )
            historical = await runtime.skill.for_scope("project").get(GetSkillRequest(artifact=first.as_ref()))
            approved_context = await runtime.context.for_scope("project").prepare(
                PrepareContextRequest(query="Regenerate clients and run contract tests")
            )

            assert second.revision == 2
            assert second.lineage.sources == (usage.source_ref,)
            assert second.lineage.artifacts == (first.as_ref(),)
            assert historical == first
            assert approved_context == pending_context

    asyncio.run(scenario())


def test_managed_skill_approval_validates_family_lineage() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            source = await runtime.sources.for_scope("project").capture(
                CaptureSource(source_id="skill-source", content="reviewed Skill source", metadata={})
            )
            initial = await runtime.skill.for_scope("project").propose(
                ProposeSkillRequest(proposal=_skill_proposal(), sources=(source.source_ref,))
            )
            initial_approval = await runtime.review.for_scope("project").approve(
                ApproveArtifactCandidateRequest(candidate_id=initial.candidate_id, expected_version=1)
            )
            assert initial_approval.result_artifact is not None

            unsupported_create = await runtime.skill.for_scope("project").propose(
                ProposeSkillRequest(
                    proposal=_skill_proposal("Use another managed Skill as the only evidence."),
                    artifacts=(initial_approval.result_artifact,),
                )
            )
            with pytest.raises(InvalidCandidateError) as unsupported_error:
                await runtime.review.for_scope("project").approve(
                    ApproveArtifactCandidateRequest(candidate_id=unsupported_create.candidate_id, expected_version=1)
                )
            assert unsupported_error.value.field == "artifacts"

            unsupported_replacement = await runtime.skill.for_scope("project").propose(
                ProposeSkillRequest(
                    proposal=_skill_proposal("Replace without direct usage evidence."),
                    artifacts=(initial_approval.result_artifact,),
                    target=initial_approval.result_artifact,
                )
            )
            with pytest.raises(InvalidCandidateError) as replacement_error:
                await runtime.review.for_scope("project").approve(
                    ApproveArtifactCandidateRequest(
                        candidate_id=unsupported_replacement.candidate_id,
                        expected_version=1,
                    )
                )
            assert replacement_error.value.field == "sources"

    asyncio.run(scenario())


def test_stale_experience_target_keeps_candidate_pending() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            captured = await runtime.sources.for_scope("project").capture(
                CaptureSource(source_id="task-1", content="first result", metadata={})
            )
            initial = await runtime.experience.for_scope("project").propose(
                ProposeExperienceRequest(proposal=_proposal(), sources=(captured.source_ref,))
            )
            approved = await runtime.review.for_scope("project").approve(
                ApproveArtifactCandidateRequest(candidate_id=initial.candidate_id, expected_version=1)
            )
            assert approved.result_artifact is not None
            with pytest.raises(InvalidCandidateError):
                await runtime.experience.for_scope("project").propose(
                    ProposeExperienceRequest(
                        proposal=_proposal("This revision omits predecessor lineage."),
                        sources=(captured.source_ref,),
                        target=approved.result_artifact,
                    )
                )
            replacement_request = ProposeExperienceRequest(
                proposal=_proposal("Use the generated diff as part of review."),
                sources=(captured.source_ref,),
                artifacts=(approved.result_artifact,),
                target=approved.result_artifact,
            )
            winner = await runtime.experience.for_scope("project").propose(replacement_request)
            stale = await runtime.experience.for_scope("project").propose(replacement_request)
            winner_result = await runtime.review.for_scope("project").approve(
                ApproveArtifactCandidateRequest(candidate_id=winner.candidate_id, expected_version=1)
            )

            with pytest.raises(ArtifactTargetConflictError):
                await runtime.review.for_scope("project").approve(
                    ApproveArtifactCandidateRequest(candidate_id=stale.candidate_id, expected_version=1)
                )
            current_stale = await runtime.review.for_scope("project").get(
                GetArtifactCandidateRequest(candidate_id=stale.candidate_id)
            )

            assert winner_result.result_artifact == approved.result_artifact.model_copy(update={"revision": 2})
            assert current_stale.status == "pending"
            assert current_stale.version == 1

    asyncio.run(scenario())


def test_failed_candidate_status_update_rolls_back_artifact_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        fixed_ids = {"candidate": "cand-fixed", "experience": "exp-fixed"}
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(
                database=profile.database,
                id_factory=lambda kind: fixed_ids[kind],
            )
            context = await contexts.get("project")
            source, _ = await context.sources.capture(
                ContentCapture(
                    source_id="task-1",
                    content="bounded evidence",
                    metadata={},
                )
            )
            source_ref = context.sources.catalog.as_ref(source)
            service = contexts.review("project")
            candidate = await service.propose_experience(
                _proposal(), sources=(source_ref,), artifacts=(), target=None, reason=None
            )

            async def fail_status_update(*_args, **_kwargs):
                raise _InjectedCandidateStatusError

            original = contexts.repositories.candidates.mark_approved
            monkeypatch.setattr(contexts.repositories.candidates, "mark_approved", fail_status_update)
            with pytest.raises(_InjectedCandidateStatusError):
                await service.approve(candidate.candidate_id, 1)
            monkeypatch.setattr(contexts.repositories.candidates, "mark_approved", original)

            current = await service.get_candidate(candidate.candidate_id)
            async with profile.database.transaction() as connection:
                with pytest.raises(RepositoryNotFoundError):
                    await contexts.repositories.artifacts.get(
                        connection,
                        "project",
                        ArtifactRef(family="experience", artifact_id="exp-fixed", revision=1),
                    )

            assert current.status == "pending"
            approved = await service.approve(candidate.candidate_id, 1)
            assert approved.result_artifact == ArtifactRef(
                family="experience",
                artifact_id="exp-fixed",
                revision=1,
            )

    asyncio.run(scenario())
