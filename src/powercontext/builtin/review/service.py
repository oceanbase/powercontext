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

"""Transactional Artifact Candidate and Review orchestration."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from typing import Any, TypeAlias, cast

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact, ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.models import (
    AtomicMemory,
    AtomicMemoryContent,
    AtomicMemoryStateValue,
)
from powercontext.builtin.artifacts.atomic_memory.service import AtomicMemoryService
from powercontext.builtin.artifacts.experience import (
    Experience,
    ExperienceCandidateInput,
    ExperienceContent,
    ExperienceDraft,
)
from powercontext.builtin.artifacts.experience.recurrence import (
    failure_item_text,
    failure_refs,
    normalize_match_text,
)
from powercontext.builtin.artifacts.handoff.models import (
    Handoff,
    HandoffArtifactCitation,
    HandoffContent,
    HandoffSourceCitation,
)
from powercontext.builtin.artifacts.profile.models import ProfileCandidateProposal, ProfileWriteContent
from powercontext.builtin.artifacts.profile.review import decide_profile, revise_profile
from powercontext.builtin.artifacts.prompt import Prompt, PromptContent, PromptRegistry
from powercontext.builtin.artifacts.prompt.models import PROMPT_KEYS
from powercontext.builtin.artifacts.skill import Skill, SkillContent, SkillDraft, build_instruction_skill_package
from powercontext.builtin.artifacts.topic_memory.models import TopicMemory, TopicMemoryContent, TopicMemoryDraft
from powercontext.builtin.authz.service import AccessAuditContext
from powercontext.builtin.evidence.models import EvidenceResolutionError, QualifiedOrigin
from powercontext.builtin.evidence.resolver import AuthorizationContext, EvidenceAuthorizer, EvidenceResolver
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.candidates import CandidateRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.dream import DreamRepository
from powercontext.builtin.persistence.errors import RepositoryError, RepositoryNotFoundError
from powercontext.builtin.persistence.experience_index import ExperienceIndex
from powercontext.builtin.persistence.family_management import HandoffManagementWriter, PromptManagementWriter
from powercontext.builtin.persistence.generation_sources import GenerationSourceAccess
from powercontext.builtin.persistence.profile import ProfilePolicyRepository
from powercontext.builtin.persistence.skill_packages import SkillPackageRepository
from powercontext.builtin.persistence.sources import StoredSource
from powercontext.builtin.persistence.topic_memory_management import TopicMemoryManagementWriter
from powercontext.builtin.review.errors import (
    ArtifactTargetConflictError,
    CandidateConflictError,
    CandidateTerminalError,
    InvalidCandidateError,
)
from powercontext.builtin.review.models import (
    MAX_CANDIDATE_EVIDENCE,
    MAX_CANDIDATE_REASON_LENGTH,
    Candidate,
    CandidateAudit,
    CandidateEvidenceView,
    CandidatePage,
    CandidateStatus,
)
from powercontext.builtin.sources.content import ContentSource
from powercontext.builtin.work.models import TaskOutcome
from powercontext.errors import ArtifactNotFoundError, RevisionConflictError
from powercontext.sources import SourceRef

IdFactory = Callable[[str], str]
ReviewedProposal: TypeAlias = (
    ExperienceContent
    | SkillContent
    | ProfileCandidateProposal
    | AtomicMemoryContent
    | TopicMemoryContent
    | HandoffContent
    | PromptContent
)
ReviewedArtifact: TypeAlias = Experience | Skill
ReviewedDraft: TypeAlias = ExperienceDraft | SkillDraft
ReviewedCandidate: TypeAlias = Candidate[ReviewedProposal]
CandidateCommitHook = Callable[[AsyncConnection, Candidate[Any]], Awaitable[None]]


class ReviewService:
    """Keep Candidate CAS and Artifact CAS inside one database transaction."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        candidates: CandidateRepository,
        artifacts: ArtifactRepository,
        experience_index: ExperienceIndex,
        skill_packages: SkillPackageRepository,
        sources: GenerationSourceAccess,
        id_factory: IdFactory,
        evidence: EvidenceResolver | None = None,
        memory: AtomicMemoryService | None = None,
        topic_writer: TopicMemoryManagementWriter | None = None,
        handoff_writer: HandoffManagementWriter | None = None,
        prompt_registry: PromptRegistry | None = None,
        authorization_context: Callable[[], AbstractAsyncContextManager[None]] = nullcontext,
        connection: AsyncConnection | None = None,
        share_access=None,
        share_scopes=None,
        share_principal=None,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._candidates = candidates
        self._artifacts = artifacts
        self._experience_index = experience_index
        self._skill_packages = skill_packages
        self._sources = sources
        self._id_factory = id_factory
        self._evidence = evidence
        # The owners a shared approval needs to re-prove its binding. Absent these, a shared
        # approval refuses rather than publishing under an unverified pin.
        self._share_access = share_access
        self._share_scopes = share_scopes
        self._share_principal = share_principal
        self._memory = memory
        self._topic_writer = topic_writer
        self._handoff_writer = handoff_writer
        self._prompt_registry = prompt_registry
        self._authorization_context = authorization_context
        self._bound_connection = connection
        self.authorize_action: Callable[[str, str, Candidate[Any]], Awaitable[None]] | None = None

    def configure_authorization(self, authorize: EvidenceAuthorizer, context: AuthorizationContext) -> None:
        if self._evidence is None:
            raise InvalidCandidateError("evidence", "Memory evidence resolution is unavailable")
        self._evidence.authorize = authorize
        self._authorization_context = context

    async def _authorize_decision(self, action: str, candidate: Candidate[Any]) -> None:
        if self.authorize_action is not None:
            await self.authorize_action(self._scope_id, action, candidate)

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[AsyncConnection]:
        async with self._authorization_context(), self._database.connection(self._bound_connection) as connection:
            yield connection

    async def propose_experience(
        self,
        proposal: ExperienceContent,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef | None,
        reason: str | None,
        candidate_id: str | None = None,
        on_commit: CandidateCommitHook | None = None,
    ) -> Candidate[ExperienceContent]:
        """Persist one Experience proposal from an ordinary caller.

        Shared provenance never travels through public arguments: a shared proposal is
        admitted only by :meth:`admit_dream_shared_experience`, which derives the audit and
        the foreign support from a verified run, so no caller can assert either directly.
        """

        candidate = await self._propose(
            Experience.family,
            proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
            candidate_id=candidate_id,
            on_commit=on_commit,
        )
        return _experience_candidate(candidate)

    async def propose_experience_batch(
        self,
        plans: Sequence[ExperienceCandidateInput],
        /,
        *,
        artifacts: tuple[ArtifactRef, ...] = (),
    ) -> tuple[Candidate[ExperienceContent], ...]:
        """Validate one generated window, then publish its independent proposals together."""

        if not plans:
            return ()
        candidates = []
        async with self._connection() as connection:
            for plan in plans:
                candidates.append(
                    await self._prepare_proposal(
                        connection,
                        Experience.family,
                        plan.proposal,
                        sources=plan.sources,
                        artifacts=artifacts,
                        target=None,
                        reason=plan.reason,
                    )
                )
            stored = await self._candidates.create_many(connection, self._scope_id, candidates)
        return tuple(_experience_candidate(_reviewed_candidate(candidate)) for candidate in stored)

    def with_sharing(self, *, access, scopes, principal) -> ReviewService:
        """Return this service bound to the owners a shared approval must re-prove against.

        Kept explicit so a runtime without sharing composition keeps refusing shared
        approvals instead of silently publishing without verification.
        """

        self._share_access = access
        self._share_scopes = scopes
        self._share_principal = principal
        return self

    async def admit_dream_shared_experience(
        self,
        proposal: ExperienceContent,
        /,
        *,
        adoption,
        reason: str | None,
        candidate_id: str,
        on_commit: CandidateCommitHook | None = None,
    ) -> Candidate[ExperienceContent]:
        """Admit one pinned shared Experience proposal through the single trusted handoff.

        ``adoption`` is the server-owned binding Dream derived from its own durable run,
        manifest and citations. This method takes no run id, pin, adopted set or policy
        digest separately, so a caller cannot assert provenance it did not observe, and an
        ordinary :meth:`propose_experience` call cannot claim to be shared.
        """

        from powercontext.builtin.dream.provenance import shared_candidate_audit

        if not adoption.snapshot_id or not adoption.run_id:
            raise InvalidCandidateError("audit", "shared adoption is incomplete")
        if not adoption.direct:
            # An adoption with no directly cited support cannot be recorded: the audit would
            # claim support the model never named.
            raise InvalidCandidateError("audit", "shared adoption has no direct support")
        foreign = tuple(origin for origin in adoption.adopted if origin.scope_id != self._scope_id)
        audit = shared_candidate_audit(adoption, proposal)
        candidate = await self._propose(
            Experience.family,
            proposal,
            sources=adoption.local_sources,
            artifacts=adoption.local_artifacts,
            target=adoption.target,
            reason=reason,
            candidate_id=candidate_id,
            on_commit=on_commit,
            audit=audit,
            foreign=foreign,
        )
        return _experience_candidate(candidate)

    async def admit_dream_shared_skill(
        self,
        proposal: SkillContent,
        /,
        *,
        adoption,
        reason: str | None,
        candidate_id: str,
        on_commit: CandidateCommitHook | None = None,
    ) -> Candidate[SkillContent]:
        """Admit one pinned shared Skill proposal through the same trusted handoff.

        A derived Skill is supported by Experience, and that Experience may be owned by
        another Scope. The adopted foreign Experience is presented as the Skill's lineage
        support while being verified at its own origin by :meth:`_validate_evidence`, so the
        foreign reference never occupies a local slot and the local evidence rules are not
        loosened.
        """

        from powercontext.builtin.dream.provenance import shared_candidate_audit

        if not adoption.snapshot_id or not adoption.run_id:
            raise InvalidCandidateError("audit", "shared adoption is incomplete")
        if not adoption.direct:
            raise InvalidCandidateError("audit", "shared adoption has no direct support")
        if adoption.target is not None:
            # A shared Skill revision needs target CAS under the pin, which is not proven.
            raise InvalidCandidateError("approval", "shared_use_revision_unavailable")
        foreign = tuple(origin for origin in adoption.adopted if origin.scope_id != self._scope_id)
        # The foreign Experience is the Skill's real support, but it must not enter the
        # Family's local lineage slots: those carry a same-Scope FK. It travels qualified,
        # is verified at its own Scope, and is recorded in the target-owned origin rows.
        artifacts = _unique_artifacts(adoption.local_artifacts)
        audit = shared_candidate_audit(adoption, proposal)
        candidate = await self._propose(
            Skill.family,
            proposal,
            sources=adoption.local_sources,
            artifacts=artifacts,
            target=None,
            reason=reason,
            candidate_id=candidate_id,
            on_commit=on_commit,
            audit=audit,
            foreign=foreign,
        )
        return _skill_candidate(candidate)

    async def propose_skill(
        self,
        proposal: SkillContent,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef | None,
        reason: str | None,
        candidate_id: str | None = None,
        on_commit: CandidateCommitHook | None = None,
    ) -> Candidate[SkillContent]:
        """Persist a human or integration supplied managed Skill proposal."""

        async with self._connection() as connection:
            proposal = await self._canonical_skill_proposal(connection, proposal)
            if target is not None and candidate_id is not None and candidate_id.startswith("cand_dream_"):
                await self._validate_skill_revision(connection, target, proposal)
            candidate = await self._propose_with_connection(
                connection,
                Skill.family,
                proposal,
                sources=sources,
                artifacts=artifacts,
                target=target,
                reason=reason,
                candidate_id=candidate_id,
                on_commit=on_commit,
            )
        return _skill_candidate(candidate)

    async def propose_profile_dream(
        self,
        proposal: ProfileCandidateProposal,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef,
        reason: str,
        candidate_id: str,
    ) -> Candidate[ProfileCandidateProposal]:
        if proposal.dream_run_id is None or proposal.source_window is not None:
            raise InvalidCandidateError("proposal", "a Dream Profile Candidate requires a Dream run")
        candidate = await self._propose(
            "profile",
            proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
            candidate_id=candidate_id,
        )
        return Candidate[ProfileCandidateProposal].model_validate(candidate.model_dump(mode="python"))

    async def propose_memory_dream(
        self,
        proposal: AtomicMemoryContent,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef,
        reason: str,
        candidate_id: str,
    ) -> Candidate[AtomicMemoryContent]:
        if target.family != AtomicMemory.family or proposal.creation is not None:
            raise InvalidCandidateError("target", "Memory Dream requires one exact Atomic Memory target")
        candidate = await self._propose(
            AtomicMemory.family,
            proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
            candidate_id=candidate_id,
        )
        return Candidate[AtomicMemoryContent].model_validate(candidate.model_dump(mode="python"))

    async def propose_topic_memory_dream(
        self,
        proposal: TopicMemoryContent,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef,
        reason: str,
        candidate_id: str,
    ) -> Candidate[TopicMemoryContent]:
        candidate = await self._propose(
            TopicMemory.family,
            proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
            candidate_id=candidate_id,
        )
        return Candidate[TopicMemoryContent].model_validate(candidate.model_dump(mode="python"))

    async def propose_handoff_dream(
        self,
        proposal: HandoffContent,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef,
        reason: str,
        candidate_id: str,
    ) -> Candidate[HandoffContent]:
        _validate_handoff_citations(proposal, sources, artifacts, target)
        candidate = await self._propose(
            Handoff.family,
            proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
            candidate_id=candidate_id,
        )
        return Candidate[HandoffContent].model_validate(candidate.model_dump(mode="python"))

    async def _propose(
        self,
        family: str,
        proposal: ReviewedProposal,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef | None,
        reason: str | None,
        candidate_id: str | None = None,
        on_commit: CandidateCommitHook | None = None,
        audit: CandidateAudit | None = None,
        foreign: tuple[QualifiedOrigin, ...] = (),
    ) -> ReviewedCandidate:
        canonical_sources = _unique_sources(sources)
        canonical_artifacts = _unique_artifacts(artifacts)
        _validate_reason(reason)
        async with self._connection() as connection:
            candidate = await self._propose_with_connection(
                connection,
                family,
                proposal,
                sources=canonical_sources,
                artifacts=canonical_artifacts,
                target=target,
                reason=reason,
                candidate_id=candidate_id,
                on_commit=on_commit,
                audit=audit,
                foreign=foreign,
            )
        return _reviewed_candidate(candidate)

    async def _propose_with_connection(
        self,
        connection: AsyncConnection,
        family: str,
        proposal: ReviewedProposal,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef | None,
        reason: str | None,
        candidate_id: str | None = None,
        on_commit: CandidateCommitHook | None = None,
        audit: CandidateAudit | None = None,
        foreign: tuple[QualifiedOrigin, ...] = (),
    ) -> ReviewedCandidate:
        candidate = await self._prepare_proposal(
            connection,
            family,
            proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
            candidate_id=candidate_id,
            audit=audit,
            foreign=foreign,
        )
        candidate = (await self._candidates.create_many(connection, self._scope_id, (candidate,)))[0]
        if on_commit is not None:
            await on_commit(connection, candidate)
        return _reviewed_candidate(candidate)

    async def _prepare_proposal(
        self,
        connection: AsyncConnection,
        family: str,
        proposal: ReviewedProposal,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef | None,
        reason: str | None,
        candidate_id: str | None = None,
        audit: CandidateAudit | None = None,
        foreign: tuple[QualifiedOrigin, ...] = (),
    ) -> Candidate[Any]:
        _validate_reason(reason)
        artifacts = _unique_artifacts(artifacts)
        sources = await self._validate_evidence(
            connection,
            _unique_sources(sources),
            artifacts,
            proposal=proposal,
            target=target,
            dream_skill_target=candidate_id is not None and candidate_id.startswith("cand_dream_"),
            foreign=foreign,
        )
        await self._validate_handoff_objective(connection, proposal, target)
        await self._validate_target(connection, family, target, artifacts)
        return Candidate(
            candidate_id=candidate_id or self._id_factory("candidate"),
            version=1,
            family=family,
            status=CandidateStatus.PENDING,
            proposal=proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
            audit=audit,
        )

    async def candidate_page(self, request, *, metadata_only=False, connection=None):
        async with self._database.connection(
            connection if connection is not None else self._bound_connection
        ) as connection:
            items, cursor = await self._candidates.page(
                connection,
                self._scope_id,
                status=request.status,
                family=request.family,
                candidate_kind=request.candidate_kind,
                cursor=request.cursor,
                limit=request.limit,
                metadata_only=metadata_only,
            )
        return tuple(_reviewed_candidate(item) if isinstance(item, Candidate) else item for item in items), cursor

    async def history(self, candidate_id):
        from sqlalchemy import select

        from powercontext.builtin.persistence.tables import CANDIDATE_VERSIONS_TABLE as versions

        async with self._connection() as connection:
            await self._candidates.get(connection, self._scope_id, candidate_id)
            rows = (
                await connection.execute(
                    select(versions)
                    .where(versions.c.scope_id == self._scope_id, versions.c.candidate_id == candidate_id)
                    .order_by(versions.c.version)
                )
            ).mappings()
            return tuple(
                self._candidates.decode_candidate({
                    **row,
                    "status": "pending",
                    "result_family": None,
                    "result_artifact_id": None,
                    "result_revision": None,
                    "decision_reason": None,
                })
                for row in rows
            )

    async def get_candidate(self, candidate_id: str, /, *, current: bool = False, connection=None) -> ReviewedCandidate:
        async with self._database.connection(
            connection if connection is not None else self._bound_connection
        ) as connection:
            candidate = await self._candidates.get(connection, self._scope_id, candidate_id, current=current)
        return _reviewed_candidate(candidate)

    async def history_summary(self, candidate_id, *, connection=None):
        from sqlalchemy import select

        from powercontext.builtin.persistence.tables import CANDIDATE_VERSIONS_TABLE as versions

        async with self._database.connection(
            connection if connection is not None else self._bound_connection
        ) as connection:
            rows = (
                await connection.execute(
                    select(versions.c.version, versions.c.reason)
                    .where(versions.c.scope_id == self._scope_id, versions.c.candidate_id == candidate_id)
                    .order_by(versions.c.version)
                )
            ).mappings()
            return tuple({**row, "status": "pending", "decision_reason": None} for row in rows)

    async def inspect_evidence(self, candidate_id: str, expected_version: int) -> CandidateEvidenceView:
        async with self._connection() as connection:
            candidate = await self._candidates.get(connection, self._scope_id, candidate_id)
            if candidate.version != expected_version:
                raise CandidateConflictError(candidate_id, expected_version, candidate.version)
            if self._evidence is None:
                return CandidateEvidenceView(
                    candidate_id=candidate_id,
                    version=candidate.version,
                    unavailable="capability_unavailable",
                )
            try:
                resolved = await self._evidence.resolve(
                    connection,
                    sources=candidate.sources,
                    artifacts=candidate.artifacts,
                )
            except EvidenceResolutionError as error:
                return CandidateEvidenceView(
                    candidate_id=candidate_id, version=candidate.version, unavailable=error.code
                )
            return CandidateEvidenceView(candidate_id=candidate_id, version=candidate.version, resolved=resolved)

    async def list_candidates(
        self,
        /,
        *,
        status: CandidateStatus,
        family: str | None,
        cursor: str | None,
        limit: int,
    ) -> CandidatePage[ReviewedProposal]:
        async with self._connection() as connection:
            page = await self._candidates.list(
                connection,
                self._scope_id,
                status=status,
                family=family,
                cursor=cursor,
                limit=limit,
            )
        return CandidatePage(
            candidates=tuple(_reviewed_candidate(candidate) for candidate in page.candidates),
            next_cursor=page.next_cursor,
        )

    async def revise(  # noqa: C901 - Family-specific validation shares one versioned transaction
        self,
        candidate_id: str,
        expected_version: int,
        proposal: ReviewedProposal | ProfileWriteContent,
        /,
        *,
        sources: tuple[SourceRef, ...] | None,
        artifacts: tuple[ArtifactRef, ...] | None,
        target: ArtifactRef | None,
        reason: str | None,
    ) -> ReviewedCandidate:
        _validate_reason(reason)
        async with self._connection() as connection:
            preview = await self._candidates.get(connection, self._scope_id, candidate_id)
            canonical_sources = preview.sources if sources is None else _unique_sources(sources)
            canonical_artifacts = preview.artifacts if artifacts is None else _unique_artifacts(artifacts)
            if preview.version != expected_version:
                raise CandidateConflictError(candidate_id, expected_version, preview.version)
            if preview.target != target:
                raise InvalidCandidateError("target", "cannot change across Candidate versions")
            if preview.family == "profile":
                await ProfilePolicyRepository().get(connection, self._scope_id, for_update=True)
            if preview.family in {"atomic-memory", "topic-memory", "handoff", "prompt"} and preview.target is not None:
                await self._artifacts.latest(
                    connection, self._scope_id, preview.family, preview.target.artifact_id, for_update=True
                )
            if preview.family in {Experience.family, Skill.family}:
                await self._lock_generic_review_inputs(
                    connection,
                    preview,
                    sources=canonical_sources,
                    artifacts=canonical_artifacts,
                )
            current = await self._candidates.lock_pending(
                connection,
                self._scope_id,
                candidate_id,
                expected_version,
            )
            if current != preview:
                raise CandidateConflictError(candidate_id, expected_version, current.version)
            reviewed = _reviewed_candidate(current)
            if reviewed.family == "profile":
                proposal = revise_profile(current, proposal, canonical_sources, canonical_artifacts, target)
            _validate_proposal_family(reviewed.family, proposal)
            if isinstance(proposal, SkillContent):
                proposal = await self._canonical_skill_proposal(connection, proposal)
                if current.target is not None and current.candidate_id.startswith("cand_dream_"):
                    await self._validate_skill_revision(connection, current.target, proposal)
            if isinstance(proposal, PromptContent):
                await self._validate_prompt_proposal(connection, current.target, proposal)
            if target != current.target:
                raise InvalidCandidateError("target", "cannot change across Candidate versions")
            if isinstance(proposal, AtomicMemoryContent) and proposal.creation is not None:
                raise InvalidCandidateError("proposal", "Memory Dream cannot alter creation metadata")
            if isinstance(proposal, HandoffContent):
                _validate_handoff_citations(proposal, canonical_sources, canonical_artifacts, target)
            canonical_sources = await self._validate_evidence(
                connection,
                canonical_sources,
                canonical_artifacts,
                proposal=proposal if isinstance(proposal, (ExperienceContent, SkillContent)) else None,
                target=current.target,
                dream_skill_target=current.candidate_id.startswith("cand_dream_"),
            )
            await self._validate_handoff_objective(connection, proposal, current.target)
            if reviewed.family != "profile":
                await self._validate_target(connection, reviewed.family, target, canonical_artifacts)
            await self._authorize_decision("revise", current.model_copy(update={"proposal": proposal}))
            revised = await self._candidates.revise(
                connection,
                self._scope_id,
                candidate_id,
                expected_version,
                proposal,
                sources=canonical_sources,
                artifacts=canonical_artifacts,
                target=target,
                reason=reason,
            )
        return _reviewed_candidate(revised)

    async def reject(
        self,
        candidate_id: str,
        expected_version: int,
        reason: str,
        /,
    ) -> ReviewedCandidate:
        _validate_reason(reason)
        async with self._connection() as connection:
            current = await self._candidates.get(connection, self._scope_id, candidate_id)
            if current.family == "profile":
                return _reviewed_candidate(
                    await decide_profile(
                        self,
                        connection,
                        candidate_id,
                        expected_version,
                        reason=reason,
                    )
                )
            current = await self._candidates.lock_pending(connection, self._scope_id, candidate_id, expected_version)
            await self._authorize_decision("reject", current)
            rejected = await self._candidates.reject(
                connection,
                self._scope_id,
                candidate_id,
                expected_version,
                reason,
            )
        return _reviewed_candidate(rejected)

    async def approve(self, candidate_id: str, expected_version: int, /) -> ReviewedCandidate:
        """Publish once and return the stored result for overlapping approval retries."""

        try:
            return await self._approve(candidate_id, expected_version)
        except (ArtifactTargetConflictError, CandidateTerminalError, RevisionConflictError):
            # Another instance can publish after our pending read. Leave the failed
            # transaction before reading the terminal decision; never
            # invert the target-before-Candidate order in the pending write path.
            async with self._connection() as connection:
                current = await self._candidates.get(connection, self._scope_id, candidate_id, current=True)
                await self._authorize_decision("approve", current)
                if current.status is CandidateStatus.REJECTED:
                    raise CandidateTerminalError(candidate_id, current.status) from None
                if current.status is CandidateStatus.APPROVED and current.version == expected_version:
                    return _reviewed_candidate(current)
            raise

    async def _approve(  # noqa: C901 - each Family keeps its own transactional publication boundary
        self,
        candidate_id: str,
        expected_version: int,
        /,
    ) -> ReviewedCandidate:
        """Atomically commit the reviewed Artifact and Candidate result."""

        async with self._connection() as connection:
            current = await self._candidates.get(connection, self._scope_id, candidate_id)
        await self._authorize_decision("approve", current)
        if current.status is CandidateStatus.REJECTED:
            raise CandidateTerminalError(candidate_id, current.status)
        if current.status is CandidateStatus.APPROVED:
            if current.version != expected_version:
                raise CandidateConflictError(candidate_id, expected_version, current.version)
            return _reviewed_candidate(current)
        if current.family == AtomicMemory.family:
            return await self._approve_memory_dream(candidate_id, expected_version)
        if current.family == TopicMemory.family:
            return await self._approve_topic_memory_dream(candidate_id, expected_version)
        if current.family == Handoff.family:
            return await self._approve_handoff_dream(candidate_id, expected_version)
        if current.family == Prompt.family:
            return await self._approve_prompt_dream(candidate_id, expected_version)
        async with self._connection() as connection:
            current = await self._candidates.get(connection, self._scope_id, candidate_id)
            if current.status is CandidateStatus.REJECTED:
                raise CandidateTerminalError(candidate_id, current.status)
            if current.family == "profile":
                return _reviewed_candidate(await decide_profile(self, connection, candidate_id, expected_version))
            if current.version != expected_version:
                raise CandidateConflictError(candidate_id, expected_version, current.version)
            _validate_approval_lineage(_reviewed_candidate(current))
            await self._lock_generic_review_inputs(
                connection,
                current,
                sources=current.sources,
                artifacts=current.artifacts,
                foreign=()
                if current.audit is None or current.audit.share_snapshot_id is None
                else current.audit.adopted_origins,
            )
            candidate = _reviewed_candidate(
                await self._candidates.lock_pending(
                    connection,
                    self._scope_id,
                    candidate_id,
                    expected_version,
                )
            )
            if candidate != current:
                raise CandidateConflictError(candidate_id, expected_version, candidate.version)
            await self._authorize_decision("approve", candidate)
            await self._validate_candidate_audit(connection, candidate)
            shared_origins = await self._verify_shared_binding(connection, candidate)
            if candidate.family == Skill.family and candidate.candidate_id.startswith("cand_dream_"):
                run_id = candidate.candidate_id.removeprefix("cand_dream_")
                run = await DreamRepository().get(connection, self._scope_id, run_id)
                if (
                    run.run.operation not in {"derive_skill", "revise_skill"}
                    or run.run.candidate is None
                    or run.run.candidate.candidate_id != candidate.candidate_id
                ):
                    raise InvalidCandidateError("origin", "Dream Skill origin is invalid")
                if run.run.operation == "revise_skill":
                    if candidate.target is None or not isinstance(candidate.proposal, SkillContent):
                        raise InvalidCandidateError("target", "Dream Skill revision requires its original target")
                    await self._validate_skill_revision(connection, candidate.target, candidate.proposal)
            _validate_approval_lineage(candidate)
            await self._validate_evidence(
                connection,
                candidate.sources,
                candidate.artifacts,
                proposal=candidate.proposal if isinstance(candidate.proposal, ExperienceContent) else None,
                target=candidate.target,
                dream_skill_target=candidate.candidate_id.startswith("cand_dream_"),
                # A shared proposal's adopted support is the evidence of record; it was
                # verified in its owning Scope by _verify_shared_binding above.
                foreign=()
                if shared_origins is None
                else tuple(origin for origin in shared_origins if origin.scope_id != self._scope_id),
            )
            if isinstance(candidate.proposal, SkillContent) and candidate.proposal.package is not None:
                await self._canonical_skill_proposal(connection, candidate.proposal)
            draft = _candidate_draft(candidate)
            if candidate.target is None:
                artifact = await self._artifacts.create(
                    connection,
                    self._scope_id,
                    self._id_factory(candidate.family),
                    draft,
                )
            else:
                try:
                    target = await self._artifacts.get(connection, self._scope_id, candidate.target)
                    artifact = await self._artifacts.revise(connection, self._scope_id, target, draft)
                except RevisionConflictError as error:
                    current = error.current
                    if not isinstance(current, Artifact) or current.family != candidate.family:
                        raise
                    raise ArtifactTargetConflictError(candidate.target, current.as_ref()) from error
            if isinstance(artifact, Experience):
                await self._experience_index.replace(connection, self._scope_id, artifact)
            elif isinstance(artifact, Skill) and artifact.content.package is not None:
                package = await self._skill_packages.get(connection, self._scope_id, artifact.content.package)
                await self._experience_index.replace_skill(connection, self._scope_id, artifact, package)
            if shared_origins is not None:
                # Target-owned exact origins, written in this same transaction as the
                # official revision and the Candidate decision. No post-commit writer.
                await self._write_shared_origins(connection, artifact.as_ref(), shared_origins)
            approved = await self._candidates.mark_approved(
                connection,
                self._scope_id,
                candidate_id,
                expected_version,
                artifact.as_ref(),
            )
        return _reviewed_candidate(approved)

    async def require_prompt_target(self, target: ArtifactRef, /) -> dict[str, object]:
        """Expose schemas as task data, never the target's text as Dream instructions."""

        async with self._connection() as connection:
            await self._prompt_target(connection, target)
        definition = cast(PromptRegistry, self._prompt_registry).require_customization(target.artifact_id)
        return {
            "key": definition.key,
            "definition_version": definition.definition_version,
            "input_schema": definition.input_type.model_json_schema(),
            "output_schema": definition.output_type.model_json_schema(),
        }

    async def _lock_generic_review_inputs(
        self,
        connection: AsyncConnection,
        preview: Candidate[Any],
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        foreign: tuple[QualifiedOrigin, ...] = (),
    ) -> None:
        """Match Dream reuse: target head, ordered evidence Memory heads, then Candidate."""

        if preview.target is not None:
            current = await self._artifacts.latest(
                connection, self._scope_id, preview.family, preview.target.artifact_id, for_update=True
            )
            if current.as_ref() != preview.target:
                raise ArtifactTargetConflictError(preview.target, current.as_ref())
        await self._validate_evidence(
            connection,
            sources,
            artifacts,
            target=preview.target,
            dream_skill_target=preview.candidate_id.startswith("cand_dream_"),
            foreign=foreign,
        )

    async def _verify_shared_binding(  # noqa: C901 - one verified binding recheck
        self, connection: AsyncConnection, candidate: Candidate[Any], /
    ) -> tuple[QualifiedOrigin, ...] | None:
        """Recheck a shared Candidate's exact pin, run and adopted support before commit.

        Everything is read under the Candidate lock in this transaction: the durable run, the
        pin bound to it, the Current agreement, the acting principal's actual origin
        authority, and every adopted origin's digest at the Scope that owns it. Any drift
        refuses the approval instead of publishing under a binding that no longer holds.
        """

        audit = candidate.audit
        if audit is None or audit.share_snapshot_id is None:
            return None
        from powercontext.builtin.dream.models import DreamError
        from powercontext.builtin.persistence.share_agreement import ShareAgreementRepository
        from powercontext.builtin.sharing.eligibility import require_bound_eligibility

        record = await DreamRepository().get(connection, self._scope_id, audit.dream_run_id)
        if record.share_snapshot_id != audit.share_snapshot_id:
            raise InvalidCandidateError("audit", "shared Candidate pin does not match its run")
        agreements = ShareAgreementRepository()
        row = await agreements.snapshot_row(connection, self._scope_id, audit.share_snapshot_id, for_update=True)
        if row is None:
            raise InvalidCandidateError("audit", "shared pin is missing")
        snapshot = agreements.decode_snapshot(row)
        if snapshot.dream_run_id != record.run.run_id:
            raise InvalidCandidateError("audit", "shared pin is bound to another run")
        if self._share_access is None or self._share_scopes is None or self._share_principal is None:
            raise InvalidCandidateError("approval", "shared_use_approval_unavailable")
        principal = self._share_principal(record.principal_id)
        if principal is None:
            raise InvalidCandidateError("approval", "shared_use_approval_unavailable")
        try:
            await require_bound_eligibility(
                connection=connection,
                access=self._share_access.with_connection(connection),
                scopes=self._share_scopes,
                agreements=agreements,
                principal=principal,
                audit=AccessAuditContext(transport="background", operation="review.share_approval"),
                scope_id=self._scope_id,
                operation=record.run.operation,
                snapshot_id=audit.share_snapshot_id,
                run_id=record.run.run_id,
            )
        except DreamError as error:
            raise InvalidCandidateError("approval", error.code) from error
        # The audit's adopted set must be exactly what the durable run declared: reconstructed
        # from its persisted manifest, not from a bare reference list that cannot name a
        # foreign identity. A direct input must also be one the pin declared, with its
        # recorded digest. Reachable support needs current reads, not pin membership.
        if record.run.target is not None:
            # A shared revision would need a proven stale-target conflict under the pin's
            # binding. That is not implemented, so refuse rather than let an unverified
            # revise path publish.
            raise InvalidCandidateError("approval", "shared_use_revision_unavailable")
        manifest = record.run.input_manifest
        if manifest is None:
            raise InvalidCandidateError("audit", "shared run has no persisted input manifest")
        if not record.share_citations:
            raise InvalidCandidateError("audit", "shared run has no persisted citations")
        adopted = await self._regenerate_adopted_support(connection, record, snapshot, audit)
        return adopted

    async def _regenerate_adopted_support(
        self, connection: AsyncConnection, record, snapshot, audit, /
    ) -> tuple[QualifiedOrigin, ...]:
        """Rerun the run's own selection and require it to equal the stored support.

        The stored citations and the durable manifest are the only inputs: selection is
        reproduced exactly as generation performed it, with the pin's qualified identities
        as the direct inputs. The regenerated set must match the audit's adopted support on
        every qualified reference *and* digest, so an origin that was never selected — even
        with unchanged bytes and a still-current target — cannot be passed off as support.
        Reachable foreign dependencies are then authorized for the acting principal before
        their bytes are read.
        """

        from powercontext.builtin.dream.provenance import unique_origins
        from powercontext.builtin.evidence.selection import qualified_direct_origins, select_evidence

        manifest = record.run.input_manifest
        assert manifest is not None  # noqa: S101 - checked by the caller
        if record.request is None:
            raise InvalidCandidateError("audit", "shared run has no executable request")
        regenerated = select_evidence(
            manifest,
            record.share_citations,
            skill=record.run.operation == "derive_skill",
            target=record.request.target,
            scope_id=self._scope_id,
        )
        expected = unique_origins(regenerated.adopted)
        if not expected:
            raise InvalidCandidateError("audit", "shared run selected no adoptable support")
        stored = unique_origins(audit.adopted_origins)
        if _origin_keys(stored) != _origin_keys(expected):
            # Identity mismatch, not a digest failure: the audit names support the run never
            # selected, or omits support it did.
            raise InvalidCandidateError("audit", "adopted support is not the selected support")
        declared = {(item.origin_scope_id, item.digest, item.reference.model_dump_json()) for item in snapshot.inputs}
        direct = {
            (origin.scope_id, origin.reference.model_dump_json())
            for origin in qualified_direct_origins(manifest, record.share_citations, scope_id=self._scope_id)
        }
        for origin in expected:
            if (origin.scope_id, origin.reference.model_dump_json()) in direct and (
                origin.scope_id,
                origin.digest,
                origin.reference.model_dump_json(),
            ) not in declared:
                # A directly cited input must be the material the pin fixed.
                raise InvalidCandidateError("audit", "direct support is not the pinned material")
        await self._authorize_reachable(connection, record, expected, direct)
        if self._evidence is None:
            raise InvalidCandidateError("approval", "shared_use_approval_unavailable")
        try:
            await self._evidence.validate_qualified(connection, expected)
        except EvidenceResolutionError as error:
            raise InvalidCandidateError("approval", "adopted support moved") from error
        return expected

    async def _authorize_reachable(self, connection, record, origins, direct, /) -> None:
        """Authorize actual reachable foreign dependencies before reading their bytes."""

        from powercontext.builtin.sharing.eligibility import require_origin_reference

        if self._share_access is None or self._share_principal is None:
            raise InvalidCandidateError("approval", "shared_use_approval_unavailable")
        principal = self._share_principal(record.principal_id)
        if principal is None:
            raise InvalidCandidateError("approval", "shared_use_approval_unavailable")
        access = self._share_access.with_connection(connection)
        audit_context = AccessAuditContext(transport="background", operation="review.share_reachable")
        for origin in origins:
            reference = origin.reference
            if origin.scope_id == self._scope_id or (origin.scope_id, reference.model_dump_json()) in direct:
                continue
            try:
                await require_origin_reference(
                    access=access,
                    principal=principal,
                    scope_id=origin.scope_id,
                    ref=reference,
                    audit=audit_context,
                )
            except Exception as error:
                raise InvalidCandidateError("approval", "reachable support is not authorized") from error

    async def _write_shared_origins(
        self,
        connection: AsyncConnection,
        result: ArtifactRef,
        origins: tuple[QualifiedOrigin, ...],
        /,
    ) -> None:
        """Record the target-owned exact origins of one committed revision.

        Written inside the approval transaction with the official revision and the Candidate
        decision. The rows are target-owned with no FK to the origin, so later origin
        retention cannot erase or block the target's own history.
        """

        from sqlalchemy import delete, insert

        from powercontext.builtin.persistence.tables import ARTIFACT_LINEAGE_ORIGINS_TABLE as ORIGINS

        await connection.execute(
            delete(ORIGINS).where(
                ORIGINS.c.scope_id == result.scope_id
                if hasattr(result, "scope_id")
                else ORIGINS.c.scope_id == self._scope_id,
                ORIGINS.c.family == result.family,
                ORIGINS.c.artifact_id == result.artifact_id,
                ORIGINS.c.revision == result.revision,
            )
        )
        rows = []
        for ordinal, origin in enumerate(origins):
            reference = origin.reference
            is_source = isinstance(reference, SourceRef)
            rows.append({
                "scope_id": self._scope_id,
                "family": result.family,
                "artifact_id": result.artifact_id,
                "revision": result.revision,
                "ordinal": ordinal,
                "origin_scope_id": origin.scope_id,
                "origin_kind": "source" if is_source else "artifact",
                "origin_family": "source" if is_source else reference.family,
                "origin_id": reference.source_id if is_source else reference.artifact_id,
                "origin_revision": None if is_source else reference.revision,
                "locator": None
                if not is_source or reference.locator is None
                else reference.locator.model_dump(mode="json"),
                "digest": origin.digest,
            })
        if rows:
            await connection.execute(insert(ORIGINS), rows)

    async def _validate_candidate_audit(self, connection: AsyncConnection, candidate: Candidate[Any]) -> None:
        """Recheck persisted generation policy and content after acquiring the Candidate lock."""

        from powercontext.builtin.dream.bindings import DREAM_SPECS
        from powercontext.builtin.dream.provenance import validation_policy_digest
        from powercontext.builtin.persistence.candidates import proposal_digest

        audit = candidate.audit
        if audit is None:
            return
        spec = DREAM_SPECS.get(audit.operation)
        if (
            spec is None
            or spec.family != candidate.family
            or spec.spec_version != audit.spec_version
            or audit.proposal_digest != proposal_digest(candidate.proposal)
        ):
            raise InvalidCandidateError("audit", "Dream Candidate content or operation contract changed")
        record = await DreamRepository().get(connection, self._scope_id, audit.dream_run_id)
        if (
            record.run.operation != audit.operation
            or record.run.target != candidate.target
            or record.run.candidate is None
            or record.run.candidate.candidate_id != candidate.candidate_id
            or validation_policy_digest(record) != audit.validation_policy_digest
        ):
            raise InvalidCandidateError("audit", "Dream Candidate policy or origin changed")

    async def _prompt_target(self, connection: AsyncConnection, target: ArtifactRef | None) -> Prompt:
        if (
            target is None
            or target.family != Prompt.family
            or target.artifact_id not in PROMPT_KEYS
            or self._prompt_registry is None
        ):
            raise InvalidCandidateError("target", "expected an existing customizable operational Prompt")
        self._prompt_registry.require_customization(target.artifact_id)
        current = await self._artifacts.get(connection, self._scope_id, target)
        if not isinstance(current, Prompt):
            raise InvalidCandidateError("target", "expected an exact Prompt revision")
        return current

    async def _validate_prompt_proposal(
        self, connection: AsyncConnection, target: ArtifactRef | None, proposal: PromptContent
    ) -> Prompt:
        current = await self._prompt_target(connection, target)
        if proposal.mode != "custom":
            raise InvalidCandidateError("proposal", "Dream Prompt proposals must use custom mode")
        cast(PromptRegistry, self._prompt_registry).validate(current.artifact_id, proposal)
        return current

    async def propose_prompt_dream(
        self,
        proposal: PromptContent,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef,
        reason: str | None,
        candidate_id: str,
    ) -> Candidate[PromptContent]:
        _validate_reason(reason)
        async with self._connection() as connection:
            await self._validate_prompt_proposal(connection, target, proposal)
            candidate = await self._propose_with_connection(
                connection,
                Prompt.family,
                proposal,
                sources=_unique_sources(sources),
                artifacts=_unique_artifacts(artifacts),
                target=target,
                reason=reason,
                candidate_id=candidate_id,
            )
        return Candidate[PromptContent].model_validate(candidate.model_dump(mode="python"))

    async def _approve_prompt_dream(self, candidate_id: str, expected_version: int) -> ReviewedCandidate:
        async with self._connection() as connection:
            preview = await self._candidates.get(connection, self._scope_id, candidate_id)
            if preview.status is CandidateStatus.REJECTED:
                raise CandidateTerminalError(candidate_id, preview.status)
            if preview.target is None:
                raise InvalidCandidateError("target", "Dream Prompt requires an existing target")
            current = await self._artifacts.latest(
                connection, self._scope_id, Prompt.family, preview.target.artifact_id, for_update=True
            )
            if current.as_ref() != preview.target:
                raise ArtifactTargetConflictError(preview.target, current.as_ref())
            candidate = _reviewed_candidate(
                await self._candidates.lock_pending(connection, self._scope_id, candidate_id, expected_version)
            )
            await self._authorize_decision("approve", candidate)
            await self._validate_candidate_audit(connection, candidate)
            if candidate.target != preview.target or not isinstance(candidate.proposal, PromptContent):
                raise InvalidCandidateError("proposal", "Prompt candidate changed during approval")
            current = await self._validate_prompt_proposal(connection, candidate.target, candidate.proposal)
            await self._validate_evidence(connection, candidate.sources, candidate.artifacts, target=candidate.target)
            artifact = await PromptManagementWriter(
                self._artifacts, cast(PromptRegistry, self._prompt_registry)
            ).commit_reviewed(
                connection,
                self._scope_id,
                current,
                candidate.proposal,
                sources=candidate.sources,
                artifacts=candidate.artifacts,
            )
            approved = await self._candidates.mark_approved(
                connection, self._scope_id, candidate_id, expected_version, artifact.as_ref()
            )
        return _reviewed_candidate(approved)

    async def _approve_memory_dream(self, candidate_id: str, expected_version: int) -> ReviewedCandidate:
        if self._memory is None:
            raise InvalidCandidateError("family", "Memory Dream writer is unavailable")
        async with self._connection() as connection:
            preview = _reviewed_candidate(await self._candidates.get(connection, self._scope_id, candidate_id))
            if preview.status is CandidateStatus.REJECTED:
                raise CandidateTerminalError(candidate_id, preview.status)
            if preview.version != expected_version:
                raise CandidateConflictError(candidate_id, expected_version, preview.version)
            if not isinstance(preview.proposal, AtomicMemoryContent) or preview.target is None:
                raise InvalidCandidateError("family", "Memory Dream proposal required")
            await self._authorize_decision("approve", preview)
            if preview.status is CandidateStatus.APPROVED:
                return preview
            state = await self._memory.states.get(connection, self._scope_id, preview.target.artifact_id)
            if state.state is not AtomicMemoryStateValue.ACTIVE:
                raise EvidenceResolutionError("memory_entry_inactive")
            plan = await self._memory.inspect_change(
                connection,
                self._scope_id,
                preview.target.artifact_id,
                preview.proposal,
                None,
                expected_revision=preview.target.revision,
                expected_state_version=state.state_version,
                lineage=ArtifactLineage(sources=preview.sources, artifacts=preview.artifacts),
            )
        prepared = await self._memory.prepare_change(plan)
        async with self._connection() as connection:
            current = await self._artifacts.latest(
                connection, self._scope_id, AtomicMemory.family, preview.target.artifact_id, for_update=True
            )
            if current.as_ref() != preview.target:
                raise ArtifactTargetConflictError(preview.target, current.as_ref())
            candidate = _reviewed_candidate(
                await self._candidates.lock_pending(connection, self._scope_id, candidate_id, expected_version)
            )
            await self._authorize_decision("approve", candidate)
            await self._validate_candidate_audit(connection, candidate)
            if candidate != preview:
                raise CandidateConflictError(candidate_id, expected_version, candidate.version)
            await self._validate_evidence(connection, candidate.sources, candidate.artifacts, target=candidate.target)
            committed = await self._memory.commit(connection, prepared, None)
            if not committed.changed:
                raise InvalidCandidateError("proposal", "Memory Dream commit produced no Artifact")
            approved = await self._candidates.mark_approved(
                connection, self._scope_id, candidate_id, expected_version, committed.primary.ref
            )
        return _reviewed_candidate(approved)

    async def _approve_topic_memory_dream(self, candidate_id: str, expected_version: int) -> ReviewedCandidate:
        if self._topic_writer is None:
            raise InvalidCandidateError("family", "Topic Memory Dream writer is unavailable")
        async with self._connection() as connection:
            preview = _reviewed_candidate(await self._candidates.get(connection, self._scope_id, candidate_id))
        if preview.status is CandidateStatus.REJECTED:
            raise CandidateTerminalError(candidate_id, preview.status)
        if preview.version != expected_version:
            raise CandidateConflictError(candidate_id, expected_version, preview.version)
        if not isinstance(preview.proposal, TopicMemoryContent) or preview.target is None:
            raise InvalidCandidateError("family", "Topic Memory Dream proposal required")
        projection = await self._topic_writer.prepare(preview.proposal, usage_scope_id=self._scope_id)
        async with self._connection() as connection:
            current = await self._artifacts.latest(
                connection, self._scope_id, TopicMemory.family, preview.target.artifact_id, for_update=True
            )
            if current.as_ref() != preview.target:
                raise ArtifactTargetConflictError(preview.target, current.as_ref())
            candidate = _reviewed_candidate(
                await self._candidates.lock_pending(connection, self._scope_id, candidate_id, expected_version)
            )
            await self._authorize_decision("approve", candidate)
            await self._validate_candidate_audit(connection, candidate)
            if (
                candidate != preview
                or not isinstance(current, TopicMemory)
                or not isinstance(candidate.proposal, TopicMemoryContent)
            ):
                raise CandidateConflictError(candidate_id, expected_version, candidate.version)
            await self._validate_evidence(connection, candidate.sources, candidate.artifacts, target=candidate.target)
            published = await self._topic_writer.topics.publish_revision(
                connection,
                self._scope_id,
                current,
                TopicMemoryDraft(
                    content=candidate.proposal,
                    sources=candidate.sources,
                    artifacts=candidate.artifacts,
                ),
                projection,
            )
            approved = await self._candidates.mark_approved(
                connection, self._scope_id, candidate_id, expected_version, published.topic.as_ref()
            )
        return _reviewed_candidate(approved)

    async def _approve_handoff_dream(self, candidate_id: str, expected_version: int) -> ReviewedCandidate:
        if self._handoff_writer is None:
            raise InvalidCandidateError("family", "Handoff Dream writer is unavailable")
        async with self._connection() as connection:
            preview = _reviewed_candidate(await self._candidates.get(connection, self._scope_id, candidate_id))
            if preview.status is CandidateStatus.REJECTED:
                raise CandidateTerminalError(candidate_id, preview.status)
            if preview.target is None:
                raise InvalidCandidateError("target", "Handoff Dream requires an existing target")
            current = await self._artifacts.latest(
                connection, self._scope_id, Handoff.family, preview.target.artifact_id, for_update=True
            )
            if current.as_ref() != preview.target:
                raise ArtifactTargetConflictError(preview.target, current.as_ref())
            candidate = _reviewed_candidate(
                await self._candidates.lock_pending(connection, self._scope_id, candidate_id, expected_version)
            )
            await self._authorize_decision("approve", candidate)
            await self._validate_candidate_audit(connection, candidate)
            if not isinstance(current, Handoff) or not isinstance(candidate.proposal, HandoffContent):
                raise InvalidCandidateError("family", "Handoff Dream proposal required")
            _validate_handoff_citations(
                candidate.proposal,
                candidate.sources,
                candidate.artifacts,
                candidate.target,
            )
            await self._validate_evidence(connection, candidate.sources, candidate.artifacts, target=candidate.target)
            committed = await self._handoff_writer.commit_reviewed(
                connection, self._scope_id, current, candidate.proposal, candidate.sources
            )
            approved = await self._candidates.mark_approved(
                connection, self._scope_id, candidate_id, expected_version, committed.as_ref()
            )
        return _reviewed_candidate(approved)

    async def get_experience(self, ref: ArtifactRef, /) -> Experience:
        return cast(Experience, await self._get_artifact(ref, Experience))

    async def get_skill(self, ref: ArtifactRef, /) -> Skill:
        return cast(Skill, await self._get_artifact(ref, Skill))

    async def _get_artifact(
        self,
        ref: ArtifactRef,
        expected: type[ReviewedArtifact],
        /,
    ) -> ReviewedArtifact:
        if ref.family != expected.family:
            raise ArtifactNotFoundError(ref)
        async with self._connection() as connection:
            try:
                artifact = await self._artifacts.get(connection, self._scope_id, ref)
            except RepositoryNotFoundError:
                raise ArtifactNotFoundError(ref) from None
        if type(artifact) is not expected:
            raise ArtifactNotFoundError(ref)
        return cast(ReviewedArtifact, artifact)

    async def _validate_evidence(
        self,
        connection: AsyncConnection,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        *,
        proposal: ReviewedProposal | None = None,
        target: ArtifactRef | None = None,
        dream_skill_target: bool = False,
        foreign: tuple[QualifiedOrigin, ...] = (),
    ) -> tuple[SourceRef, ...]:
        supporting_artifacts = tuple(
            ref
            for ref in artifacts
            if ref != target
            or ref.family
            not in (
                {"profile", "atomic-memory", "topic-memory", "handoff", "prompt"}
                | ({"skill"} if dream_skill_target else set())
            )
        )
        if not sources and not any(artifact.family != "prompt" for artifact in supporting_artifacts) and not foreign:
            # Trusted foreign support counts as an exact reference; it was validated in its
            # owning Scope below, so a foreign-only proposal is not weaker than a local one.
            raise InvalidCandidateError("evidence", "at least one exact reference is required")
        if len(sources) + len(artifacts) > MAX_CANDIDATE_EVIDENCE:
            raise InvalidCandidateError("evidence", f"must not exceed {MAX_CANDIDATE_EVIDENCE} exact references")
        # Support owned by another Scope is never looked up here: it is verified at its own
        # origin below, so an equal local decoy can neither satisfy nor mask it.
        foreign_keys = {origin.reference.model_dump_json() for origin in foreign}
        local_artifacts = tuple(ref for ref in artifacts if ref.model_dump_json() not in foreign_keys)
        try:
            source_rows = await self._sources.require_for_generation(connection, self._scope_id, sources)
            # Existence check only: the loaded revisions are intentionally unused.
            await self._artifacts.get_many(connection, self._scope_id, local_artifacts)
        except RepositoryNotFoundError as error:
            raise InvalidCandidateError("evidence", "reference is not available in this scope") from error
        await self._validate_foreign(connection, foreign)
        if self._evidence is not None:
            roots = await self._evidence.validate(
                connection,
                sources=sources,
                artifacts=tuple(ref for ref in supporting_artifacts if ref in local_artifacts),
            )
            sources = _unique_sources((*sources, *roots))
        if len(sources) + len(artifacts) > MAX_CANDIDATE_EVIDENCE:
            raise InvalidCandidateError("evidence", "resolved evidence exceeds the combined reference bound")
        if (
            isinstance(proposal, ExperienceContent)
            and proposal.failure is not None
            and not await _has_resolved_failure_evidence(
                self._sources,
                self._artifacts,
                self._evidence,
                connection,
                self._scope_id,
                source_rows,
                proposal.failure.signature.recall_cue,
            )
        ):
            raise InvalidCandidateError(
                "evidence",
                "failure records require a cited failed Task Outcome with verified exact evidence",
            )
        return sources

    async def _validate_foreign(self, connection: AsyncConnection, foreign: tuple[QualifiedOrigin, ...], /) -> None:
        """Verify trusted foreign support in the Scope that owns it, by exact digest.

        These come from the server-side selection of a pinned shared run, never from a
        caller. Each reference is read at its own origin, so a same-identity local decoy in
        the target Scope can neither satisfy nor mask it. A missing or moved origin refuses
        the proposal rather than weakening the ordinary evidence checks.
        """

        if not foreign:
            return
        if self._evidence is None:
            raise InvalidCandidateError("evidence", "foreign support requires an evidence resolver")
        for origin in foreign:
            if not origin.scope_id:
                raise InvalidCandidateError("evidence", "foreign support must name its owning Scope")
        try:
            # The resolver already reads each qualified input in the Scope that owns it and
            # compares the recorded digest, so this reuses the one authoritative path
            # instead of a second reader.
            await self._evidence.validate_qualified(connection, foreign)
        except EvidenceResolutionError as error:
            raise InvalidCandidateError("evidence", "foreign support is no longer available") from error

    async def _validate_handoff_objective(
        self, connection: AsyncConnection, proposal: object, target: ArtifactRef | None
    ) -> None:
        if isinstance(proposal, HandoffContent) and target is not None:
            current = await self._artifacts.get(connection, self._scope_id, target)
            if not isinstance(current, Handoff) or proposal.objective != current.content.objective:
                raise InvalidCandidateError("proposal", "Handoff Dream must preserve the target objective")

    async def _validate_target(
        self,
        connection: AsyncConnection,
        family: str,
        target: ArtifactRef | None,
        artifacts: tuple[ArtifactRef, ...],
    ) -> None:
        if target is None:
            return
        if target.family != family:
            raise InvalidCandidateError("target", f"must identify a {family} Artifact")
        if target not in artifacts:
            raise InvalidCandidateError("artifacts", f"must include the exact target {family} Artifact")
        try:
            current = await self._artifacts.latest(
                connection,
                self._scope_id,
                target.family,
                target.artifact_id,
            )
        except RepositoryNotFoundError as error:
            raise InvalidCandidateError("target", "Artifact is not available in this scope") from error
        if current.as_ref() != target:
            raise ArtifactTargetConflictError(target, current.as_ref())

    async def prepare_skill(self, proposal: SkillContent, /) -> SkillContent:
        """Build an instruction package before entering a Candidate commit transaction."""

        if proposal.package is not None:
            async with self._connection() as connection:
                return await self._canonical_skill_proposal(connection, proposal)
        snapshot = build_instruction_skill_package(proposal)
        async with self._connection() as connection:
            await self._skill_packages.add(connection, self._scope_id, snapshot)
        return snapshot.as_skill_content()

    async def prepare_skill_revision(self, target: ArtifactRef, proposal: SkillContent, /) -> SkillContent:
        """Rebuild a one-file Skill while preserving the target's protected metadata."""

        async with self._connection() as connection:
            current = await self._instruction_skill_target(connection, target)
            content = proposal.model_copy(
                update={
                    "name": current.content.name,
                    "license": current.content.license,
                    "compatibility": current.content.compatibility,
                    "metadata": current.content.metadata,
                    "allowed_tools": current.content.allowed_tools,
                    "package": None,
                }
            )
            snapshot = build_instruction_skill_package(content)
            await self._skill_packages.add(connection, self._scope_id, snapshot)
        return snapshot.as_skill_content()

    async def _validate_skill_revision(
        self, connection: AsyncConnection, target: ArtifactRef, proposal: SkillContent
    ) -> None:
        current = await self._instruction_skill_target(connection, target)
        if proposal.package is None:
            raise InvalidCandidateError("package", "Dream Skill revision requires a prepared standard package")
        snapshot = await self._skill_packages.get(connection, self._scope_id, proposal.package)
        if len(snapshot.entries) != 1 or snapshot.entries[0].path != "SKILL.md":
            raise InvalidCandidateError("package", "unsupported_target")
        for field in ("name", "license", "compatibility", "metadata", "allowed_tools"):
            if getattr(proposal, field) != getattr(current.content, field):
                raise InvalidCandidateError("proposal", "Dream Skill revision cannot change protected package metadata")

    async def require_instruction_skill_target(self, target: ArtifactRef, /) -> None:
        async with self._connection() as connection:
            await self._instruction_skill_target(connection, target)

    async def _instruction_skill_target(self, connection: AsyncConnection, target: ArtifactRef) -> Skill:
        current = await self._artifacts.get(connection, self._scope_id, target)
        if not isinstance(current, Skill):
            raise InvalidCandidateError("target", "expected an exact managed Skill")
        if current.content.package is not None:
            snapshot = await self._skill_packages.get(connection, self._scope_id, current.content.package)
            if len(snapshot.entries) != 1 or snapshot.entries[0].path != "SKILL.md":
                raise InvalidCandidateError("target", "unsupported_target")
        return current

    async def _canonical_skill_proposal(
        self,
        connection: AsyncConnection,
        proposal: SkillContent,
        /,
    ) -> SkillContent:
        if proposal.package is None:
            snapshot = build_instruction_skill_package(proposal)
            await self._skill_packages.add(connection, self._scope_id, snapshot)
        else:
            try:
                snapshot = await self._skill_packages.get(connection, self._scope_id, proposal.package)
            except RepositoryNotFoundError as error:
                raise InvalidCandidateError("package", "exact Skill package is not available in this scope") from error
        canonical = snapshot.as_skill_content()
        if proposal.package is not None and canonical != proposal:
            raise InvalidCandidateError("package", "cached Skill fields do not match the exact package")
        return canonical


def _reviewed_candidate(candidate: Candidate[Any]) -> ReviewedCandidate:
    _validate_proposal_family(candidate.family, candidate.proposal)
    return Candidate[ReviewedProposal].model_validate(candidate.model_dump(mode="python"))


def _experience_candidate(candidate: Candidate[Any]) -> Candidate[ExperienceContent]:
    reviewed = _reviewed_candidate(candidate)
    if reviewed.family != Experience.family or not isinstance(reviewed.proposal, ExperienceContent):
        raise InvalidCandidateError("family", candidate.family)
    return Candidate[ExperienceContent].model_validate(reviewed.model_dump(mode="python"))


def _skill_candidate(candidate: Candidate[Any]) -> Candidate[SkillContent]:
    reviewed = _reviewed_candidate(candidate)
    if reviewed.family != Skill.family or not isinstance(reviewed.proposal, SkillContent):
        raise InvalidCandidateError("family", candidate.family)
    return Candidate[SkillContent].model_validate(reviewed.model_dump(mode="python"))


def _validate_proposal_family(family: str, proposal: object) -> None:
    expected = {
        Experience.family: ExperienceContent,
        Skill.family: SkillContent,
        "profile": ProfileCandidateProposal,
        AtomicMemory.family: AtomicMemoryContent,
        TopicMemory.family: TopicMemoryContent,
        Handoff.family: HandoffContent,
        Prompt.family: PromptContent,
    }.get(family)
    if expected is None or type(proposal) is not expected:
        raise InvalidCandidateError("family", family)


def _validate_handoff_citations(
    proposal: HandoffContent,
    sources: tuple[SourceRef, ...],
    artifacts: tuple[ArtifactRef, ...],
    target: ArtifactRef | None,
) -> None:
    if proposal.generation is not None or target is None:
        raise InvalidCandidateError("proposal", "Dream Handoff cannot claim generated or activated content")
    statements = (*proposal.state, *((proposal.next_action,) if proposal.next_action is not None else ()))
    citations = tuple(citation for statement in statements for citation in statement.citations) + tuple(
        omission.citation for omission in proposal.omissions if omission.citation is not None
    )
    for citation in citations:
        if isinstance(citation, HandoffSourceCitation) and citation.source_ref in sources:
            continue
        if (
            isinstance(citation, HandoffArtifactCitation)
            and citation.artifact_ref in artifacts
            and citation.artifact_ref != target
        ):
            continue
        raise InvalidCandidateError("proposal", "Handoff claim cites evidence outside the selected inputs")


async def _has_resolved_failure_evidence(
    sources: GenerationSourceAccess,
    artifacts: ArtifactRepository,
    evidence: EvidenceResolver | None,
    connection: AsyncConnection,
    scope_id: str,
    rows: tuple[StoredSource, ...],
    cue: str,
    /,
) -> bool:
    try:
        cue_key = normalize_match_text(cue)
    except ValueError:
        return False
    for row in rows:
        if not isinstance(row.value, ContentSource) or row.value.metadata.get("kind") != "task-outcome":
            continue
        try:
            outcome = TaskOutcome.model_validate_json(row.value.content)
        except ValidationError:
            continue
        for ref in failure_refs(outcome, row.ref):
            items = outcome.checks if ref.item_kind == "check" else outcome.observations
            item = items[ref.item_index]
            if normalize_match_text(failure_item_text(item)) != cue_key:
                continue
            citations = item.evidence
            source_refs = tuple(
                citation.source_ref for citation in citations if isinstance(citation, HandoffSourceCitation)
            )
            artifact_refs = tuple(
                citation.artifact_ref for citation in citations if isinstance(citation, HandoffArtifactCitation)
            )
            try:
                await sources.require_for_generation(connection, scope_id, source_refs)
                try:
                    await artifacts.get_many(connection, scope_id, artifact_refs)
                except RepositoryError:
                    # Batch validation can reorder errors; preserve the original first failure.
                    for artifact_ref in artifact_refs:
                        await artifacts.get(connection, scope_id, artifact_ref)
            except (EvidenceResolutionError, RepositoryNotFoundError):
                continue
            return True
    return False


def _validate_approval_lineage(candidate: ReviewedCandidate) -> None:
    if candidate.family != Skill.family:
        return
    if candidate.target is None:
        if any(
            artifact.family != Experience.family
            and not (artifact.family == "prompt" and artifact.artifact_id == "skill.generate")
            for artifact in candidate.artifacts
        ):
            raise InvalidCandidateError(
                "artifacts",
                "new managed Skill lineage may reference only Experience Artifacts and its generation Prompt",
            )
        return
    if candidate.target not in candidate.artifacts:
        raise InvalidCandidateError("artifacts", "managed Skill lineage must include its exact target")
    if not candidate.sources:
        raise InvalidCandidateError("sources", "managed Skill replacement requires bounded Source evidence")


def _origin_keys(origins) -> set[tuple[str, str, str]]:
    """Compare qualified support on owning Scope, exact reference and exact digest."""

    return {(origin.scope_id, origin.reference.model_dump_json(), origin.digest) for origin in origins}


def _candidate_draft(candidate: ReviewedCandidate) -> ReviewedDraft:
    values: dict[str, object] = {
        "content": candidate.proposal,
        "sources": candidate.sources,
        "artifacts": candidate.artifacts,
    }
    if candidate.family == Experience.family and isinstance(candidate.proposal, ExperienceContent):
        return ExperienceDraft.model_validate(values)
    if candidate.family == Skill.family and isinstance(candidate.proposal, SkillContent):
        return SkillDraft.model_validate(values)
    raise InvalidCandidateError("family", candidate.family)


def _unique_sources(values: tuple[SourceRef, ...]) -> tuple[SourceRef, ...]:
    seen: set[str] = set()
    result: list[SourceRef] = []
    for value in values:
        key = value.model_dump_json()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def _unique_artifacts(values: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
    seen: set[tuple[str, str, int]] = set()
    result: list[ArtifactRef] = []
    for value in values:
        key = (value.family, value.artifact_id, value.revision)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def _validate_reason(value: str | None) -> None:
    if value is None:
        return
    if not value.strip() or value != value.strip():
        raise InvalidCandidateError("reason", "must be a non-empty trimmed string")
    if len(value) > MAX_CANDIDATE_REASON_LENGTH:
        raise InvalidCandidateError("reason", f"must not exceed {MAX_CANDIDATE_REASON_LENGTH} characters")


__all__ = ["ReviewService"]
