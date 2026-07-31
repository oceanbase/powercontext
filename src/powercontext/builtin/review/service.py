"""Transactional Experience Candidate and Review orchestration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceContent, ExperienceDraft
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.candidates import CandidateRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
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


class ReviewService:
    """Keep Candidate CAS and Artifact CAS inside one database transaction."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        candidates: CandidateRepository,
        artifacts: ArtifactRepository,
        sources: SourceRepository,
        id_factory: IdFactory,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._candidates = candidates
        self._artifacts = artifacts
        self._sources = sources
        self._id_factory = id_factory

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

        canonical_sources = _unique_sources(sources)
        canonical_artifacts = _unique_artifacts(artifacts)
        _validate_reason(reason)
        async with self._database.transaction() as connection:
            await self._validate_evidence(connection, canonical_sources, canonical_artifacts)
            await self._validate_target(connection, target, canonical_artifacts)
            candidate = await self._candidates.create(
                connection,
                self._scope_id,
                self._id_factory("candidate"),
                Experience.family,
                proposal,
                sources=canonical_sources,
                artifacts=canonical_artifacts,
                target=target,
                reason=reason,
            )
        return _experience_candidate(candidate)

    async def get_candidate(self, candidate_id: str, /) -> ArtifactCandidate[ExperienceContent]:
        async with self._database.transaction() as connection:
            candidate = await self._candidates.get(connection, self._scope_id, candidate_id)
        return _experience_candidate(candidate)

    async def list_candidates(
        self,
        /,
        *,
        status: CandidateStatus,
        family: str | None,
        cursor: str | None,
        limit: int,
    ) -> ArtifactCandidatePage[ExperienceContent]:
        async with self._database.transaction() as connection:
            page = await self._candidates.list(
                connection,
                self._scope_id,
                status=status,
                family=family,
                cursor=cursor,
                limit=limit,
            )
        return ArtifactCandidatePage(
            candidates=tuple(_experience_candidate(candidate) for candidate in page.candidates),
            next_cursor=page.next_cursor,
        )

    async def revise(
        self,
        candidate_id: str,
        expected_version: int,
        proposal: ExperienceContent,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef | None,
        reason: str | None,
    ) -> ArtifactCandidate[ExperienceContent]:
        canonical_sources = _unique_sources(sources)
        canonical_artifacts = _unique_artifacts(artifacts)
        _validate_reason(reason)
        async with self._database.transaction() as connection:
            current = await self._candidates.lock_pending(
                connection,
                self._scope_id,
                candidate_id,
                expected_version,
            )
            _experience_candidate(current)
            if target != current.target:
                raise InvalidCandidateError("target", "cannot change across Candidate versions")
            await self._validate_evidence(connection, canonical_sources, canonical_artifacts)
            await self._validate_target(connection, target, canonical_artifacts)
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
        return _experience_candidate(revised)

    async def reject(
        self,
        candidate_id: str,
        expected_version: int,
        reason: str,
        /,
    ) -> ArtifactCandidate[ExperienceContent]:
        _validate_reason(reason)
        async with self._database.transaction() as connection:
            rejected = await self._candidates.reject(
                connection,
                self._scope_id,
                candidate_id,
                expected_version,
                reason,
            )
        return _experience_candidate(rejected)

    async def approve(
        self,
        candidate_id: str,
        expected_version: int,
        /,
    ) -> ArtifactCandidate[ExperienceContent]:
        """Atomically commit the reviewed Experience and Candidate result."""

        async with self._database.transaction() as connection:
            candidate = _experience_candidate(
                await self._candidates.lock_pending(
                    connection,
                    self._scope_id,
                    candidate_id,
                    expected_version,
                )
            )
            draft = ExperienceDraft(
                content=candidate.proposal,
                sources=candidate.sources,
                artifacts=candidate.artifacts,
            )
            if candidate.target is None:
                artifact = await self._artifacts.create(
                    connection,
                    self._scope_id,
                    self._id_factory("experience"),
                    draft,
                )
            else:
                try:
                    target = await self._artifacts.get(connection, self._scope_id, candidate.target)
                    artifact = await self._artifacts.revise(connection, self._scope_id, target, draft)
                except RevisionConflictError as error:
                    current = error.current
                    if not isinstance(current, Experience):
                        raise
                    raise ArtifactTargetConflictError(candidate.target, current.as_ref()) from error
            approved = await self._candidates.mark_approved(
                connection,
                self._scope_id,
                candidate_id,
                expected_version,
                artifact.as_ref(),
            )
        return _experience_candidate(approved)

    async def get_experience(self, ref: ArtifactRef, /) -> Experience:
        if ref.family != Experience.family:
            raise ArtifactNotFoundError(ref)
        async with self._database.transaction() as connection:
            try:
                artifact = await self._artifacts.get(connection, self._scope_id, ref)
            except RepositoryNotFoundError:
                raise ArtifactNotFoundError(ref) from None
        if not isinstance(artifact, Experience):
            raise ArtifactNotFoundError(ref)
        return artifact

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
        target: ArtifactRef | None,
        artifacts: tuple[ArtifactRef, ...],
    ) -> None:
        if target is None:
            return
        if target.family != Experience.family:
            raise InvalidCandidateError("target", "must identify an Experience")
        if target not in artifacts:
            raise InvalidCandidateError("artifacts", "must include the exact target Experience")
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


def _experience_candidate(candidate: ArtifactCandidate[Any]) -> ArtifactCandidate[ExperienceContent]:
    if candidate.family != Experience.family or not isinstance(candidate.proposal, ExperienceContent):
        raise InvalidCandidateError("family", candidate.family)
    return ArtifactCandidate[ExperienceContent].model_validate(candidate.model_dump(mode="python"))


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
