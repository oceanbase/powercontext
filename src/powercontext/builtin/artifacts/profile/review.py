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


"""Profile-specific decisions inside the existing Candidate transaction."""

from datetime import UTC, datetime

from powercontext.builtin.artifacts.profile.models import (
    PROFILE_ARTIFACT_ID,
    PROFILE_SOURCE_WINDOW_BINDING,
    ProfileCandidateProposal,
    ProfileContent,
    ProfileDraft,
    ProfileGeneration,
    ProfileWriteContent,
)
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.profile import ProfilePolicyRepository
from powercontext.builtin.records import BaseValueConflictError
from powercontext.builtin.review.errors import InvalidCandidateError
from powercontext.builtin.sources import SourceCursor


async def decide_profile(service, connection, candidate_id, expected_version, *, reason=None):
    # Acquire Policy before Candidate/Artifact, matching generation's lock order.
    policies = ProfilePolicyRepository()
    policy = await policies.get(connection, service._scope_id, for_update=True)
    candidate = await service._candidates.lock_pending(connection, service._scope_id, candidate_id, expected_version)
    if policy is None or policy.pending_candidate_id != candidate_id:
        raise BaseValueConflictError("profile_candidate", (service._scope_id,))
    proposal = candidate.proposal
    if not isinstance(proposal, ProfileCandidateProposal):
        raise InvalidCandidateError("family", "profile required")
    cursors = SourceCursorRepository()
    cursor = await cursors.load(connection, service._scope_id, PROFILE_SOURCE_WINDOW_BINDING, for_update=True)
    if (0 if cursor is None else cursor.cursor.sequence) != proposal.source_window.after:
        raise BaseValueConflictError("profile_cursor", (service._scope_id,))
    await policies.update(connection, policy, pending_candidate_id=None)
    if reason is None:
        await service._validate_evidence(connection, candidate.sources, candidate.artifacts)
        try:
            current = await service._artifacts.latest(connection, service._scope_id, "profile", PROFILE_ARTIFACT_ID)
        except RepositoryNotFoundError:
            current = None
        if (None if current is None else current.as_ref()) != candidate.target:
            raise BaseValueConflictError("profile_head", (service._scope_id,))
        draft = ProfileDraft(
            content=ProfileContent(
                content=proposal.content,
                generation=ProfileGeneration(
                    mode="review_approved",
                    created_at=datetime.now(UTC),
                    generator_id=proposal.generator_id,
                    generator_version=proposal.generator_version,
                    source_window=proposal.source_window,
                ),
            ),
            sources=candidate.sources,
            artifacts=candidate.artifacts,
        )
        artifact = (
            await service._artifacts.create(connection, service._scope_id, PROFILE_ARTIFACT_ID, draft)
            if current is None
            else await service._artifacts.revise(connection, service._scope_id, current, draft)
        )
        result = await service._candidates.mark_approved(
            connection,
            service._scope_id,
            candidate_id,
            expected_version,
            artifact.as_ref(),
        )
    else:
        # Reject deliberately ignores the current Artifact head and consumes this window.
        result = await service._candidates.reject(connection, service._scope_id, candidate_id, expected_version, reason)
    await cursors.save(
        connection,
        service._scope_id,
        PROFILE_SOURCE_WINDOW_BINDING,
        SourceCursor(sequence=proposal.source_window.through),
        expected_generation=None if cursor is None else cursor.generation,
    )
    return result


def revise_profile(current, proposal, sources, artifacts, target):
    if not isinstance(proposal, ProfileWriteContent) or proposal.restored_from_revision is not None:
        raise InvalidCandidateError("proposal", "only Markdown content is writable")
    if sources != current.sources or artifacts != current.artifacts or target != current.target:
        raise InvalidCandidateError("evidence", "Profile processing evidence and target are immutable")
    return current.proposal.model_copy(update={"content": proposal.content})
