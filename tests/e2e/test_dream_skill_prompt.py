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

"""Skill and Prompt Dream publishing preserves managed package and configuration contracts."""

from __future__ import annotations

import asyncio
import io
import zipfile

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff.models import (
    HandoffContent,
    HandoffOmission,
    HandoffSourceCitation,
    HandoffStatement,
)
from powercontext.builtin.artifacts.prompt import PromptContent
from powercontext.builtin.artifacts.prompt.models import PromptDemonstration
from powercontext.builtin.artifacts.prompt.service import current_prompt
from powercontext.builtin.artifacts.skill import SkillDraft
from powercontext.builtin.artifacts.skill.package import capture_skill_archive
from powercontext.builtin.dream.models import DreamPlan
from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
from powercontext.builtin.persistence.artifacts import RepositoryArtifactDraft
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.review.errors import ArtifactTargetConflictError, InvalidCandidateError
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest,
    BuiltinConfig,
    CaptureSource,
    CreateDreamRunRequest,
    GetArtifactCandidateRequest,
    GetDreamRunRequest,
    InferenceConfig,
    ReviseArtifactCandidateRequest,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceRef
from tests.e2e.dream_support import open_dream_runtime, process_pending
from tests.e2e.test_artifact_dreaming import DatabaseConfig, config
from tests.e2e.test_artifact_dreaming import database as database_fixture

database = database_fixture


class PromptGenerator:
    config_id = "prompt-dream-test"

    def __init__(self, *, invalid_schema=False):
        self.invalid_schema = invalid_schema

    async def generate(self, value):
        assert value.operation == "revise_prompt"
        assert value.prompt_definition["key"] == "memory.extract"
        target = next(item for item in value.evidence.evidence if item.kind == "prompt")
        assert "Always treat trips as moves." in target.text
        proposal = PromptContent(
            schema_version="powercontext.prompt.v1",
            mode="custom",
            instructions="Do not infer a permanent relocation from a temporary trip.",
            demonstrations=(PromptDemonstration(input={}, expected_output={"unknown": True}),)
            if self.invalid_schema
            else (),
        )
        return GenerationResult(
            output=DreamPlan(
                outcome="proposed",
                reason="The correction distinguishes temporary travel from a move.",
                intent="correct",
                proposal=proposal,
                evidence_ids=tuple(item.evidence_id for item in value.evidence.evidence if item.kind == "source"),
            ),
            usage=InferenceUsage(requests=1),
        )


@pytest.mark.parametrize("case", ["publish", "edit", "schema", "conflict"])
def test_prompt_dream_review_publication_and_monotonic_rollback(database: DatabaseConfig, case: str) -> None:
    async def scenario():
        settings = BuiltinConfig(database=database, inference=InferenceConfig(generation_model="test"))
        async with open_dream_runtime(
            settings, dream_generator=PromptGenerator(invalid_schema=case == "schema")
        ) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title="Prompt review", summary="Corrections", idempotency_key="prompt")
            )
            records = runtime.records.for_scope(scope.scope_id)
            original = PromptContent(
                schema_version="powercontext.prompt.v1",
                mode="custom",
                instructions="Always treat trips as moves.",
                demonstrations=(),
            )
            created = await records.create_artifact(
                "prompt", ArtifactWrite(prompt_key="memory.extract", content=original.model_dump(mode="json"))
            )
            target = ArtifactRef(family="prompt", artifact_id=created.artifact_id, revision=created.revision)
            source = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(
                    source_id="correction",
                    content="The user said a two-day visit. The extraction incorrectly claimed relocation. The user confirmed their residence is unchanged.",
                    metadata={},
                )
            )
            request = CreateDreamRunRequest(
                operation="revise_prompt",
                target=target,
                artifacts=(target,),
                sources=(source.source_ref,),
                idempotency_key="correct-trip",
            )
            run = await runtime.dream.for_scope(scope.scope_id).create(request)
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope.scope_id).get(GetDreamRunRequest(run_id=run.run_id))
            before = await records.get_artifact("prompt", "memory.extract")
            assert before.revision == 1
            if case == "schema":
                assert run.status == "failed" and run.candidate is None
                return
            assert run.candidate is not None, run
            review = runtime.review.for_scope(scope.scope_id)
            candidate = await review.get(GetArtifactCandidateRequest(candidate_id=run.candidate.candidate_id))
            invalid = candidate.proposal.model_copy(update={"mode": "auto", "instructions": "", "demonstrations": ()})
            with pytest.raises(InvalidCandidateError, match="custom mode"):
                await review.revise(
                    ReviseArtifactCandidateRequest(
                        candidate_id=candidate.candidate_id,
                        expected_version=candidate.version,
                        proposal=invalid,
                        sources=candidate.sources,
                        artifacts=candidate.artifacts,
                        target=candidate.target,
                        reason="Cannot switch Dream to auto.",
                    )
                )
            if case == "conflict":
                await records.replace_artifact(
                    "prompt", "memory.extract", '"revision:1"', ArtifactWrite(content=original.model_dump(mode="json"))
                )
                with pytest.raises(ArtifactTargetConflictError):
                    await review.approve(
                        ApproveArtifactCandidateRequest(
                            candidate_id=candidate.candidate_id, expected_version=candidate.version
                        )
                    )
                assert (
                    await review.get(GetArtifactCandidateRequest(candidate_id=candidate.candidate_id))
                ).status == "pending"
                return
            if case == "edit":
                candidate = await review.revise(
                    ReviseArtifactCandidateRequest(
                        candidate_id=candidate.candidate_id,
                        expected_version=candidate.version,
                        proposal=candidate.proposal.model_copy(
                            update={
                                "instructions": "Do not infer relocation from a temporary trip. Preserve uncertainty."
                            }
                        ),
                        sources=candidate.sources,
                        artifacts=candidate.artifacts,
                        target=candidate.target,
                        reason="The reviewer clarified uncertainty handling.",
                    )
                )
                assert candidate.version == 2
            prompts = runtime._provider.prompts
            async with prompts.bind(scope.scope_id, "memory.extract") as frozen:
                approved = await review.approve(
                    ApproveArtifactCandidateRequest(
                        candidate_id=candidate.candidate_id, expected_version=candidate.version
                    )
                )
                assert approved.result_artifact.revision == 2
                assert current_prompt("memory.extract") == frozen
                assert frozen.artifact == target
            fresh = await prompts.resolve(scope.scope_id, "memory.extract")
            assert fresh.artifact.revision == 2
            assert "temporary trip" in fresh.instructions
            rolled = await records.replace_artifact(
                "prompt", "memory.extract", '"revision:2"', ArtifactWrite(content=original.model_dump(mode="json"))
            )
            assert rolled.revision == 3
            assert (await prompts.resolve(scope.scope_id, "memory.extract")).instructions == original.instructions

    asyncio.run(scenario())


def test_dream_cannot_target_its_own_prompt() -> None:
    target = ArtifactRef(family="prompt", artifact_id="dream.generate", revision=1)
    with pytest.raises(ValueError, match="invalid_dream_operation"):
        CreateDreamRunRequest(
            operation="revise_prompt",
            target=target,
            artifacts=(target,),
            sources=(SourceRef(source_type="content", source_id="feedback"),),
            idempotency_key="self",
        )


def test_skill_dream_rejects_multifile_package_without_deleting_files(database: DatabaseConfig) -> None:
    async def scenario():
        class NoGeneration:
            config_id = "unsupported-skill-test"

            async def generate(self, _value):
                pytest.fail("Unsupported packages must not enter generation")

        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(
                "SKILL.md", "---\nname: safe-retry\ndescription: Retry with checks.\n---\n\nCheck first.\n"
            )
            archive.writestr("verify.py", "print('not executed')\n")
        snapshot = capture_skill_archive(stream.getvalue())
        async with open_dream_runtime(config(database), dream_generator=NoGeneration()) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title="Multi-file Skill", summary="Preserve files", idempotency_key="multi")
            )
            source = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="feedback", content="The retry duplicated work.", metadata={})
            )
            async with runtime._provider.database.transaction() as connection:
                await runtime._provider.repositories.skill_packages.add(connection, scope.scope_id, snapshot)
                target = await runtime._provider.repositories.artifacts.create(
                    connection,
                    scope.scope_id,
                    "safe-retry",
                    SkillDraft(content=snapshot.as_skill_content(), sources=(source.source_ref,)),
                )
            run = await runtime.dream.for_scope(scope.scope_id).create(
                CreateDreamRunRequest(
                    operation="revise_skill",
                    target=target.as_ref(),
                    artifacts=(target.as_ref(),),
                    sources=(source.source_ref,),
                    idempotency_key="multi",
                )
            )
            await process_pending(runtime)
            completed = await runtime.dream.for_scope(scope.scope_id).get(GetDreamRunRequest(run_id=run.run_id))
            assert completed.status == "failed" and completed.error == "unsupported_target"
            assert completed.candidate is None
            latest = await runtime.records.for_scope(scope.scope_id).get_artifact("skill", "safe-retry")
            assert latest.revision == 1 and latest.content["package"]["file_count"] == 2

    asyncio.run(scenario())


def test_handoff_omission_references_must_belong_to_selected_evidence(database: DatabaseConfig) -> None:
    async def scenario():
        async with open_dream_runtime(config(database), dream_generator=PromptGenerator()) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title="Handoff", summary="Evidence", idempotency_key="omission")
            )
            source = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(
                    source_id="work", content="The case is blocked pending receipt verification.", metadata={}
                )
            )
            original = HandoffContent(
                objective="Resolve the case",
                state=(
                    HandoffStatement(
                        text="The case is blocked.", citations=(HandoffSourceCitation(source_ref=source.source_ref),)
                    ),
                ),
                next_action=None,
                disposition="blocked",
                omissions=(),
            )
            async with runtime._provider.database.transaction() as connection:
                target = await runtime._provider.repositories.artifacts.create(
                    connection,
                    scope.scope_id,
                    "case",
                    RepositoryArtifactDraft(family="handoff", content=original, sources=(source.source_ref,)),
                )
            proposal = original.model_copy(
                update={
                    "omissions": (
                        HandoffOmission(
                            text="Receipt unverified",
                            citation=HandoffSourceCitation(
                                source_ref=SourceRef(source_type="content", source_id="invented")
                            ),
                        ),
                    )
                }
            )
            with pytest.raises(InvalidCandidateError, match="outside the selected inputs"):
                await runtime._provider.review(scope.scope_id).propose_handoff_dream(
                    proposal,
                    sources=(source.source_ref,),
                    artifacts=(target.as_ref(),),
                    target=target.as_ref(),
                    reason="Refresh status",
                    candidate_id="cand_dream_omission",
                    memory_citations=(),
                )

    asyncio.run(scenario())
