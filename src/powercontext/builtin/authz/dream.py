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

"""Authorize Dream evidence and Candidate ownership against Access Control."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.authz.errors import AccessControlError, AccessDeniedError
from powercontext.builtin.authz.models import AccessAction, PrincipalRef, ResourceRef
from powercontext.builtin.authz.service import AccessAuditContext, AccessControlService
from powercontext.builtin.dream.models import DreamError

if TYPE_CHECKING:
    # Annotation-only: importing the Dream service here would load the Runtime package.
    from powercontext.builtin.dream.models import DreamRecord
    from powercontext.builtin.dream.service import DreamPermission
    from powercontext.builtin.evidence.resolver import EvidenceReference


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


def evidence_resource(scope_id: str, ref: EvidenceReference | None) -> ResourceRef:
    if isinstance(ref, ArtifactRef) and ref.family != "topic-memory":
        return ResourceRef.artifact(scope_id, family=ref.family, artifact_id=ref.artifact_id)
    # Sources and Topic Memory reads belong to the Scope.
    return ResourceRef.scope(scope_id)


class DreamAuthorization:
    """Check a recorded Dream principal; background work carries no request identity."""

    def __init__(self, access: AccessControlService) -> None:
        self.access = access

    def _request_id(self) -> str | None:
        return None

    async def authorize(
        self,
        scope_id: str,
        principal_id: str,
        permission: DreamPermission,
        ref: EvidenceReference | None,
    ) -> None:
        principal = _principal(principal_id)
        resource = evidence_resource(scope_id, ref)
        if permission == "contribute":
            action = AccessAction.SCOPE_CONTRIBUTE
        elif permission in {"write", "write_tags"}:
            if isinstance(ref, ArtifactRef) and ref.family in {"prompt", "topic-memory"}:
                action, resource = AccessAction.SCOPE_ADMIN, ResourceRef.scope(scope_id)
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
                    request_id=self._request_id(),
                    transport="background",
                    operation="artifact_dreaming",
                ),
            )
        except AccessDeniedError as error:
            raise DreamError("access_revoked") from error
        except AccessControlError as error:
            raise DreamError("access_unavailable") from error

    async def attest_candidate(
        self, connection: AsyncConnection, record: DreamRecord, candidate_id: str, family: str
    ) -> None:
        bound = type(self)(self.access.with_connection(connection))
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
        bound = type(self)(self.access.with_connection(connection))
        await bound.authorize(record.run.scope_id, record.principal_id, "read", record.run.target)
        if record.run.operation == "revise_tags" and record.run.tag_target is not None:
            await bound.authorize(
                record.run.scope_id,
                record.principal_id,
                "read",
                record.run.tag_target.basis_ref,
            )
            return
        owner = await bound.access.candidate_owner(record.run.scope_id, candidate_id)
        if owner is None or owner.proposed_owner != _principal(record.principal_id):
            raise DreamError("access_revoked")
