"""Transactional Artifact Candidate and Review orchestration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeAlias, cast

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceContent, ExperienceDraft
from powercontext.builtin.artifacts.skill import Skill, SkillContent, SkillDraft
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.candidates import CandidateRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.experience_index import ExperienceIndex
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.review.errors import ArtifactTargetConflictError, InvalidCandidateError
from powercontext.builtin.review.models import (
    MAX_CANDIDATE_EVIDENCE,
    MAX_CANDIDATE_REASON_LENGTH,
    ArtifactCandidate,
    ArtifactCandidatePage,
    CandidateStatus,
)
from powercontext.errors import ArtifactNotFoundError, RevisionConflictError
from powercontext.sources import SourceRef

IdFactory = Callable[[str], str]
ReviewedProposal: TypeAlias = ExperienceContent | SkillContent
ReviewedArtifact: TypeAlias = Experience | Skill
ReviewedDraft: TypeAlias = ExperienceDraft | SkillDraft
ReviewedCandidate: TypeAlias = ArtifactCandidate[ReviewedProposal]


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
        sources: SourceRepository,
        id_factory: IdFactory,
        connection: AsyncConnection | None = None,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._candidates = candidates
        self._artifacts = artifacts
        self._experience_index = experience_index
        self._sources = sources
        self._id_factory = id_factory
        self._bound_connection = connection

    async def propose_experience(
        self,
        proposal: ExperienceContent,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef | None,
        reason: str | None,
    ) -> ArtifactCandidate[ExperienceContent]:
        """Persist a human or integration supplied Experience proposal."""

        candidate = await self._propose(
            Experience.family,
            proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
        )
        return _experience_candidate(candidate)

    async def propose_skill(
        self,
        proposal: SkillContent,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef | None,
        reason: str | None,
    ) -> ArtifactCandidate[SkillContent]:
        """Persist a human or integration supplied managed Skill proposal."""

        candidate = await self._propose(
            Skill.family,
            proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
        )
        return _skill_candidate(candidate)

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
    ) -> ReviewedCandidate:
        canonical_sources = _unique_sources(sources)
        canonical_artifacts = _unique_artifacts(artifacts)
        _validate_reason(reason)
        async with self._database.connection(self._bound_connection) as connection:
            await self._validate_evidence(connection, canonical_sources, canonical_artifacts)
            await self._validate_target(connection, family, target, canonical_artifacts)
            candidate = await self._candidates.create(
                connection,
                self._scope_id,
                self._id_factory("candidate"),
                family,
                proposal,
                sources=canonical_sources,
                artifacts=canonical_artifacts,
                target=target,
                reason=reason,
            )
        return _reviewed_candidate(candidate)

    async def get_candidate(self, candidate_id: str, /) -> ReviewedCandidate:
        async with self._database.connection(self._bound_connection) as connection:
            candidate = await self._candidates.get(connection, self._scope_id, candidate_id)
        return _reviewed_candidate(candidate)

    async def list_candidates(
        self,
        /,
        *,
        status: CandidateStatus,
        family: str | None,
        cursor: str | None,
        limit: int,
    ) -> ArtifactCandidatePage[ReviewedProposal]:
        async with self._database.connection(self._bound_connection) as connection:
            page = await self._candidates.list(
                connection,
                self._scope_id,
                status=status,
                family=family,
                cursor=cursor,
                limit=limit,
            )
        return ArtifactCandidatePage(
            candidates=tuple(_reviewed_candidate(candidate) for candidate in page.candidates),
            next_cursor=page.next_cursor,
        )

    async def revise(
        self,
        candidate_id: str,
        expected_version: int,
        proposal: ReviewedProposal,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef | None,
        reason: str | None,
    ) -> ReviewedCandidate:
        canonical_sources = _unique_sources(sources)
        canonical_artifacts = _unique_artifacts(artifacts)
        _validate_reason(reason)
        async with self._database.connection(self._bound_connection) as connection:
            current = await self._candidates.lock_pending(
                connection,
                self._scope_id,
                candidate_id,
                expected_version,
            )
            reviewed = _reviewed_candidate(current)
            _validate_proposal_family(reviewed.family, proposal)
            if target != current.target:
                raise InvalidCandidateError("target", "cannot change across Candidate versions")
            await self._validate_evidence(connection, canonical_sources, canonical_artifacts)
            await self._validate_target(connection, reviewed.family, target, canonical_artifacts)
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
        async with self._database.connection(self._bound_connection) as connection:
            rejected = await self._candidates.reject(
                connection,
                self._scope_id,
                candidate_id,
                expected_version,
                reason,
            )
        return _reviewed_candidate(rejected)

    async def approve(
        self,
        candidate_id: str,
        expected_version: int,
        /,
    ) -> ReviewedCandidate:
        """Atomically commit the reviewed Artifact and Candidate result."""

        async with self._database.connection(self._bound_connection) as connection:
            candidate = _reviewed_candidate(
                await self._candidates.lock_pending(
                    connection,
                    self._scope_id,
                    candidate_id,
                    expected_version,
                )
            )
            _validate_approval_lineage(candidate)
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
            approved = await self._candidates.mark_approved(
                connection,
                self._scope_id,
                candidate_id,
                expected_version,
                artifact.as_ref(),
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
        async with self._database.connection(self._bound_connection) as connection:
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
    ) -> None:
        if not sources and not artifacts:
            raise InvalidCandidateError("evidence", "at least one exact reference is required")
        if len(sources) + len(artifacts) > MAX_CANDIDATE_EVIDENCE:
            raise InvalidCandidateError("evidence", f"must not exceed {MAX_CANDIDATE_EVIDENCE} exact references")
        try:
            for source in sources:
                await self._sources.get(connection, self._scope_id, source)
            for artifact in artifacts:
                await self._artifacts.get(connection, self._scope_id, artifact)
        except RepositoryNotFoundError as error:
            raise InvalidCandidateError("evidence", "reference is not available in this scope") from error

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


def _reviewed_candidate(candidate: ArtifactCandidate[Any]) -> ReviewedCandidate:
    _validate_proposal_family(candidate.family, candidate.proposal)
    return ArtifactCandidate[ReviewedProposal].model_validate(candidate.model_dump(mode="python"))


def _experience_candidate(candidate: ArtifactCandidate[Any]) -> ArtifactCandidate[ExperienceContent]:
    reviewed = _reviewed_candidate(candidate)
    if reviewed.family != Experience.family or not isinstance(reviewed.proposal, ExperienceContent):
        raise InvalidCandidateError("family", candidate.family)
    return ArtifactCandidate[ExperienceContent].model_validate(reviewed.model_dump(mode="python"))


def _skill_candidate(candidate: ArtifactCandidate[Any]) -> ArtifactCandidate[SkillContent]:
    reviewed = _reviewed_candidate(candidate)
    if reviewed.family != Skill.family or not isinstance(reviewed.proposal, SkillContent):
        raise InvalidCandidateError("family", candidate.family)
    return ArtifactCandidate[SkillContent].model_validate(reviewed.model_dump(mode="python"))


def _validate_proposal_family(family: str, proposal: object) -> None:
    expected = {
        Experience.family: ExperienceContent,
        Skill.family: SkillContent,
    }.get(family)
    if expected is None or type(proposal) is not expected:
        raise InvalidCandidateError("family", family)


def _validate_approval_lineage(candidate: ReviewedCandidate) -> None:
    if candidate.family != Skill.family:
        return
    if candidate.target is None:
        if any(artifact.family != Experience.family for artifact in candidate.artifacts):
            raise InvalidCandidateError(
                "artifacts",
                "new managed Skill lineage may reference only Experience Artifacts",
            )
        return
    if candidate.target not in candidate.artifacts:
        raise InvalidCandidateError("artifacts", "managed Skill lineage must include its exact target")
    if not candidate.sources:
        raise InvalidCandidateError("sources", "managed Skill replacement requires bounded Source evidence")


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
    seen: set[tuple[str, str]] = set()
    result: list[SourceRef] = []
    for value in values:
        key = (value.source_type, value.source_id)
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
