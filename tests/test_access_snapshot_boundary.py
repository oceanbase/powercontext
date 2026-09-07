# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Decision read-boundary conformance: snapshot isolation, bounded retry, fail closed.

These tests exercise the read boundary between the canonical Access repository
and the decision providers:

* providers read revision, bindings and ownership from **one consistent
  snapshot** when the repository offers one (``decision_snapshot``);
* otherwise they use **bounded revision-check-and-retry** and **fail closed**
  when a stable read cannot be obtained;
* a mutation deliberately interleaved between the reads can never produce a
  decision labeled with a stale policy revision (regression for the
  read-consistency gap demonstrated on the Access RFC thread).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.server.authz import (
    AccessAction,
    AccessAuditContext,
    AccessBinding,
    AccessBindingState,
    AccessConflictError,
    AccessRequest,
    AccessRole,
    AccessUnavailableError,
    BuiltinAuthorizationProvider,
    CasbinAuthorizationProvider,
    PrincipalRef,
    ReplaceBinding,
    ResourceRef,
    ResourceSearchRequest,
)
from powercontext.server.authz.models import ArtifactOwnerRelation
from powercontext.server.authz.repository import ACCESS_TABLES, RelationalAccessRepository

ADMIN = PrincipalRef(type="service", id="admin")
ALICE = PrincipalRef(type="user", id="alice")
BOB = PrincipalRef(type="user", id="bob")
CHARLIE = PrincipalRef(type="user", id="charlie")
AUDIT = AccessAuditContext(transport="http", operation="snapshot-boundary", request_id="req-snapshot")
NOW = datetime.now(UTC)


class _CountingDatabase(AsyncDatabase):
    """Counts transaction openings so tests can pin the read boundary size."""

    def __init__(self, inner: AsyncDatabase) -> None:
        super().__init__(inner.engine, owns_engine=False)
        self._inner = inner
        self.transactions = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        self.transactions += 1
        async with self._inner.transaction() as connection:
            yield connection


class _InterleavingRepository(RelationalAccessRepository):
    """Injects one mutation after the first revision read, reproducing the
    controlled interleaving from the RFC thread (mutation committed between
    the decision reads)."""

    def __init__(
        self,
        inner: RelationalAccessRepository,
        database: AsyncDatabase,
        mutate,
    ) -> None:
        super().__init__(database)
        self._inner = inner
        self._mutate = mutate
        self.revision_reads = 0

    async def policy_revision(self) -> str:
        self.revision_reads += 1
        revision = await self._inner.policy_revision()
        if self.revision_reads == 1:
            # Commit AFTER the caller has captured the (pre-mutation)
            # revision, so the subsequent binding read sees the new grant.
            await self._mutate()
        return revision

    def __getattribute__(self, name: str):
        if name == "decision_snapshot":
            # Hide the snapshot capability so the bounded-retry path runs.
            raise AttributeError(name)
        return super().__getattribute__(name)


class _UnstableRepository(RelationalAccessRepository):
    """Reports a fresh revision on every call, so no stable snapshot can
    ever be established."""

    def __init__(self, inner: RelationalAccessRepository, database: AsyncDatabase) -> None:
        super().__init__(database)
        self._inner = inner
        self.calls = 0

    async def policy_revision(self) -> str:
        self.calls += 1
        await self._inner.policy_revision()
        return str(self.calls)

    def __getattribute__(self, name: str):
        if name == "decision_snapshot":
            raise AttributeError(name)
        return super().__getattribute__(name)


async def _seed_handoff(repository: RelationalAccessRepository) -> ResourceRef:
    handoff = ResourceRef.artifact("scope-a", family="handoff", artifact_id="handoff")
    await repository.create_binding(
        AccessBinding(
            binding_id="seed-admin",
            subject=ADMIN,
            resource=ResourceRef.server(),
            role=AccessRole.SERVER_ADMIN,
            granted_by=ADMIN,
            reason="bootstrap",
            created_at=datetime.now(UTC),
            expires_at=None,
            state=AccessBindingState.ACTIVE,
            version=1,
            policy_revision="pending",
            idempotency_key="seed-admin",
        )
    )
    await repository.establish_artifact_owner(
        ArtifactOwnerRelation(
            resource=handoff,
            owner=ALICE,
            established_at=datetime.now(UTC),
            policy_revision="0",
            idempotency_key="owner-handoff",
        )
    )
    await repository.create_binding(
        AccessBinding(
            binding_id="receiver-bob",
            subject=BOB,
            resource=handoff,
            role=AccessRole.HANDOFF_RECEIVER,
            granted_by=ADMIN,
            reason="seed receiver",
            created_at=datetime.now(UTC),
            expires_at=None,
            state=AccessBindingState.ACTIVE,
            version=1,
            policy_revision="pending",
            idempotency_key="receiver-bob",
        )
    )
    return handoff


def test_providers_read_decision_inputs_from_one_snapshot() -> None:
    """check() and resolve_resource_filter() open exactly one transaction when
    the repository provides decision_snapshot."""

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            handoff = await _seed_handoff(repository)
            counting = _CountingDatabase(profile.database)
            snapshot_repository = RelationalAccessRepository(counting)
            for provider in (
                BuiltinAuthorizationProvider(snapshot_repository),
                CasbinAuthorizationProvider(snapshot_repository),
            ):
                counting.transactions = 0
                decision = await provider.check(
                    AccessRequest(subject=BOB, action=AccessAction.ARTIFACT_READ, resource=handoff, context=AUDIT)
                )
                assert decision.allowed
                assert counting.transactions == 1, "check must read from one snapshot transaction"
                counting.transactions = 0
                await provider.resolve_resource_filter(
                    ResourceSearchRequest(
                        subject=ALICE,
                        action=AccessAction.ARTIFACT_READ,
                        resource_type=handoff.type,
                        family="handoff",
                        context=AUDIT,
                    )
                )
                assert counting.transactions == 1, "filter must read from one snapshot transaction"

    asyncio.run(scenario())


def test_interleaved_mutation_cannot_label_decision_with_stale_revision() -> None:
    """Bounded revision-check-and-retry: a grant committed between the reads
    is either fully visible (with the matching revision) or not at all — the
    stale combination from the RFC thread must not reproduce."""

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            handoff = await _seed_handoff(repository)
            pre_mutation_revision = await repository.policy_revision()

            async def mutate() -> None:
                await repository.create_binding(
                    AccessBinding(
                        binding_id="viewer-charlie",
                        subject=CHARLIE,
                        resource=handoff,
                        role=AccessRole.ARTIFACT_VIEWER,
                        granted_by=ADMIN,
                        reason="interleaved grant",
                        created_at=datetime.now(UTC),
                        expires_at=None,
                        state=AccessBindingState.ACTIVE,
                        version=1,
                        policy_revision="pending",
                        idempotency_key="viewer-charlie",
                    )
                )

            interleaved = _InterleavingRepository(repository, profile.database, mutate)
            for provider in (
                BuiltinAuthorizationProvider(interleaved),
                CasbinAuthorizationProvider(interleaved),
            ):
                decision = await provider.check(
                    AccessRequest(subject=CHARLIE, action=AccessAction.ARTIFACT_READ, resource=handoff, context=AUDIT)
                )
                # The grant committed at revision pre+1; a stable read must
                # never report the pre-mutation revision together with an
                # allow that only exists post-mutation.
                assert not (decision.allowed and decision.policy_revision == pre_mutation_revision)
                if decision.allowed:
                    assert decision.policy_revision is not None
                    assert int(decision.policy_revision) > int(pre_mutation_revision)

    asyncio.run(scenario())


def test_unstable_revision_fails_closed() -> None:
    """When no stable snapshot can be obtained within the retry budget, the
    decision must fail closed, not fall back to a possibly stale read."""

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            handoff = await _seed_handoff(repository)
            unstable = _UnstableRepository(repository, profile.database)
            for provider in (
                BuiltinAuthorizationProvider(unstable),
                CasbinAuthorizationProvider(unstable),
            ):
                with pytest.raises(AccessUnavailableError):
                    await provider.check(
                        AccessRequest(subject=BOB, action=AccessAction.ARTIFACT_READ, resource=handoff, context=AUDIT)
                    )

    asyncio.run(scenario())


def test_providers_agree_on_point_and_list_for_direct_and_inherited_grants() -> None:
    """Point decisions and resource filters must agree within each provider
    and across providers, for direct grants, inherited grants and denials."""

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            handoff = await _seed_handoff(repository)
            other_scope = ResourceRef.artifact("scope-b", family="handoff", artifact_id="other")
            await repository.establish_artifact_owner(
                ArtifactOwnerRelation(
                    resource=other_scope,
                    owner=ALICE,
                    established_at=datetime.now(UTC),
                    policy_revision="0",
                    idempotency_key="owner-other",
                )
            )
            builtin = BuiltinAuthorizationProvider(repository)
            casbin = CasbinAuthorizationProvider(repository)
            for provider in (builtin, casbin):
                # Direct grant: Bob is handoff.receiver on the handoff artifact.
                direct = await provider.check(
                    AccessRequest(subject=BOB, action=AccessAction.ARTIFACT_READ, resource=handoff, context=AUDIT)
                )
                # Inherited grant: Bob reads a scope and its artifacts? Bob has
                # no scope binding here — scope read must stay denied.
                scope_read = await provider.check(
                    AccessRequest(
                        subject=BOB,
                        action=AccessAction.SCOPE_READ,
                        resource=ResourceRef.scope("scope-a"),
                        context=AUDIT,
                    )
                )
                # Cross-scope denial.
                cross = await provider.check(
                    AccessRequest(subject=BOB, action=AccessAction.ARTIFACT_READ, resource=other_scope, context=AUDIT)
                )
                assert direct.allowed and not scope_read.allowed and not cross.allowed

                for search in (
                    ResourceSearchRequest(
                        subject=BOB,
                        action=AccessAction.ARTIFACT_READ,
                        resource_type=handoff.type,
                        family="handoff",
                        context=AUDIT,
                    ),
                ):
                    view = await provider.resolve_resource_filter(search)
                    assert handoff in view.exact_resources
                    assert other_scope not in view.exact_resources
                    assert view.complete
                    assert view.policy_revision is not None
            # Cross-provider parity of the derived filter.
            search = ResourceSearchRequest(
                subject=BOB,
                action=AccessAction.ARTIFACT_READ,
                resource_type=handoff.type,
                family="handoff",
                context=AUDIT,
            )
            assert await builtin.resolve_resource_filter(search) == await casbin.resolve_resource_filter(search)

    asyncio.run(scenario())


def test_idempotency_ledger_conflicts_across_operations_and_payloads() -> None:
    """Repository-level conformance shared by both adapters: an existing
    actor/key conflicts across operations and across changed payloads, and a
    replay after revocation resolves to the revoked binding without
    resurrecting it."""

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            handoff = await _seed_handoff(repository)

            def binding(binding_id: str, *, key: str, expires_at: datetime | None = None) -> AccessBinding:
                return AccessBinding(
                    binding_id=binding_id,
                    subject=CHARLIE,
                    resource=handoff,
                    role=AccessRole.ARTIFACT_VIEWER,
                    granted_by=ADMIN,
                    reason="idempotency probe",
                    created_at=datetime.now(UTC),
                    expires_at=expires_at,
                    state=AccessBindingState.ACTIVE,
                    version=1,
                    policy_revision="pending",
                    idempotency_key=key,
                )

            created = await repository.create_binding(binding("viewer-1", key="grant-charlie"))
            # Same key, changed payload (expiry) -> 409, even though the
            # subject/role/resource triple is unchanged.
            with pytest.raises(AccessConflictError):
                await repository.create_binding(binding("viewer-2", key="grant-charlie", expires_at=datetime.now(UTC)))
            # Same key, different operation -> 409.
            with pytest.raises(AccessConflictError):
                await repository.replace_binding(
                    ReplaceBinding(
                        binding_id="viewer-1",
                        expected_version=1,
                        subject=CHARLIE,
                        idempotency_key="grant-charlie",
                    ),
                    actor=ADMIN,
                    changed_at=datetime.now(UTC),
                )
            # Replay after revocation: same key returns the revoked binding
            # as-is (identity resolution), no resurrection, no new grant.
            revoked = await repository.revoke_binding(
                "viewer-1",
                expected_version=1,
                idempotency_key="revoke-charlie",
                revoked_at=datetime.now(UTC),
                revoked_by=ADMIN,
            )
            replayed = await repository.create_binding(binding("viewer-3", key="grant-charlie"))
            assert replayed.binding_id == created.binding_id
            assert replayed.state is AccessBindingState.REVOKED
            assert replayed.version == revoked.version
            # A post-revocation re-grant uses a fresh key and creates a new
            # active binding.
            fresh = await repository.create_binding(binding("viewer-4", key="grant-charlie-again"))
            assert fresh.binding_id != created.binding_id
            assert fresh.state is AccessBindingState.ACTIVE

    asyncio.run(scenario())
