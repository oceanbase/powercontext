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

"""Targets are comparison context, not independent Dream support."""

import asyncio

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.profile.models import ProfileWriteContent
from powercontext.builtin.catalog_changes.models import TagChangeProposal, TagDreamTarget
from powercontext.builtin.dream.models import DreamPlan
from powercontext.builtin.inference.models import GenerationResult
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    ApproveCandidateRequest,
    BuiltinConfig,
    CaptureSource,
    CreateDreamRunRequest,
    GetCandidateRequest,
    GetDreamRunRequest,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.tags import ArtifactTagTarget
from tests.e2e.dream_support import open_dream_runtime, process_pending
from tests.e2e.test_artifact_dreaming import database as database


@pytest.mark.parametrize("operation", ["revise_profile", "revise_tags"])
@pytest.mark.parametrize("selection", ["target-and-source", "target-only", "unknown"])
def test_target_evidence_requires_independent_support(database, operation, selection):
    class Generator:
        config_id = "target-evidence-test"

        async def generate(self, value):
            assert value.target_evidence_id is not None
            selected = (value.target_evidence_id,)
            if selection == "target-and-source":
                selected += tuple(item.evidence_id for item in value.evidence.evidence if item.kind == "source")
            elif selection == "unknown":
                selected += ("invented-evidence",)
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    intent="correct",
                    reason="The Source confirms the relocation.",
                    proposal=ProfileWriteContent(content="# Profile\n\nBased in Shenzhen.")
                    if operation == "revise_profile"
                    else TagChangeProposal(after_tags=("shenzhen",)),
                    evidence_ids=selected,
                )
            )

    async def scenario():
        async with open_dream_runtime(BuiltinConfig(database=database), dream_generator=Generator()) as runtime:
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(
                        title="Relocation",
                        summary="Independent correction",
                        idempotency_key="target-evidence",
                    )
                )
            ).scope_id
            records = runtime.records.for_scope(scope)
            source = await runtime.sources.for_scope(scope).capture(
                CaptureSource(
                    source_id="correction",
                    content="I now live in Shenzhen.",
                    metadata={},
                )
            )
            initial = await records.create_artifact(
                "profile",
                ArtifactWrite(
                    content={"content": "# Profile\n\nBased in Shanghai."},
                ),
            )
            ref = ArtifactRef(family="profile", artifact_id=initial.artifact_id, revision=initial.revision)
            tag_target = ArtifactTagTarget(family="profile", artifact_id=initial.artifact_id)
            before_tags = await records.get_tags(tag_target)
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation=operation,
                    sources=(source.source_ref,),
                    idempotency_key="correction",
                    target=ref if operation == "revise_profile" else None,
                    artifacts=(ref,) if operation == "revise_profile" else (),
                    tag_target=TagDreamTarget(target=tag_target, expected_etag=before_tags.etag, basis_ref=ref)
                    if operation == "revise_tags"
                    else None,
                )
            )
            await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (await records.get_artifact("profile", initial.artifact_id)).revision == ref.revision
            assert await records.get_tags(tag_target) == before_tags
            if selection == "unknown":
                assert run.status == "failed" and run.error == "invalid_generation_output"
                assert run.candidate is None
                return
            if selection == "target-only":
                assert (run.status, run.outcome, run.candidate) == ("succeeded", "needs_evidence", None)
                return
            assert run.candidate is not None, run
            review = runtime.review.for_scope(scope)
            candidate = await review.get(GetCandidateRequest(candidate_id=run.candidate.candidate_id))
            assert candidate.sources == (source.source_ref,)
            assert candidate.artifacts == (ref,)
            approved = await review.approve(
                ApproveCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            assert approved.status == "approved"
            if operation == "revise_profile":
                latest = await records.get_artifact("profile", initial.artifact_id)
                assert latest.revision == ref.revision + 1
                assert "Shenzhen" in latest.content["content"]
            else:
                assert (await records.get_tags(tag_target)).tags == ("shenzhen",)

    asyncio.run(scenario())
