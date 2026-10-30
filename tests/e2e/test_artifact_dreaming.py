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

"""Shared Dream behavior on SQLite and opt-in isolated OceanBase databases."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from contextlib import nullcontext, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.artifacts.experience import ExperienceCandidateInput, ExperienceContent
from powercontext.builtin.artifacts.handoff.models import (
    HandoffContent,
    HandoffDraft,
    HandoffSourceCitation,
    HandoffStatement,
)
from powercontext.builtin.artifacts.profile.models import (
    ProfileContent,
    ProfileDraft,
    ProfileGeneration,
    ProfileWriteContent,
)
from powercontext.builtin.artifacts.skill import SkillContent, SkillDraft
from powercontext.builtin.artifacts.topic_memory import (
    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
    TopicMemoryContent,
    TopicMemoryDraft,
    prepare_topic_memory_projection,
)
from powercontext.builtin.dream.generation import DreamGenerationInput
from powercontext.builtin.dream.models import DreamError, DreamOperation, DreamPlan
from powercontext.builtin.evidence.models import EvidenceResolutionError
from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite, BaseValueConflictError
from powercontext.builtin.runtime import (
    ApproveCandidateRequest,
    BuiltinConfig,
    BuiltinRuntime,
    CaptureSource,
    CreateDreamRunRequest,
    GetCandidateRequest,
    GetDreamRunRequest,
    GetExperienceRequest,
    GetSkillRequest,
    ListCandidatesRequest,
    ListDreamRunsRequest,
    PrepareContextRequest,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    RejectCandidateRequest,
    ReviseCandidateRequest,
    RuntimeConfig,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource
from powercontext.server.app import ServerApplication
from powercontext.sources import Source, SourceRef
from tests.e2e.dream_support import atomic_memory_pipeline, process_pending
from tests.e2e.dream_support import open_dream_runtime as open_builtin_runtime

DatabaseConfig = SQLiteConfig | OceanBaseConfig


@pytest.fixture(params=("sqlite", "oceanbase"))
def database(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[DatabaseConfig]:
    if request.param == "sqlite":
        yield SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'dream.db'}")
        return
    configured_url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
    if configured_url is None:
        pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL with test database creation and deletion privileges")
    configured = OceanBaseConfig(url=SecretStr(configured_url))
    database_name = "pc_dream_test_" + uuid4().hex[:16]

    async def manage(statement: str) -> None:
        async with (
            OceanBaseProfile.open(configured, tables=()) as admin,
            admin.database.transaction() as connection,
        ):
            await connection.exec_driver_sql(statement)

    asyncio.run(manage(f"CREATE DATABASE `{database_name}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"))
    try:
        url = make_url(configured_url).set(database=database_name)
        yield OceanBaseConfig(url=SecretStr(url.render_as_string(hide_password=False)))
    finally:
        asyncio.run(manage(f"DROP DATABASE `{database_name}`"))


def experience() -> ExperienceContent:
    return ExperienceContent(
        situation="A timed-out write might already have committed.",
        action="Replay the write with the same idempotency key.",
        outcome="The successful replay did not create a duplicate record.",
        lesson="Retry writes only with a verified deduplication contract.",
    )


class Generator:
    config_id = "test-dream-v1"

    def __init__(self, *, blocked: bool = False, unknown_id: bool = False) -> None:
        self.inputs: list[DreamGenerationInput] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.unknown_id = unknown_id
        if not blocked:
            self.release.set()

    async def generate(self, value: DreamGenerationInput) -> GenerationResult[DreamPlan]:
        self.inputs.append(value)
        self.started.set()
        await self.release.wait()
        used = next(item.evidence_id for item in value.evidence.evidence if item.kind == "source")
        proposal = (
            experience()
            if value.operation == "refine_experience"
            else SkillContent(
                name="retry-idempotent-writes",
                description="Retry writes with a tested deduplication contract.",
                instructions="Check the commit status and replay using the original idempotency key.",
                validation=("Verify that a replay creates no duplicate record.",),
            )
        )
        return GenerationResult(
            output=DreamPlan(
                outcome="proposed",
                reason="The exact task evidence supports this bounded rule.",
                intent=("refine" if value.target_evidence_id is not None else "create")
                if value.operation == "refine_experience"
                else "derive",
                proposal=proposal,
                evidence_ids=("unknown" if self.unknown_id else used,),
            ),
            usage=InferenceUsage(requests=1, input_tokens=80, output_tokens=45),
        )


class NoChangeGenerator:
    config_id = "test-dream-no-change-v1"

    def __init__(self) -> None:
        self.inputs: list[DreamGenerationInput] = []

    async def generate(self, value: DreamGenerationInput) -> GenerationResult[DreamPlan]:
        self.inputs.append(value)
        return GenerationResult(
            output=DreamPlan(outcome="no_change", reason="The selected evidence already expresses the bounded rule."),
            usage=InferenceUsage(requests=1, input_tokens=24, output_tokens=12),
        )


class MemoryPipeline:
    async def generate(self, request: AtomicMemoryExtractionInput, /) -> GenerationResult[AtomicMemoryExtractionOutput]:
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(
                        kind="working_note",
                        text="The replay test passed without duplicate writes.",
                        evidence_ids=(evidence.evidence_id,),
                        description="Extracted fixture fact",
                    )
                    for evidence in request.evidence
                )
            )
        )


class IncubationPipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[Source, ...]] = []

    async def incubate(self, sources: tuple[Source, ...], /) -> tuple[ExperienceCandidateInput, ...]:
        self.calls.append(sources)
        return tuple(
            ExperienceCandidateInput(
                proposal=experience().model_copy(update={"outcome": source.content}),
                sources=(SourceRef(source_type="content", source_id=source.name),),
            )
            for source in sources
            if isinstance(source, ContentSource) and source.metadata.get("kind") == "task-outcome"
        )


def config(database: DatabaseConfig) -> BuiltinConfig:
    return BuiltinConfig(database=database, runtime=RuntimeConfig())


@pytest.mark.parametrize(
    "processing", ["noop_queued", "noop_generation", "noop_review", "pending", "activation", "head"]
)
def test_profile_dream_isolated_from_source_processing(  # noqa: C901 - exercise independent processing interleavings
    database: DatabaseConfig, processing: str
) -> None:
    import httpx

    from powercontext.client import PowerContextClient
    from powercontext.http import GetCandidateRequest as TransportGetCandidateRequest
    from powercontext.http import ProfileCandidateProposal as TransportProfileCandidateProposal
    from powercontext.server.app import create_app

    class OrdinaryGenerator:
        async def generate(self, value):
            return "# Profile\n\nOrdinary Source update." if processing in {"pending", "head"} else None

    class DreamGenerator:
        config_id = "profile-isolation"

        async def generate(self, value):
            assert value.profile_policy.model_dump() == {"format_version": 1, "generation_enabled": True}
            if processing == "noop_generation":
                await process_source()
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="An explicit relocation corrects the previous location.",
                    intent="correct",
                    proposal=ProfileWriteContent(content="# Profile\n\nBased in Shenzhen."),
                    evidence_ids=tuple(item.evidence_id for item in value.evidence.evidence if item.kind == "source"),
                ),
                usage=InferenceUsage(requests=1),
            )

    async def process_source():
        assert runtime is not None
        before = await runtime.profiles.get_policy(sid)
        if processing == "activation":
            await runtime.profiles.put_policy(
                sid, generation_enabled=True, activation_mode="review_required", expected_version=before.version
            )
        else:
            result = await runtime.profiles.flush(sid)
            assert result.status == (
                "review_pending" if processing == "pending" else "updated" if processing == "head" else "noop"
            )
        after = await runtime.profiles.get_policy(sid)
        assert after.version > before.version
        assert after.generation_enabled == before.generation_enabled

    runtime = None
    sid = ""

    async def scenario():
        nonlocal runtime, sid
        async with open_builtin_runtime(
            config(database), profile_generator=OrdinaryGenerator(), dream_generator=DreamGenerator()
        ) as runtime:
            sid = (
                await runtime.scopes.create(
                    ScopeDraft(
                        title="Profile isolation", summary="Independent Source processing", idempotency_key="isolation"
                    )
                )
            ).scope_id
            await runtime.profiles.put_policy(
                sid,
                generation_enabled=True,
                activation_mode="review_required" if processing == "pending" else "automatic",
                expected_version=0,
            )
            async with runtime.profiles.database.transaction() as connection:
                initial = await runtime.profiles.artifacts.create(
                    connection,
                    sid,
                    "profile",
                    ProfileDraft(
                        content=ProfileContent(
                            content="# Profile\n\nBased in Shanghai.",
                            generation=ProfileGeneration(mode="manual_create", created_at=datetime.now(UTC)),
                        )
                    ),
                )
            source = await runtime.sources.for_scope(sid).capture(
                CaptureSource(source_id="move", content="I moved to Shenzhen permanently.", metadata={})
            )
            request = CreateDreamRunRequest(
                operation="revise_profile",
                target=initial.as_ref(),
                artifacts=(initial.as_ref(),),
                sources=(source.source_ref,),
                idempotency_key="first",
            )
            dream = runtime.dream.for_scope(sid)
            review = runtime.review.for_scope(sid)
            run = await dream.create(request)
            if processing == "noop_queued":
                await process_source()
            await process_pending(runtime)
            completed = await dream.get(GetDreamRunRequest(run_id=run.run_id))
            assert completed.outcome == "proposed", completed.error
            assert completed.candidate is not None
            if processing in {"noop_review", "pending", "activation", "head"}:
                await process_source()
            policy_before = await runtime.profiles.get_policy(sid)
            async with runtime.profiles.database.transaction() as connection:
                cursor_before = await runtime.profiles.cursors.load(connection, sid, "profile-source-window")
            if processing != "head":
                second = await dream.create(request.model_copy(update={"idempotency_key": "second"}))
                await process_pending(runtime)
                reused = await dream.get(GetDreamRunRequest(run_id=second.run_id))
                assert reused.reused and reused.candidate == completed.candidate
                assert reused.usage.model_calls == 0
            approval = ApproveCandidateRequest(
                candidate_id=completed.candidate.candidate_id, expected_version=completed.candidate.version
            )
            if processing == "head":
                with pytest.raises(BaseValueConflictError):
                    await review.approve(approval)
                return
            # Candidate readback and edits preserve the frozen settings and their digest.
            candidate = await review.get(GetCandidateRequest(candidate_id=completed.candidate.candidate_id))
            assert candidate.proposal.policy_digest == candidate.proposal.policy_snapshot.digest
            app = create_app(application=cast(ServerApplication, runtime))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as transport:
                client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
                received = await client.get_candidate(
                    TransportGetCandidateRequest(scope_id=sid, candidate_id=candidate.candidate_id)
                )
                assert isinstance(received.proposal, TransportProfileCandidateProposal)
                assert received.proposal.policy_snapshot is not None
                assert received.proposal.policy_snapshot.model_dump(
                    mode="json"
                ) == candidate.proposal.policy_snapshot.model_dump(mode="json")
                assert received.proposal.policy_digest == candidate.proposal.policy_digest
            revised = await review.revise(
                ReviseCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                    proposal=ProfileWriteContent(content="# Profile\n\nBased in Shenzhen, China."),
                    sources=candidate.sources,
                    artifacts=candidate.artifacts,
                    target=candidate.target,
                    reason="Clarify the location.",
                )
            )
            assert revised.proposal.policy_snapshot == candidate.proposal.policy_snapshot
            assert revised.proposal.policy_digest == candidate.proposal.policy_digest
            approved = await review.approve(approval.model_copy(update={"expected_version": revised.version}))
            assert approved.result_artifact.revision == initial.revision + 1
            assert await runtime.profiles.get_policy(sid) == policy_before
            async with runtime.profiles.database.transaction() as connection:
                assert await runtime.profiles.cursors.load(connection, sid, "profile-source-window") == cursor_before
            if processing == "pending":
                assert policy_before.pending_candidate_id is not None
                ordinary = await review.get(GetCandidateRequest(candidate_id=policy_before.pending_candidate_id))
                assert ordinary.status == "pending"

    asyncio.run(scenario())


@pytest.mark.parametrize("policy_change", [None, "queued", "generation", "review", "memory"])
def test_profile_dream_requires_review_and_preserves_source_cursor(
    database: DatabaseConfig, policy_change: str | None
) -> None:
    class ProfileDreamGenerator:
        config_id = "profile-dream-test"

        async def generate(self, value: DreamGenerationInput) -> GenerationResult[DreamPlan]:
            assert value.operation == "revise_profile"
            assert value.profile_policy is not None
            assert value.profile_policy.model_dump() == {"format_version": 1, "generation_enabled": False}
            if policy_change == "generation":
                await change_policy()
            assert any(item.kind == "profile" for item in value.evidence.evidence)
            evidence = tuple(item.evidence_id for item in value.evidence.evidence if item.kind in {"memory", "source"})
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="The explicit relocation supersedes the old location.",
                    intent="correct",
                    proposal=ProfileWriteContent(content="# Profile\n\nBased in Shenzhen."),
                    evidence_ids=evidence,
                ),
                usage=InferenceUsage(requests=1),
            )

    async def change_policy() -> None:
        assert profiles is not None
        await profiles.put_policy(scope_id, generation_enabled=True, expected_version=1)

    profiles = None
    scope_id = ""

    async def scenario() -> None:
        nonlocal profiles, scope_id
        async with open_builtin_runtime(
            config(database),
            candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
            dream_generator=ProfileDreamGenerator(),
        ) as runtime:
            assert runtime.scopes is not None and runtime.profiles is not None
            citation = None
            if policy_change == "memory":
                scope_id, _, citation = await seed(runtime)
            else:
                scope = await runtime.scopes.create(
                    ScopeDraft(
                        title="Profile Dream", summary="Review before activation", idempotency_key="profile-dream"
                    )
                )
                scope_id = scope.scope_id
            profiles = runtime.profiles
            await runtime.profiles.put_policy(scope_id, generation_enabled=False, expected_version=0)
            async with runtime.profiles.database.transaction() as connection:
                initial = await runtime.profiles.artifacts.create(
                    connection,
                    scope_id,
                    "profile",
                    ProfileDraft(
                        content=ProfileContent(
                            content="# Profile\n\nBased in Shanghai.",
                            generation=ProfileGeneration(mode="manual_create", created_at=datetime.now(UTC)),
                        )
                    ),
                )
            source = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(
                    source_id="relocation", content="I moved to Shenzhen and am now based there.", metadata={}
                )
            )
            run = await runtime.dream.for_scope(scope_id).create(
                CreateDreamRunRequest(
                    operation="revise_profile",
                    target=initial.as_ref(),
                    artifacts=(initial.as_ref(), *((citation,) if citation is not None else ())),
                    sources=(source.source_ref,),
                    idempotency_key="relocation",
                )
            )
            if policy_change == "queued":
                await change_policy()
            await process_pending(runtime)
            completed = await runtime.dream.for_scope(scope_id).get(GetDreamRunRequest(run_id=run.run_id))
            if policy_change in {"queued", "generation"}:
                assert completed.status == "failed" and completed.error == "policy_changed"
                assert completed.candidate is None
                return
            assert completed.outcome == "proposed" and completed.candidate is not None
            async with runtime.profiles.database.transaction() as connection:
                before = await runtime.profiles.latest(connection, scope_id)
            assert before is not None and before.content.content.endswith("Shanghai.\n")
            if policy_change == "review":
                await change_policy()
                with pytest.raises(BaseValueConflictError):
                    await runtime.review.for_scope(scope_id).approve(
                        ApproveCandidateRequest(
                            candidate_id=completed.candidate.candidate_id,
                            expected_version=completed.candidate.version,
                        )
                    )
                rejected = await runtime.review.for_scope(scope_id).reject(
                    RejectCandidateRequest(
                        candidate_id=completed.candidate.candidate_id,
                        expected_version=completed.candidate.version,
                        reason="Policy changed; regenerate under the current policy.",
                    )
                )
                assert rejected.status == "rejected"
                return
            approved = await runtime.review.for_scope(scope_id).approve(
                ApproveCandidateRequest(
                    candidate_id=completed.candidate.candidate_id,
                    expected_version=completed.candidate.version,
                )
            )
            assert approved.result_artifact is not None
            async with runtime.profiles.database.transaction() as connection:
                after = await runtime.profiles.latest(connection, scope_id)
            assert after is not None and after.content.content.endswith("Shenzhen.\n")
            assert after.content.generation.mode == "dream_review_approved"
            assert after.content.generation.dream_run_id == run.run_id
            assert tuple(ref for ref in after.lineage.artifacts if ref.family == "atomic-memory") == (
                () if citation is None else (citation,)
            )
            if citation is not None:
                await forget_memory(runtime, scope_id, citation)
                correction = await runtime.dream.for_scope(scope_id).create(
                    CreateDreamRunRequest(
                        operation="revise_profile",
                        target=after.as_ref(),
                        artifacts=(after.as_ref(),),
                        sources=(source.source_ref,),
                        idempotency_key="correct-profile-after-retirement",
                    )
                )
                await process_pending(runtime)
                correction = await runtime.dream.for_scope(scope_id).get(GetDreamRunRequest(run_id=correction.run_id))
                assert correction.candidate is not None
                corrected = await runtime.review.for_scope(scope_id).approve(
                    ApproveCandidateRequest(
                        candidate_id=correction.candidate.candidate_id,
                        expected_version=correction.candidate.version,
                    )
                )
                assert corrected.result_artifact is not None and corrected.result_artifact.revision == 3

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("concurrent_revision", "target_change"), [(False, None), (True, None), (False, "forgotten"), (False, "replaced")]
)
def test_memory_dream_revises_only_selected_atomic_memory_after_review(
    database: DatabaseConfig, concurrent_revision: bool, target_change: str | None
) -> None:
    class MemoryDreamGenerator:
        config_id = "memory-dream-test"

        def __init__(self):
            self.citation = None
            self.correction = None

        async def generate(self, value: DreamGenerationInput) -> GenerationResult[DreamPlan]:
            assert value.operation == "revise_memory"
            assert self.citation is not None and self.correction is not None
            evidence = tuple(item.evidence_id for item in value.evidence.evidence if item.kind in {"memory", "source"})
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="The correction changes one current Memory entry.",
                    intent="correct",
                    proposal=AtomicMemoryContent(
                        kind="task_record",
                        text="The corrected replay did create a duplicate; avoid blind retries.",
                        description="Explicit fixture memory",
                    ),
                    evidence_ids=evidence,
                ),
                usage=InferenceUsage(requests=1),
            )

    async def scenario() -> None:
        generator = MemoryDreamGenerator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=generator
        ) as runtime:
            scope, _, citation = await seed(runtime)
            correction = await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="correction", content="The replay created two records.", metadata={})
            )
            generator.citation = citation
            generator.correction = correction.source_ref
            run = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="revise_memory",
                    target=citation,
                    artifacts=(citation,),
                    sources=(correction.source_ref,),
                    idempotency_key="correct-entry",
                )
            )
            await process_pending(runtime)
            completed = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=run.run_id))
            assert completed.outcome == "proposed" and completed.candidate is not None
            from powercontext.server.mapping import candidate_response

            candidate = await runtime.review.for_scope(scope).get(
                GetCandidateRequest(candidate_id=completed.candidate.candidate_id)
            )
            transport_proposal = candidate_response(candidate).proposal
            from powercontext.http import AtomicMemoryReadContent as TransportAtomicMemoryContent

            assert isinstance(transport_proposal, TransportAtomicMemoryContent)
            assert candidate.audit.dream_run_id == run.run_id
            before = await runtime.atomic_memory.for_scope(scope).list()
            assert any(item.ref == citation for item in before.items)
            approval = ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
            if target_change is not None:
                from powercontext.errors import RevisionConflictError

                if target_change == "forgotten":
                    await forget_memory(runtime, scope, citation)
                else:
                    await runtime._provider.records.replace_artifact(
                        scope,
                        "atomic-memory",
                        citation.artifact_id,
                        f'"revision:{citation.revision}"',
                        ArtifactWrite(
                            content={
                                "kind": "working_note",
                                "text": "A newer manual correction.",
                                "description": "Explicit fixture memory",
                            }
                        ),
                    )
                current = await runtime.atomic_memory.for_scope(scope).get(citation.artifact_id)
                expected_error = EvidenceResolutionError if target_change == "forgotten" else RevisionConflictError
                with pytest.raises(expected_error):
                    await runtime.review.for_scope(scope).approve(approval)
                assert await runtime.atomic_memory.for_scope(scope).get(citation.artifact_id) == current
                pending = await runtime.review.for_scope(scope).get(
                    GetCandidateRequest(candidate_id=candidate.candidate_id)
                )
                assert pending.status == "pending" and pending.result_artifact is None
                return
            if concurrent_revision:
                from powercontext.builtin.review.errors import CandidateConflictError, CandidateTerminalError

                results = await asyncio.wait_for(
                    asyncio.gather(
                        runtime.review.for_scope(scope).approve(approval),
                        runtime.review.for_scope(scope).revise(
                            ReviseCandidateRequest(
                                candidate_id=candidate.candidate_id,
                                expected_version=candidate.version,
                                proposal=candidate.proposal,
                                sources=candidate.sources,
                                artifacts=candidate.artifacts,
                                target=candidate.target,
                                reason="Review confirms the correction.",
                            )
                        ),
                        return_exceptions=True,
                    ),
                    timeout=10,
                )
                assert not all(isinstance(result, Exception) for result in results)
                for result in results:
                    if isinstance(result, Exception):
                        assert isinstance(result, (CandidateConflictError, CandidateTerminalError))
                latest = await runtime.review.for_scope(scope).get(
                    GetCandidateRequest(candidate_id=candidate.candidate_id)
                )
                approved = (
                    latest
                    if latest.status == "approved"
                    else await runtime.review.for_scope(scope).approve(
                        ApproveCandidateRequest(candidate_id=latest.candidate_id, expected_version=latest.version)
                    )
                )
            else:
                approved = await runtime.review.for_scope(scope).approve(approval)
            assert approved.result_artifact is not None
            after = await runtime.atomic_memory.for_scope(scope).list()
            replacement = next(item for item in after.items if item.ref.artifact_id == citation.artifact_id)
            assert replacement.artifact.content.text.startswith("The corrected replay")
            assert replacement.ref.revision == citation.revision + 1
            assert len(after.items) == len(before.items)

    asyncio.run(scenario())


@pytest.mark.parametrize("retire_evidence, exhausted_window", [(False, False), (True, False), (False, True)])
def test_topic_memory_dream_publishes_complete_revision_after_review(
    database: DatabaseConfig, retire_evidence: bool, exhausted_window: bool
) -> None:
    class TopicDreamGenerator:
        config_id = "topic-dream-test"

        async def generate(self, value: DreamGenerationInput) -> GenerationResult[DreamPlan]:
            assert value.operation == "revise_topic_memory"
            assert any(item.kind == "topic_memory" for item in value.evidence.evidence)
            source_id = next(
                (item.evidence_id for item in value.evidence.evidence if item.kind == "memory"),
                next(item.evidence_id for item in value.evidence.evidence if item.kind == "source"),
            )
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="Verified investigation changed the topic conclusion.",
                    intent="correct",
                    proposal=TopicMemoryContent(
                        title="Retry investigation",
                        summary="The replay can create a duplicate.",
                        detail="# Confirmed\nA later check found two records.\n\n# Next\nRequire idempotency.",
                    ),
                    evidence_ids=(source_id,),
                ),
                usage=InferenceUsage(requests=1),
            )

    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database),
            candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
            dream_generator=TopicDreamGenerator(),
        ) as runtime:
            assert runtime.scopes is not None
            scope_id, _, citation = await seed(runtime)
            source = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="topic-correction", content="A later replay created two records.", metadata={})
            )
            topics = runtime._provider.repositories.topic_memories
            original = TopicMemoryContent(
                title="Retry investigation",
                summary="The replay creates one record.",
                detail="# Confirmed\nAn earlier check found one record.",
            )
            async with runtime._provider.database.transaction() as connection:
                published = await topics.publish_create(
                    connection,
                    scope_id,
                    "topic-dream-target",
                    TopicMemoryDraft(content=original, sources=(source.source_ref,)),
                    prepare_topic_memory_projection(original),
                )
            if exhausted_window:
                from sqlalchemy import insert, select, update

                from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
                from powercontext.builtin.persistence.tables import (
                    ARTIFACT_PROCESSING_INTENTS_TABLE,
                    SOURCE_JOURNAL_HEADS_TABLE,
                    TOPIC_MEMORY_WORK_BUDGETS_TABLE,
                )
                from powercontext.builtin.persistence.topic_memory_budget import MAX_TOPIC_MEMORY_WORK_ATTEMPTS

                async with runtime._provider.database.transaction() as connection:
                    await ArtifactProcessingIntentRepository().mark_dirty(
                        connection, scope_id, TOPIC_MEMORY_SOURCE_WINDOW_BINDING
                    )
                    await connection.execute(
                        insert(TOPIC_MEMORY_WORK_BUDGETS_TABLE).values(
                            scope_id=scope_id,
                            binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                            source_after=0,
                            source_through=await connection.scalar(
                                select(SOURCE_JOURNAL_HEADS_TABLE.c.position).where(
                                    SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id
                                )
                            ),
                            attempt_id="exhausted-window",
                            attempts=MAX_TOPIC_MEMORY_WORK_ATTEMPTS,
                            requests=0,
                            tokens=0,
                            failure_code="provider_error",
                        )
                    )
                    await connection.execute(
                        update(ARTIFACT_PROCESSING_INTENTS_TABLE)
                        .where(
                            ARTIFACT_PROCESSING_INTENTS_TABLE.c.scope_id == scope_id,
                            ARTIFACT_PROCESSING_INTENTS_TABLE.c.binding_name == TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                        )
                        .values(consecutive_dream_attempts=4)
                    )
            run = await runtime.dream.for_scope(scope_id).create(
                CreateDreamRunRequest(
                    operation="revise_topic_memory",
                    target=published.topic.as_ref(),
                    artifacts=(published.topic.as_ref(), citation),
                    sources=(source.source_ref,),
                    idempotency_key="topic-correction",
                )
            )
            await process_pending(runtime)
            completed = await runtime.dream.for_scope(scope_id).get(GetDreamRunRequest(run_id=run.run_id))
            assert completed.outcome == "proposed" and completed.candidate is not None
            if exhausted_window:
                from powercontext.builtin.runtime.topic_memory_scope import topic_memory_processing_block

                async with runtime._provider.database.transaction() as connection:
                    # Finishing Dream does not reopen the exhausted automatic window.
                    assert (
                        await topic_memory_processing_block(
                            connection, scope_id, TOPIC_MEMORY_SOURCE_WINDOW_BINDING, dream_enabled=True
                        )
                        is not None
                    )
            async with runtime._provider.database.transaction() as connection:
                before = await runtime._provider.repositories.artifacts.latest(
                    connection, scope_id, "topic-memory", "topic-dream-target"
                )
            assert before.revision == 1
            candidate = await runtime.review.for_scope(scope_id).get(
                GetCandidateRequest(candidate_id=completed.candidate.candidate_id)
            )
            assert citation in candidate.artifacts
            if retire_evidence:
                await forget_memory(runtime, scope_id, citation)
                with pytest.raises(EvidenceResolutionError, match="memory_entry_inactive"):
                    await runtime.review.for_scope(scope_id).approve(
                        ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
                    )
                async with runtime._provider.database.transaction() as connection:
                    unchanged = await topics.get_exact(connection, scope_id, published.topic.as_ref())
                assert unchanged.is_current
                return
            approved = await runtime.review.for_scope(scope_id).approve(
                ApproveCandidateRequest(
                    candidate_id=completed.candidate.candidate_id,
                    expected_version=completed.candidate.version,
                )
            )
            assert approved.result_artifact is not None and approved.result_artifact.revision == 2
            async with runtime._provider.database.transaction() as connection:
                after = await topics.get_exact(connection, scope_id, approved.result_artifact)
            assert after.topic.content.summary == "The replay can create a duplicate."
            assert after.is_current
            assert citation in after.topic.lineage.artifacts
            # A retired historical dependency must not prevent correcting the target
            # using independent, still-valid evidence.
            await forget_memory(runtime, scope_id, citation)
            second = await runtime.dream.for_scope(scope_id).create(
                CreateDreamRunRequest(
                    operation="revise_topic_memory",
                    target=after.topic.as_ref(),
                    artifacts=(after.topic.as_ref(),),
                    sources=(source.source_ref,),
                    idempotency_key="correct-after-retirement",
                )
            )
            await process_pending(runtime)
            second = await runtime.dream.for_scope(scope_id).get(GetDreamRunRequest(run_id=second.run_id))
            assert second.outcome == "proposed" and second.candidate is not None
            candidate = await runtime.review.for_scope(scope_id).get(
                GetCandidateRequest(candidate_id=second.candidate.candidate_id)
            )
            revised = await runtime.review.for_scope(scope_id).revise(
                ReviseCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                    proposal=candidate.proposal,
                    sources=candidate.sources,
                    artifacts=candidate.artifacts,
                    target=candidate.target,
                    reason="Confirmed against the independent source.",
                )
            )
            corrected = await runtime.review.for_scope(scope_id).approve(
                ApproveCandidateRequest(
                    candidate_id=revised.candidate_id,
                    expected_version=revised.version,
                )
            )
            assert corrected.result_artifact is not None and corrected.result_artifact.revision == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("change_objective", [False, True])
def test_handoff_dream_publishes_for_explicit_continue(database: DatabaseConfig, change_objective: bool) -> None:
    class HandoffDreamGenerator:
        config_id = "handoff-dream-test"

        def __init__(self):
            self.correction = None

        async def generate(self, value: DreamGenerationInput) -> GenerationResult[DreamPlan]:
            assert value.operation == "refresh_handoff" and self.correction is not None
            source_id = next(item.evidence_id for item in value.evidence.evidence if item.kind == "source")
            citation = HandoffSourceCitation(source_ref=self.correction)
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="The new source confirms the handoff progress.",
                    intent="correct",
                    proposal=HandoffContent(
                        objective="Work on another task." if change_objective else "Finish the support request.",
                        state=(HandoffStatement(text="The replacement has been issued.", citations=(citation,)),),
                        disposition="continuable",
                        next_action=HandoffStatement(
                            text="Confirm the tracking number with the recipient.", citations=(citation,)
                        ),
                    ),
                    evidence_ids=(source_id,),
                ),
                usage=InferenceUsage(requests=1),
            )

    async def scenario() -> None:
        generator = HandoffDreamGenerator()
        async with open_builtin_runtime(config(database), dream_generator=generator) as runtime:
            capability = next(
                item
                for item in (await runtime.capabilities()).artifact_dreaming_operations
                if item.operation == "refresh_handoff"
            )
            assert capability.effect == "review_then_publish"
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Handoff Dream", summary="Review before publication", idempotency_key="handoff-dream")
            )
            scope_id = scope.scope_id
            original_source = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="handoff-original", content="The replacement is pending.", metadata={})
            )
            handoffs = runtime.handoff.for_scope(scope_id)
            initial = await handoffs.finalize(
                HandoffDraft(
                    objective="Finish the support request.",
                    state=(
                        HandoffStatement(
                            text="The replacement is pending.",
                            citations=(HandoffSourceCitation(source_ref=original_source.source_ref),),
                        ),
                    ),
                    disposition="continuable",
                )
            )
            current = await handoffs.commit(initial)
            correction = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="handoff-correction", content="The replacement has been issued.", metadata={})
            )
            generator.correction = correction.source_ref
            run = await runtime.dream.for_scope(scope_id).create(
                CreateDreamRunRequest(
                    operation="refresh_handoff",
                    target=current.as_ref(),
                    artifacts=(current.as_ref(),),
                    sources=(correction.source_ref,),
                    idempotency_key="refresh-handoff",
                )
            )
            for _ in range(3):
                await process_pending(runtime)
                completed = await runtime.dream.for_scope(scope_id).get(GetDreamRunRequest(run_id=run.run_id))
                if completed.terminal:
                    break
            if change_objective:
                assert completed.status == "failed" and completed.candidate is None
                assert (await handoffs.latest()).content.objective == "Finish the support request."
                return
            assert completed.outcome == "proposed" and completed.candidate is not None, (
                completed.status,
                completed.error,
            )
            assert (await handoffs.latest()).revision == 1
            assert (await handoffs.continue_latest()).selected_revision == current.as_ref()
            from powercontext.builtin.review.errors import CandidateConflictError, InvalidCandidateError

            candidate = await runtime.review.for_scope(scope_id).get(
                GetCandidateRequest(candidate_id=completed.candidate.candidate_id)
            )
            with pytest.raises(InvalidCandidateError, match="preserve the target objective"):
                await runtime.review.for_scope(scope_id).revise(
                    ReviseCandidateRequest(
                        candidate_id=candidate.candidate_id,
                        expected_version=candidate.version,
                        proposal=candidate.proposal.model_copy(update={"objective": "Work on another task."}),
                        sources=candidate.sources,
                        artifacts=candidate.artifacts,
                        target=candidate.target,
                        reason="Changed task.",
                    )
                )
            approved = await runtime.review.for_scope(scope_id).approve(
                ApproveCandidateRequest(
                    candidate_id=completed.candidate.candidate_id,
                    expected_version=completed.candidate.version,
                )
            )
            assert approved.result_artifact is not None
            retry = await runtime.review.for_scope(scope_id).approve(
                ApproveCandidateRequest(
                    candidate_id=completed.candidate.candidate_id,
                    expected_version=completed.candidate.version,
                )
            )
            assert retry == approved
            with pytest.raises(CandidateConflictError):
                await runtime.review.for_scope(scope_id).approve(
                    ApproveCandidateRequest(
                        candidate_id=completed.candidate.candidate_id,
                        expected_version=completed.candidate.version + 1,
                    )
                )
            updated = await handoffs.latest()
            assert updated is not None and updated.revision == 2
            assert updated.content.generation is None
            assert updated.content.state[0].text == "The replacement has been issued."
            resolution = await handoffs.continue_latest()
            assert resolution.selected_revision == updated.as_ref()
            assert resolution.content == updated.content

    asyncio.run(scenario())


@pytest.mark.parametrize("evidence_kind", ["source", "approved-experience", "memory"])
def test_skill_dream_revision_publishes_after_review_without_trusted_evaluation(
    database: DatabaseConfig, evidence_kind: str
) -> None:
    class SkillRevisionGenerator(Generator):
        async def generate(self, value: DreamGenerationInput) -> GenerationResult[DreamPlan]:
            assert value.operation == "revise_skill"
            target = next(item for item in value.evidence.evidence if item.kind == "skill")
            assert "Retry immediately." in target.text
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="The feedback records duplicate execution.",
                    intent="correct",
                    proposal=SkillContent(
                        name="receipt-check",
                        description="Check the receipt before retrying.",
                        instructions="Check the receipt first, then retry if needed.",
                        validation=("No duplicate action was issued.",),
                    ),
                    evidence_ids=tuple(
                        item.evidence_id for item in value.evidence.evidence if item.kind in {"source", "memory"}
                    ),
                ),
                usage=InferenceUsage(requests=1),
            )

    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database),
            candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
            dream_generator=SkillRevisionGenerator(),
        ) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Skill Revision", summary="Package stays pending", idempotency_key="skill-revision")
            )
            source = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="skill-feedback", content="The retry duplicated the action.", metadata={})
            )
            original = SkillContent(
                name="receipt-check",
                description="Retry safely.",
                instructions="Retry immediately.",
                validation=("A retry was attempted.",),
                license="MIT",
            )
            async with runtime._provider.database.transaction() as connection:
                target = await runtime._provider.repositories.artifacts.create(
                    connection,
                    scope.scope_id,
                    "receipt-check",
                    SkillDraft(content=original, sources=(source.source_ref,)),
                )
            prepared = await runtime._provider.review(scope.scope_id).prepare_skill_revision(
                target.as_ref(),
                SkillContent(
                    name="invented-name",
                    description="Check the receipt before retrying.",
                    instructions="Check the receipt first, then retry if needed.",
                    validation=("No duplicate action was issued.",),
                ),
            )
            assert prepared.name == "receipt-check" and prepared.license == "MIT"
            assert prepared.package is not None and prepared.package.file_count == 1
            assert (
                await runtime.skill.for_scope(scope.scope_id).get(GetSkillRequest(artifact=target.as_ref()))
            ).revision == 1
            artifacts = (target.as_ref(),)
            sources = (source.source_ref,)
            citations = ()
            if evidence_kind == "memory":
                await runtime.atomic_memory.for_scope(scope.scope_id).flush()
                citations = ((await runtime.atomic_memory.for_scope(scope.scope_id).list()).items[0].ref,)
                sources = ()
            if evidence_kind == "approved-experience":
                evidence = await runtime.experience.for_scope(scope.scope_id).propose(
                    ProposeExperienceRequest(proposal=experience(), sources=sources)
                )
                evidence = await runtime.review.for_scope(scope.scope_id).approve(
                    ApproveCandidateRequest(candidate_id=evidence.candidate_id, expected_version=evidence.version)
                )
                assert evidence.result_artifact is not None
                artifacts = (*artifacts, evidence.result_artifact)
                sources = ()
            run = await runtime.dream.for_scope(scope.scope_id).create(
                CreateDreamRunRequest(
                    operation="revise_skill",
                    target=target.as_ref(),
                    artifacts=(*artifacts, *citations),
                    sources=sources,
                    idempotency_key="skill-feedback",
                )
            )
            await process_pending(runtime)
            completed = await runtime.dream.for_scope(scope.scope_id).get(GetDreamRunRequest(run_id=run.run_id))
            assert completed.candidate is not None, completed
            candidate = await runtime.review.for_scope(scope.scope_id).get(
                GetCandidateRequest(candidate_id=completed.candidate.candidate_id)
            )
            assert tuple(ref for ref in candidate.artifacts if ref.family == "atomic-memory") == citations
            changed_metadata = await runtime._provider.review(scope.scope_id).prepare_skill(
                candidate.proposal.model_copy(update={"name": "changed-name", "package": None})
            )
            from powercontext.builtin.review.errors import InvalidCandidateError

            with pytest.raises(InvalidCandidateError, match="protected package metadata"):
                await runtime.review.for_scope(scope.scope_id).revise(
                    ReviseCandidateRequest(
                        candidate_id=candidate.candidate_id,
                        expected_version=candidate.version,
                        proposal=changed_metadata,
                        sources=candidate.sources,
                        artifacts=candidate.artifacts,
                        target=candidate.target,
                        reason="A revision cannot rename the managed Skill.",
                    )
                )
            approved = await runtime.review.for_scope(scope.scope_id).approve(
                ApproveCandidateRequest(
                    candidate_id=completed.candidate.candidate_id, expected_version=completed.candidate.version
                )
            )
            assert approved.result_artifact is not None and approved.result_artifact.revision == 2
            skill = await runtime.skill.for_scope(scope.scope_id).get(
                GetSkillRequest(artifact=approved.result_artifact)
            )
            assert skill.content.name == "receipt-check" and skill.content.license == "MIT"
            assert skill.content.instructions.startswith("Check the receipt first, then retry if needed.")
            assert skill.content.package is not None and skill.content.package.file_count == 1
            assert tuple(ref for ref in skill.lineage.artifacts if ref.family == "atomic-memory") == citations
            import httpx

            from powercontext.client import PowerContextClient
            from powercontext.http import GetSkillRequest as TransportGetSkillRequest
            from powercontext.server.app import create_app

            app = create_app(application=cast(ServerApplication, runtime))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as transport:
                client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
                result = await client.get_skill(
                    TransportGetSkillRequest.model_validate({
                        "scope_id": scope.scope_id,
                        "artifact": approved.result_artifact.model_dump(mode="json"),
                    })
                )
                assert [
                    item.model_dump(mode="json") for item in result.artifact_refs if item.family == "atomic-memory"
                ] == [item.model_dump(mode="json") for item in citations]

    asyncio.run(scenario())


@pytest.mark.parametrize("existing", [False, True], ids=["new-config", "enable-skill"])
def test_wizard_skill_selection_accepts_dream_derivation(
    tmp_path: Path, existing: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from powercontext.builtin.runtime import open_builtin_runtime as open_runtime
    from powercontext.cli import config_wizard
    from powercontext.cli.config import app as config_app
    from powercontext.server.configuration import server_settings_context

    monkeypatch.setattr(config_wizard, "_listener_port_available", lambda host, port: True)
    output = tmp_path / "server.env"
    choices = "custom\nn\nn\nn\nn\nn\ny\nn\n"
    if existing:
        output.write_text(
            "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n"
            "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai-chat:test-model\n"
            "OPENAI_API_KEY=example-test-key\n"
            "POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_FAMILIES='[\"experience\"]'\n"
        )
        answers = "edit\ncapabilities\n" + choices + "done\ny\n"
    else:
        answers = "local\n" + choices + "n\n\nbailian\n\n\nexample-test-key\nnone\ny\ny\n"
    result = CliRunner().invoke(
        config_app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'dream.db'}\n" + answers,
    )
    assert result.exit_code == 0, result.output

    async def scenario(settings) -> None:
        # Admission uses the generated processing configuration; any background
        # model request stays local instead of reaching the wizard's provider.
        inference = settings.inference.model_copy(update={"generation_base_url": "http://127.0.0.1:9/v1"})
        configuration = BuiltinConfig(database=settings.database, runtime=settings.runtime, inference=inference)
        async with open_runtime(configuration, candidate_pipeline=atomic_memory_pipeline(MemoryPipeline())) as runtime:
            scope, _, citation = await seed(runtime)
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(proposal=experience(), artifacts=(citation,))
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
            )
            assert approved.result_artifact is not None
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="derive_skill", artifacts=(approved.result_artifact,), idempotency_key="wizard-skill"
                )
            )
            assert accepted.status == "queued"
            stored = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert stored.operation == "derive_skill"

    with server_settings_context(env_file=output) as settings:
        asyncio.run(scenario(settings))


async def seed(runtime: BuiltinRuntime):
    assert runtime.scopes is not None
    scope = await runtime.scopes.create(
        ScopeDraft(title="Dream test", summary="Bounded evidence", idempotency_key="dream")
    )
    captured = await runtime.sources.for_scope(scope.scope_id).capture(
        CaptureSource(
            source_id="write-test",
            content="Replayed the write with key K; exactly one row remained.",
            metadata={},
        )
    )
    assert runtime.atomic_memory is not None
    await runtime.atomic_memory.for_scope(scope.scope_id).flush()
    await runtime.atomic_memory.for_scope(scope.scope_id).create((
        AtomicMemoryContent(
            kind="private_note", text="UNSELECTED_SIBLING_SENTINEL", description="Explicit fixture memory"
        ),
    ))
    entries = await runtime.atomic_memory.for_scope(scope.scope_id).list()
    citation = next(item.ref for item in entries.items if item.artifact.content.kind == "working_note")
    return scope.scope_id, captured.source_ref, citation


async def forget_memory(runtime: BuiltinRuntime, scope_id: str, ref):
    assert runtime.atomic_memory is not None
    memories = runtime.atomic_memory.for_scope(scope_id)
    current = await memories.get(ref.artifact_id)
    return await memories.forget(
        ref.artifact_id, expected_revision=current.ref.revision, expected_state_version=current.state.state_version
    )


def test_memory_dream_approval_and_skill_preserve_exact_provenance(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=generator
        ) as runtime:
            scope, root, citation = await seed(runtime)
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation, citation), idempotency_key="memory"
            )
            accepted = await runtime.dream.for_scope(scope).create(request)
            assert accepted.status == "queued"
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (run.status, run.outcome, run.error) == ("succeeded", "proposed", None)
            assert run.input_manifest is not None
            assert run.candidate is not None
            assert run.attempt_count == run.usage.model_calls == 1
            assert run.usage.input_tokens == 80
            assert "UNSELECTED_SIBLING_SENTINEL" not in generator.inputs[0].model_dump_json()
            assert generator.inputs[0].target_evidence_id is None
            assert "The replay test passed" not in run.input_manifest.model_dump_json()
            assert len([item for item in generator.inputs[0].evidence.evidence if item.kind == "memory"]) == 1
            assert run.input_manifest.root_groups[0].independence == "unknown"
            candidate = await runtime.review.for_scope(scope).get(
                GetCandidateRequest(candidate_id=run.candidate.candidate_id)
            )
            assert (candidate.family, candidate.status, candidate.target) == ("experience", "pending", None)
            assert candidate.artifacts == (citation,)
            assert candidate.sources == (root,)
            followup = PrepareContextRequest(query=experience().lesson)
            pending_context = await runtime.context.for_scope(scope).prepare(followup)
            assert experience().lesson not in (pending_context.content or "")
            approved = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            assert approved.result_artifact is not None
            artifact = await runtime.experience.for_scope(scope).get(
                GetExperienceRequest(artifact=approved.result_artifact)
            )
            assert artifact.lineage.artifacts == (citation,)
            approved_context = await runtime.context.for_scope(scope).prepare(followup)
            assert approved_context.status == "ready" and approved_context.content is not None
            assert artifact.artifact_id in approved_context.content
            assert experience().lesson in approved_context.content
            skill_run = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="derive_skill",
                    artifacts=(artifact.as_ref(),),
                    idempotency_key="skill",
                )
            )
            await process_pending(runtime)
            derived = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=skill_run.run_id))
            assert (derived.status, derived.outcome, derived.error) == ("succeeded", "proposed", None)
            assert derived.attempt_count == derived.usage.model_calls == 1
            assert all(item.kind != "memory" for item in generator.inputs[1].evidence.evidence)
            assert generator.inputs[1].target_evidence_id is None
            assert derived.candidate is not None
            skill_candidate = await runtime.review.for_scope(scope).get(
                GetCandidateRequest(candidate_id=derived.candidate.candidate_id)
            )
            assert (skill_candidate.family, skill_candidate.status, skill_candidate.target) == (
                "skill",
                "pending",
                None,
            )
            assert skill_candidate.artifacts == (artifact.as_ref(),)
            skill_approval = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(
                    candidate_id=skill_candidate.candidate_id,
                    expected_version=skill_candidate.version,
                )
            )
            assert skill_approval.result_artifact is not None
            skill = await runtime.skill.for_scope(scope).get(GetSkillRequest(artifact=skill_approval.result_artifact))
            assert skill.content.package is not None
            replay = await runtime.dream.for_scope(scope).create(request)
            assert replay == run
            assert len(generator.inputs) == 2
            page = await runtime.dream.for_scope(scope).list(ListDreamRunsRequest(limit=1))
            assert page.runs[0].run_id == skill_run.run_id
            older = await runtime.dream.for_scope(scope).list(ListDreamRunsRequest(limit=1, cursor=page.next_cursor))
            assert older.runs[0].run_id == run.run_id
            with pytest.raises(DreamError, match="idempotency_conflict"):
                await runtime.dream.for_scope(scope).create(request.model_copy(update={"sources": (root,)}))

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["refine_experience", "derive_skill"])
def test_explicit_dream_no_change_is_terminal_without_a_candidate(
    database: DatabaseConfig, operation: DreamOperation
) -> None:
    async def scenario() -> None:
        generator = NoChangeGenerator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=generator
        ) as runtime:
            scope, root, citation = await seed(runtime)
            artifacts = (citation,)
            family = "experience"
            if operation == "derive_skill":
                proposed = await runtime.experience.for_scope(scope).propose(
                    ProposeExperienceRequest(proposal=experience(), sources=(root,))
                )
                approved = await runtime.review.for_scope(scope).approve(
                    ApproveCandidateRequest(
                        candidate_id=proposed.candidate_id,
                        expected_version=proposed.version,
                    )
                )
                assert approved.result_artifact is not None
                artifacts = (approved.result_artifact,)
                family = "skill"

            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation=operation,
                    artifacts=artifacts,
                    idempotency_key=f"no-change-{operation}",
                )
            )
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            pending = await runtime.review.for_scope(scope).list(ListCandidatesRequest(family=family))

            assert (run.status, run.outcome, run.error, run.candidate) == ("succeeded", "no_change", None, None)
            assert run.input_manifest is not None
            assert run.attempt_count == run.usage.model_calls == 1
            assert run.usage.input_tokens == 24
            assert len(generator.inputs) == 1
            assert generator.inputs[0].operation == operation
            assert pending.candidates == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["api", "background"])
def test_split_roles_accept_declared_dream_work(database: DatabaseConfig, role: str) -> None:
    if isinstance(database, SQLiteConfig):
        pytest.skip("SQLite supports only the all process role")

    async def scenario() -> None:
        configured = config(database).model_copy(
            update={
                "runtime": RuntimeConfig.model_validate({
                    "artifact_processing_role": role,
                    "artifact_processing_families": ("experience", "skill"),
                })
            }
        )
        async with open_builtin_runtime(
            configured, candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=Generator()
        ) as runtime:
            scope, _, citation = await seed(runtime)
            assert (await runtime.capabilities()).artifact_dreaming
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience", artifacts=(citation,), idempotency_key="split-role"
                )
            )
            assert accepted.status == "queued"
            assert accepted.model_config_id is None
            assert (await runtime.dream.for_scope(scope).list(ListDreamRunsRequest())).runs == (accepted,)
            if role == "background":
                await process_pending(runtime)
                assert (
                    await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
                ).status == "succeeded"

    asyncio.run(scenario())


def test_retirement_during_generation_prevents_candidate_commit(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        generator = Generator(blocked=True)
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=generator
        ) as runtime:
            scope, _, citation = await seed(runtime)
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="retired"
            )
            accepted = await runtime.dream.for_scope(scope).create(request)
            worker = asyncio.create_task(process_pending(runtime))
            await asyncio.wait_for(generator.started.wait(), timeout=5)
            await forget_memory(runtime, scope, citation)
            generator.release.set()
            await worker
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (run.status, run.error, run.candidate) == ("failed", "memory_entry_inactive", None)
            assert (await runtime.review.for_scope(scope).list(ListCandidatesRequest())).candidates == ()
            assert await runtime.dream.for_scope(scope).create(request) == run

    asyncio.run(scenario())


def test_review_rechecks_memory_and_revision_omission_preserves_citations(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline())
        ) as runtime:
            scope, root, citation = await seed(runtime)
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=experience(),
                    artifacts=(citation,),
                )
            )
            revised = await runtime.review.for_scope(scope).revise(
                ReviseCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=1,
                    proposal=experience(),
                    sources=(root,),
                    artifacts=(citation,),
                )
            )
            assert revised.artifacts == (citation,)
            await forget_memory(runtime, scope, citation)
            with pytest.raises(EvidenceResolutionError, match="memory_entry_inactive"):
                await runtime.review.for_scope(scope).approve(
                    ApproveCandidateRequest(
                        candidate_id=candidate.candidate_id,
                        expected_version=2,
                    )
                )
            cleared = await runtime.review.for_scope(scope).revise(
                ReviseCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=2,
                    proposal=experience(),
                    sources=(root,),
                    artifacts=(),
                )
            )
            assert cleared.artifacts == ()
            approved = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=3,
                )
            )
            assert approved.result_artifact is not None

    asyncio.run(scenario())


def test_unknown_model_evidence_fails_without_a_candidate(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database),
            candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
            dream_generator=Generator(unknown_id=True),
        ) as runtime:
            scope, _, citation = await seed(runtime)
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience",
                    artifacts=(citation,),
                    idempotency_key="invalid",
                )
            )
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (run.status, run.error) == ("failed", "invalid_generation_output")
            assert (await runtime.review.for_scope(scope).list(ListCandidatesRequest())).candidates == ()

    asyncio.run(scenario())


def test_dream_http_client_accepts_active_and_terminal_replays(database: DatabaseConfig) -> None:
    import httpx

    from powercontext.client import PowerContextClient
    from powercontext.http import CreateDreamRunRequest as TransportCreateDreamRunRequest
    from powercontext.http import DreamStatus, ExperienceProposal
    from powercontext.http import GetCandidateRequest as TransportGetCandidateRequest
    from powercontext.http import ReviseCandidateRequest as TransportReviseCandidateRequest
    from powercontext.server.app import create_app

    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=generator
        ) as runtime:
            scope, _, citation = await seed(runtime)
            app = create_app(application=cast(ServerApplication, runtime))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as transport:
                client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
                request = TransportCreateDreamRunRequest.model_validate_json(
                    CreateDreamRunRequest(
                        operation="refine_experience",
                        artifacts=(citation,),
                        idempotency_key="http",
                    ).model_dump_json()
                )
                accepted = await client.create_dream_run(scope, request)
                assert accepted.status == DreamStatus.QUEUED
                active = await transport.post(f"/v1/scopes/{scope}/dream", json=request.model_dump(mode="json"))
                assert active.status_code == 202
                assert active.json()["run_id"] == accepted.run_id
                await process_pending(runtime)
                complete = await client.get_dream_run(scope, accepted.run_id)
                assert complete.status == DreamStatus.SUCCEEDED
                assert await client.create_dream_run(scope, request) == complete
                replay = await transport.post(f"/v1/scopes/{scope}/dream", json=request.model_dump(mode="json"))
                assert replay.status_code == 200
                assert (await client.list_dream_runs(scope)).runs == [complete]
                assert len(generator.inputs) == 1
                assert complete.candidate is not None
                candidate = await client.get_candidate(
                    TransportGetCandidateRequest(scope_id=scope, candidate_id=complete.candidate.candidate_id)
                )
                assert isinstance(candidate.proposal, ExperienceProposal)
                revision = TransportReviseCandidateRequest(
                    scope_id=scope,
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                    proposal=candidate.proposal,
                    source_refs=candidate.source_refs,
                    artifact_refs=candidate.artifact_refs,
                )
                retained = await client.revise_candidate(revision)
                assert retained.artifact_refs == candidate.artifact_refs
                retained = await client.revise_candidate(
                    revision.model_copy(update={"expected_version": retained.version, "artifact_refs": None})
                )
                assert retained.artifact_refs == candidate.artifact_refs
                cleared = await client.revise_candidate(
                    revision.model_copy(update={"expected_version": retained.version, "artifact_refs": []})
                )
                assert cleared.artifact_refs == []
                assert await client.get_dream_run(scope, complete.run_id) == complete

    asyncio.run(scenario())


def test_concurrent_admission_and_workers_create_one_candidate(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=generator
        ) as runtime:
            scope, _, citation = await seed(runtime)
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="concurrent"
            )
            accepted = await asyncio.gather(*(runtime.dream.for_scope(scope).create(request) for _ in range(8)))
            assert len({run.run_id for run in accepted}) == 1
            await asyncio.gather(*(process_pending(runtime) for _ in range(3)))
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted[0].run_id))
            assert run.status == "succeeded"
            assert len(generator.inputs) == 1
            assert len((await runtime.review.for_scope(scope).list(ListCandidatesRequest())).candidates) == 1

    asyncio.run(scenario())


def test_transient_failure_retries_same_projection_with_bounded_calls(database: DatabaseConfig) -> None:
    from powercontext.builtin.inference.errors import InferenceUnavailableError

    class TransientGenerator(Generator):
        async def generate(self, value):
            result = await super().generate(value)
            if len(self.inputs) == 1:
                raise InferenceUnavailableError("generation")
            return result

    async def scenario() -> None:
        generator = TransientGenerator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=generator
        ) as runtime:
            scope, _, citation = await seed(runtime)
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(operation="refine_experience", artifacts=(citation,), idempotency_key="retry")
            )
            await process_pending(runtime)
            first = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert first.status == "queued"
            await process_pending(runtime)
            complete = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert complete.status == "succeeded"
            assert complete.attempt_count == complete.usage.model_calls == 2
            assert complete.started_at == first.started_at
            assert complete.input_manifest == first.input_manifest
            assert generator.inputs[0] == generator.inputs[1]
            assert complete.usage.input_tokens is None  # The failed call's token usage is unknown.

    asyncio.run(scenario())


def test_explicit_dream_preserves_incubation_cursor_and_pending_source_work(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        incubation = IncubationPipeline()
        async with open_builtin_runtime(
            config(database),
            candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
            experience_pipeline=incubation,
            dream_generator=Generator(),
        ) as runtime:
            scope, _, citation = await seed(runtime)
            first = await runtime.sources.for_scope(scope).capture(
                CaptureSource(
                    source_id="incubated-before-dream",
                    content="The first bounded task completed.",
                    metadata={"kind": "task-outcome"},
                )
            )
            initial = await runtime.experience.for_scope(scope).incubate()
            assert initial.current_cursor == first.sequence

            pending = await runtime.sources.for_scope(scope).capture(
                CaptureSource(
                    source_id="pending-during-dream",
                    content="The second bounded task completed.",
                    metadata={"kind": "task-outcome"},
                )
            )
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience",
                    artifacts=(citation,),
                    idempotency_key="preserve-incubation-cursor",
                )
            )
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (run.status, run.outcome) == ("succeeded", "proposed")

            incubated = await runtime.experience.for_scope(scope).incubate()
            assert (incubated.previous_cursor, incubated.current_cursor) == (first.sequence, pending.sequence)
            assert incubated.candidate_count == 1
            assert len(incubation.calls) == 2

    asyncio.run(scenario())


def test_provider_exceeding_output_budget_leaves_no_candidate(database: DatabaseConfig) -> None:
    class OversizedGenerator(Generator):
        async def generate(self, value):
            result = await super().generate(value)
            return result.model_copy(update={"usage": InferenceUsage(requests=1, output_tokens=4097)})

    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database),
            candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
            dream_generator=OversizedGenerator(),
        ) as runtime:
            scope, _, citation = await seed(runtime)
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience", artifacts=(citation,), idempotency_key="output-budget"
                )
            )
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (run.status, run.error, run.candidate) == ("failed", "budget_exceeded", None)
            assert run.usage.output_tokens == 4097

    asyncio.run(scenario())


def test_enforced_access_rechecks_background_actor_and_attests_candidate(database: DatabaseConfig) -> None:
    import httpx
    from starlette.middleware import Middleware

    from powercontext.builtin.authz import AccessRole, PrincipalRef, ResourceRef
    from powercontext.builtin.authz.composition import open_builtin_access_control
    from powercontext.builtin.authz.dream import principal_identity
    from powercontext.builtin.authz.service import AccessAuditContext, CreateBinding
    from powercontext.server.app import create_app
    from powercontext.server.authentication import StaticBearerAuthenticationProvider
    from powercontext.server.dream_access import DreamAccess
    from powercontext.server.middleware import AuthenticationMiddleware

    admin = PrincipalRef(type="service", id="dream-admin")
    author = PrincipalRef(type="user", id="dream-author")
    context = AccessAuditContext(transport="http", operation="test_dream")

    async def scenario() -> None:
        async with open_builtin_access_control(database, bootstrap_administrators=(admin,)) as access:
            adapter = DreamAccess(access)
            generator = Generator(blocked=True)
            async with open_builtin_runtime(
                BuiltinConfig(database=database, runtime=RuntimeConfig()),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
                dream_authorizer=adapter.authorize,
                dream_authorization_context=access.defer_decision_audit,
                dream_candidate_attester=adapter.attest_candidate,
            ) as runtime:
                scope, _, citation = await seed(runtime)
                assert runtime.atomic_memory is not None
                for record in (await runtime.atomic_memory.for_scope(scope).list()).items:
                    await access.establish_artifact_owner(
                        ResourceRef.artifact(scope, family=record.ref.family, artifact_id=record.ref.artifact_id),
                        admin,
                        idempotency_key=f"dream-memory-owner:{record.ref.artifact_id}",
                        context=context,
                    )
                await access.create_binding(
                    admin,
                    CreateBinding(
                        subject=admin,
                        resource=ResourceRef.scope(scope),
                        role=AccessRole.SCOPE_REVIEWER,
                        idempotency_key="dream-scope-owner",
                    ),
                    context=context,
                )
                binding = await access.create_binding(
                    admin,
                    CreateBinding(
                        subject=author,
                        resource=ResourceRef.scope(scope),
                        role=AccessRole.SCOPE_CONTRIBUTOR,
                        idempotency_key="dream-contributor",
                    ),
                    context=context,
                )
                request = CreateDreamRunRequest(
                    operation="refine_experience", artifacts=(citation,), idempotency_key="access-revoked"
                )
                app = create_app(
                    application=cast(ServerApplication, runtime),
                    access_control=access,
                    access_mode="enforced",
                    authentication_provider=StaticBearerAuthenticationProvider("test-token", author),
                    middleware=[
                        Middleware(
                            AuthenticationMiddleware, provider=StaticBearerAuthenticationProvider("test-token", author)
                        )
                    ],
                )
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                    headers={"Authorization": "Bearer test-token"},
                ) as client:
                    response = await client.post(f"/v1/scopes/{scope}/dream", json=request.model_dump(mode="json"))
                    assert response.status_code == 202, response.text
                    worker = asyncio.create_task(process_pending(runtime))
                    await asyncio.wait_for(generator.started.wait(), timeout=10)
                    await access.revoke_binding(
                        admin,
                        binding.binding_id,
                        expected_version=binding.version,
                        idempotency_key="revoke-dream-contributor",
                        context=context,
                    )
                    generator.release.set()
                    await worker
                    run = await runtime.dream.for_scope(scope, principal_id=principal_identity(admin)).get(
                        GetDreamRunRequest(run_id=response.json()["run_id"])
                    )
                    assert (run.status, run.error, run.candidate) == ("failed", "access_revoked", None)
                    assert (await client.get(f"/v1/scopes/{scope}/dream/{run.run_id}")).status_code == 403
                    await access.create_binding(
                        admin,
                        CreateBinding(
                            subject=author,
                            resource=ResourceRef.scope(scope),
                            role=AccessRole.SCOPE_CONTRIBUTOR,
                            idempotency_key="restore-dream-contributor",
                        ),
                        context=context,
                    )
                    accepted = await client.post(
                        f"/v1/scopes/{scope}/dream",
                        json=request.model_copy(update={"idempotency_key": "access-allowed"}).model_dump(mode="json"),
                    )
                    assert accepted.status_code == 202, accepted.text
                    await process_pending(runtime)
                    completed = await client.get(f"/v1/scopes/{scope}/dream/{accepted.json()['run_id']}")
                    assert completed.json()["status"] == "succeeded", completed.text
                    candidate = completed.json()["candidate"]
                    reviewed = await client.post(
                        "/v1/candidates/get",
                        json={
                            "scope_id": scope,
                            "candidate_id": candidate["candidate_id"],
                        },
                    )
                    assert reviewed.status_code == 200, reviewed.text
                    # The author has contribution rights, but cannot approve their proposal.
                    forbidden = await client.post(
                        "/v1/candidates/approve",
                        json={
                            "scope_id": scope,
                            "candidate_id": candidate["candidate_id"],
                            "expected_version": candidate["version"],
                        },
                    )
                    assert forbidden.status_code == 403, forbidden.text
                reviewer_app = create_app(
                    application=cast(ServerApplication, runtime),
                    access_control=access,
                    access_mode="enforced",
                    authentication_provider=StaticBearerAuthenticationProvider("review-token", admin),
                    middleware=[
                        Middleware(
                            AuthenticationMiddleware, provider=StaticBearerAuthenticationProvider("review-token", admin)
                        )
                    ],
                )
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=reviewer_app),
                    base_url="http://testserver",
                    headers={"Authorization": "Bearer review-token"},
                ) as reviewer:
                    approved = await reviewer.post(
                        "/v1/candidates/approve",
                        json={
                            "scope_id": scope,
                            "candidate_id": candidate["candidate_id"],
                            "expected_version": candidate["version"],
                        },
                    )
                    assert approved.status_code == 200, approved.text
                    result = approved.json()["result_artifact"]
                    ownership = await access.artifact_owner(
                        ResourceRef.artifact(
                            scope,
                            family=result["family"],
                            artifact_id=result["artifact_id"],
                        )
                    )
                    assert ownership is not None and ownership.owner == author
                    assert approved.json()["artifact_refs"] == [citation.model_dump(mode="json")]

    asyncio.run(scenario())


def test_dream_candidate_attestation_preserves_configured_casbin_denial(tmp_path: Path) -> None:
    from powercontext.builtin.authz import AccessAction, AccessControlService, AccessRole, PrincipalRef, ResourceRef
    from powercontext.builtin.authz.composition import open_builtin_access_control, open_casbin_access_control
    from powercontext.builtin.authz.dream import principal_identity
    from powercontext.builtin.authz.service import AccessAuditContext, CreateBinding
    from powercontext.server.dream_access import DreamAccess

    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'dream.db'}")
    decision_database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'decisions.db'}")
    admin = PrincipalRef(type="service", id="dream-admin")
    author = PrincipalRef(type="user", id="dream-author")
    context = AccessAuditContext(transport="background", operation="test_dream")

    async def scenario() -> None:
        async with (
            open_builtin_access_control(database, bootstrap_administrators=(admin,)) as relationships,
            open_casbin_access_control(decision_database, bootstrap_administrators=(admin,)) as decisions,
        ):
            access = AccessControlService(
                decisions.provider, relationships=relationships.relationships, audit=relationships.audit
            )
            adapter = DreamAccess(access)
            attempted_candidate_id: str | None = None

            async def attest_candidate(connection, record, candidate_id, family):
                nonlocal attempted_candidate_id
                attempted_candidate_id = candidate_id
                # The configured decision authority revokes contribution while
                # the relationship store still contains the contributor grant.
                await decisions.revoke_binding(
                    admin,
                    contributor.binding_id,
                    expected_version=contributor.version,
                    idempotency_key="revoke-decision-contributor",
                    context=context,
                )
                await adapter.attest_candidate(connection, record, candidate_id, family)

            async with open_builtin_runtime(
                config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=Generator(),
                dream_authorizer=adapter.authorize,
                dream_authorization_context=access.defer_decision_audit,
                dream_candidate_attester=attest_candidate,
            ) as runtime:
                scope, _, citation = await seed(runtime)
                resource = ResourceRef.scope(scope)
                contribution = CreateBinding(
                    subject=author,
                    resource=resource,
                    role=AccessRole.SCOPE_CONTRIBUTOR,
                    idempotency_key="dream-contributor",
                )
                await relationships.create_binding(admin, contribution, context=context)
                contributor = await decisions.create_binding(admin, contribution, context=context)
                await decisions.create_binding(
                    admin,
                    CreateBinding(
                        subject=author,
                        resource=resource,
                        role=AccessRole.SCOPE_VIEWER,
                        idempotency_key="dream-viewer",
                    ),
                    context=context,
                )
                memory = ResourceRef.artifact(scope, family=citation.family, artifact_id=citation.artifact_id)
                for authority in (relationships, decisions):
                    await authority.establish_artifact_owner(
                        memory, admin, idempotency_key="dream-memory-owner", context=context
                    )
                dream = runtime.dream.for_scope(scope, principal_id=principal_identity(author))
                accepted = await dream.create(
                    CreateDreamRunRequest(
                        operation="refine_experience", artifacts=(citation,), idempotency_key="decision-revoked"
                    )
                )
                await process_pending(runtime)
                run = await dream.get(GetDreamRunRequest(run_id=accepted.run_id))
                assert (run.status, run.error, run.candidate) == ("failed", "access_revoked", None)
                assert attempted_candidate_id is not None
                assert await access.candidate_owner(scope, attempted_candidate_id) is None
                assert (await runtime.review.for_scope(scope).list(ListCandidatesRequest())).candidates == ()
                assert (
                    await relationships.check(author, AccessAction.SCOPE_CONTRIBUTE, resource, context=context)
                ).allowed
                assert not (
                    await access.check(author, AccessAction.SCOPE_CONTRIBUTE, resource, context=context)
                ).allowed

    asyncio.run(scenario())


def test_restart_recovers_expired_run_with_its_pinned_input(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        settings = BuiltinConfig(
            database=database,
            runtime=RuntimeConfig(),
        )
        interrupted = Generator(blocked=True)
        async with open_builtin_runtime(
            settings, candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=interrupted
        ) as runtime:
            scope, _, citation = await seed(runtime)
            assert runtime._dream_service is not None
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(operation="refine_experience", artifacts=(citation,), idempotency_key="restart")
            )
            worker = asyncio.create_task(process_pending(runtime))
            await asyncio.wait_for(interrupted.started.wait(), timeout=5)
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
            stranded = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert stranded.status == "running"
        await asyncio.sleep(0.2)
        recovered = Generator()
        async with open_builtin_runtime(settings, dream_generator=recovered) as restarted:
            await process_pending(restarted)
            async with asyncio.timeout(5):
                while True:
                    complete = await restarted.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
                    if complete.terminal:
                        break
                    await asyncio.sleep(0.02)
            assert complete.status == "succeeded"
            assert complete.input_manifest == stranded.input_manifest
            assert complete.started_at == stranded.started_at
            assert complete.attempt_count == complete.usage.model_calls == 2
            assert interrupted.inputs == recovered.inputs
            assert len((await restarted.review.for_scope(scope).list(ListCandidatesRequest())).candidates) == 1

    asyncio.run(scenario())


def test_missing_provenance_requires_maintenance_without_changing_existing_records(database: DatabaseConfig) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from powercontext.builtin.persistence.dream_schema import DreamSchemaNotReadyError

    async def scenario() -> None:
        settings = BuiltinConfig(database=database)
        async with open_builtin_runtime(
            settings, candidate_pipeline=atomic_memory_pipeline(MemoryPipeline())
        ) as runtime:
            scope, root, _ = await seed(runtime)
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=experience(),
                    sources=(root,),
                )
            )
            await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
        url = database.url.get_secret_value() if isinstance(database, OceanBaseConfig) else database.url
        engine = create_async_engine(url, hide_parameters=True)
        try:
            async with engine.begin() as connection:
                await connection.exec_driver_sql("DROP INDEX ix_pc_dream_runs_proposal_fingerprint")
                await connection.exec_driver_sql("ALTER TABLE pc_dream_runs DROP COLUMN proposal_fingerprint")
            async with engine.connect() as connection:
                before = {
                    name: (await connection.exec_driver_sql(f"SELECT * FROM {name}")).all()  # noqa: S608 - fixed names
                    for name in ("pc_artifacts", "pc_candidate_heads", "pc_candidate_versions")
                }
            with pytest.raises(DreamSchemaNotReadyError, match="migration_required"):
                async with open_builtin_runtime(settings, dream_generator=Generator()):
                    pytest.fail("Legacy provenance requires explicit migration")
            async with engine.connect() as connection:
                for name, rows in before.items():
                    assert (await connection.exec_driver_sql(f"SELECT * FROM {name}")).all() == rows  # noqa: S608 - fixed table names
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_replacement_dream_identifies_the_exact_target_in_model_input(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=generator
        ) as runtime:
            scope, root, _ = await seed(runtime)
            targets = []
            for lesson in ("Check the commit status before retrying.", "Preserve the idempotency key when retrying."):
                candidate = await runtime.experience.for_scope(scope).propose(
                    ProposeExperienceRequest(
                        proposal=experience().model_copy(update={"lesson": lesson}), sources=(root,)
                    )
                )
                approved = await runtime.review.for_scope(scope).approve(
                    ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
                )
                assert approved.result_artifact is not None
                targets.append(approved.result_artifact)
            runs = []
            for target in targets:
                accepted = await runtime.dream.for_scope(scope).create(
                    CreateDreamRunRequest(
                        operation="refine_experience",
                        artifacts=tuple(targets),
                        target=target,
                        idempotency_key=target.artifact_id,
                    )
                )
                await process_pending(runtime)
                run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
                assert run.status == "succeeded" and run.candidate is not None
                candidate = await runtime.review.for_scope(scope).get(
                    GetCandidateRequest(candidate_id=run.candidate.candidate_id)
                )
                assert candidate.target == target
                runs.append(run)
            assert generator.inputs[0].evidence == generator.inputs[1].evidence
            assert generator.inputs[0] != generator.inputs[1]
            for target, run, value in zip(targets, runs, generator.inputs, strict=True):
                assert run.input_manifest is not None
                target_node = next(node for node in run.input_manifest.nodes if node.artifact == target)
                assert value.target_evidence_id == target_node.evidence_id
                projected = next(
                    item for item in value.evidence.evidence if item.evidence_id == value.target_evidence_id
                )
                stored = await runtime.experience.for_scope(scope).get(GetExperienceRequest(artifact=target))
                assert projected.kind == "experience" and projected.text == stored.content.model_dump_json()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["propose", "revise", "approve"])
@pytest.mark.parametrize("invalidation", ["retired", "access_revoked"])
def test_skill_replacement_rechecks_memory_through_skill_lineage(
    database: DatabaseConfig, phase: str, invalidation: str
) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=Generator()
        ) as runtime:
            scope, root, citation = await seed(runtime)
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(proposal=experience(), artifacts=(citation,))
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
            )
            assert approved.result_artifact is not None
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="derive_skill", artifacts=(approved.result_artifact,), idempotency_key="skill-lineage"
                )
            )
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert run.candidate is not None
            approved = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(candidate_id=run.candidate.candidate_id, expected_version=run.candidate.version)
            )
            assert approved.result_artifact is not None
            skill = await runtime.skill.for_scope(scope).get(GetSkillRequest(artifact=approved.result_artifact))
            # Keep more than one Skill revision between the candidate and its Memory evidence.
            candidate = await runtime.skill.for_scope(scope).propose(
                ProposeSkillRequest(
                    proposal=skill.content, sources=(root,), artifacts=(skill.as_ref(),), target=skill.as_ref()
                )
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
            )
            assert approved.result_artifact is not None
            skill = await runtime.skill.for_scope(scope).get(GetSkillRequest(artifact=approved.result_artifact))
            request = ProposeSkillRequest(
                proposal=skill.content, sources=(root,), artifacts=(skill.as_ref(),), target=skill.as_ref()
            )
            pending = None if phase == "propose" else await runtime.skill.for_scope(scope).propose(request)
            before = await runtime.review.for_scope(scope).list(ListCandidatesRequest())
            if invalidation == "retired":
                await forget_memory(runtime, scope, citation)
                error_type, error_code = EvidenceResolutionError, "memory_entry_inactive"
            else:

                async def authorize_review(_scope, ref):
                    if ref == citation:
                        raise DreamError("access_revoked")

                async def unused(*_args):
                    return None

                runtime.configure_evidence_authorization(
                    dream=unused, review=authorize_review, context=nullcontext, attest_candidate=unused
                )
                error_type, error_code = DreamError, "access_revoked"
            with pytest.raises(error_type, match=error_code):
                if phase == "propose":
                    await runtime.skill.for_scope(scope).propose(request)
                elif phase == "revise":
                    assert pending is not None
                    await runtime.review.for_scope(scope).revise(
                        ReviseCandidateRequest(
                            candidate_id=pending.candidate_id,
                            expected_version=pending.version,
                            proposal=pending.proposal,
                            sources=(root,),
                            artifacts=(skill.as_ref(),),
                            target=skill.as_ref(),
                        )
                    )
                else:
                    assert pending is not None
                    await runtime.review.for_scope(scope).approve(
                        ApproveCandidateRequest(candidate_id=pending.candidate_id, expected_version=pending.version)
                    )
            assert await runtime.review.for_scope(scope).list(ListCandidatesRequest()) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["propose", "revise", "approve"])
@pytest.mark.parametrize("revise_memory", [False, True])
def test_active_merged_memory_keeps_exact_frozen_history_for_new_review(
    database: DatabaseConfig, phase: str, revise_memory: bool
) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=Generator()
        ) as runtime:
            scope, root, _ = await seed(runtime)
            assert runtime.atomic_memory is not None
            memories = runtime.atomic_memory.for_scope(scope)
            inputs = (await memories.list()).items
            merged = await memories.merge(
                tuple(item.as_read() for item in inputs),
                AtomicMemoryContent(
                    kind="working_note",
                    text="Replaying the write key preserved exactly one row.",
                    description="Explicit fixture memory",
                ),
            )
            target = merged.primary.ref
            if revise_memory:
                updated = await runtime._provider.records.replace_artifact(
                    scope,
                    "atomic-memory",
                    target.artifact_id,
                    '"revision:1"',
                    ArtifactWrite(
                        content={
                            "kind": "working_note",
                            "text": "The key replay remained idempotent.",
                            "description": "Explicit fixture memory",
                        }
                    ),
                )
                target = (await memories.get(updated.artifact_id)).ref
            for item in inputs:
                historical = await memories.get(item.ref.artifact_id, revision=item.ref.revision)
                assert historical.ref == item.ref and historical.state.state == "merged"
            service = runtime.experience.for_scope(scope)
            candidate = await service.propose(ProposeExperienceRequest(proposal=experience(), artifacts=(target,)))
            if phase == "revise":
                candidate = await runtime.review.for_scope(scope).revise(
                    ReviseCandidateRequest(
                        candidate_id=candidate.candidate_id,
                        expected_version=candidate.version,
                        proposal=candidate.proposal,
                        artifacts=(target,),
                    )
                )
            elif phase == "approve":
                candidate = await runtime.review.for_scope(scope).approve(
                    ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
                )
                assert candidate.result_artifact is not None
            assert candidate.sources == (root,)
            assert candidate.artifacts == (target,)

    asyncio.run(scenario())


def test_skill_approval_rechecks_transitive_memory_state(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=Generator()
        ) as runtime:
            scope, _, citation = await seed(runtime)
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=experience(),
                    artifacts=(citation,),
                )
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            assert approved.result_artifact is not None
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="derive_skill",
                    artifacts=(approved.result_artifact,),
                    idempotency_key="transitive",
                )
            )
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert run.candidate is not None
            await forget_memory(runtime, scope, citation)
            with pytest.raises(EvidenceResolutionError, match="memory_entry_inactive"):
                await runtime.review.for_scope(scope).approve(
                    ApproveCandidateRequest(
                        candidate_id=run.candidate.candidate_id,
                        expected_version=run.candidate.version,
                    )
                )
            pending = await runtime.review.for_scope(scope).get(
                GetCandidateRequest(
                    candidate_id=run.candidate.candidate_id,
                )
            )
            assert pending.status == "pending" and pending.result_artifact is None

    asyncio.run(scenario())


def test_memory_without_task_sources_cannot_produce_experience(database: DatabaseConfig) -> None:
    class UnsupportedGenerator(Generator):
        async def generate(self, value):
            self.inputs.append(value)
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="A rule inferred from an unsupported note.",
                    intent="create",
                    proposal=experience(),
                    evidence_ids=(value.evidence.evidence[0].evidence_id,),
                )
            )

    async def scenario() -> None:
        async with open_builtin_runtime(config(database), dream_generator=UnsupportedGenerator()) as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(
                        title="Unsupported note",
                        summary="No task evidence",
                        idempotency_key="unsupported",
                    )
                )
            ).scope_id
            await runtime.atomic_memory.for_scope(scope).create((
                AtomicMemoryContent(
                    kind="preference",
                    text="I prefer retrying writes without checking the previous result.",
                    description="Explicit fixture memory",
                ),
            ))
            citation = (await runtime.atomic_memory.for_scope(scope).list()).items[0].ref
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience",
                    artifacts=(citation,),
                    idempotency_key="no-task-root",
                )
            )
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (run.status, run.outcome, run.candidate) == ("succeeded", "needs_evidence", None)

    asyncio.run(scenario())


def test_replacement_dream_keeps_target_and_replays_after_head_advances(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=Generator()
        ) as runtime:
            scope, _, citation = await seed(runtime)
            first = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=experience(),
                    artifacts=(citation,),
                )
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(
                    candidate_id=first.candidate_id,
                    expected_version=first.version,
                )
            )
            target = approved.result_artifact
            assert target is not None
            request = CreateDreamRunRequest(
                operation="refine_experience",
                artifacts=(target, citation),
                target=target,
                idempotency_key="replacement",
            )
            accepted = await runtime.dream.for_scope(scope).create(request)
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert run.candidate is not None
            candidate = await runtime.review.for_scope(scope).get(
                GetCandidateRequest(candidate_id=run.candidate.candidate_id)
            )
            assert candidate.target == target and target in candidate.artifacts
            replacement = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            assert replacement.result_artifact is not None
            assert replacement.result_artifact.revision == target.revision + 1
            assert await runtime.dream.for_scope(scope).create(request) == run
            with pytest.raises(DreamError, match="artifact_conflict"):
                await runtime.dream.for_scope(scope).create(
                    request.model_copy(update={"idempotency_key": "stale-target"})
                )

    asyncio.run(scenario())


def test_dream_model_cannot_claim_an_existing_skill_package(database: DatabaseConfig) -> None:
    from powercontext.builtin.artifacts.skill import SkillPackageRef

    class PackageClaimingGenerator(Generator):
        async def generate(self, value):
            result = await super().generate(value)
            proposal = result.output.proposal
            assert isinstance(proposal, SkillContent)
            package = SkillPackageRef(
                tree_digest="0" * 64, archive_digest="1" * 64, file_count=1, uncompressed_size=1, archive_size=1
            )
            return result.model_copy(
                update={
                    "output": result.output.model_copy(
                        update={
                            "proposal": proposal.model_copy(update={"package": package}),
                        }
                    )
                }
            )

    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database),
            candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
            dream_generator=PackageClaimingGenerator(),
        ) as runtime:
            scope, _, citation = await seed(runtime)
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=experience(),
                    artifacts=(citation,),
                )
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            assert approved.result_artifact is not None
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="derive_skill",
                    artifacts=(approved.result_artifact,),
                    idempotency_key="package-claim",
                )
            )
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (run.status, run.error, run.candidate) == ("failed", "invalid_generation_output", None)

    asyncio.run(scenario())


def test_multiple_entries_and_experience_reusing_a_source_keep_one_root(database: DatabaseConfig) -> None:
    class EchoPipeline:
        async def generate(self, request: AtomicMemoryExtractionInput, /):
            return GenerationResult(
                output=AtomicMemoryExtractionOutput(
                    candidates=tuple(
                        AtomicMemoryCandidate(
                            kind="working_note",
                            text=text,
                            evidence_ids=(evidence.evidence_id,),
                            description="Extracted fixture fact",
                        )
                        for evidence in request.evidence
                        for text in (
                            "Replaying the original key kept one row.",
                            "The same replay produced no duplicate write.",
                        )
                    )
                )
            )

    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(EchoPipeline()), dream_generator=generator
        ) as runtime:
            scope, root, citation = await seed(runtime)
            entries = await runtime.atomic_memory.for_scope(scope).list()
            citations = tuple(item.ref for item in entries.items if item.artifact.content.kind == "working_note")
            assert len(citations) == 2
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=experience(),
                    artifacts=(citation,),
                )
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            assert approved.result_artifact is not None
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience",
                    artifacts=(approved.result_artifact, *citations),
                    idempotency_key="one-root",
                )
            )
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert run.candidate is not None and run.input_manifest is not None
            assert len(run.input_manifest.root_groups) == 1
            assert run.input_manifest.root_groups[0].independence == "unknown"
            assert len([item for item in generator.inputs[0].evidence.evidence if item.kind == "source"]) == 1
            result = await runtime.review.for_scope(scope).get(
                GetCandidateRequest(candidate_id=run.candidate.candidate_id)
            )
            assert result.sources == (root,)
            assert {ref.model_dump_json() for ref in result.artifacts} == {
                ref.model_dump_json() for ref in (approved.result_artifact, *citations)
            }

    asyncio.run(scenario())


def test_superseded_supervisor_cannot_overwrite_recovered_result(  # noqa: C901 - controlled late return and fenced handoff
    database: DatabaseConfig,
) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_LEASES_TABLE
    from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkerOutcome
    from tests.e2e.dream_support import Controller, Handle

    class LateGenerator(Generator):
        def __init__(self) -> None:
            super().__init__(blocked=True)
            self.cancel_received = asyncio.Event()
            self.returned = asyncio.Event()

        async def generate(self, value: DreamGenerationInput) -> GenerationResult[DreamPlan]:
            model_call = asyncio.create_task(super().generate(value))
            while True:
                try:
                    result = await asyncio.shield(model_call)
                except asyncio.CancelledError:
                    if model_call.done():
                        raise
                    self.cancel_received.set()
                else:
                    self.returned.set()
                    return result

    class LateHandle(Handle):
        def __init__(self, controller, assignment):
            super().__init__(controller, assignment)
            self.cancel_requested = False

        async def terminate(self):
            if not self.cancel_requested:
                self.cancel_requested = True
                self.task.cancel()
            try:
                # The remote model request can outlive cancellation. Shield its
                # task so the Supervisor's cleanup deadline remains effective.
                await asyncio.shield(self.task)
            except asyncio.CancelledError:
                if self.task.cancelled():
                    return
                if not self.task.done():
                    self.controller.termination_interrupted.set()
                raise

    class LateController(Controller):
        def __init__(self) -> None:
            super().__init__()
            self.termination_interrupted = asyncio.Event()

        async def start(self, assignment):
            handle = LateHandle(self, assignment)
            self.handles.append(handle)
            return handle

    async def scenario() -> None:
        delayed = LateGenerator()
        original_controller = LateController()
        async with open_builtin_runtime(
            config(database),
            candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
            dream_generator=delayed,
            controller=original_controller,
        ) as original:
            scope, _, citation = await seed(original)
            accepted = await original.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience", artifacts=(citation,), idempotency_key="late-worker"
                )
            )
            worker = asyncio.create_task(process_pending(original))
            try:
                await asyncio.wait_for(delayed.started.wait(), timeout=10)
                assert original._dream_service is not None
                # Supersede the Supervisor while its model request is still outstanding.
                async with original._dream_service.database.transaction() as connection:
                    await connection.execute(
                        update(ARTIFACT_PROCESSING_LEASES_TABLE)
                        .where(ARTIFACT_PROCESSING_LEASES_TABLE.c.supervisor_group == "global")
                        .values(
                            holder_id="superseded",
                            lease_expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1),
                        )
                    )
                assert original.artifact_processing_supervisor is not None
                original.artifact_processing_supervisor.wake()
                replacement = Generator()
                async with open_builtin_runtime(config(database), dream_generator=replacement) as recovered:
                    await process_pending(recovered)
                    async with asyncio.timeout(15):
                        while True:
                            run = await recovered.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
                            if run.terminal:
                                break
                            await asyncio.sleep(0.02)
                    assert run.status == "succeeded"
                    assert run.attempt_count == run.usage.model_calls == 2
                    await asyncio.wait_for(delayed.cancel_received.wait(), timeout=10)
                    await asyncio.wait_for(original_controller.termination_interrupted.wait(), timeout=10)
                    assert not worker.done()
                    assert not delayed.returned.is_set()
                    delayed.release.set()
                    with suppress(asyncio.CancelledError):
                        await asyncio.wait_for(worker, timeout=10)
                    assert delayed.returned.is_set()
                    assert all(
                        handle.task.done() and not handle.task.cancelled() for handle in original_controller.handles
                    )
                    assert all(
                        handle.task.result().outcome is ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST
                        for handle in original_controller.handles
                    )
                    assert await recovered.dream.for_scope(scope).get(GetDreamRunRequest(run_id=run.run_id)) == run
                    candidates = await recovered.review.for_scope(scope).list(ListCandidatesRequest())
                    assert len(candidates.candidates) == 1
                    assert delayed.inputs == replacement.inputs
            finally:
                delayed.release.set()
                with suppress(asyncio.CancelledError):
                    await asyncio.wait_for(worker, timeout=10)

    asyncio.run(scenario())


def test_candidate_and_run_rollback_together(database: DatabaseConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=generator
        ) as runtime:
            scope, _, citation = await seed(runtime)
            assert runtime._dream_service is not None
            repository = runtime._dream_service.repository
            finish = repository.finish

            async def interrupt_commit(connection, record, run):
                if run.status == "succeeded":
                    raise RuntimeError("simulated_commit_interruption")
                await finish(connection, record, run)

            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="rollback"
            )
            accepted = await runtime.dream.for_scope(scope).create(request)
            with monkeypatch.context() as patch:
                patch.setattr(repository, "finish", interrupt_commit)
                await process_pending(runtime)
            failed = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (failed.status, failed.candidate) == ("failed", None)
            assert (await runtime.review.for_scope(scope).list(ListCandidatesRequest())).candidates == ()
            assert await runtime.dream.for_scope(scope).create(request) == failed
            retried = await runtime.dream.for_scope(scope).create(
                request.model_copy(update={"idempotency_key": "retry"})
            )
            await process_pending(runtime)
            complete = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=retried.run_id))
            assert complete.status == "succeeded"
            assert len((await runtime.review.for_scope(scope).list(ListCandidatesRequest())).candidates) == 1

    asyncio.run(scenario())


def test_dream_keeps_prompt_lineage_out_of_factual_evidence(database: DatabaseConfig) -> None:
    from powercontext.artifacts import ArtifactRef
    from powercontext.builtin.records import ArtifactWrite
    from powercontext.builtin.review.errors import InvalidCandidateError

    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=generator
        ) as runtime:
            scope, root, citation = await seed(runtime)
            prompt = await runtime.records.for_scope(scope).create_artifact(
                "prompt",
                ArtifactWrite(
                    prompt_key="experience.generate",
                    content={
                        "schema_version": "powercontext.prompt.v1",
                        "mode": "auto",
                        "instructions": "",
                        "demonstrations": [],
                    },
                ),
            )
            prompt_ref = ArtifactRef(family="prompt", artifact_id=prompt.artifact_id, revision=prompt.revision)
            with pytest.raises(InvalidCandidateError):
                await runtime.experience.for_scope(scope).propose(
                    ProposeExperienceRequest(proposal=experience(), artifacts=(prompt_ref,))
                )
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(proposal=experience(), sources=(root,), artifacts=(prompt_ref, citation))
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
            )
            assert approved.result_artifact is not None
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="derive_skill", artifacts=(approved.result_artifact,), idempotency_key="with-prompt"
                )
            )
            await process_pending(runtime)
            completed = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert completed.status == "succeeded" and completed.candidate is not None
            assert completed.input_manifest is not None
            assert not completed.input_manifest.incomplete
            configuration = [node for node in completed.input_manifest.nodes if node.artifact == prompt_ref]
            assert len(configuration) == 1 and configuration[0].role == "lineage_only"
            assert "powercontext.prompt.v1" not in generator.inputs[0].model_dump_json()
            skill = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(
                    candidate_id=completed.candidate.candidate_id, expected_version=completed.candidate.version
                )
            )
            assert skill.result_artifact is not None and skill.result_artifact.family == "skill"

    asyncio.run(scenario())


@pytest.mark.parametrize("with_memory", [False, True])
def test_ordinary_experience_revisions_do_not_inherit_dream_depth_budget(
    database: DatabaseConfig, with_memory: bool
) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database),
            candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()) if with_memory else None,
            dream_generator=Generator(),
        ) as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Revisions", summary="Ordinary review", idempotency_key="revisions")
                )
            ).scope_id
            source = await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="task", content="The write replay preserved one row.", metadata={})
            )
            citations = ()
            if with_memory:
                await runtime.atomic_memory.for_scope(scope).flush()
                entries = await runtime.atomic_memory.for_scope(scope).list()
                citations = (entries.items[0].ref,)
            target = None
            for revision in range(1, 13):
                refs = () if target is None else (target,)
                candidate = await runtime.experience.for_scope(scope).propose(
                    ProposeExperienceRequest(
                        proposal=experience(),
                        sources=() if target is None and citations else (source.source_ref,),
                        artifacts=(*refs, *(citations if target is None else ())),
                        target=target,
                    )
                )
                approved = await runtime.review.for_scope(scope).approve(
                    ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
                )
                target = approved.result_artifact
                assert target is not None and target.revision == revision
                stored = await runtime.experience.for_scope(scope).get(GetExperienceRequest(artifact=target))
                assert stored.lineage.sources == (source.source_ref,)
                assert stored.lineage.artifacts == (*refs, *(citations if revision == 1 else ()))
            with pytest.raises(EvidenceResolutionError, match="evidence_limit_exceeded"):
                await runtime.dream.for_scope(scope).create(
                    CreateDreamRunRequest(operation="derive_skill", artifacts=(target,), idempotency_key="bounded")
                )
            if citations:
                candidate = await runtime.skill.for_scope(scope).propose(
                    ProposeSkillRequest(
                        proposal=SkillContent(
                            name="verified-write-replay",
                            description="Replay timed-out writes.",
                            instructions="Reuse the same key.",
                            validation=("Verify that a replay preserves one row.",),
                        ),
                        artifacts=(target,),
                    )
                )
                await forget_memory(runtime, scope, citations[0])
                with pytest.raises(EvidenceResolutionError, match="memory_entry_inactive"):
                    await runtime.review.for_scope(scope).approve(
                        ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
                    )
                pending = await runtime.review.for_scope(scope).get(
                    GetCandidateRequest(candidate_id=candidate.candidate_id)
                )
                assert pending.status == "pending" and pending.result_artifact is None

    asyncio.run(scenario())


def test_ordinary_skill_review_keeps_indirect_sources_out_of_direct_reference_budget(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(config(database)) as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Source lineage", summary="Ordinary review", idempotency_key="sources")
                )
            ).scope_id
            sources = tuple([
                (
                    await runtime.sources.for_scope(scope).capture(
                        CaptureSource(source_id=f"task-{index}", content="The write replay passed.", metadata={})
                    )
                ).source_ref
                for index in range(32)
            ])
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(proposal=experience(), sources=sources)
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
            )
            assert approved.result_artifact is not None
            refs = (approved.result_artifact,)
            proposal = SkillContent(
                name="verified-write-replay",
                description="Replay timed-out writes.",
                instructions="Reuse the same key.",
                validation=("Verify that a replay preserves one row.",),
            )
            candidate = await runtime.skill.for_scope(scope).propose(
                ProposeSkillRequest(proposal=proposal, artifacts=refs)
            )
            assert candidate.sources == () and candidate.artifacts == refs

            denied = True

            async def authorize_review(_scope, ref):
                if denied and ref == sources[-1]:
                    raise DreamError("access_revoked")

            async def unused(*_args):
                return None

            runtime.configure_evidence_authorization(
                dream=unused, review=authorize_review, context=nullcontext, attest_candidate=unused
            )
            revision = ReviseCandidateRequest(
                candidate_id=candidate.candidate_id,
                expected_version=candidate.version,
                proposal=candidate.proposal,
                artifacts=refs,
            )
            with pytest.raises(DreamError, match="access_revoked"):
                await runtime.review.for_scope(scope).revise(revision)
            denied = False
            revised = await runtime.review.for_scope(scope).revise(revision)
            assert revised.sources == () and revised.artifacts == refs
            approval = ApproveCandidateRequest(candidate_id=revised.candidate_id, expected_version=revised.version)
            denied = True
            with pytest.raises(DreamError, match="access_revoked"):
                await runtime.review.for_scope(scope).approve(approval)
            denied = False
            approved = await runtime.review.for_scope(scope).approve(approval)
            assert approved.result_artifact is not None
            skill = await runtime.skill.for_scope(scope).get(GetSkillRequest(artifact=approved.result_artifact))
            assert skill.lineage.sources == () and skill.lineage.artifacts == refs

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["global", "dedicated"])
def test_dream_requests_arriving_during_generation_survive_without_an_automatic_schedule(
    database: DatabaseConfig, mode: str
) -> None:
    from powercontext.builtin.artifacts.experience import EXPERIENCE_INCUBATION_CURSOR_NAME
    from powercontext.builtin.persistence.cursors import SourceCursorRepository

    async def scenario() -> None:
        settings = config(database).model_copy(
            update={
                "runtime": RuntimeConfig.model_validate({
                    "artifact_processing_supervisor_mode": mode,
                })
            }
        )
        generator = Generator(blocked=True)
        async with open_builtin_runtime(
            settings, candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()), dream_generator=generator
        ) as runtime:
            scope, _, citation = await seed(runtime)
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="first"
            )
            first = await runtime.dream.for_scope(scope).create(request)
            work = asyncio.create_task(process_pending(runtime))
            try:
                await asyncio.wait_for(generator.started.wait(), timeout=10)
                second = await runtime.dream.for_scope(scope).create(
                    request.model_copy(update={"idempotency_key": "later"})
                )
                assert first.run_id != second.run_id
            finally:
                generator.release.set()
                await work
            assert (
                await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=first.run_id))
            ).status == "succeeded"
            assert (
                await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=second.run_id))
            ).status == "queued"
            await process_pending(runtime)
            assert (
                await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=second.run_id))
            ).status == "succeeded"
            candidates = (await runtime.review.for_scope(scope).list(ListCandidatesRequest())).candidates
            assert len(candidates) == len(generator.inputs) == 1
            reused = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=second.run_id))
            assert reused.reused and reused.candidate is not None
            assert reused.candidate.candidate_id == candidates[0].candidate_id
            assert all(candidate.status == "pending" for candidate in candidates)
            assert runtime._dream_service is not None
            async with runtime._dream_service.database.transaction() as connection:
                assert await SourceCursorRepository().load(connection, scope, EXPERIENCE_INCUBATION_CURSOR_NAME) is None

    asyncio.run(scenario())


def test_memory_dream_example_matches_native_and_http_request_contract():
    from powercontext.http import CreateDreamRunRequest as HttpRequest

    # Keep the wire example self-contained: the RFC lives in a separate PR.
    example = {
        "operation": "revise_memory",
        "target": {"family": "atomic-memory", "artifact_id": "city", "revision": 8},
        "sources": [{"source_type": "content", "source_id": "S03"}],
        "artifacts": [{"family": "atomic-memory", "artifact_id": "city", "revision": 8}],
        "idempotency_key": "city-explicit-change",
    }
    native = CreateDreamRunRequest.model_validate(example)
    transport = HttpRequest.model_validate(example)
    assert native.target is not None and native.target in native.artifacts
    assert CreateDreamRunRequest.model_validate(transport.model_dump(mode="json")) == native


@pytest.mark.parametrize("decision", ["approve", "reject", "policy"])
def test_profile_dream_admission_completes_with_concurrent_decision(  # noqa: C901 - three public races share a lock barrier
    database, monkeypatch, decision
):
    from powercontext.builtin.artifacts.profile.models import PROFILE_SOURCE_WINDOW_BINDING, ProfileCandidateProposal
    from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
    from powercontext.builtin.persistence.profile import ProfilePolicyRepository
    from powercontext.builtin.records import ArtifactWrite
    from powercontext.builtin.runtime.relational import RelationalContexts

    class ProfileGenerator:
        async def generate(self, value):
            return "# Profile\n\nPrefers Chinese."

    async def scenario():
        async with open_builtin_runtime(config(database), dream_generator=Generator()) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title="Concurrent Profile", summary="Review and Dream", idempotency_key="profile-locks")
            )
            sid = scope.scope_id
            contexts = RelationalContexts(database=runtime.profiles.database)
            profiles = contexts.profiles
            initial = await contexts.records.create_artifact(
                sid, "profile", ArtifactWrite(content={"content": "# Profile\n\nBased in Shanghai."})
            )
            policy = await profiles.get_policy(sid)
            await profiles.put_policy(
                sid, generation_enabled=True, activation_mode="review_required", expected_version=policy.version
            )
            source = await runtime.sources.for_scope(sid).capture(
                CaptureSource(source_id="preference", content="Please use Chinese.", metadata={})
            )
            profiles.generator = ProfileGenerator()
            pending = await profiles.flush(sid)
            assert pending.status == "review_pending" and pending.candidate_id is not None
            candidate = await contexts.review(sid).get_candidate(pending.candidate_id)
            target = candidate.target
            assert target is not None
            assert isinstance(candidate.proposal, ProfileCandidateProposal)
            source_window = candidate.proposal.source_window
            assert source_window is not None
            policy = await profiles.get_policy(sid)
            dream_ready, competitor_ready, release_dream = asyncio.Event(), asyncio.Event(), asyncio.Event()
            original_policy_get = ProfilePolicyRepository.get
            original_intent_load = ArtifactProcessingIntentRepository.load

            async def policy_get(repository, *args, **kwargs):
                task = asyncio.current_task()
                assert task is not None
                if task.get_name() == "profile-dream-admission" and kwargs.get("for_update"):
                    dream_ready.set()
                    await release_dream.wait()
                return await original_policy_get(repository, *args, **kwargs)

            async def intent_load(repository, connection, scope_id, binding, **kwargs):
                task = asyncio.current_task()
                assert task is not None
                if task.get_name() == "profile-decision" and binding == PROFILE_SOURCE_WINDOW_BINDING:
                    competitor_ready.set()
                return await original_intent_load(repository, connection, scope_id, binding, **kwargs)

            # Pause admission with its Intent held, before it requests Policy.
            # The competing public operation must finish without circular lock waits.
            monkeypatch.setattr(ProfilePolicyRepository, "get", policy_get)
            monkeypatch.setattr(ArtifactProcessingIntentRepository, "load", intent_load)

            async def decide():
                if decision == "policy":
                    return await profiles.put_policy(
                        sid,
                        generation_enabled=False,
                        activation_mode="review_required",
                        expected_version=policy.version,
                    )
                review = runtime.review.for_scope(sid)
                if decision == "approve":
                    return await review.approve(
                        ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=1)
                    )
                return await review.reject(
                    RejectCandidateRequest(
                        candidate_id=candidate.candidate_id, expected_version=1, reason="Needs clarification"
                    )
                )

            tasks = []
            try:
                async with asyncio.timeout(15):
                    admission = asyncio.create_task(
                        runtime.dream.for_scope(sid).create(
                            CreateDreamRunRequest(
                                operation="revise_profile",
                                target=target,
                                artifacts=(target,),
                                sources=(source.source_ref,),
                                idempotency_key="concurrent-profile",
                            )
                        ),
                        name="profile-dream-admission",
                    )
                    tasks.append(admission)
                    await dream_ready.wait()
                    competing = asyncio.create_task(decide(), name="profile-decision")
                    tasks.append(competing)
                    await competitor_ready.wait()
                    release_dream.set()
                    run, result = await asyncio.gather(admission, competing)
            finally:
                release_dream.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            assert run.status == "queued"
            saved = await contexts.review(sid).get_candidate(candidate.candidate_id)
            current = await contexts.records.get_artifact(sid, "profile", initial.artifact_id)
            current_policy = await profiles.get_policy(sid)
            async with profiles.database.transaction() as connection:
                cursor = await profiles.cursors.load(connection, sid, PROFILE_SOURCE_WINDOW_BINDING)
            if decision == "policy":
                assert not result.generation_enabled
                assert saved.status == "pending" and current_policy.pending_candidate_id == candidate.candidate_id
                assert (0 if cursor is None else cursor.cursor.sequence) == pending.current_cursor
            else:
                assert saved.status == ("approved" if decision == "approve" else "rejected")
                assert current_policy.pending_candidate_id is None
                assert cursor is not None and cursor.cursor.sequence == source_window.through
            assert current.revision == initial.revision + (decision == "approve")

    asyncio.run(scenario())


@pytest.mark.parametrize("pause_at", ["admission", "planning"])
@pytest.mark.parametrize("winner", ["approve", "reject", "advance", "revoke"])
def test_memory_approval_replays_across_servers(  # noqa: C901 - keep race barriers and public outcomes together
    database: DatabaseConfig, monkeypatch, pause_at, winner
) -> None:
    """A competing publication must not turn identical approvals into stale-target errors."""
    from contextvars import ContextVar

    import httpx

    from powercontext.builtin.artifacts.atomic_memory.service import AtomicMemoryService
    from powercontext.builtin.authz.errors import AccessDeniedError
    from powercontext.builtin.review.service import ReviewService
    from powercontext.server.app import create_app

    delayed = ContextVar("delayed_memory_approval", default=False)

    async def scenario() -> None:  # noqa: C901 - both servers share the controlled scheduling boundary
        async with (
            open_builtin_runtime(
                config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=Generator(),
            ) as first,
            open_builtin_runtime(
                config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=Generator(),
            ) as second,
        ):
            scope, source, target = await seed(first)
            candidate = await first._review(scope).propose_memory_dream(
                AtomicMemoryContent(
                    kind="task_record",
                    text="Verify the committed result before retrying.",
                    description="Explicit fixture memory",
                ),
                sources=(source,),
                artifacts=(target,),
                target=target,
                reason="Correct the retry result.",
                candidate_id="memory-approval-race",
            )
            competing = None
            if winner == "advance":
                competing = await first._review(scope).propose_memory_dream(
                    AtomicMemoryContent(
                        kind="task_record",
                        text="A separate correction supersedes the target.",
                        description="Explicit fixture memory",
                    ),
                    sources=(source,),
                    artifacts=(target,),
                    target=target,
                    reason="Independent correction.",
                    candidate_id="competing-memory-approval",
                )
            entered, release = asyncio.Event(), asyncio.Event()
            paused = revoked = False

            async def barrier():
                nonlocal paused
                if delayed.get() and not paused:
                    paused = True
                    entered.set()
                    await asyncio.wait_for(release.wait(), 10)

            authorize = ReviewService._authorize_decision
            inspect = AtomicMemoryService.inspect_change

            async def controlled_authorize(self, action, value):
                await authorize(self, action, value)
                if delayed.get() and revoked:
                    raise AccessDeniedError
                if pause_at == "admission" and action == "approve":
                    await barrier()

            async def controlled_inspect(self, *args, **kwargs):
                if pause_at == "planning":
                    await barrier()
                return await inspect(self, *args, **kwargs)

            monkeypatch.setattr(ReviewService, "_authorize_decision", controlled_authorize)
            monkeypatch.setattr(AtomicMemoryService, "inspect_change", controlled_inspect)
            payload = {"scope_id": scope, "candidate_id": candidate.candidate_id, "expected_version": candidate.version}
            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(application=cast(ServerApplication, first))),
                    base_url="http://first",
                ) as a,
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(application=cast(ServerApplication, second))),
                    base_url="http://second",
                ) as b,
            ):

                async def delayed_request():
                    token = delayed.set(True)
                    try:
                        return await b.post("/v1/candidates/approve", json=payload)
                    finally:
                        delayed.reset(token)

                pending = asyncio.create_task(delayed_request())
                try:
                    await asyncio.wait_for(entered.wait(), 10)
                    winning_payload = (
                        payload if competing is None else {**payload, "candidate_id": competing.candidate_id}
                    )
                    if winner == "reject":
                        winning_payload = {**payload, "reason": "Not suitable."}
                    result = await a.post(
                        "/v1/candidates/" + ("reject" if winner == "reject" else "approve"), json=winning_payload
                    )
                    assert result.status_code == 200, result.text
                    revoked = winner == "revoke"
                finally:
                    release.set()
                replay = await asyncio.wait_for(pending, 10)
                if winner == "approve":
                    assert replay.status_code == 200, replay.text
                    assert replay.json() == result.json()
                else:
                    assert replay.status_code == (403 if winner == "revoke" else 409), replay.text
                current = await first.atomic_memory.for_scope(scope).get(target.artifact_id)
                assert current.ref.revision == target.revision + (winner != "reject")
                stored = await first.review.for_scope(scope).get(
                    GetCandidateRequest(candidate_id=candidate.candidate_id)
                )
                expected_status = {
                    "approve": "approved",
                    "revoke": "approved",
                    "advance": "pending",
                    "reject": "rejected",
                }
                assert stored.status == expected_status[winner]
                wrong_version = await b.post(
                    "/v1/candidates/approve", json={**payload, "expected_version": candidate.version + 1}
                )
                assert wrong_version.status_code == 409, wrong_version.text

    asyncio.run(scenario())
