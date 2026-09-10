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
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.builtin.dream.generation import DreamGenerationInput
from powercontext.builtin.dream.models import DreamError, DreamPlan
from powercontext.builtin.evidence.models import EvidenceResolutionError
from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest,
    BuiltinConfig,
    BuiltinRuntime,
    CaptureSource,
    CreateDreamRunRequest,
    GetArtifactCandidateRequest,
    GetDreamRunRequest,
    GetExperienceRequest,
    GetSkillRequest,
    ListArtifactCandidatesRequest,
    ListDreamRunsRequest,
    PrepareContextRequest,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    RememberMemoryRequest,
    RetireMemoryEntryRequest,
    ReviseArtifactCandidateRequest,
    RuntimeConfig,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.app import ServerApplication
from tests.e2e.dream_support import open_dream_runtime as open_builtin_runtime
from tests.e2e.dream_support import process_pending

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


class MemoryPipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="task_record", text="The replay test passed without duplicate writes.", sources=(source,)
            )
            for source in request.sources
        )


def config(database: DatabaseConfig) -> BuiltinConfig:
    return BuiltinConfig(database=database, runtime=RuntimeConfig())


@pytest.mark.parametrize("existing", [False, True], ids=["new-config", "enable-skill"])
def test_wizard_skill_selection_accepts_dream_derivation(tmp_path: Path, existing: bool) -> None:
    from typer.testing import CliRunner

    from powercontext.builtin.runtime import open_builtin_runtime as open_runtime
    from powercontext.cli.config import app as config_app
    from powercontext.server.configuration import server_settings_context

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
        answers = "local\n" + choices + "n\nbailian\n\n\nexample-test-key\nnone\ny\ny\n"
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
        async with open_runtime(configuration, candidate_pipeline=MemoryPipeline()) as runtime:
            scope, _, citation = await seed(runtime)
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(proposal=experience(), memory_citations=(citation,))
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
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
    await runtime.memory.for_scope(scope.scope_id).flush()
    await runtime.memory.for_scope(scope.scope_id).remember(
        RememberMemoryRequest(entries=(MemoryEntryInput(kind="private_note", text="UNSELECTED_SIBLING_SENTINEL"),))
    )
    entries = await runtime.memory.for_scope(scope.scope_id).list()
    citation = next(item.citation for item in entries.entries if item.entry.kind == "task_record")
    return scope.scope_id, captured.source_ref, citation


def test_memory_dream_approval_and_skill_preserve_exact_provenance(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=generator
        ) as runtime:
            scope, root, citation = await seed(runtime)
            request = CreateDreamRunRequest(
                operation="refine_experience", memory_citations=(citation, citation), idempotency_key="memory"
            )
            accepted = await runtime.dream.for_scope(scope).create(request)
            assert accepted.status == "queued"
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (run.status, run.outcome, run.error) == ("succeeded", "proposed", None)
            assert run.input_manifest is not None
            assert run.candidate is not None
            assert run.usage.input_tokens == 80
            assert "UNSELECTED_SIBLING_SENTINEL" not in generator.inputs[0].model_dump_json()
            assert generator.inputs[0].target_evidence_id is None
            assert "The replay test passed" not in run.input_manifest.model_dump_json()
            assert len([item for item in generator.inputs[0].evidence.evidence if item.kind == "memory"]) == 1
            assert run.input_manifest.root_groups[0].independence == "unknown"
            candidate = await runtime.review.for_scope(scope).get(
                GetArtifactCandidateRequest(candidate_id=run.candidate.candidate_id)
            )
            assert candidate.memory_citations == (citation,)
            assert candidate.sources == (root,)
            followup = PrepareContextRequest(query=experience().lesson)
            pending_context = await runtime.context.for_scope(scope).prepare(followup)
            assert experience().lesson not in (pending_context.content or "")
            approved = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            assert approved.result_artifact is not None
            artifact = await runtime.experience.for_scope(scope).get(
                GetExperienceRequest(artifact=approved.result_artifact)
            )
            assert artifact.lineage.memory_citations == (citation,)
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
            assert all(item.kind != "memory" for item in generator.inputs[1].evidence.evidence)
            assert generator.inputs[1].target_evidence_id is None
            assert derived.candidate is not None
            skill_candidate = await runtime.review.for_scope(scope).get(
                GetArtifactCandidateRequest(candidate_id=derived.candidate.candidate_id)
            )
            assert skill_candidate.memory_citations == ()
            assert skill_candidate.artifacts == (artifact.as_ref(),)
            skill_approval = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(
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
            configured, candidate_pipeline=MemoryPipeline(), dream_generator=Generator()
        ) as runtime:
            scope, _, citation = await seed(runtime)
            assert (await runtime.capabilities()).artifact_dreaming
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience", memory_citations=(citation,), idempotency_key="split-role"
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
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=generator
        ) as runtime:
            scope, _, citation = await seed(runtime)
            request = CreateDreamRunRequest(
                operation="refine_experience", memory_citations=(citation,), idempotency_key="retired"
            )
            accepted = await runtime.dream.for_scope(scope).create(request)
            worker = asyncio.create_task(process_pending(runtime))
            await asyncio.wait_for(generator.started.wait(), timeout=5)
            await runtime.memory.for_scope(scope).retire(RetireMemoryEntryRequest(citation=citation))
            generator.release.set()
            await worker
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (run.status, run.error, run.candidate) == ("failed", "memory_entry_inactive", None)
            assert (await runtime.review.for_scope(scope).list(ListArtifactCandidatesRequest())).candidates == ()
            assert await runtime.dream.for_scope(scope).create(request) == run

    asyncio.run(scenario())


def test_review_rechecks_memory_and_revision_omission_preserves_citations(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(config(database), candidate_pipeline=MemoryPipeline()) as runtime:
            scope, root, citation = await seed(runtime)
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=experience(),
                    memory_citations=(citation,),
                )
            )
            revised = await runtime.review.for_scope(scope).revise(
                ReviseArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=1,
                    proposal=experience(),
                    sources=(root,),
                )
            )
            assert revised.memory_citations == (citation,)
            await runtime.memory.for_scope(scope).retire(RetireMemoryEntryRequest(citation=citation))
            with pytest.raises(EvidenceResolutionError, match="memory_entry_inactive"):
                await runtime.review.for_scope(scope).approve(
                    ApproveArtifactCandidateRequest(
                        candidate_id=candidate.candidate_id,
                        expected_version=2,
                    )
                )
            cleared = await runtime.review.for_scope(scope).revise(
                ReviseArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=2,
                    proposal=experience(),
                    sources=(root,),
                    memory_citations=(),
                )
            )
            assert cleared.memory_citations == ()
            approved = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=3,
                )
            )
            assert approved.result_artifact is not None

    asyncio.run(scenario())


def test_unknown_model_evidence_fails_without_a_candidate(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=Generator(unknown_id=True)
        ) as runtime:
            scope, _, citation = await seed(runtime)
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience",
                    memory_citations=(citation,),
                    idempotency_key="invalid",
                )
            )
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (run.status, run.error) == ("failed", "invalid_generation_output")
            assert (await runtime.review.for_scope(scope).list(ListArtifactCandidatesRequest())).candidates == ()

    asyncio.run(scenario())


def test_dream_http_client_accepts_active_and_terminal_replays(database: DatabaseConfig) -> None:
    import httpx

    from powercontext.client import PowerContextClient
    from powercontext.http import CreateDreamRunRequest as TransportCreateDreamRunRequest
    from powercontext.http import DreamStatus, ExperienceProposal
    from powercontext.http import GetArtifactCandidateRequest as TransportGetArtifactCandidateRequest
    from powercontext.http import ReviseArtifactCandidateRequest as TransportReviseArtifactCandidateRequest
    from powercontext.server.app import create_app

    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=generator
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
                        memory_citations=(citation,),
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
                candidate = await client.get_artifact_candidate(
                    TransportGetArtifactCandidateRequest(scope_id=scope, candidate_id=complete.candidate.candidate_id)
                )
                assert isinstance(candidate.proposal, ExperienceProposal)
                revision = TransportReviseArtifactCandidateRequest(
                    scope_id=scope,
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                    proposal=candidate.proposal,
                    source_refs=candidate.source_refs,
                    artifact_refs=candidate.artifact_refs,
                )
                retained = await client.revise_artifact_candidate(revision)
                assert retained.memory_citations == candidate.memory_citations
                retained = await client.revise_artifact_candidate(
                    revision.model_copy(update={"expected_version": retained.version, "memory_citations": None})
                )
                assert retained.memory_citations == candidate.memory_citations
                cleared = await client.revise_artifact_candidate(
                    revision.model_copy(update={"expected_version": retained.version, "memory_citations": []})
                )
                assert cleared.memory_citations == []
                assert await client.get_dream_run(scope, complete.run_id) == complete

    asyncio.run(scenario())


def test_concurrent_admission_and_workers_create_one_candidate(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=generator
        ) as runtime:
            scope, _, citation = await seed(runtime)
            request = CreateDreamRunRequest(
                operation="refine_experience", memory_citations=(citation,), idempotency_key="concurrent"
            )
            accepted = await asyncio.gather(*(runtime.dream.for_scope(scope).create(request) for _ in range(8)))
            assert len({run.run_id for run in accepted}) == 1
            await asyncio.gather(*(process_pending(runtime) for _ in range(3)))
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted[0].run_id))
            assert run.status == "succeeded"
            assert len(generator.inputs) == 1
            assert len((await runtime.review.for_scope(scope).list(ListArtifactCandidatesRequest())).candidates) == 1

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
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=generator
        ) as runtime:
            scope, _, citation = await seed(runtime)
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience", memory_citations=(citation,), idempotency_key="retry"
                )
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


def test_provider_exceeding_output_budget_leaves_no_candidate(database: DatabaseConfig) -> None:
    class OversizedGenerator(Generator):
        async def generate(self, value):
            result = await super().generate(value)
            return result.model_copy(update={"usage": InferenceUsage(requests=1, output_tokens=4097)})

    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=OversizedGenerator()
        ) as runtime:
            scope, _, citation = await seed(runtime)
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience", memory_citations=(citation,), idempotency_key="output-budget"
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

    from powercontext.server.app import create_app
    from powercontext.server.authentication import StaticBearerAuthenticationProvider
    from powercontext.server.authz import AccessRole, MemoryEntrySelector, PrincipalRef, ResourceRef
    from powercontext.server.authz.composition import open_builtin_access_control
    from powercontext.server.authz.service import AccessAuditContext, CreateBinding
    from powercontext.server.dream_access import DreamAccess, principal_identity
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
                candidate_pipeline=MemoryPipeline(),
                dream_generator=generator,
                dream_authorizer=adapter.authorize,
                dream_authorization_context=access.defer_decision_audit,
                dream_candidate_attester=adapter.attest_candidate,
            ) as runtime:
                scope, _, citation = await seed(runtime)
                entries = await runtime.memory.for_scope(scope).list()
                for item in entries.entries:
                    await access.establish_artifact_owner(
                        ResourceRef.artifact(
                            scope,
                            family="memory",
                            artifact_id=item.citation.memory_ref.artifact_id,
                            selector=MemoryEntrySelector(entry_id=item.citation.entry_id),
                        ),
                        admin,
                        idempotency_key="seed-owner:" + item.citation.entry_id,
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
                    operation="refine_experience", memory_citations=(citation,), idempotency_key="access-revoked"
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
                        "/v1/artifact-candidates/get",
                        json={
                            "scope_id": scope,
                            "candidate_id": candidate["candidate_id"],
                        },
                    )
                    assert reviewed.status_code == 200, reviewed.text
                    # The author has contribution rights, but cannot approve their proposal.
                    forbidden = await client.post(
                        "/v1/artifact-candidates/approve",
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
                        "/v1/artifact-candidates/approve",
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
                    assert approved.json()["memory_citations"] == [citation.model_dump(mode="json")]

    asyncio.run(scenario())


def test_restart_recovers_expired_run_with_its_pinned_input(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        settings = BuiltinConfig(
            database=database,
            runtime=RuntimeConfig(),
        )
        interrupted = Generator(blocked=True)
        async with open_builtin_runtime(
            settings, candidate_pipeline=MemoryPipeline(), dream_generator=interrupted
        ) as runtime:
            scope, _, citation = await seed(runtime)
            assert runtime._dream_service is not None
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience", memory_citations=(citation,), idempotency_key="restart"
                )
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
            assert len((await restarted.review.for_scope(scope).list(ListArtifactCandidatesRequest())).candidates) == 1

    asyncio.run(scenario())


def test_additive_migration_preserves_existing_experience_and_candidate(database: DatabaseConfig) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    async def scenario() -> None:
        settings = BuiltinConfig(database=database)
        async with open_builtin_runtime(settings, candidate_pipeline=MemoryPipeline()) as runtime:
            scope, root, _ = await seed(runtime)
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=experience(),
                    sources=(root,),
                )
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
        url = database.url.get_secret_value() if isinstance(database, OceanBaseConfig) else database.url
        engine = create_async_engine(url, hide_parameters=True)
        try:
            async with engine.begin() as connection:
                await connection.exec_driver_sql("ALTER TABLE pc_artifacts DROP COLUMN memory_citations")
                await connection.exec_driver_sql(
                    "ALTER TABLE pc_artifact_candidate_versions DROP COLUMN memory_citations"
                )
                await connection.exec_driver_sql("DROP TABLE pc_dream_runs")
        finally:
            await engine.dispose()
        async with open_builtin_runtime(settings, dream_generator=Generator()) as runtime:
            assert approved.result_artifact is not None
            restored = await runtime.experience.for_scope(scope).get(
                GetExperienceRequest(artifact=approved.result_artifact)
            )
            assert restored.content == experience()
            assert restored.lineage.memory_citations == ()
            assert restored.lineage.sources == (root,)
            restored_candidate = await runtime.review.for_scope(scope).get(
                GetArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                )
            )
            assert restored_candidate == approved
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="derive_skill",
                    artifacts=(restored.as_ref(),),
                    idempotency_key="after-migration",
                )
            )
            await process_pending(runtime)
            assert (
                await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            ).status == "succeeded"

    asyncio.run(scenario())


def test_replacement_dream_identifies_the_exact_target_in_model_input(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=generator
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
                    ApproveArtifactCandidateRequest(
                        candidate_id=candidate.candidate_id, expected_version=candidate.version
                    )
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
                    GetArtifactCandidateRequest(candidate_id=run.candidate.candidate_id)
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
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=Generator()
        ) as runtime:
            scope, root, citation = await seed(runtime)
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(proposal=experience(), memory_citations=(citation,))
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
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
                ApproveArtifactCandidateRequest(
                    candidate_id=run.candidate.candidate_id, expected_version=run.candidate.version
                )
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
                ApproveArtifactCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
            )
            assert approved.result_artifact is not None
            skill = await runtime.skill.for_scope(scope).get(GetSkillRequest(artifact=approved.result_artifact))
            request = ProposeSkillRequest(
                proposal=skill.content, sources=(root,), artifacts=(skill.as_ref(),), target=skill.as_ref()
            )
            pending = None if phase == "propose" else await runtime.skill.for_scope(scope).propose(request)
            before = await runtime.review.for_scope(scope).list(ListArtifactCandidatesRequest())
            if invalidation == "retired":
                await runtime.memory.for_scope(scope).retire(RetireMemoryEntryRequest(citation=citation))
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
                        ReviseArtifactCandidateRequest(
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
                        ApproveArtifactCandidateRequest(
                            candidate_id=pending.candidate_id, expected_version=pending.version
                        )
                    )
            assert await runtime.review.for_scope(scope).list(ListArtifactCandidatesRequest()) == before

    asyncio.run(scenario())


def test_skill_approval_rechecks_transitive_memory_state(database: DatabaseConfig) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=Generator()
        ) as runtime:
            scope, _, citation = await seed(runtime)
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=experience(),
                    memory_citations=(citation,),
                )
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(
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
            await runtime.memory.for_scope(scope).retire(RetireMemoryEntryRequest(citation=citation))
            with pytest.raises(EvidenceResolutionError, match="memory_entry_inactive"):
                await runtime.review.for_scope(scope).approve(
                    ApproveArtifactCandidateRequest(
                        candidate_id=run.candidate.candidate_id,
                        expected_version=run.candidate.version,
                    )
                )
            pending = await runtime.review.for_scope(scope).get(
                GetArtifactCandidateRequest(
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
            await runtime.memory.for_scope(scope).remember(
                RememberMemoryRequest(
                    entries=(
                        MemoryEntryInput(
                            kind="preference",
                            text="I prefer retrying writes without checking the previous result.",
                        ),
                    )
                )
            )
            citation = (await runtime.memory.for_scope(scope).list()).entries[0].citation
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience",
                    memory_citations=(citation,),
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
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=Generator()
        ) as runtime:
            scope, _, citation = await seed(runtime)
            first = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=experience(),
                    memory_citations=(citation,),
                )
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=first.candidate_id,
                    expected_version=first.version,
                )
            )
            target = approved.result_artifact
            assert target is not None
            request = CreateDreamRunRequest(
                operation="refine_experience",
                artifacts=(target,),
                target=target,
                memory_citations=(citation,),
                idempotency_key="replacement",
            )
            accepted = await runtime.dream.for_scope(scope).create(request)
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert run.candidate is not None
            candidate = await runtime.review.for_scope(scope).get(
                GetArtifactCandidateRequest(candidate_id=run.candidate.candidate_id)
            )
            assert candidate.target == target and target in candidate.artifacts
            replacement = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(
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
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=PackageClaimingGenerator()
        ) as runtime:
            scope, _, citation = await seed(runtime)
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=experience(),
                    memory_citations=(citation,),
                )
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(
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
        async def extract(self, request: MemoryCandidateRequest, /):
            return tuple(
                MemoryEntryInput(kind="task_record", text=text, sources=(source,))
                for source in request.sources
                for text in ("Replaying the original key kept one row.", "The same replay produced no duplicate write.")
            )

    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=EchoPipeline(), dream_generator=generator
        ) as runtime:
            scope, root, citation = await seed(runtime)
            entries = await runtime.memory.for_scope(scope).list()
            citations = tuple(item.citation for item in entries.entries if item.entry.kind == "task_record")
            assert len(citations) == 2
            candidate = await runtime.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=experience(),
                    memory_citations=(citation,),
                )
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            assert approved.result_artifact is not None
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience",
                    artifacts=(approved.result_artifact,),
                    memory_citations=citations,
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
                GetArtifactCandidateRequest(candidate_id=run.candidate.candidate_id)
            )
            assert result.sources == (root,)
            assert len(result.memory_citations) == 2
            assert result.artifacts == (approved.result_artifact,)

    asyncio.run(scenario())


def test_superseded_supervisor_cannot_overwrite_recovered_result(database: DatabaseConfig) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_LEASES_TABLE

    async def scenario() -> None:
        delayed = Generator(blocked=True)
        async with open_builtin_runtime(
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=delayed
        ) as original:
            scope, _, citation = await seed(original)
            accepted = await original.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience", memory_citations=(citation,), idempotency_key="late-worker"
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
                    delayed.release.set()
                    with suppress(asyncio.CancelledError):
                        await worker
                    assert await recovered.dream.for_scope(scope).get(GetDreamRunRequest(run_id=run.run_id)) == run
                    candidates = await recovered.review.for_scope(scope).list(ListArtifactCandidatesRequest())
                    assert len(candidates.candidates) == 1
                    assert delayed.inputs == replacement.inputs
            finally:
                delayed.release.set()
                with suppress(asyncio.CancelledError):
                    await worker

    asyncio.run(scenario())


def test_candidate_and_run_rollback_together(database: DatabaseConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=generator
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
                operation="refine_experience", memory_citations=(citation,), idempotency_key="rollback"
            )
            accepted = await runtime.dream.for_scope(scope).create(request)
            with monkeypatch.context() as patch:
                patch.setattr(repository, "finish", interrupt_commit)
                await process_pending(runtime)
            failed = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (failed.status, failed.candidate) == ("failed", None)
            assert (await runtime.review.for_scope(scope).list(ListArtifactCandidatesRequest())).candidates == ()
            assert await runtime.dream.for_scope(scope).create(request) == failed
            retried = await runtime.dream.for_scope(scope).create(
                request.model_copy(update={"idempotency_key": "retry"})
            )
            await process_pending(runtime)
            complete = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=retried.run_id))
            assert complete.status == "succeeded"
            assert len((await runtime.review.for_scope(scope).list(ListArtifactCandidatesRequest())).candidates) == 1

    asyncio.run(scenario())


def test_dream_keeps_prompt_lineage_out_of_factual_evidence(database: DatabaseConfig) -> None:
    from powercontext.artifacts import ArtifactRef
    from powercontext.builtin.records import ArtifactWrite
    from powercontext.builtin.review.errors import InvalidCandidateError

    async def scenario() -> None:
        generator = Generator()
        async with open_builtin_runtime(
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=generator
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
                ProposeExperienceRequest(
                    proposal=experience(), sources=(root,), artifacts=(prompt_ref,), memory_citations=(citation,)
                )
            )
            approved = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
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
                ApproveArtifactCandidateRequest(
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
            candidate_pipeline=MemoryPipeline() if with_memory else None,
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
                await runtime.memory.for_scope(scope).flush()
                entries = await runtime.memory.for_scope(scope).list()
                citations = (entries.entries[0].citation,)
            target = None
            for revision in range(1, 13):
                refs = () if target is None else (target,)
                candidate = await runtime.experience.for_scope(scope).propose(
                    ProposeExperienceRequest(
                        proposal=experience(),
                        sources=() if target is None and citations else (source.source_ref,),
                        artifacts=refs,
                        target=target,
                        memory_citations=citations if target is None else (),
                    )
                )
                approved = await runtime.review.for_scope(scope).approve(
                    ApproveArtifactCandidateRequest(
                        candidate_id=candidate.candidate_id, expected_version=candidate.version
                    )
                )
                target = approved.result_artifact
                assert target is not None and target.revision == revision
                stored = await runtime.experience.for_scope(scope).get(GetExperienceRequest(artifact=target))
                assert stored.lineage.sources == (source.source_ref,)
                assert stored.lineage.artifacts == refs
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
                await runtime.memory.for_scope(scope).retire(RetireMemoryEntryRequest(citation=citations[0]))
                with pytest.raises(EvidenceResolutionError, match="memory_entry_inactive"):
                    await runtime.review.for_scope(scope).approve(
                        ApproveArtifactCandidateRequest(
                            candidate_id=candidate.candidate_id, expected_version=candidate.version
                        )
                    )
                pending = await runtime.review.for_scope(scope).get(
                    GetArtifactCandidateRequest(candidate_id=candidate.candidate_id)
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
                ApproveArtifactCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
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
            revision = ReviseArtifactCandidateRequest(
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
            approval = ApproveArtifactCandidateRequest(
                candidate_id=revised.candidate_id, expected_version=revised.version
            )
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
            settings, candidate_pipeline=MemoryPipeline(), dream_generator=generator
        ) as runtime:
            scope, _, citation = await seed(runtime)
            request = CreateDreamRunRequest(
                operation="refine_experience", memory_citations=(citation,), idempotency_key="first"
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
            candidates = (await runtime.review.for_scope(scope).list(ListArtifactCandidatesRequest())).candidates
            assert len(candidates) == len(generator.inputs) == 2
            assert all(candidate.status == "pending" for candidate in candidates)
            assert runtime._dream_service is not None
            async with runtime._dream_service.database.transaction() as connection:
                assert await SourceCursorRepository().load(connection, scope, EXPERIENCE_INCUBATION_CURSOR_NAME) is None

    asyncio.run(scenario())
