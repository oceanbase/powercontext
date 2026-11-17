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

"""Authority for the standing agreement: who may consent, and what a pin verifies.

Fixture SQLite and the real Access control service only. Every negative case here fails
because the real authority or the real stored record says so, not because a mock was
configured to fail. No provider, backend or host acceptance is claimed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceDraft
from powercontext.builtin.authz import AccessRole, PrincipalRef, ResourceRef
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.errors import AccessDeniedError
from powercontext.builtin.authz.service import AccessAuditContext, CreateBinding
from powercontext.builtin.evidence.models import content_digest
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.runtime.relational import BUILTIN_ARTIFACT_TYPES
from powercontext.builtin.scope import ScopeApplication, ScopeDraft, ScopeSelection
from powercontext.builtin.scope.repository import ScopeRepository
from powercontext.builtin.sharing.models import (
    ShareInput,
    ShareRejectedError,
    ShareRuleDraft,
    ShareRuleTerms,
    ShareRuleVersionConflictError,
    ShareSnapshotDraft,
    ShareTermsChangedError,
    membership_digest,
)
from powercontext.builtin.sharing.service import ShareAgreementService
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY, ContentSource
from powercontext.sources import SourceMaterialization

ADMIN = PrincipalRef(type="service", id="share-admin")
ALICE = PrincipalRef(type="user", id="alice")
BOB = PrincipalRef(type="user", id="bob")
CAROL = PrincipalRef(type="user", id="carol")
AUDIT = AccessAuditContext(transport="http", operation="share_agreement")


def _tables():
    from powercontext.builtin.persistence.tables import BUILTIN_TABLES, SHARE_TABLES

    return BUILTIN_TABLES + SHARE_TABLES


def _terms() -> ShareRuleTerms:
    return ShareRuleTerms(use="project-conclusion", audience="project-members")


async def _grant(access, subject: PrincipalRef, scope_id: str, role: AccessRole) -> str:
    binding = await access.create_binding(
        ADMIN,
        CreateBinding(
            subject=subject,
            resource=ResourceRef.scope(scope_id),
            role=role,
            idempotency_key=f"{role.value}-{subject.id}-{scope_id}",
        ),
        context=AUDIT,
    )
    return binding.binding_id


def _service(profile, access) -> ShareAgreementService:
    """The owning service with the authoritative readers a pin must verify against."""

    sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
    return ShareAgreementService(
        profile.database,
        access=access,
        scopes=ScopeRepository(),
        sources=sources,
        artifacts=ArtifactRepository(BUILTIN_ARTIFACT_TYPES, sources=sources),
    )


async def _store_source(profile, scope_id: str, text: str = "Observed one exact task."):
    """Persist one real Source and return its reference and recorded digest."""

    sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
    async with profile.database.transaction() as connection:
        stored = await sources.add(
            connection,
            scope_id,
            ContentSource(name="task-1", materialization=SourceMaterialization.CAPTURED, content=text),
        )
    return stored.ref, content_digest(stored.value.model_dump_json().encode())


def test_acceptance_records_only_the_authorized_principal(tmp_path: Path) -> None:
    """An authorized caller cannot record somebody else's consent, and coverage is narrow."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'acceptance.db'}")
        tables = _tables()
        async with (
            SQLiteProfile.open(database, tables=tables) as profile,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            service = _service(profile, access)
            scopes = ScopeApplication(profile.database)
            origin = (
                await scopes.create(ScopeDraft(title="Origin", summary="Shares", idempotency_key="origin"))
            ).scope_id
            other = (
                await scopes.create(ScopeDraft(title="Other", summary="Unrelated", idempotency_key="other"))
            ).scope_id
            await _grant(access, ALICE, origin, AccessRole.SCOPE_ADMIN)
            await _grant(access, BOB, other, AccessRole.SCOPE_ADMIN)

            rule = await service.create(
                origin,
                ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(origin,)), terms=_terms()),
                principal=ALICE,
                context=AUDIT,
            )

            # Alice administers exactly `origin`. There is no parameter by which she can
            # name another acceptor: the recorded identity is hers, from authentication.
            accepted = await service.accept(
                origin,
                rule.rule_id,
                rule.version,
                principal=ALICE,
                side="origin",
                covered_scope_ids=(origin,),
                context=AUDIT,
            )
            assert len(accepted.acceptances) == 1
            recorded = accepted.acceptances[0]
            assert recorded.actor == '{"type":"user","id":"alice"}'
            assert recorded.side == "origin"
            assert recorded.covered_scope_ids == (origin,)
            assert recorded.terms_digest == accepted.terms_digest

            # A side outside the contract is refused rather than coerced to audience.
            with pytest.raises(ShareRejectedError) as wrong_side:
                await service.accept(
                    origin,
                    rule.rule_id,
                    rule.version,
                    principal=ALICE,
                    side="both",
                    covered_scope_ids=(origin,),
                    context=AUDIT,
                )
            assert wrong_side.value.code == "shared_use_rejected"

            # Claiming no Scope is not consent to anything.
            with pytest.raises(ShareRejectedError):
                await service.accept(
                    origin,
                    rule.rule_id,
                    rule.version,
                    principal=ALICE,
                    side="origin",
                    covered_scope_ids=(),
                    context=AUDIT,
                )

            # A Scope the rule never declared cannot be smuggled into the coverage.
            with pytest.raises(ShareRejectedError):
                await service.accept(
                    origin,
                    rule.rule_id,
                    rule.version,
                    principal=ALICE,
                    side="origin",
                    covered_scope_ids=(origin, other),
                    context=AUDIT,
                )

            # The rule still carries exactly the one authentic acceptance it recorded.
            reread = await service.get(origin, rule.rule_id)
            assert [item.actor for item in reread.acceptances] == ['{"type":"user","id":"alice"}']

    asyncio.run(scenario())


def test_acceptance_requires_current_administration_of_every_declared_claim(tmp_path: Path) -> None:
    """A declared but unadministered Scope still cannot be consented to on another's behalf."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'declared.db'}")
        tables = _tables()
        async with (
            SQLiteProfile.open(database, tables=tables) as profile,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            service = _service(profile, access)
            scopes = ScopeApplication(profile.database)
            origin = (
                await scopes.create(ScopeDraft(title="Origin", summary="Shares", idempotency_key="origin"))
            ).scope_id
            other = (
                await scopes.create(ScopeDraft(title="Other", summary="Also shared", idempotency_key="other"))
            ).scope_id
            # Alice administers `origin`; Bob, not Alice, administers `other`.
            await _grant(access, ALICE, origin, AccessRole.SCOPE_ADMIN)
            await _grant(access, BOB, other, AccessRole.SCOPE_ADMIN)

            rule = await service.create(
                origin,
                ShareRuleDraft(
                    selection=ScopeSelection(mode="exact", scope_ids=(origin, other)),
                    terms=_terms(),
                ),
                principal=ALICE,
                context=AUDIT,
            )

            # `other` is declared by the rule, so coverage passes; authority does not.
            with pytest.raises(AccessDeniedError):
                await service.accept(
                    origin,
                    rule.rule_id,
                    rule.version,
                    principal=ALICE,
                    side="origin",
                    covered_scope_ids=(other,),
                    context=AUDIT,
                )
            assert (await service.get(origin, rule.rule_id)).acceptances == ()

    asyncio.run(scenario())


def test_agreement_and_exact_pin_read_back_through_reopen(tmp_path: Path) -> None:
    """One rule version, one authentic acceptance and one verified pin are durable."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pin.db'}")
        tables = _tables()
        async with (
            SQLiteProfile.open(database, tables=tables) as profile,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            service = _service(profile, access)
            scopes = ScopeApplication(profile.database)
            origin = (
                await scopes.create(ScopeDraft(title="Origin", summary="Shares", idempotency_key="origin"))
            ).scope_id
            await _grant(access, ALICE, origin, AccessRole.SCOPE_ADMIN)
            await _grant(access, BOB, origin, AccessRole.SCOPE_CONTRIBUTOR)
            source_ref, digest = await _store_source(profile, origin)

            draft = ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(origin,)), terms=_terms())
            rule = await service.create(origin, draft, principal=ALICE, context=AUDIT)
            assert (rule.version, rule.status) == (1, "active")
            assert rule.terms_digest == draft.terms_digest()
            assert rule.terms_digest != membership_digest(((origin, 1),))

            # Alice is both the target administrator and the consenting origin.
            await service.accept(
                origin,
                rule.rule_id,
                rule.version,
                principal=ALICE,
                side="origin",
                covered_scope_ids=(origin,),
                context=AUDIT,
            )

            versions = ((origin, (await scopes.get(origin)).version),)
            pin_draft = ShareSnapshotDraft(
                rule_id=rule.rule_id,
                rule_version=rule.version,
                terms_digest=rule.terms_digest,
                membership_digest=membership_digest(versions),
                inputs=(ShareInput(kind="source", origin_scope_id=origin, source=source_ref, digest=digest),),
                notification_key="discover-1",
            )
            pinned = await service.pin(origin, pin_draft, principal=BOB, context=AUDIT)
            assert pinned.claimed is False and pinned.dream_run_id is None
            assert pinned.inputs[0].digest == digest
            assert pinned.inputs_digest != pinned.terms_digest

            # A duplicate notification returns the recorded pin, not a second evaluation.
            again = await service.pin(origin, pin_draft, principal=BOB, context=AUDIT)
            assert again.snapshot_id == pinned.snapshot_id

            # The same key carrying different material is a collision, not a replay, so it
            # is refused instead of silently returning the first pin.
            with pytest.raises(ShareRejectedError) as collision:
                await service.pin(
                    origin,
                    pin_draft.model_copy(update={"rule_version": pin_draft.rule_version + 1}),
                    principal=BOB,
                    context=AUDIT,
                )
            assert collision.value.code == "shared_use_rejected"

            # A stale update is a compare-and-swap failure.
            with pytest.raises(ShareRuleVersionConflictError):
                await service.create(
                    origin, draft, principal=ALICE, context=AUDIT, rule_id=rule.rule_id, expected_version=0
                )

        async with (
            SQLiteProfile.open(database, tables=tables) as reopened,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            service = _service(reopened, access)
            rule = await service.get(origin, rule.rule_id)
            assert (rule.version, rule.status) == (1, "active")
            assert [item.covered_scope_ids for item in rule.acceptances] == [(origin,)]
            stored_pin = await service.snapshot(origin, pinned.snapshot_id)
            assert stored_pin.inputs == pin_draft.inputs
            assert (stored_pin.inputs_digest, stored_pin.membership_digest) == (
                pinned.inputs_digest,
                pinned.membership_digest,
            )
            assert stored_pin.claimed is False

    asyncio.run(scenario())


def test_pin_refuses_unverified_agreement_and_unauthorized_origins(tmp_path: Path) -> None:
    """A pin cannot assert an agreement, a structure or an origin the stores do not confirm."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'refuse.db'}")
        tables = _tables()
        async with (
            SQLiteProfile.open(database, tables=tables) as profile,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            service = _service(profile, access)
            scopes = ScopeApplication(profile.database)
            origin = (
                await scopes.create(ScopeDraft(title="Origin", summary="Shares", idempotency_key="origin"))
            ).scope_id
            await _grant(access, ALICE, origin, AccessRole.SCOPE_ADMIN)
            await _grant(access, BOB, origin, AccessRole.SCOPE_CONTRIBUTOR)
            await _grant(access, CAROL, origin, AccessRole.SCOPE_VIEWER)
            source_ref, digest = await _store_source(profile, origin)

            draft = ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(origin,)), terms=_terms())
            rule = await service.create(origin, draft, principal=ALICE, context=AUDIT)
            versions = ((origin, (await scopes.get(origin)).version),)

            base_inputs = (ShareInput(kind="source", origin_scope_id=origin, source=source_ref, digest=digest),)

            def pin(
                *,
                rule_version: int | None = None,
                terms_digest: str | None = None,
                membership: str | None = None,
                inputs: tuple[ShareInput, ...] | None = None,
                notification_key: str | None = None,
            ) -> ShareSnapshotDraft:
                return ShareSnapshotDraft(
                    rule_id=rule.rule_id,
                    rule_version=rule.version if rule_version is None else rule_version,
                    terms_digest=rule.terms_digest if terms_digest is None else terms_digest,
                    membership_digest=membership_digest(versions) if membership is None else membership,
                    inputs=base_inputs if inputs is None else inputs,
                    notification_key=notification_key,
                )

            # No authentic acceptance exists, so nobody agreed to this use yet.
            with pytest.raises(ShareRejectedError) as not_agreed:
                await service.pin(origin, pin(), principal=BOB, context=AUDIT)
            assert not_agreed.value.code == "shared_use_rejected"

            await service.accept(
                origin,
                rule.rule_id,
                rule.version,
                principal=ALICE,
                side="origin",
                covered_scope_ids=(origin,),
                context=AUDIT,
            )

            # A caller-declared structure digest is evidence, never authorization.
            with pytest.raises(ShareTermsChangedError):
                await service.pin(
                    origin, pin(membership=membership_digest(((origin, 99),))), principal=BOB, context=AUDIT
                )

            # A caller-declared terms digest is compared with the recorded agreement.
            with pytest.raises(ShareTermsChangedError):
                await service.pin(origin, pin(terms_digest="sha256:" + "b" * 64), principal=BOB, context=AUDIT)

            # A material digest that no stored row produces is refused.
            with pytest.raises(ShareTermsChangedError):
                await service.pin(
                    origin,
                    pin(
                        inputs=(
                            ShareInput(
                                kind="source",
                                origin_scope_id=origin,
                                source=source_ref,
                                digest="sha256:" + "c" * 64,
                            ),
                        )
                    ),
                    principal=BOB,
                    context=AUDIT,
                )

            # A pinner needs current target contribution, and a viewer has none.
            with pytest.raises(AccessDeniedError):
                await service.pin(origin, pin(), principal=CAROL, context=AUDIT)

            # The refusals above recorded nothing: a verified pin still succeeds.
            recorded = await service.pin(origin, pin(notification_key="after-refusals"), principal=BOB, context=AUDIT)
            assert recorded.claimed is False
            assert (await service.snapshot(origin, recorded.snapshot_id)).inputs_digest == recorded.inputs_digest

            # A pin cannot name a version that is no longer the exact current one.
            # Advancing the head leaves v1 readable but superseded, so recording v1 now
            # would claim material under an agreement today's head no longer states.
            advanced = await service.create(
                origin, draft, principal=ALICE, context=AUDIT, rule_id=rule.rule_id, expected_version=1
            )
            assert advanced.version == 2
            with pytest.raises(ShareTermsChangedError):
                await service.pin(
                    origin,
                    pin(rule_version=rule.version, notification_key="superseded"),
                    principal=BOB,
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_pin_refuses_an_origin_outside_the_declared_coverage(tmp_path: Path) -> None:
    """A pin cannot widen the agreement to a Scope the rule never named."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'widen.db'}")
        tables = _tables()
        async with (
            SQLiteProfile.open(database, tables=tables) as profile,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            service = _service(profile, access)
            scopes = ScopeApplication(profile.database)
            origin = (
                await scopes.create(ScopeDraft(title="Origin", summary="Shares", idempotency_key="origin"))
            ).scope_id
            foreign = (
                await scopes.create(ScopeDraft(title="Foreign", summary="Not shared", idempotency_key="foreign"))
            ).scope_id
            await _grant(access, ALICE, origin, AccessRole.SCOPE_ADMIN)
            await _grant(access, ALICE, foreign, AccessRole.SCOPE_ADMIN)
            await _grant(access, BOB, origin, AccessRole.SCOPE_CONTRIBUTOR)
            await _grant(access, BOB, foreign, AccessRole.SCOPE_VIEWER)
            foreign_ref, foreign_digest = await _store_source(profile, foreign, "Never agreed.")

            rule = await service.create(
                origin,
                ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(origin,)), terms=_terms()),
                principal=ALICE,
                context=AUDIT,
            )
            await service.accept(
                origin,
                rule.rule_id,
                rule.version,
                principal=ALICE,
                side="origin",
                covered_scope_ids=(origin,),
                context=AUDIT,
            )
            # Even with current foreign read authority, `foreign` is not in the rule.
            with pytest.raises(ShareRejectedError):
                await service.pin(
                    origin,
                    ShareSnapshotDraft(
                        rule_id=rule.rule_id,
                        rule_version=rule.version,
                        terms_digest=rule.terms_digest,
                        membership_digest=membership_digest(((origin, 1),)),
                        inputs=(
                            ShareInput(
                                kind="source",
                                origin_scope_id=foreign,
                                source=foreign_ref,
                                digest=foreign_digest,
                            ),
                        ),
                    ),
                    principal=BOB,
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_artifact_input_digest_is_verified_against_the_stored_revision(tmp_path: Path) -> None:
    """An Artifact pin compares the recorded digest with the revision read now."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'artifact.db'}")
        tables = _tables()
        async with (
            SQLiteProfile.open(database, tables=tables) as profile,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            service = _service(profile, access)
            artifacts = ArtifactRepository(BUILTIN_ARTIFACT_TYPES, sources=SourceRepository(BUILTIN_SOURCE_REGISTRY))
            scopes = ScopeApplication(profile.database)
            origin = (
                await scopes.create(ScopeDraft(title="Origin", summary="Shares", idempotency_key="origin"))
            ).scope_id
            await _grant(access, ALICE, origin, AccessRole.SCOPE_ADMIN)
            await _grant(access, BOB, origin, AccessRole.SCOPE_CONTRIBUTOR)

            async with profile.database.transaction() as connection:
                artifact = await artifacts.create(
                    connection,
                    origin,
                    "exp-1",
                    ExperienceDraft(
                        content=ExperienceContent(
                            situation="A task ran",
                            action="Observe",
                            outcome="It finished",
                            lesson="Keep the exact read",
                        )
                    ),
                )
            ref = ArtifactRef(family="experience", artifact_id="exp-1", revision=1)
            digest = content_digest(artifact.model_dump_json().encode())

            rule = await service.create(
                origin,
                ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(origin,)), terms=_terms()),
                principal=ALICE,
                context=AUDIT,
            )
            await service.accept(
                origin,
                rule.rule_id,
                rule.version,
                principal=ALICE,
                side="origin",
                covered_scope_ids=(origin,),
                context=AUDIT,
            )
            versions = ((origin, (await scopes.get(origin)).version),)

            # The exact recorded Artifact digest is accepted.
            pinned = await service.pin(
                origin,
                ShareSnapshotDraft(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    terms_digest=rule.terms_digest,
                    membership_digest=membership_digest(versions),
                    inputs=(ShareInput(kind="artifact", origin_scope_id=origin, artifact=ref, digest=digest),),
                ),
                principal=BOB,
                context=AUDIT,
            )
            assert pinned.inputs[0].digest == digest

            # A different digest is refused: it is not what the owning store holds.
            with pytest.raises(ShareTermsChangedError):
                await service.pin(
                    origin,
                    ShareSnapshotDraft(
                        rule_id=rule.rule_id,
                        rule_version=rule.version,
                        terms_digest=rule.terms_digest,
                        membership_digest=membership_digest(versions),
                        inputs=(
                            ShareInput(
                                kind="artifact",
                                origin_scope_id=origin,
                                artifact=ref,
                                digest="sha256:" + "d" * 64,
                            ),
                        ),
                    ),
                    principal=BOB,
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_revoking_the_acceptor_authority_blocks_a_new_pin(tmp_path: Path) -> None:
    """A recorded acceptance is not a perpetual grant: the acceptor's right is rechecked."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'revoke.db'}")
        tables = _tables()
        async with (
            SQLiteProfile.open(database, tables=tables) as profile,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            service = _service(profile, access)
            scopes = ScopeApplication(profile.database)
            origin = (
                await scopes.create(ScopeDraft(title="Origin", summary="Shares", idempotency_key="origin"))
            ).scope_id
            admin_binding = await _grant(access, ALICE, origin, AccessRole.SCOPE_ADMIN)
            await _grant(access, BOB, origin, AccessRole.SCOPE_CONTRIBUTOR)
            source_ref, digest = await _store_source(profile, origin)

            rule = await service.create(
                origin,
                ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(origin,)), terms=_terms()),
                principal=ALICE,
                context=AUDIT,
            )
            await service.accept(
                origin,
                rule.rule_id,
                rule.version,
                principal=ALICE,
                side="origin",
                covered_scope_ids=(origin,),
                context=AUDIT,
            )
            versions = ((origin, (await scopes.get(origin)).version),)
            snapshot = ShareSnapshotDraft(
                rule_id=rule.rule_id,
                rule_version=rule.version,
                terms_digest=rule.terms_digest,
                membership_digest=membership_digest(versions),
                inputs=(ShareInput(kind="source", origin_scope_id=origin, source=source_ref, digest=digest),),
            )
            first = await service.pin(origin, snapshot, principal=BOB, context=AUDIT)
            assert first.claimed is False

            await access.revoke_binding(
                ADMIN,
                admin_binding,
                expected_version=1,
                idempotency_key="revoke-alice-admin",
                context=AUDIT,
            )

            # The target is still current and Alice's acceptance row is still recorded,
            # but her authority to consent no longer holds, so no new pin may rely on it.
            with pytest.raises((ShareRejectedError, AccessDeniedError)):
                await service.pin(
                    origin,
                    snapshot.model_copy(update={"notification_key": "after-revocation"}),
                    principal=BOB,
                    context=AUDIT,
                )

    asyncio.run(scenario())
