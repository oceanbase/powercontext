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
from pathlib import Path

import pytest
from sqlalchemy import event, func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.experience import (
    Experience,
    ExperienceContent,
    FailureRecord,
    FailureSignature,
    FailureVerification,
)
from powercontext.builtin.artifacts.handoff import HandoffArtifactCitation, HandoffSourceCitation
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.skill import Skill, SkillContent, SkillPackageSnapshot
from powercontext.builtin.artifacts.topic_memory import TopicMemoryContent
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.errors import InvalidStoredPayloadError, RepositoryNotFoundError
from powercontext.builtin.persistence.experience_index import NoExperienceIndex
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE, BUILTIN_TABLES
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.review import (
    ArtifactTargetConflictError,
    CandidateConflictError,
    CandidateStatus,
    CandidateTerminalError,
    InvalidCandidateError,
)
from powercontext.builtin.runtime import (
    ApproveCandidateRequest,
    BuiltinConfig,
    BuiltinRuntime,
    CaptureSource,
    GetCandidateRequest,
    GetExperienceRequest,
    GetSkillRequest,
    ListCandidatesRequest,
    PrepareContextRequest,
    PreparedContext,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    RejectCandidateRequest,
    ReviseCandidateRequest,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.source_eligibility import SourceNotEligibleError
from powercontext.builtin.sources import ContentCapture
from powercontext.builtin.work.models import TaskCheck, TaskOutcome, WorkClaim
from powercontext.sources import SourceRef


async def _create_scope(runtime: BuiltinRuntime, idempotency_key: str = "project") -> str:
    assert runtime.scopes is not None
    scope = await runtime.scopes.create(
        ScopeDraft(title="Project", summary="Review service tests", idempotency_key=idempotency_key)
    )
    return scope.scope_id


class _InjectedCandidateStatusError(RuntimeError):
    pass


def test_duplicate_candidate_identity_preserves_original_proposal() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Review", summary="Candidate identity", idempotency_key="duplicate-candidate")
            )
            source = await contexts.records.capture_source(
                scope.scope_id, "content", "review-evidence", "Verified result", {}
            )
            review = contexts.review(scope.scope_id)
            proposal = ExperienceContent(
                situation="A verified task finished.",
                action="Record the result.",
                outcome="The next task can use it.",
                lesson="Keep the verified evidence.",
            )
            source_ref = SourceRef(source_type="content", source_id=source.source_id)
            original = await review.propose_experience(
                proposal,
                sources=(source_ref,),
                artifacts=(),
                target=None,
                reason="Verified result",
                candidate_id="same-candidate",
            )
            with pytest.raises(CandidateConflictError):
                await review.propose_experience(
                    proposal.model_copy(update={"lesson": "Different instruction."}),
                    sources=(source_ref,),
                    artifacts=(),
                    target=None,
                    reason="Verified result",
                    candidate_id="same-candidate",
                )
            async with contexts.database.transaction() as connection:
                stored = await contexts.repositories.candidates.get(connection, scope.scope_id, original.candidate_id)
            assert stored.proposal == proposal and stored.version == 1

    asyncio.run(scenario())


class _InjectedExperienceProjectionError(RuntimeError):
    pass


class _FailingExperienceIndex(NoExperienceIndex):
    async def initialize(self, _connection: AsyncConnection, /) -> None:
        pass

    async def remove(self, _connection: AsyncConnection, _scope_id: str, _artifact_id: str, /) -> None:
        raise _InjectedExperienceProjectionError

    async def replace(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _experience: Experience,
        /,
    ) -> None:
        raise _InjectedExperienceProjectionError

    async def replace_skill(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _skill: Skill,
        _package: SkillPackageSnapshot,
        /,
    ) -> None:
        pass


def _proposal(lesson: str = "Regenerate the Client before contract tests.") -> ExperienceContent:
    return ExperienceContent(
        situation="The public OpenAPI contract changes.",
        action="Regenerate the checked-in Client and run contract tests.",
        outcome="The generated transport and contract remain aligned.",
        lesson=lesson,
    )


def test_failure_proposal_requires_cited_failed_task_outcome_evidence() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime)
            source = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="plain-source", content="not a Task Outcome", metadata={})
            )
            proposal = _proposal().model_copy(
                update={
                    "failure": FailureRecord(
                        signature=FailureSignature(recall_cue="the contract test failed"),
                        repair_surface="experience_content",
                        verification=FailureVerification(
                            condition="the contract file was edited",
                            check_subject="generated code matches the contract",
                        ),
                    )
                }
            )

            with pytest.raises(InvalidCandidateError, match="failed Task Outcome"):
                await runtime.experience.for_scope(scope_id).propose(
                    ProposeExperienceRequest(proposal=proposal, sources=(source.source_ref,))
                )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "name, accepted", (("the contract test failed", True), ("the database migration failed", False))
)
def test_failure_proposal_requires_failure_evidence_to_match_its_recall_cue(name: str, accepted: bool) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime)
            proof = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="plain-source", content="Verified failure output.", metadata={})
            )
            check = _failure_check(HandoffSourceCitation(source_ref=proof.source_ref)).model_copy(update={"name": name})
            request = await _failure_request(runtime, scope_id, (check,))
            if accepted:
                candidate = await runtime.experience.for_scope(scope_id).propose(request)
                await _assert_failure_candidate(runtime, scope_id, candidate, request)
            else:
                with pytest.raises(InvalidCandidateError, match="failed Task Outcome"):
                    await runtime.experience.for_scope(scope_id).propose(request)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "citation",
    (
        HandoffSourceCitation(source_ref=SourceRef(source_type="content", source_id="never-existed")),
        HandoffArtifactCitation(artifact_ref=ArtifactRef(family="experience", artifact_id="never-existed", revision=1)),
    ),
)
def test_failure_proposal_rejects_missing_nested_task_outcome_evidence(citation) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime)
            request = await _failure_request(runtime, scope_id, (_failure_check(citation),))
            with pytest.raises(InvalidCandidateError, match="failed Task Outcome"):
                await runtime.experience.for_scope(scope_id).propose(request)
            assert (await runtime.review.for_scope(scope_id).list(ListCandidatesRequest())).candidates == ()

    asyncio.run(scenario())


_FAILURE_CUE = "the contract test failed"


def _failure_check(*citations: HandoffArtifactCitation | HandoffSourceCitation) -> TaskCheck:
    return TaskCheck(name=_FAILURE_CUE, status="failed", basis="verified", evidence=citations)


async def _failure_request(
    runtime: BuiltinRuntime,
    scope_id: str,
    checks: tuple[TaskCheck, ...],
) -> ProposeExperienceRequest:
    outcome = TaskOutcome(
        objective="Run contract tests.",
        status="failed",
        summary="The contract check failed.",
        observations=(WorkClaim(text="The runner reported a failure."),),
        checks=checks,
    )
    source = await runtime.sources.for_scope(scope_id).capture(
        CaptureSource(
            source_id="failed-task",
            content=outcome.model_dump_json(by_alias=True),
            metadata={"kind": "task-outcome"},
        )
    )
    proposal = _proposal().model_copy(
        update={
            "failure": FailureRecord(
                signature=FailureSignature(recall_cue=_FAILURE_CUE),
                repair_surface="experience_content",
                verification=FailureVerification(
                    condition="the contract file was edited", check_subject="generated code matches the contract"
                ),
            )
        }
    )
    return ProposeExperienceRequest(proposal=proposal, sources=(source.source_ref,))


async def _assert_failure_candidate(runtime, scope_id, candidate, request) -> None:
    stored = await runtime.review.for_scope(scope_id).get(GetCandidateRequest(candidate_id=candidate.candidate_id))
    assert stored.proposal == candidate.proposal == request.proposal
    assert stored.sources == candidate.sources == request.sources
    assert stored.artifacts == candidate.artifacts == request.artifacts
    assert stored.target == candidate.target == request.target
    assert stored.status == candidate.status == CandidateStatus.PENDING


async def _corrupt_artifact(config: SQLiteConfig, scope_id: str, ref: ArtifactRef) -> None:
    engine = create_async_engine(config.url)
    try:
        async with engine.begin() as connection:
            changed = await connection.execute(
                update(ARTIFACTS_TABLE)
                .where(
                    ARTIFACTS_TABLE.c.scope_id == scope_id,
                    ARTIFACTS_TABLE.c.family == ref.family,
                    ARTIFACTS_TABLE.c.artifact_id == ref.artifact_id,
                    ARTIFACTS_TABLE.c.revision == ref.revision,
                )
                .values(content=b"{}")
            )
            assert changed.rowcount == 1
    finally:
        await engine.dispose()


def test_failure_proposal_uses_exact_evidence_and_stops_after_a_match(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'selection.db'}")
        async with open_builtin_runtime(BuiltinConfig(database=config)) as runtime:
            scope_id = await _create_scope(runtime)
            records = runtime.records.for_scope(scope_id)
            created = await records.create_artifact(
                "experience", ArtifactWrite(content=_proposal().model_dump(mode="json"))
            )
            historical = ArtifactRef(family="experience", artifact_id=created.artifact_id, revision=1)
            await records.replace_artifact(
                "experience",
                created.artifact_id,
                '"revision:1"',
                ArtifactWrite(content=_proposal().model_dump(mode="json")),
            )
            current = historical.model_copy(update={"revision": 2})
            await _corrupt_artifact(config, scope_id, current)
            missing = ArtifactRef(family="experience", artifact_id="absent", revision=1)
            historical_citation = HandoffArtifactCitation(artifact_ref=historical)
            request = await _failure_request(
                runtime,
                scope_id,
                (
                    _failure_check(HandoffArtifactCitation(artifact_ref=missing)),
                    _failure_check(historical_citation, historical_citation),
                    _failure_check(HandoffArtifactCitation(artifact_ref=current)),
                ),
            )
            candidate = await runtime.experience.for_scope(scope_id).propose(request)
            await _assert_failure_candidate(runtime, scope_id, candidate, request)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "other_family",
    (
        pytest.param("experience", id="missing-after"),
        pytest.param("not-registered", id="unsupported-after"),
        pytest.param("atomic-memory", id="atomic-after"),
    ),
)
def test_failure_proposal_preserves_nested_evidence_error_precedence(tmp_path: Path, other_family: str) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'errors.db'}")
        async with open_builtin_runtime(BuiltinConfig(database=config)) as runtime:
            scope_id = await _create_scope(runtime)
            records = runtime.records.for_scope(scope_id)
            corrupt = await records.create_artifact(
                "experience", ArtifactWrite(content=_proposal().model_dump(mode="json"))
            )
            corrupt_ref = ArtifactRef(family="experience", artifact_id=corrupt.artifact_id, revision=1)
            await _corrupt_artifact(config, scope_id, corrupt_ref)
            other = ArtifactRef(family=other_family, artifact_id="absent", revision=1)
            if other_family == "atomic-memory":
                atomic = await records.create_artifact(
                    "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": "Proof."})
                )
                other = ArtifactRef(family="atomic-memory", artifact_id=atomic.artifact_id, revision=1)
                await _corrupt_artifact(config, scope_id, other)
            request = await _failure_request(
                runtime,
                scope_id,
                (
                    _failure_check(
                        HandoffArtifactCitation(artifact_ref=corrupt_ref), HandoffArtifactCitation(artifact_ref=other)
                    ),
                ),
            )
            with pytest.raises(InvalidStoredPayloadError) as error:
                await runtime.experience.for_scope(scope_id).propose(request)
            assert str(error.value) == str(
                InvalidStoredPayloadError("artifact", "experience", "payload does not match the model")
            )
            assert (await runtime.review.for_scope(scope_id).list(ListCandidatesRequest())).candidates == ()

    asyncio.run(scenario())


# Measured at seven statements for a complete SQLite proposal; allow one statement of slack.
_FAILURE_EVIDENCE_STATEMENT_CEILING = 8


def test_failure_proposal_statement_count_does_not_grow_with_artifact_evidence(record_property) -> None:
    async def counted(evidence_count: int) -> list[int]:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime)
            citations = []
            for _ in range(evidence_count):
                created = await runtime.records.for_scope(scope_id).create_artifact(
                    "experience", ArtifactWrite(content=_proposal().model_dump(mode="json"))
                )
                citations.append(
                    HandoffArtifactCitation(
                        artifact_ref=ArtifactRef(family="experience", artifact_id=created.artifact_id, revision=1)
                    )
                )
            request = await _failure_request(runtime, scope_id, (_failure_check(*citations),))
            await runtime.experience.for_scope(scope_id).propose(request)
            counts = []
            candidate_ids = set()
            statements = []

            def record(_conn, _cursor, statement, *_args) -> None:
                statements.append(statement)

            for _ in range(3):
                statements.clear()
                event.listen(Engine, "before_cursor_execute", record)
                try:
                    candidate = await runtime.experience.for_scope(scope_id).propose(request)
                finally:
                    event.remove(Engine, "before_cursor_execute", record)
                counts.append(len(statements))
                candidate_ids.add(candidate.candidate_id)
                await _assert_failure_candidate(runtime, scope_id, candidate, request)
            assert len(candidate_ids) == 3
            return counts

    small = asyncio.run(counted(4))
    large = asyncio.run(counted(32))
    record_property("failure_evidence_statements", {4: small, 32: large})
    assert small == large
    assert len(set(small)) == 1
    assert max(large) <= _FAILURE_EVIDENCE_STATEMENT_CEILING


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
            scope_id = await _create_scope(runtime)
            assert runtime.atomic_memory is not None
            remembered = await runtime.atomic_memory.for_scope(scope_id).create((
                AtomicMemoryContent(kind="decision", text="Keep Memory direct."),
            ))
            inbox = await runtime.review.for_scope(scope_id).list(ListCandidatesRequest())

            assert remembered.changed is True
            assert len(remembered.records) == 1
            assert remembered.records[0].ref.family == "atomic-memory"
            assert inbox.candidates == ()

    asyncio.run(scenario())


def test_experience_projection_failure_rolls_back_approval_artifact_and_status() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            from powercontext.builtin.persistence.tables import SCOPES_TABLE

            async with profile.database.transaction() as connection:
                await connection.execute(
                    insert(SCOPES_TABLE).values(
                        scope_id="project",
                        title="Project",
                        summary="Review",
                        scope_id_search="project",
                        title_search="project",
                        summary_search="review",
                        version=1,
                    )
                )
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


def test_approval_rechecks_sources_saved_by_an_older_candidate_path() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            from powercontext.builtin.persistence.tables import SCOPES_TABLE

            async with profile.database.transaction() as connection:
                await connection.execute(
                    insert(SCOPES_TABLE).values(
                        scope_id="project",
                        title="Project",
                        summary="Review",
                        scope_id_search="project",
                        title_search="project",
                        summary_search="review",
                        version=1,
                    )
                )
            contexts = RelationalContexts(database=profile.database)
            async with profile.database.transaction() as connection:
                await contexts.atomic_memory_index.initialize(connection)
            await contexts.get("project")
            created = await contexts.records.create_artifact(
                "project",
                "atomic-memory",
                ArtifactWrite(content={"kind": "fact", "text": "Direct input."}),
            )
            async with contexts.database.transaction() as connection:
                candidate = await contexts.repositories.candidates.create(
                    connection,
                    "project",
                    "candidate-legacy",
                    "experience",
                    _proposal(),
                    sources=created.sources,
                    artifacts=(),
                    target=None,
                    reason=None,
                )

            with pytest.raises(SourceNotEligibleError):
                await contexts.review("project").approve(candidate.candidate_id, candidate.version)

            current = await contexts.review("project").get_candidate(candidate.candidate_id)
            async with contexts.database.transaction() as connection:
                experiences = await connection.scalar(
                    select(func.count())
                    .select_from(ARTIFACTS_TABLE)
                    .where(
                        ARTIFACTS_TABLE.c.scope_id == "project",
                        ARTIFACTS_TABLE.c.family == Experience.family,
                    )
                )
            assert current.status == "pending"
            assert experiences == 0

    asyncio.run(scenario())


def test_experience_candidate_revise_approve_and_retrieval_gate() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime)
            isolated_scope_id = await _create_scope(runtime, "other-project")
            captured = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="task-1", content="api-generate then contract-test passed", metadata={})
            )
            candidate = await runtime.experience.for_scope(scope_id).propose(
                ProposeExperienceRequest(
                    proposal=_proposal(),
                    sources=(captured.source_ref,),
                )
            )
            inbox = await runtime.review.for_scope(scope_id).list(ListCandidatesRequest())
            prepared = await runtime.context.for_scope(scope_id).prepare(
                PrepareContextRequest(query="Regenerate the Client before contract tests.")
            )
            assert isinstance(prepared, PreparedContext)
            with pytest.raises(InvalidCandidateError):
                await runtime.review.for_scope(scope_id).revise(
                    ReviseCandidateRequest(
                        candidate_id=candidate.candidate_id,
                        expected_version=1,
                        proposal=_skill_proposal(),
                        sources=(captured.source_ref,),
                    )
                )
            revised = await runtime.review.for_scope(scope_id).revise(
                ReviseCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=1,
                    proposal=_proposal("Regenerate and inspect the Client before contract tests."),
                    sources=(captured.source_ref,),
                )
            )
            with pytest.raises(CandidateConflictError):
                await runtime.review.for_scope(scope_id).approve(
                    ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=1)
                )
            approved = await runtime.review.for_scope(scope_id).approve(
                ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=2)
            )
            assert approved.result_artifact is not None
            experience = await runtime.experience.for_scope(scope_id).get(
                GetExperienceRequest(artifact=approved.result_artifact)
            )
            approved_context = await runtime.context.for_scope(scope_id).prepare(
                PrepareContextRequest(query="Regenerate and inspect the Client before contract tests.")
            )
            assert isinstance(approved_context, PreparedContext)
            isolated_context = await runtime.context.for_scope(isolated_scope_id).prepare(
                PrepareContextRequest(query="Regenerate and inspect the Client before contract tests.")
            )
            assert isinstance(isolated_context, PreparedContext)
            pending = await runtime.review.for_scope(scope_id).list(ListCandidatesRequest())
            approved_page = await runtime.review.for_scope(scope_id).list(
                ListCandidatesRequest(status=CandidateStatus.APPROVED)
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
            replay = await runtime.review.for_scope(scope_id).approve(
                ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=2)
            )
            assert replay == approved

    asyncio.run(scenario())


def test_rejected_candidate_is_terminal_and_scope_evidence_isolated() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_a = await _create_scope(runtime, "scope-a")
            scope_b = await _create_scope(runtime, "scope-b")
            captured = await runtime.sources.for_scope(scope_a).capture(
                CaptureSource(source_id="task-1", content="bounded evidence", metadata={})
            )
            with pytest.raises(InvalidCandidateError):
                await runtime.experience.for_scope(scope_b).propose(
                    ProposeExperienceRequest(proposal=_proposal(), sources=(captured.source_ref,))
                )
            candidate = await runtime.experience.for_scope(scope_a).propose(
                ProposeExperienceRequest(proposal=_proposal(), sources=(captured.source_ref,))
            )
            rejected = await runtime.review.for_scope(scope_a).reject(
                RejectCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=1,
                    reason="The outcome does not support the lesson.",
                )
            )
            prepared = await runtime.context.for_scope(scope_a).prepare(
                PrepareContextRequest(query="Regenerate the Client before contract tests.")
            )
            assert isinstance(prepared, PreparedContext)

            assert rejected.status == "rejected"
            assert rejected.result_artifact is None
            assert rejected.decision_reason == "The outcome does not support the lesson."
            assert prepared.status == "empty"
            with pytest.raises(CandidateTerminalError):
                await runtime.review.for_scope(scope_a).revise(
                    ReviseCandidateRequest(
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
            scope_id = await _create_scope(runtime)
            task = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="task-1", content="api generation and contract validation passed", metadata={})
            )
            experience_candidate = await runtime.experience.for_scope(scope_id).propose(
                ProposeExperienceRequest(proposal=_proposal(), sources=(task.source_ref,))
            )
            experience_approval = await runtime.review.for_scope(scope_id).approve(
                ApproveCandidateRequest(
                    candidate_id=experience_candidate.candidate_id,
                    expected_version=1,
                )
            )
            assert experience_approval.result_artifact is not None

            candidate = await runtime.skill.for_scope(scope_id).propose(
                ProposeSkillRequest(
                    proposal=_skill_proposal(),
                    artifacts=(experience_approval.result_artifact,),
                    reason="Incubated from reviewed task evidence.",
                )
            )
            pending_skills = await runtime.review.for_scope(scope_id).list(ListCandidatesRequest(family="skill"))
            pending_context = await runtime.context.for_scope(scope_id).prepare(
                PrepareContextRequest(query="Regenerate clients and run contract tests")
            )
            assert isinstance(pending_context, PreparedContext)

            assert candidate.family == "skill"
            assert candidate.result_artifact is None
            assert pending_skills.candidates == (candidate,)
            assert pending_context.status == "ready"
            assert pending_context.content is not None
            assert '"kind":"experience"' in pending_context.content
            assert '"family":"skill"' not in pending_context.content

            approved = await runtime.review.for_scope(scope_id).approve(
                ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=1)
            )
            assert approved.result_artifact is not None
            first = await runtime.skill.for_scope(scope_id).get(GetSkillRequest(artifact=approved.result_artifact))

            usage = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(
                    source_id="task-2",
                    content="The managed Skill was used and both validation commands passed.",
                    metadata={},
                )
            )
            replacement = await runtime.skill.for_scope(scope_id).propose(
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
            replacement_approval = await runtime.review.for_scope(scope_id).approve(
                ApproveCandidateRequest(candidate_id=replacement.candidate_id, expected_version=1)
            )
            assert replacement_approval.result_artifact is not None
            second = await runtime.skill.for_scope(scope_id).get(
                GetSkillRequest(artifact=replacement_approval.result_artifact)
            )
            historical = await runtime.skill.for_scope(scope_id).get(GetSkillRequest(artifact=first.as_ref()))
            approved_context = await runtime.context.for_scope(scope_id).prepare(
                PrepareContextRequest(query="Regenerate clients and run contract tests")
            )
            assert isinstance(approved_context, PreparedContext)

            assert second.revision == 2
            assert second.lineage.sources == (usage.source_ref,)
            assert second.lineage.artifacts == (first.as_ref(),)
            assert historical == first
            assert approved_context == pending_context

    asyncio.run(scenario())


def test_managed_skill_approval_validates_family_lineage() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime)
            source = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="skill-source", content="reviewed Skill source", metadata={})
            )
            initial = await runtime.skill.for_scope(scope_id).propose(
                ProposeSkillRequest(proposal=_skill_proposal(), sources=(source.source_ref,))
            )
            initial_approval = await runtime.review.for_scope(scope_id).approve(
                ApproveCandidateRequest(candidate_id=initial.candidate_id, expected_version=1)
            )
            assert initial_approval.result_artifact is not None

            unsupported_create = await runtime.skill.for_scope(scope_id).propose(
                ProposeSkillRequest(
                    proposal=_skill_proposal("Use another managed Skill as the only evidence."),
                    artifacts=(initial_approval.result_artifact,),
                )
            )
            with pytest.raises(InvalidCandidateError) as unsupported_error:
                await runtime.review.for_scope(scope_id).approve(
                    ApproveCandidateRequest(candidate_id=unsupported_create.candidate_id, expected_version=1)
                )
            assert unsupported_error.value.field == "artifacts"

            unsupported_replacement = await runtime.skill.for_scope(scope_id).propose(
                ProposeSkillRequest(
                    proposal=_skill_proposal("Replace without direct usage evidence."),
                    artifacts=(initial_approval.result_artifact,),
                    target=initial_approval.result_artifact,
                )
            )
            with pytest.raises(InvalidCandidateError) as replacement_error:
                await runtime.review.for_scope(scope_id).approve(
                    ApproveCandidateRequest(
                        candidate_id=unsupported_replacement.candidate_id,
                        expected_version=1,
                    )
                )
            assert replacement_error.value.field == "sources"

    asyncio.run(scenario())


@pytest.mark.parametrize("rejected", [False, True])
def test_stale_experience_target_preserves_candidate_decision(rejected: bool) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime)
            captured = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="task-1", content="first result", metadata={})
            )
            initial = await runtime.experience.for_scope(scope_id).propose(
                ProposeExperienceRequest(proposal=_proposal(), sources=(captured.source_ref,))
            )
            approved = await runtime.review.for_scope(scope_id).approve(
                ApproveCandidateRequest(candidate_id=initial.candidate_id, expected_version=1)
            )
            assert approved.result_artifact is not None
            with pytest.raises(InvalidCandidateError):
                await runtime.experience.for_scope(scope_id).propose(
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
            winner = await runtime.experience.for_scope(scope_id).propose(replacement_request)
            stale = await runtime.experience.for_scope(scope_id).propose(replacement_request)
            if rejected:
                await runtime.review.for_scope(scope_id).reject(
                    RejectCandidateRequest(
                        candidate_id=stale.candidate_id, expected_version=1, reason="Do not publish this proposal."
                    )
                )
            winner_result = await runtime.review.for_scope(scope_id).approve(
                ApproveCandidateRequest(candidate_id=winner.candidate_id, expected_version=1)
            )

            with pytest.raises(CandidateTerminalError if rejected else ArtifactTargetConflictError):
                await runtime.review.for_scope(scope_id).approve(
                    ApproveCandidateRequest(candidate_id=stale.candidate_id, expected_version=1)
                )
            current_stale = await runtime.review.for_scope(scope_id).get(
                GetCandidateRequest(candidate_id=stale.candidate_id)
            )

            assert winner_result.result_artifact == approved.result_artifact.model_copy(update={"revision": 2})
            assert current_stale.status == ("rejected" if rejected else "pending")
            assert current_stale.version == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["atomic-memory", "topic-memory"])
def test_rejected_memory_approval_does_not_request_embeddings(family: str) -> None:
    class Embedding:
        profile = EmbeddingProfile(
            profile_id="review-terminal", model="controlled", dimension=3, distance="l2", normalization="unit"
        )

        def __init__(self) -> None:
            self.requests = 0

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.requests += 1
            return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))

    async def scenario() -> None:
        embedding = Embedding()
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig()), embedding_model=embedding) as contexts:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Review", summary="Rejected approval", idempotency_key="rejected")
            )
            original = (
                AtomicMemoryContent(kind="task_record", text="The replacement is pending.")
                if family == "atomic-memory"
                else TopicMemoryContent(title="Replacement", summary="Pending", detail="The replacement is pending.")
            )
            target = await contexts.records.create_artifact(
                scope.scope_id, family, ArtifactWrite(content=original.model_dump(mode="json", exclude={"creation"}))
            )
            context = await contexts.get(scope.scope_id)
            source, _ = await context.sources.capture(
                ContentCapture(source_id="progress", content="The replacement has been issued.", metadata={})
            )
            source_ref = context.sources.catalog.as_ref(source)
            service = contexts.review(scope.scope_id)
            target_ref = ArtifactRef(family=family, artifact_id=target.artifact_id, revision=target.revision)
            if isinstance(original, AtomicMemoryContent):
                candidate = await service.propose_memory_dream(
                    original.model_copy(update={"text": "The replacement has been issued."}),
                    sources=(source_ref,),
                    artifacts=(target_ref,),
                    target=target_ref,
                    reason="Correct the pending status.",
                    candidate_id="rejected-memory",
                )
            else:
                candidate = await service.propose_topic_memory_dream(
                    original.model_copy(update={"summary": "Issued", "detail": "The replacement has been issued."}),
                    sources=(source_ref,),
                    artifacts=(target_ref,),
                    target=target_ref,
                    reason="Correct the pending status.",
                    candidate_id="rejected-memory",
                )
            await service.reject(candidate.candidate_id, candidate.version, "The evidence does not support this.")
            before_requests = embedding.requests
            for expected_version in (candidate.version, candidate.version + 1):
                with pytest.raises(CandidateTerminalError) as error:
                    await service.approve(candidate.candidate_id, expected_version)
                assert error.value.status == "rejected"
            assert embedding.requests == before_requests
            current = await service.get_candidate(candidate.candidate_id)
            assert current.status == "rejected" and current.result_artifact is None

            async def deny_approval(*_args) -> None:
                raise PermissionError

            service.authorize_action = deny_approval
            with pytest.raises(PermissionError):
                await service.approve(candidate.candidate_id, candidate.version)

    asyncio.run(scenario())


def test_failed_candidate_status_update_rolls_back_artifact_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        fixed_ids = {"candidate": "cand-fixed", "experience": "exp-fixed"}
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            from powercontext.builtin.persistence.tables import SCOPES_TABLE

            async with profile.database.transaction() as connection:
                await connection.execute(
                    insert(SCOPES_TABLE).values(
                        scope_id="project",
                        title="Project",
                        summary="Review",
                        scope_id_search="project",
                        title_search="project",
                        summary_search="review",
                        version=1,
                    )
                )
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
