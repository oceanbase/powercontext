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

"""Proposal identity survives distinct request keys without losing review history."""

import asyncio

import pytest

from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest,
    CreateDreamRunRequest,
    GetArtifactCandidateRequest,
    GetDreamRunRequest,
    ProposeExperienceRequest,
    RejectArtifactCandidateRequest,
    RetireMemoryEntryRequest,
    ReviseArtifactCandidateRequest,
)
from tests.e2e.dream_support import open_dream_runtime, process_pending
from tests.e2e.test_artifact_dreaming import (
    Generator,
    MemoryPipeline,
    config,
    experience,
    seed,
)
from tests.e2e.test_artifact_dreaming import (
    database as database,
)


@pytest.mark.parametrize("decision", ["pending", "rejected", "revised"])
def test_dream_suppresses_unchanged_proposals_but_preserves_edited_candidate(database, decision):
    async def scenario():
        generator = Generator()
        async with open_dream_runtime(
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=generator
        ) as runtime:
            scope, _, citation = await seed(runtime)
            dream = runtime.dream.for_scope(scope)
            review = runtime.review.for_scope(scope)
            request = CreateDreamRunRequest(
                operation="refine_experience", memory_citations=(citation,), idempotency_key="first"
            )
            first = await dream.create(request)
            await process_pending(runtime)
            first = await dream.get(GetDreamRunRequest(run_id=first.run_id))
            assert first.candidate is not None
            candidate = await review.get(GetArtifactCandidateRequest(candidate_id=first.candidate.candidate_id))
            assert candidate.audit is not None
            assert candidate.audit.dream_run_id == first.run_id
            assert candidate.audit.proposal_digest.startswith("sha256:")
            if decision == "rejected":
                await review.reject(
                    RejectArtifactCandidateRequest(
                        candidate_id=candidate.candidate_id, expected_version=1, reason="The rule is too broad."
                    )
                )
            elif decision == "revised":
                revised = await review.revise(
                    ReviseArtifactCandidateRequest(
                        candidate_id=candidate.candidate_id,
                        expected_version=1,
                        proposal=candidate.proposal.model_copy(
                            update={"lesson": "Replay only after checking the server's exact contract."}
                        ),
                        sources=candidate.sources,
                        artifacts=candidate.artifacts,
                        memory_citations=candidate.memory_citations,
                        target=candidate.target,
                        reason="Review narrows the applicability.",
                    )
                )
                assert revised.audit is not None and revised.audit.proposal_fingerprint is None
                assert revised.audit.proposal_digest != candidate.audit.proposal_digest
            second = await dream.create(request.model_copy(update={"idempotency_key": "second"}))
            await process_pending(runtime)
            second = await dream.get(GetDreamRunRequest(run_id=second.run_id))
            assert second.status == "succeeded", second.error
            assert second.run_id != first.run_id
            assert second.candidate is not None
            if decision == "revised":
                assert second.candidate != first.candidate
                assert not second.reused
            else:
                assert second.candidate == first.candidate
                assert second.reused
                assert second.outcome == ("no_change" if decision == "rejected" else "proposed")
                assert second.usage.model_calls == 0

    asyncio.run(scenario())


def test_dream_fingerprint_never_reuses_another_principals_candidate(database):
    async def authorize(*_args):
        return None

    async def scenario():
        async with open_dream_runtime(
            config(database),
            candidate_pipeline=MemoryPipeline(),
            dream_generator=Generator(),
            dream_authorizer=authorize,
        ) as runtime:
            scope, _, citation = await seed(runtime)
            request = CreateDreamRunRequest(
                operation="refine_experience", memory_citations=(citation,), idempotency_key="same-key"
            )
            runs = []
            for principal in ("alice", "bob"):
                dream = runtime.dream.for_scope(scope, principal_id=principal)
                accepted = await dream.create(request)
                await process_pending(runtime)
                completed = await dream.get(GetDreamRunRequest(run_id=accepted.run_id))
                assert completed.status == "succeeded", completed.error
                assert not completed.reused and completed.candidate is not None
                runs.append(completed)
            assert runs[0].candidate != runs[1].candidate

    asyncio.run(scenario())


def test_shared_root_does_not_reuse_candidate_with_unselected_retired_lineage(database):
    async def scenario():
        async with open_dream_runtime(
            config(database), candidate_pipeline=MemoryPipeline(), dream_generator=Generator()
        ) as runtime:
            scope, source, citation = await seed(runtime)
            review = runtime.review.for_scope(scope)
            experiences = runtime.experience.for_scope(scope)
            first_evidence = await experiences.propose(
                ProposeExperienceRequest(proposal=experience(), memory_citations=(citation,))
            )
            first_evidence = await review.approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=first_evidence.candidate_id, expected_version=first_evidence.version
                )
            )
            assert first_evidence.result_artifact is not None
            second_evidence = await experiences.propose(
                ProposeExperienceRequest(
                    proposal=experience().model_copy(
                        update={"lesson": "Check the exact operation contract before replaying."}
                    ),
                    sources=(source,),
                )
            )
            second_evidence = await review.approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=second_evidence.candidate_id, expected_version=second_evidence.version
                )
            )
            assert second_evidence.result_artifact is not None
            dream = runtime.dream.for_scope(scope)
            first = await dream.create(
                CreateDreamRunRequest(
                    operation="refine_experience",
                    artifacts=(first_evidence.result_artifact,),
                    idempotency_key="old-chain",
                )
            )
            await process_pending(runtime)
            first = await dream.get(GetDreamRunRequest(run_id=first.run_id))
            assert first.candidate is not None
            await runtime.memory.for_scope(scope).retire(RetireMemoryEntryRequest(citation=citation))
            second = await dream.create(
                CreateDreamRunRequest(
                    operation="refine_experience",
                    artifacts=(second_evidence.result_artifact,),
                    idempotency_key="current-chain",
                )
            )
            await process_pending(runtime)
            second = await dream.get(GetDreamRunRequest(run_id=second.run_id))
            assert second.candidate is not None, second
            assert not second.reused and second.candidate != first.candidate
            candidate = await review.get(GetArtifactCandidateRequest(candidate_id=second.candidate.candidate_id))
            assert second_evidence.result_artifact in candidate.artifacts
            assert first_evidence.result_artifact not in candidate.artifacts
            approved = await review.approve(
                ApproveArtifactCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
            )
            assert approved.result_artifact is not None

    asyncio.run(scenario())
