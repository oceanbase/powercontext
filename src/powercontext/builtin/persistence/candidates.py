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

"""Relational persistence for immutable Candidate versions and mutable heads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, RootModel
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError
from powercontext.builtin.persistence.tables import (
    ARTIFACT_CANDIDATE_HEADS_TABLE,
    ARTIFACT_CANDIDATE_VERSIONS_TABLE,
)
from powercontext.builtin.review.errors import (
    CandidateConflictError,
    CandidateNotFoundError,
    CandidateTerminalError,
    InvalidCandidateError,
)
from powercontext.builtin.review.models import (
    MAX_CANDIDATE_PAGE_SIZE,
    ArtifactCandidate,
    ArtifactCandidatePage,
    CandidateStatus,
)
from powercontext.limits import MAX_SCOPE_ID_LENGTH
from powercontext.sources import SourceRef


class _SourceRefs(RootModel[tuple[SourceRef, ...]]):
    pass


class _ArtifactRefs(RootModel[tuple[ArtifactRef, ...]]):
    pass


class CandidateRepository:
    """Store family-neutral Candidate envelopes with family-owned proposals."""

    def __init__(self, proposal_types: Mapping[str, type[BaseModel]], /) -> None:
        if not proposal_types:
            raise ValueError("at least one Candidate proposal type is required")  # noqa: TRY003
        self._proposal_types = dict(proposal_types)

    async def create(
        self,
        connection: AsyncConnection,
        scope_id: str,
        candidate_id: str,
        family: str,
        proposal: BaseModel,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef | None,
        reason: str | None,
    ) -> ArtifactCandidate[Any]:
        """Create the first immutable proposal version and its pending head."""

        _require_scope(scope_id)
        self._require_proposal(family, proposal)
        candidate = ArtifactCandidate(
            candidate_id=candidate_id,
            version=1,
            family=family,
            status=CandidateStatus.PENDING,
            proposal=proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
        )
        existing = await self._find_head(connection, scope_id, candidate_id)
        if existing is not None:
            raise CandidateConflictError(candidate_id, 1, int(existing["version"]))
        await self._insert_version(connection, scope_id, candidate)
        await connection.execute(
            insert(ARTIFACT_CANDIDATE_HEADS_TABLE).values(
                scope_id=scope_id,
                candidate_id=candidate_id,
                family=family,
                version=1,
                status=CandidateStatus.PENDING.value,
            )
        )
        return candidate

    async def get(
        self,
        connection: AsyncConnection,
        scope_id: str,
        candidate_id: str,
        /,
    ) -> ArtifactCandidate[Any]:
        """Return the current Candidate head and exact proposal version."""

        _require_scope(scope_id)
        row = await self._find_current(connection, scope_id, candidate_id)
        if row is None:
            raise CandidateNotFoundError(candidate_id)
        return self._decode_row(row)

    async def list(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        status: CandidateStatus,
        family: str | None,
        cursor: str | None,
        limit: int,
    ) -> ArtifactCandidatePage[Any]:
        """List one stable Review Inbox page ordered by Candidate identity."""

        _require_scope(scope_id)
        if limit < 1 or limit > MAX_CANDIDATE_PAGE_SIZE:
            raise InvalidRepositoryArgumentError("limit", f"must be between 1 and {MAX_CANDIDATE_PAGE_SIZE}")
        statement = (
            select(ARTIFACT_CANDIDATE_HEADS_TABLE, ARTIFACT_CANDIDATE_VERSIONS_TABLE)
            .join(
                ARTIFACT_CANDIDATE_VERSIONS_TABLE,
                (ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.scope_id == ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id)
                & (ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.candidate_id == ARTIFACT_CANDIDATE_HEADS_TABLE.c.candidate_id)
                & (ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.version == ARTIFACT_CANDIDATE_HEADS_TABLE.c.version),
            )
            .where(
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id == scope_id,
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.status == status.value,
            )
            .order_by(ARTIFACT_CANDIDATE_HEADS_TABLE.c.candidate_id)
            .limit(limit + 1)
        )
        if family is not None:
            if family not in self._proposal_types:
                raise InvalidCandidateError("family", family)
            statement = statement.where(ARTIFACT_CANDIDATE_HEADS_TABLE.c.family == family)
        if cursor is not None:
            statement = statement.where(ARTIFACT_CANDIDATE_HEADS_TABLE.c.candidate_id > cursor)
        rows = list((await connection.execute(statement)).mappings())
        has_more = len(rows) > limit
        selected = rows[:limit]
        candidates = tuple(self._decode_row(row) for row in selected)
        next_cursor = candidates[-1].candidate_id if has_more and candidates else None
        return ArtifactCandidatePage(candidates=candidates, next_cursor=next_cursor)

    async def revise(
        self,
        connection: AsyncConnection,
        scope_id: str,
        candidate_id: str,
        expected_version: int,
        proposal: BaseModel,
        /,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef | None,
        reason: str | None,
    ) -> ArtifactCandidate[Any]:
        """Append a complete immutable proposal and advance the pending head."""

        current = await self.lock_pending(connection, scope_id, candidate_id, expected_version)
        self._require_proposal(current.family, proposal)
        revised = ArtifactCandidate(
            candidate_id=candidate_id,
            version=current.version + 1,
            family=current.family,
            status=CandidateStatus.PENDING,
            proposal=proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
        )
        await self._insert_version(connection, scope_id, revised)
        advanced = await connection.execute(
            update(ARTIFACT_CANDIDATE_HEADS_TABLE)
            .where(
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id == scope_id,
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.candidate_id == candidate_id,
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.version == expected_version,
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.status == CandidateStatus.PENDING.value,
            )
            .values(version=revised.version)
        )
        if advanced.rowcount != 1:
            latest = await self.get(connection, scope_id, candidate_id)
            self._raise_stale_or_terminal(latest, expected_version)
        return revised

    async def reject(
        self,
        connection: AsyncConnection,
        scope_id: str,
        candidate_id: str,
        expected_version: int,
        reason: str,
        /,
    ) -> ArtifactCandidate[Any]:
        """Move a pending Candidate to its rejected terminal state."""

        current = await self.lock_pending(connection, scope_id, candidate_id, expected_version)
        rejected = await connection.execute(
            update(ARTIFACT_CANDIDATE_HEADS_TABLE)
            .where(
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id == scope_id,
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.candidate_id == candidate_id,
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.version == expected_version,
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.status == CandidateStatus.PENDING.value,
            )
            .values(status=CandidateStatus.REJECTED.value, decision_reason=reason)
        )
        if rejected.rowcount != 1:
            latest = await self.get(connection, scope_id, candidate_id)
            self._raise_stale_or_terminal(latest, expected_version)
        return current.model_copy(update={"status": CandidateStatus.REJECTED, "decision_reason": reason})

    async def lock_pending(
        self,
        connection: AsyncConnection,
        scope_id: str,
        candidate_id: str,
        expected_version: int,
    ) -> ArtifactCandidate[Any]:
        """Acquire the pending head CAS before a lifecycle write."""

        _require_scope(scope_id)
        locked = await connection.execute(
            update(ARTIFACT_CANDIDATE_HEADS_TABLE)
            .where(
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id == scope_id,
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.candidate_id == candidate_id,
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.version == expected_version,
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.status == CandidateStatus.PENDING.value,
            )
            .values(version=ARTIFACT_CANDIDATE_HEADS_TABLE.c.version)
        )
        current = await self.get(connection, scope_id, candidate_id)
        if locked.rowcount != 1:
            self._raise_stale_or_terminal(current, expected_version)
        return current

    async def mark_approved(
        self,
        connection: AsyncConnection,
        scope_id: str,
        candidate_id: str,
        expected_version: int,
        result: ArtifactRef,
        /,
    ) -> ArtifactCandidate[Any]:
        """Record an Artifact result after its commit in the same transaction."""

        approved = await connection.execute(
            update(ARTIFACT_CANDIDATE_HEADS_TABLE)
            .where(
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id == scope_id,
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.candidate_id == candidate_id,
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.version == expected_version,
                ARTIFACT_CANDIDATE_HEADS_TABLE.c.status == CandidateStatus.PENDING.value,
            )
            .values(
                status=CandidateStatus.APPROVED.value,
                result_family=result.family,
                result_artifact_id=result.artifact_id,
                result_revision=result.revision,
            )
        )
        if approved.rowcount != 1:
            current = await self.get(connection, scope_id, candidate_id)
            self._raise_stale_or_terminal(current, expected_version)
        return await self.get(connection, scope_id, candidate_id)

    async def _insert_version(
        self,
        connection: AsyncConnection,
        scope_id: str,
        candidate: ArtifactCandidate[Any],
    ) -> None:
        target = candidate.target
        await connection.execute(
            insert(ARTIFACT_CANDIDATE_VERSIONS_TABLE).values(
                scope_id=scope_id,
                candidate_id=candidate.candidate_id,
                version=candidate.version,
                family=candidate.family,
                proposal=dump_model(candidate.proposal, kind="candidate-proposal", name=candidate.family),
                source_refs=dump_model(_SourceRefs(candidate.sources), kind="candidate", name="source-refs"),
                artifact_refs=dump_model(_ArtifactRefs(candidate.artifacts), kind="candidate", name="artifact-refs"),
                target_family=None if target is None else target.family,
                target_artifact_id=None if target is None else target.artifact_id,
                target_revision=None if target is None else target.revision,
                reason=candidate.reason,
            )
        )

    async def _find_head(
        self,
        connection: AsyncConnection,
        scope_id: str,
        candidate_id: str,
    ) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(ARTIFACT_CANDIDATE_HEADS_TABLE).where(
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id == scope_id,
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.candidate_id == candidate_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def _find_current(
        self,
        connection: AsyncConnection,
        scope_id: str,
        candidate_id: str,
    ) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(ARTIFACT_CANDIDATE_HEADS_TABLE, ARTIFACT_CANDIDATE_VERSIONS_TABLE)
                    .join(
                        ARTIFACT_CANDIDATE_VERSIONS_TABLE,
                        (ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.scope_id == ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id)
                        & (
                            ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.candidate_id
                            == ARTIFACT_CANDIDATE_HEADS_TABLE.c.candidate_id
                        )
                        & (ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.version == ARTIFACT_CANDIDATE_HEADS_TABLE.c.version),
                    )
                    .where(
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id == scope_id,
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.candidate_id == candidate_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    def _decode_row(self, row: Mapping[Any, Any]) -> ArtifactCandidate[Any]:
        family = str(row["family"])
        proposal_type = self._proposal_types.get(family)
        if proposal_type is None:
            raise InvalidCandidateError("family", family)
        target_family = row["target_family"]
        result_family = row["result_family"]
        return ArtifactCandidate(
            candidate_id=str(row["candidate_id"]),
            version=int(row["version"]),
            family=family,
            status=CandidateStatus(str(row["status"])),
            proposal=load_model(
                proposal_type,
                stored_bytes(row["proposal"], column="proposal"),
                kind="candidate-proposal",
                name=family,
            ),
            sources=load_model(
                _SourceRefs,
                stored_bytes(row["source_refs"], column="source_refs"),
                kind="candidate",
                name="source-refs",
            ).root,
            artifacts=load_model(
                _ArtifactRefs,
                stored_bytes(row["artifact_refs"], column="artifact_refs"),
                kind="candidate",
                name="artifact-refs",
            ).root,
            target=(
                None
                if target_family is None
                else ArtifactRef(
                    family=str(target_family),
                    artifact_id=str(row["target_artifact_id"]),
                    revision=int(row["target_revision"]),
                )
            ),
            reason=None if row["reason"] is None else str(row["reason"]),
            result_artifact=(
                None
                if result_family is None
                else ArtifactRef(
                    family=str(result_family),
                    artifact_id=str(row["result_artifact_id"]),
                    revision=int(row["result_revision"]),
                )
            ),
            decision_reason=None if row["decision_reason"] is None else str(row["decision_reason"]),
        )

    def _require_proposal(self, family: str, proposal: BaseModel) -> None:
        expected = self._proposal_types.get(family)
        if expected is None:
            raise InvalidCandidateError("family", family)
        if type(proposal) is not expected:
            raise InvalidCandidateError("proposal", f"expected {expected.__name__}")

    @staticmethod
    def _raise_stale_or_terminal(candidate: ArtifactCandidate[Any], expected_version: int) -> None:
        if candidate.status is not CandidateStatus.PENDING:
            raise CandidateTerminalError(candidate.candidate_id, candidate.status.value)
        raise CandidateConflictError(candidate.candidate_id, expected_version, candidate.version)


def _require_scope(scope_id: object) -> None:
    if not isinstance(scope_id, str) or not scope_id.strip() or scope_id != scope_id.strip():
        raise InvalidRepositoryArgumentError("scope_id", "must be a non-empty trimmed string")
    if len(scope_id) > MAX_SCOPE_ID_LENGTH:
        raise InvalidRepositoryArgumentError(
            "scope_id",
            f"must not exceed {MAX_SCOPE_ID_LENGTH} characters",
        )


__all__ = ["CandidateRepository"]
