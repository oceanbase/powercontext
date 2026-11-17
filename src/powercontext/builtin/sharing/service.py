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

"""The one owning service for a standing sharing agreement.

It writes the rule version, records an authentic acceptance, and pins one immutable
input set. It never grants: creating or accepting an agreement requires the acting
principal's current administration of the Scope it claims, and the acceptance records
the authenticated principal, never a caller-supplied actor.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from powercontext.builtin.authz.models import AccessAction, ResourceRef
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError
from powercontext.builtin.persistence.share_agreement import ShareAgreementRepository, _RulePayload
from powercontext.builtin.sharing.models import (
    ShareAcceptance,
    ShareRejectedError,
    ShareRule,
    ShareRuleDraft,
    ShareRuleNotFoundError,
    ShareRuleVersionConflictError,
    ShareSnapshot,
    ShareSnapshotDraft,
    membership_digest,
)
from powercontext.limits import MAX_SCOPE_ID_LENGTH

if TYPE_CHECKING:
    from powercontext.builtin.authz.models import PrincipalRef
    from powercontext.builtin.authz.service import AccessAuditContext, AccessControlService


def _require_scope(scope_id: object) -> str:
    if not isinstance(scope_id, str) or not scope_id.strip() or scope_id != scope_id.strip():
        raise InvalidRepositoryArgumentError("scope_id", "must be a non-empty trimmed string")
    if len(scope_id) > MAX_SCOPE_ID_LENGTH:
        raise InvalidRepositoryArgumentError("scope_id", f"must not exceed {MAX_SCOPE_ID_LENGTH} characters")
    return scope_id


class ShareAgreementService:
    """Rule versions, acceptances and pins for one deployment."""

    def __init__(
        self,
        database: AsyncDatabase,
        *,
        access: AccessControlService | None = None,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = ShareAgreementRepository()
        self.database = database
        self.access = access
        self._id_factory = id_factory
        self._clock = clock

    def _now(self) -> datetime:
        return datetime.now(UTC) if self._clock is None else self._clock()

    def _new_id(self, kind: str) -> str:
        from uuid import uuid4

        return f"{kind}_{uuid4().hex}"

    async def _require_scope_admin(
        self, principal: PrincipalRef | None, scope_id: str, context: AccessAuditContext, /
    ) -> None:
        """An agreement is administered at the Scope it belongs to, not merely read."""

        if self.access is None:
            raise ShareRejectedError("access_unavailable")
        await self.access.require(principal, AccessAction.SCOPE_ADMIN, ResourceRef.scope(scope_id), context=context)

    async def create(
        self,
        scope_id: str,
        draft: ShareRuleDraft,
        /,
        *,
        principal: PrincipalRef | None,
        context: AccessAuditContext,
        rule_id: str | None = None,
        expected_version: int = 0,
        acceptances: tuple[ShareAcceptance, ...] = (),
    ) -> ShareRule:
        """Write one immutable rule version as the Scope's new head.

        ``expected_version`` is the head the caller saw, so two administrators updating
        the same rule serialize instead of silently overwriting each other.
        """

        scope_id = _require_scope(scope_id)
        await self._require_scope_admin(principal, scope_id, context)
        terms_digest = draft.terms_digest()
        payload = _RulePayload(selection=draft.selection, terms=draft.terms)
        async with self.database.transaction() as connection:
            if rule_id is None:
                rule_id = self._new_id("rule")
                head = await self.repository.head(connection, scope_id, rule_id, for_update=True)
                if head is not None:
                    raise ShareRuleVersionConflictError(rule_id, expected_version, int(head["version"]))
                version = 1
                await self.repository.insert_head(connection, scope_id, rule_id, version, self._now())
            else:
                head = await self.repository.head(connection, scope_id, rule_id, for_update=True)
                if head is None:
                    raise ShareRuleNotFoundError(rule_id)
                current = int(head["version"])
                if current != expected_version:
                    raise ShareRuleVersionConflictError(rule_id, expected_version, current)
                version = current + 1
                await self.repository.insert_version(
                    connection, scope_id, rule_id, version, payload, terms_digest, self._now()
                )
                if not await self.repository.advance_head(connection, scope_id, rule_id, current, version):
                    raise ShareRuleVersionConflictError(rule_id, expected_version, current)
            if rule_id is not None and version == 1:
                await self.repository.insert_version(
                    connection, scope_id, rule_id, version, payload, terms_digest, self._now()
                )
            for acceptance in acceptances:
                await self._require_acceptance_authority(principal, acceptance, context)
                await self.repository.insert_acceptance(connection, scope_id, rule_id, version, acceptance)
            return await self.repository.build_rule(connection, scope_id, rule_id, version)

    async def _require_acceptance_authority(
        self, principal: PrincipalRef | None, acceptance: ShareAcceptance, context: AccessAuditContext, /
    ) -> None:
        """An acceptor administers exactly the Scopes it claims, and is who it says."""

        if self.access is None:
            raise ShareRejectedError("access_unavailable")
        if not acceptance.covered_scope_ids:
            raise ShareRejectedError("shared_use_audience")
        for covered in acceptance.covered_scope_ids:
            await self.access.require(principal, AccessAction.SCOPE_ADMIN, ResourceRef.scope(covered), context=context)

    async def accept(
        self,
        scope_id: str,
        rule_id: str,
        version: int,
        /,
        *,
        principal: PrincipalRef | None,
        actor: str,
        side: str,
        covered_scope_ids: tuple[str, ...],
        context: AccessAuditContext,
    ) -> ShareRule:
        """Record one authentic acceptance of an existing rule version."""

        scope_id = _require_scope(scope_id)
        async with self.database.transaction() as connection:
            rule = await self.repository.build_rule(connection, scope_id, rule_id, version)
            acceptance = ShareAcceptance(
                acceptance_id=self._new_id("acc"),
                actor=actor,
                side="origin" if side == "origin" else "audience",
                covered_scope_ids=tuple(sorted(set(covered_scope_ids))),
                terms_digest=rule.terms_digest,
                accepted_at=self._now(),
            )
            await self._require_acceptance_authority(principal, acceptance, context)
            await self.repository.insert_acceptance(connection, scope_id, rule_id, version, acceptance)
            return await self.repository.build_rule(connection, scope_id, rule_id, version)

    async def get(self, scope_id: str, rule_id: str, /, *, version: int | None = None) -> ShareRule:
        scope_id = _require_scope(scope_id)
        async with self.database.transaction() as connection:
            head = await self.repository.head(connection, scope_id, rule_id)
            if head is None:
                raise ShareRuleNotFoundError(rule_id)
            return await self.repository.build_rule(
                connection, scope_id, rule_id, int(head["version"]) if version is None else version
            )

    async def pin(
        self,
        scope_id: str,
        draft: ShareSnapshotDraft,
        /,
        *,
        principal: PrincipalRef | None,
        context: AccessAuditContext,
        scope_versions: tuple[tuple[str, int], ...],
        snapshot_id: str | None = None,
    ) -> ShareSnapshot:
        """Write one immutable pin. Claiming it is a later bounded step.

        A repeated notification key returns the pin already recorded for it, so a
        duplicate notification never creates a second evaluation of the same material.
        """

        scope_id = _require_scope(scope_id)
        async with self.database.transaction() as connection:
            rule = await self.repository.build_rule(connection, scope_id, draft.rule_id, draft.rule_version)
            if draft.terms_digest != rule.terms_digest:
                # The agreement moved after this pin was prepared.
                from powercontext.builtin.sharing.models import ShareTermsChangedError

                raise ShareTermsChangedError()
            if draft.membership_digest != membership_digest(scope_versions):
                from powercontext.builtin.sharing.models import ShareTermsChangedError

                raise ShareTermsChangedError()
            if draft.notification_key is not None:
                existing = await self.repository.snapshot_by_notification(
                    connection, scope_id, draft.rule_id, draft.notification_key
                )
                if existing is not None:
                    return self.repository.decode_snapshot(existing)
            snapshot = ShareSnapshot(
                scope_id=scope_id,
                snapshot_id=snapshot_id or self._new_id("snap"),
                rule_id=draft.rule_id,
                rule_version=draft.rule_version,
                terms_digest=draft.terms_digest,
                membership_digest=draft.membership_digest,
                inputs_digest=draft.inputs_digest(),
                inputs=draft.inputs,
                notification_key=draft.notification_key,
                created_at=self._now(),
            )
            del principal, context  # pinning itself confers no authority change
            await self.repository.insert_snapshot(connection, snapshot)
            return snapshot

    async def snapshot(self, scope_id: str, snapshot_id: str, /) -> ShareSnapshot:
        scope_id = _require_scope(scope_id)
        async with self.database.transaction() as connection:
            row = await self.repository.snapshot_row(connection, scope_id, snapshot_id)
            if row is None:
                from powercontext.builtin.sharing.models import ShareError

                raise ShareError("share_snapshot_not_found")
            return self.repository.decode_snapshot(row)


__all__ = ["ShareAgreementService"]
