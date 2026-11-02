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

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, RootModel
from sqlalchemy import case, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.evidence.models import content_digest
from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError, InvalidStoredPayloadError
from powercontext.builtin.persistence.tables import (
    CANDIDATE_HEADS_TABLE,
    CANDIDATE_VERSIONS_TABLE,
)
from powercontext.builtin.review.errors import (
    CandidateConflictError,
    CandidateNotFoundError,
    CandidateTerminalError,
    InvalidCandidateError,
)
from powercontext.builtin.review.models import (
    MAX_CANDIDATE_PAGE_SIZE,
    Candidate,
    CandidateAudit,
    CandidateCatalogItem,
    CandidatePage,
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
    ) -> Candidate[Any]:
        """Create the first immutable proposal version and its pending head."""

        _require_scope(scope_id)
        self._require_proposal(family, proposal)
        candidate = Candidate(
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
        return (await self.create_many(connection, scope_id, (candidate,)))[0]

    async def create_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        candidates: Sequence[Candidate[Any]],
        /,
    ) -> tuple[Candidate[Any], ...]:
        """Persist independent pending proposals from one operation by table."""

        _require_scope(scope_id)
        if not candidates:
            return ()
        versions = []
        heads = []
        seen: set[str] = set()
        for candidate in candidates:
            self._require_proposal(candidate.family, candidate.proposal)
            if candidate.version != 1 or candidate.status is not CandidateStatus.PENDING:
                raise InvalidCandidateError("candidate", "bulk creation requires pending first versions")
            if candidate.candidate_id in seen:
                raise CandidateConflictError(candidate.candidate_id, 1, 1)
            seen.add(candidate.candidate_id)
            versions.append(self._version_row(scope_id, candidate))
            heads.append({
                "scope_id": scope_id,
                "candidate_id": candidate.candidate_id,
                "family": candidate.family,
                "version": 1,
                "status": CandidateStatus.PENDING.value,
            })
        try:
            await connection.execute(insert(CANDIDATE_VERSIONS_TABLE), versions)
            await connection.execute(insert(CANDIDATE_HEADS_TABLE), heads)
        except IntegrityError:
            for candidate in candidates:
                existing = await self._find_head(connection, scope_id, candidate.candidate_id)
                if existing is not None:
                    raise CandidateConflictError(candidate.candidate_id, 1, int(existing["version"])) from None
            raise
        return tuple(candidates)

    async def get(
        self,
        connection: AsyncConnection,
        scope_id: str,
        candidate_id: str,
        /,
        *,
        current: bool = False,
    ) -> Candidate[Any]:
        """Return the current Candidate head and exact proposal version."""

        _require_scope(scope_id)
        row = await self._find_current(connection, scope_id, candidate_id, current=current)
        if row is None:
            raise CandidateNotFoundError(candidate_id)
        return self.decode_candidate(row)

    async def page(self, connection, scope_id, *, status, family, candidate_kind, cursor, limit, metadata_only=False):
        """Read a stable bounded window; Tag proposals retain their required target basis."""
        from powercontext.builtin.persistence.catalog_changes import decode_tag_candidate

        _require_scope(scope_id)
        if not 1 <= limit <= MAX_CANDIDATE_PAGE_SIZE:
            raise InvalidRepositoryArgumentError("limit", f"must be between 1 and {MAX_CANDIDATE_PAGE_SIZE}")
        heads, versions = CANDIDATE_HEADS_TABLE, CANDIDATE_VERSIONS_TABLE
        columns = (heads, versions)
        if metadata_only:
            columns = (
                heads.c.candidate_id,
                heads.c.candidate_kind,
                heads.c.family,
                heads.c.version,
                heads.c.status,
                versions.c.reason,
                heads.c.result_payload,
                heads.c.decision_reason,
                case((heads.c.candidate_kind == "tag", versions.c.proposal), else_=None).label("proposal"),
            )
        statement = (
            select(*columns)
            .join(
                versions,
                (heads.c.scope_id == versions.c.scope_id)
                & (heads.c.candidate_id == versions.c.candidate_id)
                & (heads.c.version == versions.c.version),
            )
            .where(heads.c.scope_id == scope_id, heads.c.status == status.value)
        )
        if family is not None:
            statement = statement.where(heads.c.family == family)
        if candidate_kind is not None:
            statement = statement.where(heads.c.candidate_kind == candidate_kind)
        if cursor is not None:
            statement = statement.where(heads.c.candidate_id > cursor)
        rows = tuple((await connection.execute(statement.order_by(heads.c.candidate_id).limit(limit + 1))).mappings())
        items = tuple(
            decode_tag_candidate(row)
            if row["candidate_kind"] == "tag"
            else CandidateCatalogItem.model_validate(row)
            if metadata_only
            else self.decode_candidate(row)
            for row in rows[:limit]
        )
        return items, rows[limit - 1]["candidate_id"] if len(rows) > limit else None

    async def list(self, connection, scope_id, /, *, status, family, cursor, limit):
        """List one stable Artifact Review Inbox page ordered by Candidate identity."""
        if family is not None and family not in self._proposal_types:
            raise InvalidCandidateError("family", family)
        items, cursor = await self.page(
            connection, scope_id, status=status, family=family, candidate_kind="artifact", cursor=cursor, limit=limit
        )
        return CandidatePage(candidates=items, next_cursor=cursor)

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
    ) -> Candidate[Any]:
        """Append a complete immutable proposal and advance the pending head."""

        current = await self.lock_pending(connection, scope_id, candidate_id, expected_version)
        self._require_proposal(current.family, proposal)
        revised = Candidate(
            candidate_id=candidate_id,
            version=current.version + 1,
            family=current.family,
            status=CandidateStatus.PENDING,
            proposal=proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
            audit=None
            if current.audit is None
            else current.audit.model_copy(
                update={
                    "proposal_digest": proposal_digest(proposal),
                    "evidence_manifest_ref": f"candidate:{candidate_id}:{current.version + 1}",
                    "proposal_fingerprint": None,
                }
            ),
        )
        await self._insert_version(connection, scope_id, revised)
        advanced = await connection.execute(
            update(CANDIDATE_HEADS_TABLE)
            .where(
                CANDIDATE_HEADS_TABLE.c.scope_id == scope_id,
                CANDIDATE_HEADS_TABLE.c.candidate_kind == "artifact",
                CANDIDATE_HEADS_TABLE.c.candidate_id == candidate_id,
                CANDIDATE_HEADS_TABLE.c.version == expected_version,
                CANDIDATE_HEADS_TABLE.c.status == CandidateStatus.PENDING.value,
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
    ) -> Candidate[Any]:
        """Move a pending Candidate to its rejected terminal state."""

        current = await self.lock_pending(connection, scope_id, candidate_id, expected_version)
        rejected = await connection.execute(
            update(CANDIDATE_HEADS_TABLE)
            .where(
                CANDIDATE_HEADS_TABLE.c.scope_id == scope_id,
                CANDIDATE_HEADS_TABLE.c.candidate_kind == "artifact",
                CANDIDATE_HEADS_TABLE.c.candidate_id == candidate_id,
                CANDIDATE_HEADS_TABLE.c.version == expected_version,
                CANDIDATE_HEADS_TABLE.c.status == CandidateStatus.PENDING.value,
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
    ) -> Candidate[Any]:
        """Acquire the pending head CAS before a lifecycle write."""

        _require_scope(scope_id)
        locked = await connection.execute(
            update(CANDIDATE_HEADS_TABLE)
            .where(
                CANDIDATE_HEADS_TABLE.c.scope_id == scope_id,
                CANDIDATE_HEADS_TABLE.c.candidate_kind == "artifact",
                CANDIDATE_HEADS_TABLE.c.candidate_id == candidate_id,
                CANDIDATE_HEADS_TABLE.c.version == expected_version,
                CANDIDATE_HEADS_TABLE.c.status == CandidateStatus.PENDING.value,
            )
            .values(version=CANDIDATE_HEADS_TABLE.c.version)
        )
        current = await self.get(connection, scope_id, candidate_id, current=True)
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
    ) -> Candidate[Any]:
        """Record an Artifact result after its commit in the same transaction."""

        approved = await connection.execute(
            update(CANDIDATE_HEADS_TABLE)
            .where(
                CANDIDATE_HEADS_TABLE.c.scope_id == scope_id,
                CANDIDATE_HEADS_TABLE.c.candidate_kind == "artifact",
                CANDIDATE_HEADS_TABLE.c.candidate_id == candidate_id,
                CANDIDATE_HEADS_TABLE.c.version == expected_version,
                CANDIDATE_HEADS_TABLE.c.status == CandidateStatus.PENDING.value,
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
        candidate: Candidate[Any],
    ) -> None:
        await connection.execute(insert(CANDIDATE_VERSIONS_TABLE).values(**self._version_row(scope_id, candidate)))

    @staticmethod
    def _version_row(scope_id: str, candidate: Candidate[Any]) -> dict[str, Any]:
        target = candidate.target
        return {
            "scope_id": scope_id,
            "candidate_id": candidate.candidate_id,
            "version": candidate.version,
            "family": candidate.family,
            "proposal": _encode_proposal(candidate),
            "source_refs": dump_model(_SourceRefs(candidate.sources), kind="candidate", name="source-refs"),
            "artifact_refs": dump_model(_ArtifactRefs(candidate.artifacts), kind="candidate", name="artifact-refs"),
            "target_family": None if target is None else target.family,
            "target_artifact_id": None if target is None else target.artifact_id,
            "target_revision": None if target is None else target.revision,
            "reason": candidate.reason,
        }

    async def _find_head(
        self,
        connection: AsyncConnection,
        scope_id: str,
        candidate_id: str,
    ) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(CANDIDATE_HEADS_TABLE).where(
                        CANDIDATE_HEADS_TABLE.c.scope_id == scope_id,
                        CANDIDATE_HEADS_TABLE.c.candidate_kind == "artifact",
                        CANDIDATE_HEADS_TABLE.c.candidate_id == candidate_id,
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
        *,
        current: bool = False,
    ) -> Mapping[Any, Any] | None:
        heads, versions = CANDIDATE_HEADS_TABLE, CANDIDATE_VERSIONS_TABLE
        statement = (
            select(heads, versions)
            .join(
                versions,
                (versions.c.scope_id == heads.c.scope_id)
                & (versions.c.candidate_id == heads.c.candidate_id)
                & (versions.c.version == heads.c.version),
            )
            .where(
                heads.c.scope_id == scope_id, heads.c.candidate_id == candidate_id, heads.c.candidate_kind == "artifact"
            )
        )
        if current:
            statement = statement.with_for_update()
        return (await connection.execute(statement)).mappings().one_or_none()

    def decode_candidate(self, row: Mapping[Any, Any], /) -> Candidate[Any]:
        """Decode and validate the complete stored Candidate using its public read contract."""

        family = str(row["family"])
        proposal_type = self._proposal_types.get(family)
        if proposal_type is None:
            raise InvalidCandidateError("family", family)
        target_family = row["target_family"]
        result_family = row["result_family"]
        proposal_payload, audit = decode_candidate_proposal_envelope(stored_bytes(row["proposal"], column="proposal"))
        proposal = load_model(proposal_type, proposal_payload, kind="candidate-proposal", name=family)
        return Candidate(
            candidate_id=str(row["candidate_id"]),
            version=int(row["version"]),
            family=family,
            status=CandidateStatus(str(row["status"])),
            proposal=proposal,
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
            audit=audit,
        )

    def _require_proposal(self, family: str, proposal: BaseModel) -> None:
        expected = self._proposal_types.get(family)
        if expected is None:
            raise InvalidCandidateError("family", family)
        if type(proposal) is not expected:
            raise InvalidCandidateError("proposal", f"expected {expected.__name__}")

    @staticmethod
    def _raise_stale_or_terminal(candidate: Candidate[Any], expected_version: int) -> None:
        if candidate.status is not CandidateStatus.PENDING:
            raise CandidateTerminalError(candidate.candidate_id, candidate.status.value)
        raise CandidateConflictError(candidate.candidate_id, expected_version, candidate.version)


def proposal_digest(proposal: BaseModel) -> str:
    return content_digest(json.dumps(proposal.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode())


def _encode_proposal(candidate: Candidate[Any]) -> bytes:
    if candidate.audit is None:
        return dump_model(candidate.proposal, kind="candidate-proposal", name=candidate.family)
    return json.dumps(
        {
            "candidate_payload_version": 1,
            "proposal": candidate.proposal.model_dump(mode="json"),
            "audit": candidate.audit.model_dump(mode="json"),
        },
        ensure_ascii=False,
    ).encode()


def decode_candidate_proposal_envelope(payload: bytes, /) -> tuple[bytes, CandidateAudit | None]:
    """Unwrap persisted proposal provenance without changing the family-owned payload."""

    try:
        decoded = json.loads(payload)
        if isinstance(decoded, dict) and decoded.get("candidate_payload_version") == 1:
            return json.dumps(decoded["proposal"], ensure_ascii=False).encode(), CandidateAudit.model_validate(
                decoded["audit"]
            )
    except (KeyError, ValueError, TypeError) as error:
        raise InvalidStoredPayloadError("candidate-proposal", "envelope", "invalid proposal envelope") from error
    return payload, None


async def attach_candidate_audit(
    connection: AsyncConnection, scope_id: str, candidate: Candidate[Any], audit: CandidateAudit
) -> None:
    """Complete a newly created version inside its original uncommitted transaction."""
    if candidate.version != 1 or candidate.audit is not None or candidate.status is not CandidateStatus.PENDING:
        raise InvalidCandidateError("audit", "Dream audit must be assigned at proposal creation")
    await connection.execute(
        update(CANDIDATE_VERSIONS_TABLE)
        .where(
            CANDIDATE_VERSIONS_TABLE.c.scope_id == scope_id,
            CANDIDATE_VERSIONS_TABLE.c.candidate_id == candidate.candidate_id,
            CANDIDATE_VERSIONS_TABLE.c.version == 1,
        )
        .values(proposal=_encode_proposal(candidate.model_copy(update={"audit": audit})))
    )


def _require_scope(scope_id: object) -> None:
    if not isinstance(scope_id, str) or not scope_id.strip() or scope_id != scope_id.strip():
        raise InvalidRepositoryArgumentError("scope_id", "must be a non-empty trimmed string")
    if len(scope_id) > MAX_SCOPE_ID_LENGTH:
        raise InvalidRepositoryArgumentError(
            "scope_id",
            f"must not exceed {MAX_SCOPE_ID_LENGTH} characters",
        )


__all__ = ["CandidateRepository", "decode_candidate_proposal_envelope"]
