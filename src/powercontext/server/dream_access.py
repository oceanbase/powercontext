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

"""Bind Dream and Review authorization to the current Server request principal."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import override

from powercontext.builtin.authz.dream import DreamAuthorization, evidence_resource, principal_identity
from powercontext.builtin.authz.errors import AccessControlError, AccessDeniedError, AccessIdentityRequiredError
from powercontext.builtin.authz.models import AccessAction, ResourceRef
from powercontext.builtin.authz.service import AccessAuditContext
from powercontext.builtin.catalog_changes.models import TagDreamTarget
from powercontext.builtin.dream.models import DreamError
from powercontext.builtin.evidence.resolver import EvidenceReference
from powercontext.builtin.review.models import Candidate
from powercontext.server.context import current_principal, current_request_id

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime


class DreamAccess(DreamAuthorization):
    """Add request-principal Review checks to Dream authorization."""

    def bind(self, runtime: BuiltinRuntime) -> None:
        runtime.configure_evidence_authorization(
            dream=self.authorize,
            review=self.review,
            review_many=self.review_many,
            context=self.access.defer_decision_audit,
            attest_candidate=self.attest_candidate,
            authorize_candidate=self.authorize_candidate,
            catalog_action=self.catalog_action,
            review_action=self.review_action,
        )

    @override
    def _request_id(self) -> str | None:
        return current_request_id()

    async def review(self, scope_id: str, ref: EvidenceReference) -> None:
        principal = current_principal()
        if principal is None:
            raise AccessIdentityRequiredError
        await self.authorize(scope_id, principal_identity(principal), "read", ref)

    async def review_many(self, scope_id, refs, connection):
        if not refs:
            return
        access = self.access.for_read(connection)
        if not access.provider_capabilities.multi_requirement_check:
            for ref in refs:
                await self.review(scope_id, ref)
            return
        principal = current_principal()
        if principal is None:
            raise AccessIdentityRequiredError
        checks = {}
        for ref in refs:
            resource = evidence_resource(scope_id, ref)
            action = AccessAction.SCOPE_READ if resource == ResourceRef.scope(scope_id) else AccessAction.ARTIFACT_READ
            checks[action, resource.key] = (action, resource)
        try:
            await access.require_all(
                principal,
                tuple(checks.values()),
                context=AccessAuditContext(
                    request_id=current_request_id(), transport="background", operation="artifact_dreaming"
                ),
            )
        except AccessDeniedError as error:
            raise DreamError("access_revoked") from error
        except AccessControlError as error:
            raise DreamError("access_unavailable") from error

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
            await self.authorize(scope_id, principal_identity(principal), "write_tags", target.basis_ref)

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
        if candidate.target is not None:
            await self.authorize(scope_id, principal_identity(principal), "write", candidate.target)
