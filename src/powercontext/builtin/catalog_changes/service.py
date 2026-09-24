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

"""Review Tag changes with an ETag and an independently checked content basis."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory.models import Memory
from powercontext.builtin.catalog_changes.models import (
    CatalogCandidateEvidence,
    CatalogCandidatePage,
    CatalogChangeCandidate,
    CatalogChangeProposal,
    TagDreamTarget,
)
from powercontext.builtin.evidence.models import EvidenceResolutionError
from powercontext.builtin.evidence.resolver import AuthorizationContext, EvidenceAuthorizer, EvidenceResolver
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.candidates import proposal_digest
from powercontext.builtin.persistence.catalog_changes import CatalogCandidateRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE
from powercontext.builtin.persistence.tags import RelationalTagService
from powercontext.builtin.records import InvalidCursorError
from powercontext.builtin.review.errors import (
    ArtifactTargetConflictError,
    CandidateConflictError,
    CandidateTerminalError,
    InvalidCandidateError,
)
from powercontext.builtin.review.models import CandidateAudit
from powercontext.builtin.tags import ArtifactTagSet, TagPreconditionError
from powercontext.sources import SourceRef


class CatalogChangeService:
    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        artifacts: ArtifactRepository,
        evidence: EvidenceResolver,
        id_factory: Callable[[str], str],
        tags: RelationalTagService | None = None,
        connection: AsyncConnection | None = None,
        authorization_context: Callable[[], AbstractAsyncContextManager[None]] = nullcontext,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._artifacts = artifacts
        self._evidence = evidence
        self._id_factory = id_factory
        self._tags = tags or RelationalTagService(database, artifacts)
        self._repository = CatalogCandidateRepository()
        self._bound_connection = connection
        self._authorization_context = authorization_context
        self.authorize_action: Callable[[str, str, TagDreamTarget], Awaitable[None]] | None = None

    def configure_authorization(self, authorize: EvidenceAuthorizer, context: AuthorizationContext) -> None:
        self._evidence.authorize = authorize
        self._authorization_context = context

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[AsyncConnection]:
        async with self._authorization_context(), self._database.connection(self._bound_connection) as connection:
            yield connection

    async def _authorize_target(self, target: TagDreamTarget) -> None:
        reference = target.basis_ref if target.basis_ref is not None else target.basis_citation
        if reference is None:
            raise InvalidCandidateError("basis", "missing content basis")
        if self._evidence.authorize is not None:
            await self._evidence.authorize(reference)

    async def _basis(self, connection: AsyncConnection, target: TagDreamTarget) -> None:
        await self._authorize_target(target)
        artifact = await self._artifacts.latest(
            connection, self._scope_id, target.target.family, target.target.artifact_id, for_update=True
        )
        table = ARTIFACT_HEADS_TABLE
        lifecycle = await connection.scalar(
            select(table.c.lifecycle_state)
            .where(
                table.c.scope_id == self._scope_id,
                table.c.family == target.target.family,
                table.c.artifact_id == target.target.artifact_id,
            )
            .with_for_update()
        )
        if lifecycle != "active":
            raise InvalidCandidateError("target", "target_inactive")
        if target.basis_ref is not None:
            if artifact.as_ref() != target.basis_ref:
                raise ArtifactTargetConflictError(target.basis_ref, artifact.as_ref())
        else:
            citation = target.basis_citation
            if citation is None:
                raise InvalidCandidateError("basis", "missing entry basis")
            if not isinstance(artifact, Memory):
                raise InvalidCandidateError("target", "expected a Memory entry")
            # Validate the old exact manifest and entry version as well as today's
            # entry. Unrelated changes elsewhere in this Memory do not invalidate it.
            await self._evidence.memory_reader(connection, citation)
            entry = next(
                (entry for entry in artifact.content.manifest.entries if entry.entry_id == citation.entry_id), None
            )
            if entry is None or entry.state != "active" or entry.entry_version_id != citation.entry_version_id:
                raise ArtifactTargetConflictError(citation.memory_ref, artifact.as_ref())

    async def _lock_target(self, connection: AsyncConnection, target: TagDreamTarget) -> None:
        # Profile's automatic and review writers serialize on Policy first.
        # Other Families lock their owning head before evidence and Candidate.
        if target.target.family == "profile":
            from powercontext.builtin.persistence.profile import ProfilePolicyRepository

            await ProfilePolicyRepository().get(connection, self._scope_id, for_update=True)
        await self._tags.lock_target(connection, self._scope_id, target.target)

    async def _evidence_and_lock(self, connection: AsyncConnection, candidate: CatalogChangeCandidate) -> None:
        await self._lock_target(connection, candidate.proposal)
        citations = candidate.memory_citations
        basis = candidate.proposal.basis_citation
        if basis is not None and basis not in citations:
            citations = (*citations, basis)
        supporting_artifacts = tuple(ref for ref in candidate.artifacts if ref != candidate.proposal.basis_ref)
        supporting_citations = tuple(citation for citation in citations if citation != basis)
        if not candidate.sources and not supporting_artifacts and not supporting_citations:
            raise InvalidCandidateError("evidence", "Tag changes require evidence beyond the content basis")
        await self._evidence.validate(
            connection,
            sources=candidate.sources,
            artifacts=supporting_artifacts,
            memory_citations=supporting_citations,
        )
        await self._basis(connection, candidate.proposal)

    async def inspect_target(self, target: TagDreamTarget) -> ArtifactTagSet:
        """Validate both bases before accepting or generating a Tag Dream."""
        async with self._connection() as connection:
            await self._lock_target(connection, target)
            await self._basis(connection, target)
            current = await self._tags.read_in_transaction(connection, self._scope_id, target.target)
            if current.etag != target.expected_etag:
                raise TagPreconditionError
            return current

    async def propose(
        self,
        proposal: CatalogChangeProposal,
        *,
        reason: str,
        sources: tuple[SourceRef, ...] = (),
        artifacts: tuple[ArtifactRef, ...] = (),
        memory_citations: tuple[MemoryCitation, ...] = (),
        dream_run_id: str | None = None,
        candidate_id: str | None = None,
        audit: CandidateAudit | None = None,
    ) -> CatalogChangeCandidate:
        candidate = CatalogChangeCandidate(
            candidate_id=candidate_id or self._id_factory("candidate"),
            version=1,
            proposal=proposal,
            sources=sources,
            artifacts=artifacts,
            memory_citations=memory_citations,
            reason=reason,
            dream_run_id=dream_run_id,
            origin="dream" if dream_run_id else "manual",
            audit=audit,
        )
        async with self._connection() as connection:
            await self._evidence_and_lock(connection, candidate)
            current = await self._tags.read_in_transaction(connection, self._scope_id, proposal.target)
            if current.etag != proposal.expected_etag or current.tags != proposal.before_tags:
                raise TagPreconditionError
            return await self._repository.create(connection, self._scope_id, candidate)

    async def get(self, candidate_id: str, *, current: bool = False) -> CatalogChangeCandidate:
        async with self._connection() as connection:
            candidate = await self._repository.get(connection, self._scope_id, candidate_id, current=current)
            await self._authorize_target(candidate.proposal)
            return candidate

    async def list(
        self,
        *,
        status: Literal["pending", "approved", "rejected"] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CatalogCandidatePage:
        if not 1 <= limit <= 100:
            raise InvalidCandidateError("limit", "expected 1 to 100")
        if cursor is not None and (not cursor or len(cursor) > 128):
            raise InvalidCursorError
        async with self._connection() as connection:
            candidates = await self._repository.list(
                connection, self._scope_id, status=status, after=cursor or "", limit=limit + 1
            )
            for candidate in candidates[:limit]:
                await self._authorize_target(candidate.proposal)
            return CatalogCandidatePage(
                candidates=candidates[:limit],
                next_cursor=candidates[limit - 1].candidate_id if len(candidates) > limit else None,
            )

    async def history(self, candidate_id: str) -> tuple[CatalogChangeCandidate, ...]:
        async with self._connection() as connection:
            candidates = await self._repository.history(connection, self._scope_id, candidate_id)
            await self._authorize_target(candidates[-1].proposal)
            return candidates

    async def revise(
        self,
        candidate_id: str,
        expected_version: int,
        *,
        after_tags: tuple[str, ...],
        reason: str,
        proposal: CatalogChangeProposal | None = None,
        sources: tuple[SourceRef, ...] | None = None,
        artifacts: tuple[ArtifactRef, ...] | None = None,
        memory_citations: tuple[MemoryCitation, ...] | None = None,
    ) -> CatalogChangeCandidate:
        async with self._connection() as connection:
            preview = await self._repository.get(connection, self._scope_id, candidate_id)
            if self.authorize_action is not None:
                await self.authorize_action(self._scope_id, "revise", preview.proposal)
            expected = CatalogChangeProposal.model_validate({**preview.proposal.model_dump(), "after_tags": after_tags})
            if proposal is not None and proposal != expected:
                raise InvalidCandidateError("proposal", "Tag revision cannot change its original target or baseline")
            proposal = expected
            revised = CatalogChangeCandidate.model_validate({
                **preview.model_dump(),
                "version": expected_version + 1,
                "proposal": proposal,
                "sources": preview.sources if sources is None else sources,
                "artifacts": preview.artifacts if artifacts is None else artifacts,
                "memory_citations": preview.memory_citations if memory_citations is None else memory_citations,
                "reason": reason,
            })
            if revised.audit is not None:
                revised = revised.model_copy(
                    update={
                        "audit": revised.audit.model_copy(
                            update={
                                "proposal_digest": proposal_digest(revised.proposal),
                                "evidence_manifest_ref": f"candidate:{candidate_id}:{revised.version}",
                                "proposal_fingerprint": None,
                            }
                        )
                    }
                )
            await self._evidence_and_lock(connection, revised)
            current = await self._repository.lock_pending(connection, self._scope_id, candidate_id, expected_version)
            if current != preview:
                raise CandidateConflictError(candidate_id, expected_version, current.version)
            return await self._repository.save(connection, self._scope_id, current, revised)

    async def approve(self, candidate_id: str, expected_version: int) -> CatalogChangeCandidate:
        try:
            return await self._approve_once(candidate_id, expected_version)
        except (CandidateTerminalError, TagPreconditionError):
            async with self._connection() as connection:
                current = await self._repository.get(connection, self._scope_id, candidate_id, current=True)
                if current.status != "approved" or current.version != expected_version:
                    raise
                if self.authorize_action is not None:
                    await self.authorize_action(self._scope_id, "approve", current.proposal)
                await self._authorize_target(current.proposal)
                return current

    async def _approve_once(self, candidate_id: str, expected_version: int) -> CatalogChangeCandidate:
        async with self._connection() as connection:
            preview = await self._repository.get(connection, self._scope_id, candidate_id)
            if self.authorize_action is not None:
                await self.authorize_action(self._scope_id, "approve", preview.proposal)
            if preview.status == "approved" and preview.version == expected_version:
                await self._authorize_target(preview.proposal)
                return preview
            await self._evidence_and_lock(connection, preview)
            current = await self._repository.lock_pending(connection, self._scope_id, candidate_id, expected_version)
            if current != preview:
                raise CandidateConflictError(candidate_id, expected_version, current.version)
            await self._validate_audit(connection, current)
            result = await self._tags.replace_in_transaction(
                connection,
                self._scope_id,
                current.proposal.target,
                current.proposal.after_tags,
                expected_etag=current.proposal.expected_etag,
            )
            approved = CatalogChangeCandidate.model_validate({
                **current.model_dump(),
                "status": "approved",
                "result": result,
            })
            return await self._repository.save(connection, self._scope_id, current, approved)

    async def _validate_audit(self, connection: AsyncConnection, candidate: CatalogChangeCandidate) -> None:
        from powercontext.builtin.dream.bindings import DREAM_SPECS
        from powercontext.builtin.dream.provenance import validation_policy_digest
        from powercontext.builtin.persistence.dream import DreamRepository

        audit = candidate.audit
        if audit is None:
            if candidate.origin == "dream":
                raise InvalidCandidateError("audit", "Dream Catalog candidate has no provenance")
            return
        spec = DREAM_SPECS.get(audit.operation)
        if (
            audit.operation != "revise_tags"
            or audit.dream_run_id != candidate.dream_run_id
            or spec is None
            or spec.spec_version != audit.spec_version
            or audit.proposal_digest != proposal_digest(candidate.proposal)
        ):
            raise InvalidCandidateError("audit", "Catalog candidate content or operation contract changed")
        record = await DreamRepository().get(connection, self._scope_id, audit.dream_run_id)
        baseline = TagDreamTarget.model_validate({
            key: value
            for key, value in candidate.proposal.model_dump().items()
            if key not in {"before_tags", "after_tags"}
        })
        reference = record.run.candidate
        if (
            record.run.operation != "revise_tags"
            or record.run.tag_target != baseline
            or reference is None
            or reference.kind != "tag"
            or reference.candidate_id != candidate.candidate_id
            or reference.version > candidate.version
            or validation_policy_digest(record) != audit.validation_policy_digest
        ):
            raise InvalidCandidateError("audit", "Catalog candidate policy or Dream origin changed")

    async def reject(self, candidate_id: str, expected_version: int, reason: str) -> CatalogChangeCandidate:
        async with self._connection() as connection:
            preview = await self._repository.get(connection, self._scope_id, candidate_id)
            if self.authorize_action is not None:
                await self.authorize_action(self._scope_id, "reject", preview.proposal)
            await self._authorize_target(preview.proposal)
            current = await self._repository.lock_pending(connection, self._scope_id, candidate_id, expected_version)
            rejected = CatalogChangeCandidate.model_validate({
                **current.model_dump(),
                "status": "rejected",
                "decision_reason": reason,
            })
            return await self._repository.save(connection, self._scope_id, current, rejected)

    async def evidence(self, candidate_id: str) -> CatalogCandidateEvidence:
        async with self._connection() as connection:
            candidate = await self._repository.get(connection, self._scope_id, candidate_id)
            await self._authorize_target(candidate.proposal)
            try:
                resolved = await self._evidence.resolve(
                    connection,
                    sources=candidate.sources,
                    artifacts=candidate.artifacts,
                    memory_citations=candidate.memory_citations,
                )
            except EvidenceResolutionError as error:
                return CatalogCandidateEvidence(
                    candidate_id=candidate_id, version=candidate.version, unavailable=error.code
                )
            return CatalogCandidateEvidence(candidate_id=candidate_id, version=candidate.version, resolved=resolved)
