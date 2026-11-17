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

"""Eligibility of one pinned shared input set for one Dream execution.

This is the single authority that decides whether a run bound to a share snapshot may
use that pin. It is called before model input, reads every fact fresh, and refuses rather
than narrowing: an unsupported use, an unagreed origin, a stale digest, an ineligible
Source or an operation whose spec does not admit foreign evidence all fail closed.

It grants nothing by itself. Access still decides every read, and the resolver still
performs the qualified read and digest comparison; this module only decides whether the
pin is a complete and currently valid instruction for the operation that would run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.authz.errors import AccessControlError
from powercontext.builtin.authz.models import AccessAction, PrincipalRef, ResourceRef
from powercontext.builtin.dream.bindings import operation_spec
from powercontext.builtin.dream.models import DreamError, DreamOperation
from powercontext.builtin.evidence.models import QualifiedOrigin
from powercontext.builtin.sharing.models import ShareInput, ShareRule, ShareSnapshot
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

    from powercontext.builtin.authz.service import AccessAuditContext, AccessControlService
    from powercontext.builtin.persistence.share_agreement import ShareAgreementRepository
    from powercontext.builtin.scope.application import ScopeApplication

# The declared agreement values this checkpoint can enforce. A rule naming anything else
# is refused instead of being treated as its nearest equivalent.
SUPPORTED_USES = frozenset({"project-conclusion"})
SUPPORTED_AUDIENCES = frozenset({"project-members"})

# Families whose body is owned by the Scope rather than the Artifact. This mirrors the
# existing exact-revision split and is not a second permission model. Only the legacy
# Memory and Topic Memory families are Scope-owned; ``atomic-memory`` is Artifact-owned
# and must never be admitted on a Scope read alone.
_SCOPE_OWNED_BODY_FAMILIES = frozenset({"memory", "topic-memory"})


class ShareIneligibleError(DreamError):
    """A pinned input set is not currently eligible for this execution."""

    def __init__(self, code: str) -> None:
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class BoundShare:
    """The exact facts one bound execution was admitted against."""

    rule: ShareRule
    snapshot: ShareSnapshot
    origins: tuple[QualifiedOrigin, ...]


async def require_origin_reference(
    *,
    access: AccessControlService,
    principal: PrincipalRef,
    scope_id: str,
    ref: SourceRef | ArtifactRef,
    audit: AccessAuditContext,
) -> None:
    """Authorize one foreign origin reference at the Scope that owns it.

    This is the resolver's origin hook, so it runs for a directly pinned reference and for
    every lineage read the resolver performs in that Scope. A foreign ``atomic-memory``
    body is refused outright: this checkpoint does not support cross-Scope atomic evidence
    and will not read it under a Scope grant.
    """

    try:
        await access.require(principal, AccessAction.SCOPE_READ, ResourceRef.scope(scope_id), context=audit)
        if isinstance(ref, ArtifactRef):
            family = ref.family
            if family == "atomic-memory":
                raise ShareIneligibleError("shared_use_unsupported")
            if family not in _SCOPE_OWNED_BODY_FAMILIES:
                await access.require(
                    principal,
                    AccessAction.ARTIFACT_READ,
                    ResourceRef.artifact(scope_id, family=family, artifact_id=ref.artifact_id),
                    context=audit,
                )
    except AccessControlError as error:
        raise ShareIneligibleError("access_revoked") from error


async def _require_current_rule(
    agreements: ShareAgreementRepository,
    connection: AsyncConnection,
    scope_id: str,
    snapshot: ShareSnapshot,
    /,
) -> ShareRule:
    """The pin must still state the current active agreement, with a supported use."""

    rule = await agreements.build_rule(connection, scope_id, snapshot.rule_id, snapshot.rule_version)
    if rule.status != "active":
        raise ShareIneligibleError("share_rule_invalidated")
    head = await agreements.head(connection, scope_id, snapshot.rule_id)
    if head is None or int(head["version"]) != snapshot.rule_version:
        # The agreement moved after this pin was written; the pin no longer states terms.
        raise ShareIneligibleError("shared_use_terms")
    if rule.terms_digest != snapshot.terms_digest:
        raise ShareIneligibleError("shared_use_terms")
    if rule.terms.use not in SUPPORTED_USES or rule.terms.audience not in SUPPORTED_AUDIENCES:
        raise ShareIneligibleError("shared_use_unsupported")
    return rule


async def require_bound_eligibility(
    *,
    connection: AsyncConnection,
    access: AccessControlService,
    scopes: ScopeApplication,
    agreements: ShareAgreementRepository,
    principal: PrincipalRef,
    audit: AccessAuditContext,
    scope_id: str,
    operation: DreamOperation,
    snapshot_id: str,
    run_id: str,
) -> BoundShare:
    """Read back and authorize the exact pin this run is bound to.

    Every value is read now: the Scope, the pin, the rule and its terms, the authentic
    acceptances, each acceptor's current authority, the caller's own target and origin
    rights, and the declared membership. A pin that cannot be proven complete and current
    raises instead of contributing a partial input set.
    """

    row = await agreements.snapshot_row(connection, scope_id, snapshot_id, for_update=True)
    if row is None:
        raise ShareIneligibleError("share_snapshot_not_found")
    snapshot = agreements.decode_snapshot(row)
    # The binding is one-to-one and exact: this pin must already name *this* execution.
    # A null binding or another run's id is not this run's instruction.
    if snapshot.dream_run_id is None:
        raise ShareIneligibleError("share_snapshot_unclaimed")
    if snapshot.dream_run_id != run_id:
        raise ShareIneligibleError("share_snapshot_claimed")

    rule = await _require_current_rule(agreements, connection, scope_id, snapshot)

    spec = operation_spec(operation)
    for item in snapshot.inputs:
        if not _spec_admits(spec.allowed_evidence, item):
            raise ShareIneligibleError("shared_use_ineligible_operation")

    declared = await _declared_scopes(scopes, rule.selection)
    _require_membership(declared, snapshot)
    try:
        await _require_acceptances(access, rule, snapshot, declared, audit)
        await _require_target_contribution(access, principal, scope_id, audit)
        await _require_origin_rights(access, principal, snapshot, audit)
    except AccessControlError as error:
        # Access is the authority; this only reports its refusal as a Dream failure so the
        # attempt records a stable code instead of an internal error.
        raise ShareIneligibleError("access_revoked") from error

    return BoundShare(
        rule=rule,
        snapshot=snapshot,
        origins=tuple(_qualified(item) for item in snapshot.inputs),
    )


def _spec_admits(allowed: tuple[str, ...], item: ShareInput, /) -> bool:
    """Whether the operation's own declared evidence admits this Family."""

    family = item.reference.family if isinstance(item.reference, ArtifactRef) else "source"
    return family in allowed


def _qualified(item: ShareInput, /) -> QualifiedOrigin:
    """The resolver's qualified origin for one pinned input."""

    if isinstance(item.reference, SourceRef):
        return QualifiedOrigin(scope_id=item.origin_scope_id, source=item.reference, digest=item.digest)
    return QualifiedOrigin(
        scope_id=item.origin_scope_id,
        artifact=item.reference,
        digest=item.digest,
    )


def _require_membership(declared: tuple[tuple[str, int], ...], snapshot: ShareSnapshot, /) -> None:
    """The pin's recorded structure must still be the declared structure."""

    from powercontext.builtin.sharing.models import membership_digest

    if membership_digest(declared) != snapshot.membership_digest:
        raise ShareIneligibleError("shared_use_terms")


async def _declared_scopes(scopes: ScopeApplication, selection, /) -> tuple[tuple[str, int], ...]:
    """Resolve the rule's declared range through P1's bounded membership owner.

    Expansion is complete or refused: the bounded resolver raises rather than returning a
    truncated membership, so a pin is never validated against part of its own range.
    """

    from powercontext.builtin.scope.errors import ScopeRangeLimitError

    if selection.mode == "all":
        # An unbounded collection is not a bounded agreement.
        raise ShareIneligibleError("shared_use_unsupported")
    try:
        resolved = await scopes.resolve_range(selection)
    except ScopeRangeLimitError as error:
        raise ShareIneligibleError("shared_use_scope_range") from error
    return tuple(sorted((scope.scope_id, int(scope.version)) for scope in resolved.scopes))


async def _require_acceptances(
    access: AccessControlService,
    rule: ShareRule,
    snapshot: ShareSnapshot,
    declared: tuple[tuple[str, int], ...],
    audit: AccessAuditContext,
    /,
) -> None:
    """Every pinned origin needs an authentic acceptance by an acceptor with current rights."""

    declared_ids = {scope_id for scope_id, _ in declared}
    for item in snapshot.inputs:
        origin = item.origin_scope_id
        if origin not in declared_ids:
            raise ShareIneligibleError("shared_use_audience")
        acceptor = next(
            (
                acceptance
                for acceptance in rule.acceptances
                if acceptance.side == "origin" and origin in acceptance.covered_scope_ids
            ),
            None,
        )
        if acceptor is None or acceptor.terms_digest != rule.terms_digest:
            raise ShareIneligibleError("shared_use_not_agreed")
        # A recorded acceptance is not a perpetual grant.
        await access.require(
            _principal(acceptor.actor), AccessAction.SCOPE_ADMIN, ResourceRef.scope(origin), context=audit
        )


async def _require_target_contribution(
    access: AccessControlService, principal: PrincipalRef, scope_id: str, audit: AccessAuditContext, /
) -> None:
    await access.require(principal, AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id), context=audit)


async def _require_origin_rights(
    access: AccessControlService,
    principal: PrincipalRef,
    snapshot: ShareSnapshot,
    audit: AccessAuditContext,
    /,
) -> None:
    """The acting principal must be able to read every origin body this run would use.

    ``scope.read`` alone is not body access: an Artifact-owned Family additionally needs
    ``artifact.read`` on that exact Artifact, exactly as the exact-revision path requires.
    """

    for item in snapshot.inputs:
        origin = item.origin_scope_id
        await access.require(principal, AccessAction.SCOPE_READ, ResourceRef.scope(origin), context=audit)
        if isinstance(item.reference, ArtifactRef):
            family = item.reference.family
            if family not in _SCOPE_OWNED_BODY_FAMILIES:
                await access.require(
                    principal,
                    AccessAction.ARTIFACT_READ,
                    ResourceRef.artifact(origin, family=family, artifact_id=item.reference.artifact_id),
                    context=audit,
                )


def _principal(actor: str) -> PrincipalRef:
    """Decode a recorded acceptor identity written by the agreement service."""

    from powercontext.builtin.authz.errors import AccessInvalidRequestError

    try:
        decoded = json.loads(actor)
    except ValueError as error:
        raise ShareIneligibleError("shared_use_acceptor_invalid") from error
    if not isinstance(decoded, dict) or set(decoded) != {"type", "id"}:
        raise ShareIneligibleError("shared_use_acceptor_invalid")
    try:
        return PrincipalRef(type=decoded["type"], id=decoded["id"])
    except AccessInvalidRequestError as error:
        raise ShareIneligibleError("shared_use_acceptor_invalid") from error


__all__ = [
    "SUPPORTED_AUDIENCES",
    "SUPPORTED_USES",
    "BoundShare",
    "ShareIneligibleError",
    "require_bound_eligibility",
    "require_origin_reference",
]
