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

Pinning is a checked mutation, not a trusted one. It requires current target
contribution, the exact current rule version and agreement, an authentic origin-side
acceptance covering every pinned origin whose administration is still current, and the
content actually read now at that origin. Nothing the caller declares — a Scope version,
a structure digest, an input digest — is treated as authorization; those values are
compared against what the owning stores currently hold.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from powercontext.builtin.authz.errors import AccessInvalidRequestError
from powercontext.builtin.authz.models import AccessAction, ResourceRef
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError
from powercontext.builtin.persistence.share_agreement import ShareAgreementRepository, _RulePayload
from powercontext.builtin.scope.application import ScopeApplication
from powercontext.builtin.scope.models import ScopeDescriptor, ScopeSelection
from powercontext.builtin.scope.repository import ScopeRepository
from powercontext.builtin.sharing.models import (
    ShareAcceptance,
    ShareInput,
    ShareRejectedError,
    ShareRule,
    ShareRuleDraft,
    ShareRuleNotFoundError,
    ShareRuleVersionConflictError,
    ShareSnapshot,
    ShareSnapshotDraft,
    ShareTermsChangedError,
    membership_digest,
)
from powercontext.limits import MAX_SCOPE_ID_LENGTH

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

    from powercontext.builtin.authz.models import PrincipalRef
    from powercontext.builtin.authz.service import AccessAuditContext, AccessControlService
    from powercontext.builtin.persistence.artifacts import ArtifactRepository
    from powercontext.builtin.persistence.sources import SourceRepository

_SIDES = frozenset({"origin", "audience"})


def _require_scope(scope_id: object) -> str:
    if not isinstance(scope_id, str) or not scope_id.strip() or scope_id != scope_id.strip():
        raise InvalidRepositoryArgumentError("scope_id", "must be a non-empty trimmed string")
    if len(scope_id) > MAX_SCOPE_ID_LENGTH:
        raise InvalidRepositoryArgumentError("scope_id", f"must not exceed {MAX_SCOPE_ID_LENGTH} characters")
    return scope_id


def _actor_for(principal: PrincipalRef) -> str:
    """The exact recorded identity of an authenticated principal."""

    return json.dumps({"type": principal.type, "id": principal.id}, separators=(",", ":"))


def _principal_from_actor(actor: str) -> PrincipalRef:
    """Decode a recorded acceptor identity back into an authorization subject.

    Only a value this service wrote decodes; a malformed record raises instead of
    silently matching some other principal.
    """

    from powercontext.builtin.authz.models import PrincipalRef as _PrincipalRef

    try:
        decoded = json.loads(actor)
    except ValueError as error:
        raise ShareRejectedError("shared_use_acceptor_invalid") from error
    if not isinstance(decoded, dict) or set(decoded) != {"type", "id"}:
        raise ShareRejectedError("shared_use_acceptor_invalid")
    principal_type, principal_id = decoded["type"], decoded["id"]
    if not isinstance(principal_type, str) or not isinstance(principal_id, str):
        raise ShareRejectedError("shared_use_acceptor_invalid")
    try:
        return _PrincipalRef(type=principal_type, id=principal_id)
    except AccessInvalidRequestError as error:
        raise ShareRejectedError("shared_use_acceptor_invalid") from error


class ShareAgreementService:
    """Rule versions, acceptances and pins for one deployment."""

    def __init__(
        self,
        database: AsyncDatabase,
        *,
        access: AccessControlService | None = None,
        scopes: ScopeRepository | None = None,
        sources: SourceRepository | None = None,
        artifacts: ArtifactRepository | None = None,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = ShareAgreementRepository()
        self.database = database
        self.access = access
        # The owning readers of the structures a pin must verify. When one is absent the
        # pin refuses the input it cannot check instead of recording an unverified claim.
        self.scopes = ScopeRepository() if scopes is None else scopes
        # The bounded membership owner. Used for a structural declared range so the sharing
        # service never walks Scope children itself.
        self._range_owner = ScopeApplication(database)
        self.sources = sources
        self.artifacts = artifacts
        self._id_factory = id_factory
        self._clock = clock

    def _now(self) -> datetime:
        return datetime.now(UTC) if self._clock is None else self._clock()

    def _new_id(self, kind: str) -> str:
        from uuid import uuid4

        return f"{kind}_{uuid4().hex}"

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
    ) -> ShareRule:
        """Write one immutable rule version as the Scope's new head.

        ``expected_version`` is the head the caller saw, so two administrators updating
        the same rule serialize instead of silently overwriting each other. Consent is
        never accepted here: an acceptance is only ever recorded by :meth:`accept` from
        the authenticated principal's own authorized call.
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
            return await self.repository.build_rule(connection, scope_id, rule_id, version)

    async def accept(
        self,
        scope_id: str,
        rule_id: str,
        version: int,
        /,
        *,
        principal: PrincipalRef | None,
        side: str,
        covered_scope_ids: tuple[str, ...],
        context: AccessAuditContext,
    ) -> ShareRule:
        """Record one authentic acceptance of an existing rule version.

        The recorded identity is derived from ``principal`` after authorization, so no
        parameter lets a caller name another acceptor. The two sides are addressed
        differently, and the difference is the contract:

        - ``audience`` consent covers exactly the rule-owning Scope. It is independent of
          the origin selector, so an origin-only range that excludes the target is the
          normal shape; the target is never forced into origin membership. The acceptor
          must currently administer that target.
        - ``origin`` consent covers only Scopes the rule actually declares, and the
          acceptor must currently administer each of them exactly.
        """

        scope_id = _require_scope(scope_id)
        claimed = tuple(sorted(set(covered_scope_ids)))
        if side not in _SIDES:
            raise ShareRejectedError("shared_use_side")
        if not claimed:
            raise ShareRejectedError("shared_use_audience")
        async with self.database.transaction() as connection:
            rule = await self.repository.build_rule(connection, scope_id, rule_id, version)
            if rule.status != "active":
                raise ShareTermsChangedError()
            if side == "audience":
                # Consent to receive is consent of the rule-owning target itself, and it
                # does not depend on whether the selector happens to include that Scope.
                if claimed != (scope_id,):
                    raise ShareRejectedError("shared_use_audience")
            else:
                declared = {scope.scope_id for scope in await self._declared_scopes(connection, rule.selection)}
                uncovered = next((covered for covered in claimed if covered not in declared), None)
                if uncovered is not None:
                    raise ShareRejectedError("shared_use_audience")
            actor = await self._require_acceptance_authority(principal, side, claimed, context)
            acceptance = ShareAcceptance(
                acceptance_id=self._new_id("acc"),
                actor=actor,
                side="origin" if side == "origin" else "audience",
                covered_scope_ids=claimed,
                terms_digest=rule.terms_digest,
                accepted_at=self._now(),
            )
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
        snapshot_id: str | None = None,
    ) -> ShareSnapshot:
        """Write one immutable pin as a checked caller.

        Before anything is recorded this verifies, against the owning stores: current
        target contribution; that the requested rule version is the *exact* current
        version and still active; that the caller's terms digest matches the recorded
        agreement; that the declared structure's current Scope versions reproduce the
        membership digest; that every pinned origin is declared by the rule and covered
        by an authentic origin-side acceptance whose acceptor still administers it; that
        the caller may currently read each origin; and that each recorded input digest is
        the digest of the content read there now.

        A caller-supplied digest is evidence to compare, never authorization. A repeated
        notification key returns the pin already recorded for it, so a duplicate
        notification never creates a second evaluation of the same material.
        """

        scope_id = _require_scope(scope_id)
        if self.access is None:
            raise ShareRejectedError("access_unavailable")
        if principal is None:
            raise ShareRejectedError("access_identity_required")
        await self.access.require(
            principal, AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id), context=context
        )
        async with self.database.transaction() as connection:
            if draft.notification_key is not None:
                existing = await self.repository.snapshot_by_notification(
                    connection, scope_id, draft.rule_id, draft.notification_key
                )
                if existing is not None:
                    # A duplicate notification returns the pin already recorded for it,
                    # so it never creates a second evaluation. The same key with different
                    # material is a collision, not a replay, and is refused.
                    recorded = self.repository.decode_snapshot(existing)
                    if (
                        recorded.rule_version != draft.rule_version
                        or recorded.terms_digest != draft.terms_digest
                        or recorded.membership_digest != draft.membership_digest
                        or recorded.inputs_digest != draft.inputs_digest()
                    ):
                        raise ShareRejectedError("shared_use_notification_conflict")
                    return recorded
            rule = await self._require_current_rule(connection, scope_id, draft)
            declared = await self._declared_scopes(connection, rule.selection)
            self._require_declared_structure(draft, declared)
            await self._require_pin_agreement(connection, rule, declared, draft, principal, context)
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

    async def _require_scope_admin(
        self, principal: PrincipalRef | None, scope_id: str, context: AccessAuditContext, /
    ) -> None:
        """An agreement is administered at the Scope it belongs to, not merely read."""

        if self.access is None:
            raise ShareRejectedError("access_unavailable")
        await self.access.require(principal, AccessAction.SCOPE_ADMIN, ResourceRef.scope(scope_id), context=context)

    async def _require_acceptance_authority(
        self,
        principal: PrincipalRef | None,
        side: str,
        covered_scope_ids: tuple[str, ...],
        context: AccessAuditContext,
        /,
    ) -> str:
        """Authorize an acceptance and return the identity that is actually recorded.

        The recorded acceptor is the authenticated principal, so an authorized caller
        cannot record somebody else's consent, and only the Scopes it administers may be
        claimed. Nothing here reads a caller-supplied actor.
        """

        if self.access is None:
            raise ShareRejectedError("access_unavailable")
        if side not in _SIDES:
            raise ShareRejectedError("shared_use_side")
        if not covered_scope_ids:
            raise ShareRejectedError("shared_use_audience")
        if principal is None:
            raise ShareRejectedError("access_identity_required")
        for covered in covered_scope_ids:
            await self.access.require(principal, AccessAction.SCOPE_ADMIN, ResourceRef.scope(covered), context=context)
        return _actor_for(principal)

    async def _declared_scopes(
        self, connection: AsyncConnection, selection: ScopeSelection, /
    ) -> tuple[ScopeDescriptor, ...]:
        """Resolve the rule's own declared Scope range through P1's bounded owner.

        Complete or refuse, on every mode: the owner raises instead of truncating a subtree or
        scanning the whole collection, so a pin is never validated against part of its own
        range and an unbounded ``all`` selection is not treated as a bounded agreement. A pin
        compares against this, never against versions a caller reports.
        """

        from powercontext.builtin.scope.errors import ScopeRangeLimitError

        if selection.mode == "all":
            # An unbounded collection is not a bounded agreement; it is unsupported, not
            # merely over budget, and it never falls back to a whole-collection scan.
            raise ShareRejectedError("shared_use_unsupported")
        try:
            resolved = await self._range_owner.resolve_range_on(connection, selection)
        except ScopeRangeLimitError as error:
            raise ShareRejectedError("shared_use_scope_range") from error
        return tuple(resolved.scopes)

    async def _require_current_rule(
        self, connection: AsyncConnection, scope_id: str, draft: ShareSnapshotDraft, /
    ) -> ShareRule:
        """Refuse a pin unless the named version is exactly the current active one."""

        head = await self.repository.head(connection, scope_id, draft.rule_id, for_update=True)
        if head is None:
            raise ShareRuleNotFoundError(draft.rule_id)
        # Exact equality, not "at least": a superseded version is not the current
        # agreement, and pinning it would record material no acceptance covers today.
        if str(head["status"]) != "active" or int(head["version"]) != draft.rule_version:
            raise ShareTermsChangedError()
        rule = await self.repository.build_rule(connection, scope_id, draft.rule_id, draft.rule_version)
        if draft.terms_digest != rule.terms_digest:
            raise ShareTermsChangedError()
        return rule

    def _require_declared_structure(self, draft: ShareSnapshotDraft, declared: tuple[ScopeDescriptor, ...], /) -> None:
        """The pinned membership must be the structure the owning store reports now."""

        versions = tuple(sorted((scope.scope_id, int(scope.version)) for scope in declared))
        if draft.membership_digest != membership_digest(versions):
            raise ShareTermsChangedError()

    async def _require_pin_agreement(
        self,
        connection: AsyncConnection,
        rule: ShareRule,
        declared: tuple[ScopeDescriptor, ...],
        draft: ShareSnapshotDraft,
        principal: PrincipalRef,
        context: AccessAuditContext,
        /,
    ) -> None:
        """Every pinned origin is declared, agreed, still administered and unchanged.

        The origin-side acceptance is the recorded consent; its acceptor's ``scope.admin``
        is rechecked now against the owning store, because a recorded acceptance is not a
        perpetual grant. The pinning principal separately needs current read authority at
        each origin it actually pins.

        This checks the *origin* side only. Audience consent is the target's own agreement
        to receive, and it is enforced where the target is actually the audience: at bound
        execution and at approval. Requiring it here would also make writing a pin depend
        on the target's relation to the origin range.
        """

        assert self.access is not None  # noqa: S101 - the public pin path rejects a missing service
        declared_ids = {scope.scope_id for scope in declared}
        for item in draft.inputs:
            origin = item.origin_scope_id
            if origin not in declared_ids:
                # A pin cannot widen the agreement to a Scope the rule never named.
                raise ShareRejectedError("shared_use_audience")
            acceptor = next(
                (
                    acceptance
                    for acceptance in rule.acceptances
                    if acceptance.side == "origin" and origin in acceptance.covered_scope_ids
                ),
                None,
            )
            if acceptor is None or acceptor.terms_digest != rule.terms_digest:
                raise ShareRejectedError("shared_use_not_agreed")
            await self.access.require(
                _principal_from_actor(acceptor.actor),
                AccessAction.SCOPE_ADMIN,
                ResourceRef.scope(origin),
                context=context,
            )
            await self.access.require(principal, AccessAction.SCOPE_READ, ResourceRef.scope(origin), context=context)
            await self._require_recorded_digest(connection, item)

    async def _require_recorded_digest(self, connection: AsyncConnection, item: ShareInput, /) -> None:
        """Compare the recorded digest with the content the owning reader returns now.

        The comparison uses the same material convention the evidence resolver applies at
        generation, so a pinned digest is the one admission will later accept. When the
        owning reader for a kind is not composed, the pin refuses rather than asserting a
        digest it never verified.
        """

        from powercontext.builtin.evidence.models import content_digest
        from powercontext.sources import SourceRef

        reference = item.reference
        if isinstance(reference, SourceRef):
            if self.sources is None:
                raise ShareRejectedError("shared_use_source_reader_unavailable")
            stored = await self.sources.get_many(connection, item.origin_scope_id, (reference,))
            digest = content_digest(stored[0].value.model_dump_json().encode())
        else:
            if self.artifacts is None:
                raise ShareRejectedError("shared_use_artifact_reader_unavailable")
            artifact = await self.artifacts.get(connection, item.origin_scope_id, reference)
            digest = content_digest(artifact.model_dump_json().encode())
        if digest != item.digest:
            # The origin's content moved since this pin was prepared.
            raise ShareTermsChangedError()


__all__ = ["ShareAgreementService"]
