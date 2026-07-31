from __future__ import annotations

import asyncio

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
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
    ListArtifactCandidatesRequest,
    PrepareContextRequest,
    ProposeExperienceRequest,
    RejectArtifactCandidateRequest,
    RememberMemoryRequest,
    ReviseArtifactCandidateRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.sources import ContentCapture


class _InjectedCandidateStatusError(RuntimeError):
    pass


def _proposal(lesson: str = "Regenerate the Client before contract tests.") -> ExperienceContent:
    return ExperienceContent(
        situation="The public OpenAPI contract changes.",
        action="Regenerate the checked-in Client and run contract tests.",
        outcome="The generated transport and contract remain aligned.",
        lesson=lesson,
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

            assert rejected.status == "rejected"
            assert rejected.result_artifact is None
            assert rejected.decision_reason == "The outcome does not support the lesson."
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
