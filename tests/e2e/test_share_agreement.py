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

"""Durable readback of one rule version, an authentic acceptance and one pin.

Fixture SQLite and the real Access control service only. No provider, backend or host
acceptance is claimed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.authz import AccessRole, PrincipalRef, ResourceRef
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.errors import AccessDeniedError
from powercontext.builtin.authz.service import AccessAuditContext, CreateBinding
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.scope import ScopeDraft, ScopeSelection
from powercontext.builtin.sharing.models import (
    ShareInput,
    ShareRejectedError,
    ShareRuleDraft,
    ShareRuleTerms,
    ShareRuleVersionConflictError,
    ShareSnapshotDraft,
    membership_digest,
)
from powercontext.builtin.sharing.service import ShareAgreementService

ADMIN = PrincipalRef(type="service", id="share-admin")
OWNER = PrincipalRef(type="user", id="share-owner")
AUDIT = AccessAuditContext(transport="http", operation="share_agreement")
DIGEST = "sha256:" + "a" * 64


def _draft() -> ShareRuleDraft:
    return ShareRuleDraft(
        selection=ScopeSelection(mode="exact", scope_ids=("scp_alice",)),
        terms=ShareRuleTerms(use="project-conclusion", audience="project-members"),
    )


def _tables():
    from powercontext.builtin.persistence.tables import BUILTIN_TABLES, SHARE_TABLES

    return BUILTIN_TABLES + SHARE_TABLES


def test_one_rule_version_acceptance_and_pin_survive_reopen(tmp_path: Path) -> None:
    """The agreement records are durable and read back exactly through the service."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'share.db'}")
        tables = _tables()
        async with (
            SQLiteProfile.open(database, tables=tables) as profile,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            service = ShareAgreementService(profile.database, access=access)
            from powercontext.builtin.scope.application import ScopeApplication

            scopes = ScopeApplication(profile.database)
            scope_id = (
                await scopes.create(ScopeDraft(title="Orders", summary="Shared", idempotency_key="orders"))
            ).scope_id
            await access.create_binding(
                ADMIN,
                CreateBinding(
                    subject=OWNER,
                    resource=ResourceRef.scope(scope_id),
                    role=AccessRole.SCOPE_ADMIN,
                    idempotency_key="share-owner",
                ),
                context=AUDIT,
            )
            # Only a Scope administrator may write the agreement.
            with pytest.raises((ShareRejectedError, AccessDeniedError)):
                await service.create("scp_unowned", _draft(), principal=OWNER, context=AUDIT)
            draft = _draft()
            created = await service.create(scope_id, draft, principal=OWNER, context=AUDIT)
            assert (created.scope_id, created.version, created.status) == (scope_id, 1, "active")
            # Three distinct digests: terms, structure, and the exact pin.
            expected_terms = draft.terms_digest()
            assert created.terms_digest == expected_terms
            assert created.terms_digest != membership_digest(((scope_id, 1),))

            accepted = await service.accept(
                scope_id,
                created.rule_id,
                created.version,
                principal=OWNER,
                actor=f"{OWNER.type}:{OWNER.id}",
                side="origin",
                covered_scope_ids=(scope_id,),
                context=AUDIT,
            )
            assert len(accepted.acceptances) == 1
            assert accepted.acceptances[0].covered_scope_ids == (scope_id,)
            assert accepted.acceptances[0].terms_digest == accepted.terms_digest

            input_ = ShareInput(
                kind="artifact",
                origin_scope_id=scope_id,
                artifact=ArtifactRef(family="experience", artifact_id="exp1", revision=1),
                digest=DIGEST,
            )
            versions = ((scope_id, 1),)
            pinned = await service.pin(
                scope_id,
                ShareSnapshotDraft(
                    rule_id=created.rule_id,
                    rule_version=created.version,
                    terms_digest=created.terms_digest,
                    membership_digest=membership_digest(versions),
                    inputs=(input_,),
                    notification_key="discover-1",
                ),
                principal=OWNER,
                context=AUDIT,
                scope_versions=versions,
            )
            assert pinned.claimed is False and pinned.dream_run_id is None
            assert pinned.inputs[0].digest == DIGEST
            assert pinned.inputs_digest != pinned.terms_digest
            # A duplicate notification returns the recorded pin, not a second one.
            again = await service.pin(
                scope_id,
                ShareSnapshotDraft(
                    rule_id=created.rule_id,
                    rule_version=created.version,
                    terms_digest=created.terms_digest,
                    membership_digest=membership_digest(versions),
                    inputs=(input_,),
                    notification_key="discover-1",
                ),
                principal=OWNER,
                context=AUDIT,
                scope_versions=versions,
            )
            assert again.snapshot_id == pinned.snapshot_id

            # A version advance is a compare-and-swap, so a stale update is refused.
            with pytest.raises(ShareRuleVersionConflictError):
                await service.create(
                    scope_id,
                    _draft(),
                    principal=OWNER,
                    context=AUDIT,
                    rule_id=created.rule_id,
                    expected_version=0,
                )

        # Reopen: everything reads back from storage, including the pin.
        async with (
            SQLiteProfile.open(database, tables=tables) as reopened,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            service = ShareAgreementService(reopened.database, access=access)
            rule = await service.get(scope_id, created.rule_id)
            assert (rule.version, rule.terms_digest) == (1, created.terms_digest)
            assert rule.acceptances[0].actor == f"{OWNER.type}:{OWNER.id}"
            stored = await service.snapshot(scope_id, pinned.snapshot_id)
            assert stored.inputs == (input_,)
            assert (stored.inputs_digest, stored.membership_digest) == (
                pinned.inputs_digest,
                pinned.membership_digest,
            )
            assert stored.claimed is False

    asyncio.run(scenario())
