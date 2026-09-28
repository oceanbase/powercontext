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

"""Current-principal authorization for durable Dream and Review evidence."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory.models import MemoryDreamCandidateProposal
from powercontext.builtin.catalog_changes.models import TagDreamTarget
from powercontext.builtin.dream.models import DreamError, DreamRecord
from powercontext.builtin.dream.service import DreamPermission
from powercontext.builtin.evidence.resolver import EvidenceReference
from powercontext.builtin.review.models import Candidate
from powercontext.server.authz.errors import AccessControlError, AccessDeniedError, AccessIdentityRequiredError
from powercontext.server.authz.models import AccessAction, MemoryEntrySelector, PrincipalRef, ResourceRef
from powercontext.server.authz.service import AccessAuditContext, AccessControlService
from powercontext.server.context import current_principal, current_request_id

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime


def principal_identity(principal: PrincipalRef) -> str:
    return json.dumps({"type": principal.type, "id": principal.id}, ensure_ascii=False, separators=(",", ":"))


def _principal(identity: str) -> PrincipalRef:
    try:
        value = json.loads(identity)
    except (ValueError, TypeError) as error:
        raise DreamError("access_revoked") from error
    if not isinstance(value, dict) or set(value) != {"type", "id"}:
        raise DreamError("access_revoked")
    try:
        return PrincipalRef(type=value["type"], id=value["id"])
    except (ValueError, TypeError, AccessControlError) as error:
        raise DreamError("access_revoked") from error


def _resource(scope_id: str, ref: EvidenceReference | None) -> ResourceRef:
    if isinstance(ref, MemoryCitation):
        return ResourceRef.artifact(
            scope_id,
            family="memory",
            artifact_id=ref.memory_ref.artifact_id,
            selector=MemoryEntrySelector(entry_id=ref.entry_id),
        )
    if isinstance(ref, ArtifactRef) and ref.family not in {"memory", "topic-memory"}:
        return ResourceRef.artifact(scope_id, family=ref.family, artifact_id=ref.artifact_id)
    # Sources belong to the Scope. A bare Memory revision is unresolved metadata,
    # never authority to expand all of its entries. Topic Memory reads are also
    # Scope-owned; Topics have no Artifact sharing profile or owner.
    return ResourceRef.scope(scope_id)


class DreamAccess:
    def __init__(self, access: AccessControlService) -> None:
        self.access = access

    def bind(self, runtime: BuiltinRuntime) -> None:
        runtime.configure_evidence_authorization(
            dream=self.authorize,
            review=self.review,
            context=self.access.defer_decision_audit,
            attest_candidate=self.attest_candidate,
            authorize_candidate=self.authorize_candidate,
            catalog_action=self.catalog_action,
            review_action=self.review_action,
        )

    async def authorize(
        self,
        scope_id: str,
        principal_id: str,
        permission: DreamPermission,
        ref: EvidenceReference | None,
    ) -> None:
        principal = _principal(principal_id)
        resource = _resource(scope_id, ref)
        if permission == "contribute":
            action = AccessAction.SCOPE_CONTRIBUTE
        elif permission in {"write", "write_tags"}:
            if isinstance(ref, ArtifactRef) and ref.family in {"prompt", "topic-memory"}:
                action, resource = AccessAction.SCOPE_ADMIN, ResourceRef.scope(scope_id)
            elif isinstance(ref, ArtifactRef) and ref.family == "memory":
                # Memory content edits authorize each selected entry separately. Whole-Artifact tags are Scope-owned.
                action = AccessAction.SCOPE_ADMIN if permission == "write_tags" else AccessAction.SCOPE_READ
                resource = ResourceRef.scope(scope_id)
            else:
                action = AccessAction.ARTIFACT_WRITE
        else:
            action = AccessAction.SCOPE_READ if resource == ResourceRef.scope(scope_id) else AccessAction.ARTIFACT_READ
        try:
            await self.access.require(
                principal,
                action,
                resource,
                context=AccessAuditContext(
                    request_id=current_request_id(),
                    transport="background",
                    operation="artifact_dreaming",
                ),
            )
        except AccessDeniedError as error:
            raise DreamError("access_revoked") from error
        except AccessControlError as error:
            raise DreamError("access_unavailable") from error

    async def review(self, scope_id: str, ref: EvidenceReference) -> None:
        principal = current_principal()
        if principal is None:
            raise AccessIdentityRequiredError
        await self.authorize(scope_id, principal_identity(principal), "read", ref)

    async def attest_candidate(
        self, connection: AsyncConnection, record: DreamRecord, candidate_id: str, family: str
    ) -> None:
        bound = DreamAccess(self.access.with_connection(connection))
        await bound.authorize(record.run.scope_id, record.principal_id, "contribute", None)
        await bound.access.attest_candidate_owner(
            scope_id=record.run.scope_id,
            candidate_id=candidate_id,
            family=family,
            proposed_owner=_principal(record.principal_id),
            target=None
            if record.run.target is None
            else ResourceRef.artifact(
                record.run.scope_id, family=record.run.target.family, artifact_id=record.run.target.artifact_id
            ),
            idempotency_key=f"dream-candidate-owner:{record.run.run_id}",
        )

    async def authorize_candidate(
        self, connection: AsyncConnection, record: DreamRecord, candidate_id: str, family: str
    ) -> None:
        bound = DreamAccess(self.access.with_connection(connection))
        await bound.authorize(record.run.scope_id, record.principal_id, "read", record.run.target)
        if record.run.operation == "revise_tags" and record.run.tag_target is not None:
            await bound.authorize(
                record.run.scope_id,
                record.principal_id,
                "read",
                record.run.tag_target.basis_ref or record.run.tag_target.basis_citation,
            )
            return
        owner = await bound.access.candidate_owner(record.run.scope_id, candidate_id)
        if owner is None or owner.proposed_owner != _principal(record.principal_id):
            raise DreamError("access_revoked")

    async def catalog_action(self, scope_id: str, action: str, target: TagDreamTarget) -> None:
        principal = current_principal()
        if principal is None:
            raise AccessIdentityRequiredError
        await self.access.require(
            principal,
            AccessAction.SCOPE_REVIEW,
            ResourceRef.scope(scope_id),
            context=AccessAuditContext(
                request_id=current_request_id(), transport="http", operation="review_catalog_change"
            ),
        )
        if action != "reject":
            await self.authorize(
                scope_id, principal_identity(principal), "write_tags", target.basis_ref or target.basis_citation
            )

    async def review_action(self, scope_id: str, action: str, candidate: Candidate[Any]) -> None:
        principal = current_principal()
        if principal is None:
            raise AccessIdentityRequiredError
        await self.access.require(
            principal,
            AccessAction.SCOPE_REVIEW,
            ResourceRef.scope(scope_id),
            context=AccessAuditContext(request_id=current_request_id(), transport="http", operation="review_candidate"),
        )
        if action == "reject":
            return
        if action == "revise":
            owner = await self.access.candidate_owner(scope_id, candidate.candidate_id)
            if owner is None or owner.proposed_owner != principal:
                raise AccessDeniedError
        if isinstance(candidate.proposal, MemoryDreamCandidateProposal):
            for change in candidate.proposal.changes:
                citation = MemoryCitation(
                    memory_ref=candidate.proposal.base,
                    entry_id=change.entry_id,
                    entry_version_id=change.entry_version_id,
                )
                await self.authorize(scope_id, principal_identity(principal), "write", citation)
        elif candidate.target is not None:
            await self.authorize(scope_id, principal_identity(principal), "write", candidate.target)
