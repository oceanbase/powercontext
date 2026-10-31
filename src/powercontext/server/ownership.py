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

"""Supported ownership migration after switching Access enforcement on.

Data accepted while Access was disabled has no Artifact owner and no Review
Candidate owner attestation, so switching ``ACCESS_MODE`` to ``enforced``
leaves that data read-only for every Principal and blocks Candidate approval.
Migration never invents identity: an administrator names the Principal for one
explicit call, existing relations stay untouched, and every item write is one
idempotent Access-store transaction that can be safely repeated.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import TYPE_CHECKING

from powercontext.server.authz import (
    AccessAction,
    AccessAuditContext,
    AccessConflictError,
    AccessInvalidRequestError,
    AccessUnavailableError,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.models import MemoryEntrySelector
from powercontext.server.authz.owner_recovery import flush_owner_writes, owner_recovery_session, owner_write_access

if TYPE_CHECKING:
    from powercontext.builtin.runtime import BuiltinRuntime
    from powercontext.server.app import ServerApplication
    from powercontext.server.authz.owner_recovery import OwnerCommit, OwnerRecovery
    from powercontext.server.authz.service import AccessControlService

    Application = ServerApplication | BuiltinRuntime

OWNED_ARTIFACT_FAMILIES = ("atomic-memory", "experience", "skill", "profile")
RESOURCE_PAGE_SIZE = 100
CANDIDATE_PAGE_SIZE = 100

PLAN_OPERATION = "access.ownership.plan"
MIGRATION_OPERATION = "access.ownership.apply"


@dataclass(frozen=True, slots=True)
class ArtifactOwnershipGap:
    """One logical Artifact identity whose owner relation is absent."""

    scope_id: str
    family: str
    artifact_id: str
    entry_id: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateOwnershipGap:
    """One pending artifact-kind Candidate whose owner attestation is absent."""

    scope_id: str
    candidate_id: str
    family: str
    version: int
    entry_ids: tuple[str, ...] = ()
    target_family: str | None = None
    target_artifact_id: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeOwnershipPlan:
    scope_id: str
    artifact_gaps: tuple[ArtifactOwnershipGap, ...]
    candidate_gaps: tuple[CandidateOwnershipGap, ...]


@dataclass(frozen=True, slots=True)
class BackgroundPermissionGap:
    """One affected Scope where the named background Principal lacks write authority."""

    scope_id: str
    principal_id: str
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class OwnershipPlan:
    """The complete missing-attribution inventory for the requested Scopes."""

    scopes: tuple[ScopeOwnershipPlan, ...]
    background: tuple[BackgroundPermissionGap, ...]


@dataclass(frozen=True, slots=True)
class OwnershipDisposition:
    """One migration item with its per-item result."""

    gap: ArtifactOwnershipGap | CandidateOwnershipGap
    outcome: str  # "applied" or "conflict"
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class OwnershipMigrationResult:
    applied: tuple[OwnershipDisposition, ...]
    conflicts: tuple[OwnershipDisposition, ...]


def migration_context(
    operation: str, *, transport: str = "http", context: AccessAuditContext | None = None
) -> AccessAuditContext:
    if context is not None:
        return replace(context, operation=operation)
    return AccessAuditContext(transport=transport, operation=operation)


def ownership_report(value: OwnershipPlan | OwnershipMigrationResult) -> dict[str, object]:
    """Serialize a plan or migration result for operator-facing JSON output."""

    from dataclasses import asdict

    return asdict(value)


async def plan_ownership_migration(
    application: Application,
    access: AccessControlService | None,
    scope_ids: Sequence[str],
    *,
    background_principal: PrincipalRef | None = None,
    transport: str = "http",
    context: AccessAuditContext | None = None,
) -> OwnershipPlan:
    """Inventory missing Artifact owners and Candidate attestations for Scopes.

    Without an active Access store (pre-switch ``disabled`` mode) every owned
    Artifact Family and every pending Candidate is a gap, which is the check an
    administrator runs before enabling enforcement.
    """

    if background_principal is not None and access is None:
        raise AccessUnavailableError("relationship_management_unavailable")
    scopes: list[ScopeOwnershipPlan] = []
    selected = tuple(dict.fromkeys(scope_ids))
    for scope_id in selected:
        scopes.append(
            ScopeOwnershipPlan(
                scope_id=scope_id,
                artifact_gaps=await _artifact_gaps(application, access, scope_id),
                candidate_gaps=await _candidate_gaps(application, access, scope_id),
            )
        )
    background: list[BackgroundPermissionGap] = []
    if background_principal is not None and access is not None:
        # The checked background Principal does not inherit the administrator's groups.
        context = replace(migration_context(PLAN_OPERATION, transport=transport, context=context), subject_groups=())
        for scope_id in selected:
            decision = await access.check(
                background_principal,
                AccessAction.SCOPE_CONTRIBUTE,
                ResourceRef.scope(scope_id),
                context=context,
            )
            if not decision.allowed:
                background.append(
                    BackgroundPermissionGap(
                        scope_id=scope_id,
                        principal_id=background_principal.id,
                        reason_code=decision.reason_code,
                    )
                )
                continue
            records = application.records.for_scope(scope_id)
            page = await records.query_artifacts("profile", cursor=None, limit=RESOURCE_PAGE_SIZE)
            if any(record.artifact_id == "profile" for record in page.items):
                profile_decision = await access.check(
                    background_principal,
                    AccessAction.ARTIFACT_WRITE,
                    ResourceRef.artifact(scope_id, family="profile", artifact_id="profile"),
                    context=context,
                )
                if not profile_decision.allowed:
                    background.append(
                        BackgroundPermissionGap(
                            scope_id=scope_id,
                            principal_id=background_principal.id,
                            reason_code=profile_decision.reason_code,
                        )
                    )
    return OwnershipPlan(scopes=tuple(scopes), background=tuple(background))


async def apply_ownership_migration(
    application: Application,
    access: AccessControlService,
    scope_ids: Sequence[str],
    *,
    owner: PrincipalRef,
    dry_run: bool = False,
    transport: str = "http",
    owner_recovery: OwnerRecovery | None = None,
    context: AccessAuditContext | None = None,
) -> OwnershipMigrationResult:
    """Backfill the relations missing per plan, attributed to ``owner``.

    The inventory is recomputed at apply time, so interrupted or repeated runs
    refill only still-missing items. An existing relation with a different
    Principal is reported as a conflict, never overwritten.
    """

    _validate_migration_owner(owner)
    context = migration_context(MIGRATION_OPERATION, transport=transport, context=context)
    items: list[ArtifactOwnershipGap | CandidateOwnershipGap] = []
    conflicts: list[OwnershipDisposition] = []
    for scope_id in dict.fromkeys(scope_ids):
        inventory = (
            *await _artifact_gaps(application, None, scope_id),
            *await _candidate_gaps(application, None, scope_id),
        )
        for item in inventory:
            if isinstance(item, ArtifactOwnershipGap):
                relation = await access.artifact_owner(_owner_identity(item)[1])
                existing_owner = None if relation is None else relation.owner
                conflict_code = "artifact-owner"
            else:
                attestation = await access.candidate_owner(item.scope_id, item.candidate_id)
                existing_owner = None if attestation is None else attestation.proposed_owner
                conflict_code = "candidate-owner"
            if existing_owner is None:
                items.append(item)
            elif existing_owner != owner:
                conflicts.append(_conflict(item, AccessConflictError(conflict_code)))
            elif isinstance(item, ArtifactOwnershipGap) and not dry_run:
                # The relation may have committed before its external audit failed.
                # Complete the original intent before an idempotent retry succeeds.
                async with owner_recovery_session(owner_recovery):
                    await flush_owner_writes(artifact=_owner_identity(item)[1])
    protected = await _owner_recovery_conflicts(owner_recovery, items)
    protected_keys = {_migration_item_key(item.gap) for item in protected}
    items = [item for item in items if _migration_item_key(item) not in protected_keys]
    if dry_run:
        return OwnershipMigrationResult(applied=_preview_gaps(items), conflicts=(*conflicts, *protected))
    applied: list[OwnershipDisposition] = []
    conflicts.extend(protected)
    for item in items:
        if owner_recovery is None:
            item_applied, item_conflicts = await _apply_gap(access, item, owner, context)
        else:
            async with (
                owner_recovery_session(owner_recovery),
                owner_recovery.database.transaction() as connection,
            ):
                writer = owner_write_access(access, connection)
                item_applied, item_conflicts = await _apply_gap(writer, item, owner, context)
        applied.extend(item_applied)
        conflicts.extend(item_conflicts)
    return OwnershipMigrationResult(applied=tuple(applied), conflicts=tuple(conflicts))


def _preview_gaps(items: Sequence[ArtifactOwnershipGap | CandidateOwnershipGap]) -> tuple[OwnershipDisposition, ...]:
    preview: list[OwnershipDisposition] = []
    for item in items:
        if isinstance(item, CandidateOwnershipGap):
            preview.extend(
                OwnershipDisposition(
                    gap=ArtifactOwnershipGap(
                        scope_id=item.scope_id,
                        family=item.family,
                        artifact_id=item.target_artifact_id or "",
                        entry_id=entry_id,
                    ),
                    outcome="applied",
                )
                for entry_id in item.entry_ids
            )
        preview.append(OwnershipDisposition(gap=item, outcome="applied"))
    return tuple(preview)


async def _owner_recovery_conflicts(
    recovery: OwnerRecovery | None,
    items: Sequence[ArtifactOwnershipGap | CandidateOwnershipGap],
) -> list[OwnershipDisposition]:
    if recovery is None:
        return []
    conflicts: list[OwnershipDisposition] = []
    by_identity = {_migration_item_key(item): item for item in items}
    for scope_id in dict.fromkeys(item.scope_id for item in items):
        for kind in ("artifact", "candidate"):
            after: tuple[str, str] | None = None
            while True:
                page = await recovery.inventory_gaps(scope_id, kind=kind, after=after)
                for evidence in page.items:
                    if evidence.operation_id is None:
                        continue
                    key = (
                        ResourceRef.artifact(scope_id, family=evidence.family, artifact_id=evidence.identity).key
                        if kind == "artifact"
                        else evidence.identity
                    )
                    item = by_identity.get((scope_id, kind, key))
                    if item is not None:
                        conflicts.append(
                            OwnershipDisposition(
                                gap=item, outcome="conflict", detail=f"owner-recovery-{evidence.reason}"
                            )
                        )
                after = page.after
                if after is None:
                    break
    return conflicts


def _migration_item_key(item: ArtifactOwnershipGap | CandidateOwnershipGap) -> tuple[str, str, str]:
    if isinstance(item, ArtifactOwnershipGap):
        return (item.scope_id, "artifact", _owner_identity(item)[1].key)
    return (item.scope_id, "candidate", item.candidate_id)


async def _apply_gap(
    access: AccessControlService | OwnerCommit,
    item: ArtifactOwnershipGap | CandidateOwnershipGap,
    owner: PrincipalRef,
    context: AccessAuditContext,
) -> tuple[list[OwnershipDisposition], list[OwnershipDisposition]]:
    """Write one idempotent item; conflicts report the loser without failing the run."""

    if isinstance(item, ArtifactOwnershipGap):
        idempotency_key, resource = _owner_identity(item)
        try:
            await access.establish_artifact_owner(resource, owner, idempotency_key=idempotency_key, context=context)
        except AccessConflictError as error:
            return [], [_conflict(item, error)]
        return [OwnershipDisposition(gap=item, outcome="applied")], []
    applied: list[OwnershipDisposition] = []
    conflicts: list[OwnershipDisposition] = []
    for entry_id in item.entry_ids:
        entry_gap = ArtifactOwnershipGap(
            scope_id=item.scope_id,
            family=item.family,
            artifact_id=item.target_artifact_id or "",
            entry_id=entry_id,
        )
        identity_key, entry_resource = _owner_identity(entry_gap)
        try:
            await access.establish_artifact_owner(
                entry_resource,
                owner,
                idempotency_key=identity_key,
                context=context,
            )
        except AccessConflictError as error:
            conflicts.append(_conflict(entry_gap, error))
            continue
        applied.append(OwnershipDisposition(gap=entry_gap, outcome="applied"))
    try:
        await _attest_gap_owner(access, item, owner)
    except AccessConflictError as error:
        conflicts.append(_conflict(item, error))
        return applied, conflicts
    applied.append(OwnershipDisposition(gap=item, outcome="applied"))
    return applied, conflicts


def _conflict(
    gap: ArtifactOwnershipGap | CandidateOwnershipGap,
    error: AccessConflictError,
) -> OwnershipDisposition:
    return OwnershipDisposition(gap=gap, outcome="conflict", detail=error.code)


def _validate_migration_owner(owner: PrincipalRef) -> None:
    if owner.type not in {"user", "service"}:
        raise AccessInvalidRequestError("principal")
    if not owner.id or owner.id != owner.id.strip() or len(owner.id) > 256:
        raise AccessInvalidRequestError("principal")


async def _artifact_gaps(
    application: Application,
    access: AccessControlService | None,
    scope_id: str,
) -> tuple[ArtifactOwnershipGap, ...]:
    records = application.records.for_scope(scope_id)

    gaps: list[ArtifactOwnershipGap] = []
    for record in await records.logical_artifacts():
        if record.family not in OWNED_ARTIFACT_FAMILIES:
            continue
        resource = ResourceRef.artifact(scope_id, family=record.family, artifact_id=record.artifact_id)
        if access is not None and await access.artifact_owner(resource) is not None:
            continue
        gaps.append(ArtifactOwnershipGap(scope_id, record.family, record.artifact_id))
    if application.handoff is not None and await application.handoff.for_scope(scope_id).revisions():
        resource = ResourceRef.artifact(scope_id, family="handoff", artifact_id="handoff")
        if access is None or await access.artifact_owner(resource) is None:
            gaps.append(ArtifactOwnershipGap(scope_id=scope_id, family="handoff", artifact_id="handoff"))
    return tuple(gaps)


async def _candidate_gaps(
    application: Application,
    access: AccessControlService | None,
    scope_id: str,
) -> tuple[CandidateOwnershipGap, ...]:
    from powercontext.builtin.review.models import CandidateStatus
    from powercontext.builtin.runtime.models import ListCandidatesRequest

    review = application.review.for_scope(scope_id)
    gaps: list[CandidateOwnershipGap] = []
    cursor: str | None = None
    while True:
        page = await review.list(
            ListCandidatesRequest(status=CandidateStatus.PENDING, cursor=cursor, limit=CANDIDATE_PAGE_SIZE)
        )
        for candidate in page.candidates:
            if candidate.candidate_kind != "artifact":
                continue
            if access is not None and await access.candidate_owner(scope_id, candidate.candidate_id) is not None:
                continue
            target = candidate.target
            gaps.append(
                CandidateOwnershipGap(
                    scope_id=scope_id,
                    candidate_id=candidate.candidate_id,
                    family=candidate.family,
                    version=candidate.version,
                    entry_ids=_memory_entry_ids(candidate) if candidate.family == "memory" else (),
                    target_family=None if target is None else target.family,
                    target_artifact_id=None if target is None else target.artifact_id,
                )
            )
        cursor = page.next_cursor
        if cursor is None:
            return tuple(gaps)


async def _attest_gap_owner(
    access: AccessControlService | OwnerCommit,
    gap: CandidateOwnershipGap,
    owner: PrincipalRef,
) -> None:
    target = (
        None
        if gap.target_family is None or gap.target_artifact_id is None
        else ResourceRef.artifact(gap.scope_id, family=gap.target_family, artifact_id=gap.target_artifact_id)
    )
    await access.attest_candidate_owner(
        scope_id=gap.scope_id,
        candidate_id=gap.candidate_id,
        family=gap.family,
        proposed_owner=owner,
        target=target,
        idempotency_key=_migration_key("candidate-owner", gap.scope_id, gap.candidate_id),
    )


def _memory_entry_ids(candidate: object) -> tuple[str, ...]:
    changes = getattr(getattr(candidate, "proposal", None), "changes", None)
    if not isinstance(changes, Sequence):
        return ()
    entry_ids: list[str] = []
    for change in changes:
        entry_id = getattr(change, "entry_id", None)
        if isinstance(entry_id, str) and entry_id not in entry_ids:
            entry_ids.append(entry_id)
    return tuple(entry_ids)


def _owner_identity(gap: ArtifactOwnershipGap) -> tuple[str, ResourceRef]:
    if gap.entry_id is not None:
        resource = ResourceRef.artifact(
            gap.scope_id,
            family=gap.family,
            artifact_id=gap.artifact_id,
            selector=MemoryEntrySelector(entry_id=gap.entry_id),
        )
        return f"base-artifact-owner:{sha256(resource.key.encode()).hexdigest()}", resource
    if gap.family == "profile" and gap.artifact_id == "profile":
        key = _migration_key("profile-owner", gap.scope_id)
    else:
        key = _migration_key(f"{gap.family}-owner", gap.scope_id, gap.artifact_id)
    return key, ResourceRef.artifact(gap.scope_id, family=gap.family, artifact_id=gap.artifact_id)


def _migration_key(prefix: str, *identity: str) -> str:
    """Keep accepted keys stable; hash complete identity parts for longer keys."""
    key = f"{prefix}:{':'.join(identity)}"
    if len(key) <= 255:
        return key
    encoded = json.dumps((prefix, *identity), ensure_ascii=False, separators=(",", ":")).encode()
    return f"{prefix}:{sha256(encoded).hexdigest()}"
