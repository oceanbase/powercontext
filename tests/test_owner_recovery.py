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

"""Recovery uses a separate real relationship database and the original identity."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace

import pytest
from sqlalchemy import Column, MetaData, String, Table, event, insert, select, update

from powercontext.builtin.authz import (
    AccessAuditContext,
    AccessConflictError,
    AccessUnavailableError,
    CandidateOwnerAttestation,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.owner_recovery import OWNER_RECOVERY_TABLE, OwnerRecovery
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile

ALICE = PrincipalRef(type="user", id="alice")
BOB = PrincipalRef(type="user", id="bob")
RESOURCE = ResourceRef.artifact("scope", family="profile", artifact_id="profile")
CONTEXT = AccessAuditContext(transport="http", operation="create_artifact", request_id="original-request")
DOMAIN_FAILURE = "domain failed"
PRIVATE_AUDIT_FAILURE = "private audit backend detail"
ACK_FAILURE = "primary acknowledgement failed"
METADATA = MetaData()
OBJECTS = Table("test_domain_objects", METADATA, Column("identity", String(128), primary_key=True))


@asynccontextmanager
async def _stores(tmp_path):
    primary = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'primary.db'}")
    external = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'external.db'}")
    async with (
        SQLiteProfile.open(primary, tables=(OBJECTS,)) as profile,
        open_builtin_access_control(external, deployment_id="recovery-test") as access,
    ):
        recovery = OwnerRecovery(profile.database, access)
        await recovery.initialize()
        yield profile.database, access, recovery


async def _create(database, recovery):
    async with database.transaction() as connection:
        await connection.execute(insert(OBJECTS).values(identity="profile"))
        operation = await recovery.for_transaction(connection).establish_artifact_owner(
            RESOURCE, ALICE, idempotency_key="original-owner", context=CONTEXT
        )
    return operation


def test_domain_rollback_also_discards_recovery_intent(tmp_path):
    async def scenario():
        async with _stores(tmp_path) as (database, access, recovery):
            with pytest.raises(RuntimeError, match="domain failed"):
                async with database.transaction() as connection:
                    await connection.execute(insert(OBJECTS).values(identity="profile"))
                    await recovery.for_transaction(connection).establish_artifact_owner(
                        RESOURCE, ALICE, idempotency_key="original-owner", context=CONTEXT
                    )
                    raise RuntimeError(DOMAIN_FAILURE)
            async with database.transaction() as connection:
                assert (await connection.execute(select(OBJECTS))).all() == []
            assert await recovery.pending() == ()
            assert await access.artifact_owner(RESOURCE) is None

    asyncio.run(scenario())


def test_staging_before_the_first_domain_write_does_not_commit_early(tmp_path):
    async def scenario():
        async with _stores(tmp_path) as (database, _access, recovery):
            with pytest.raises(RuntimeError, match="domain failed"):
                async with database.transaction() as connection:
                    await recovery.for_transaction(connection).establish_artifact_owner(
                        RESOURCE, ALICE, idempotency_key="original-owner", context=CONTEXT
                    )
                    raise RuntimeError(DOMAIN_FAILURE)
            assert await recovery.pending() == ()

    asyncio.run(scenario())


def test_recovery_survives_reopen_without_the_retrying_principal(tmp_path):
    async def scenario():
        async with _stores(tmp_path) as (database, access, recovery):
            await _create(database, recovery)
            assert await access.artifact_owner(RESOURCE) is None
            pending = await recovery.pending()
            assert len(pending) == 1
            operation_id = pending[0].operation_id
        async with _stores(tmp_path) as (database, access, recovery):
            await recovery.replay(operation_id)
            relation = await access.artifact_owner(RESOURCE)
            assert relation is not None and relation.owner == ALICE
            assert relation.idempotency_key == "original-owner"
            assert await recovery.pending() == ()
            async with database.transaction() as connection:
                assert len((await connection.execute(select(OBJECTS))).all()) == 1
            audits = await access.audit.list_audit(resource=RESOURCE)
            assert len(audits) == 1
            assert audits[0].principal == ALICE
            assert audits[0].request_id == "original-request"
            await recovery.replay(operation_id)
            assert len(await access.audit.list_audit(resource=RESOURCE)) == 1

    asyncio.run(scenario())


def test_audit_failure_after_external_owner_commit_is_recoverable(tmp_path, monkeypatch):
    async def scenario():
        async with _stores(tmp_path) as (database, access, recovery):
            await _create(database, recovery)
            operation_id = (await recovery.pending())[0].operation_id
            original = access.audit.append_audit_once

            async def unavailable(_event):
                raise RuntimeError(PRIVATE_AUDIT_FAILURE)

            monkeypatch.setattr(access.audit, "append_audit_once", unavailable)
            with pytest.raises(Exception, match="unavailable"):
                await recovery.replay(operation_id)
            relation = await access.artifact_owner(RESOURCE)
            assert relation is not None and relation.owner == ALICE
            assert len(await recovery.pending()) == 1
            monkeypatch.setattr(access.audit, "append_audit_once", original)
            restarted = OwnerRecovery(database, access)
            await restarted.replay(operation_id)
            assert await restarted.pending() == ()
            assert len(await access.audit.list_audit(resource=RESOURCE)) == 1

    asyncio.run(scenario())


def test_lost_ack_replays_the_same_audit_event_once(tmp_path):
    async def scenario():
        async with _stores(tmp_path) as (database, access, recovery):
            await _create(database, recovery)
            operation_id = (await recovery.pending())[0].operation_id

            def lose_ack(_connection, _cursor, statement, _parameters, _context, _many):
                if statement.startswith("UPDATE pc_access_owner_recovery"):
                    raise RuntimeError(ACK_FAILURE)

            event.listen(database.engine.sync_engine, "before_cursor_execute", lose_ack)
            try:
                with pytest.raises(RuntimeError, match="acknowledgement"):
                    await recovery.replay(operation_id)
            finally:
                event.remove(database.engine.sync_engine, "before_cursor_execute", lose_ack)
            assert len(await recovery.pending()) == 1
            first = await access.audit.list_audit(resource=RESOURCE)
            assert len(first) == 1
            await recovery.replay(operation_id)
            assert await access.audit.list_audit(resource=RESOURCE) == first
            assert await recovery.pending() == ()

    asyncio.run(scenario())


def test_existing_owner_conflict_is_not_transferred_or_acknowledged(tmp_path):
    async def scenario():
        async with _stores(tmp_path) as (database, access, recovery):
            await _create(database, recovery)
            await access.establish_artifact_owner(RESOURCE, BOB, idempotency_key="conflicting-owner", context=CONTEXT)
            operation_id = (await recovery.pending())[0].operation_id
            with pytest.raises(AccessConflictError):
                await recovery.replay(operation_id)
            relation = await access.artifact_owner(RESOURCE)
            assert relation is not None and relation.owner == BOB
            assert len(await recovery.pending()) == 1
            assert len(await access.audit.list_audit(resource=RESOURCE)) == 1

    asyncio.run(scenario())


def test_concurrent_recovery_keeps_one_owner_and_one_audit(tmp_path):
    async def scenario():
        async with _stores(tmp_path) as (database, access, recovery):
            await _create(database, recovery)
            operation_id = (await recovery.pending())[0].operation_id
            second = OwnerRecovery(database, access)
            await asyncio.gather(recovery.replay(operation_id), second.replay(operation_id))
            assert await recovery.pending() == ()
            relation = await access.artifact_owner(RESOURCE)
            assert relation is not None and relation.owner == ALICE
            assert len(await access.audit.list_audit(resource=RESOURCE)) == 1

    asyncio.run(scenario())


def test_candidate_recovery_retains_exact_existing_target(tmp_path):
    async def scenario():
        async with _stores(tmp_path) as (database, access, recovery):
            async with database.transaction() as connection:
                await connection.execute(insert(OBJECTS).values(identity="candidate"))
                await recovery.for_transaction(connection).attest_candidate_owner(
                    scope_id="scope",
                    candidate_id="candidate",
                    family="profile",
                    proposed_owner=ALICE,
                    target=RESOURCE,
                    idempotency_key="candidate-owner",
                )
            operation_id = (await recovery.pending())[0].operation_id
            await recovery.replay(operation_id)
            attestation = await access.candidate_owner("scope", "candidate")
            assert attestation is not None
            assert attestation.target == RESOURCE and attestation.proposed_owner == ALICE
            assert await access.artifact_owner(RESOURCE) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("rollback", [False, True])
def test_candidate_batch_recovery_preserves_the_domain_transaction_and_proposers(tmp_path, rollback):
    attestations = (
        CandidateOwnerAttestation("scope", "new", "profile", ALICE, None, "new-owner"),
        CandidateOwnerAttestation("scope", "update", "profile", BOB, RESOURCE, "update-owner"),
    )

    async def scenario():
        async with _stores(tmp_path) as (database, access, recovery):
            with pytest.raises(RuntimeError, match="domain failed") if rollback else nullcontext():
                async with database.transaction() as connection:
                    await connection.execute(insert(OBJECTS).values(identity="batch"))
                    await recovery.for_transaction(connection).attest_candidate_owners(attestations)
                    if rollback:
                        raise RuntimeError(DOMAIN_FAILURE)
            assert await access.candidate_owner("scope", "new") is None
            assert await access.candidate_owner("scope", "update") is None
        async with _stores(tmp_path) as (database, access, recovery):
            pending = await recovery.pending()
            assert len(pending) == (0 if rollback else 2)
            async with database.transaction() as connection:
                assert len((await connection.execute(select(OBJECTS))).all()) == (0 if rollback else 1)
            for item in pending:
                await recovery.replay(item.operation_id)
            for expected in attestations:
                assert await access.candidate_owner("scope", expected.candidate_id) == (None if rollback else expected)
            assert await recovery.pending() == ()

    asyncio.run(scenario())


def test_retry_cannot_replace_the_staged_principal_or_target(tmp_path):
    async def scenario():
        async with _stores(tmp_path) as (database, _access, recovery):
            await _create(database, recovery)
            with pytest.raises(AccessConflictError):
                async with database.transaction() as connection:
                    await recovery.for_transaction(connection).establish_artifact_owner(
                        RESOURCE, BOB, idempotency_key="original-owner", context=CONTEXT
                    )
            await recovery.replay((await recovery.pending())[0].operation_id)

    asyncio.run(scenario())


def test_normal_retry_and_recovery_preserve_the_original_relation(tmp_path):
    async def scenario():
        async with _stores(tmp_path) as (database, access, recovery):
            operation_id = await _create(database, recovery)

            async def retry():
                async with database.transaction() as connection:
                    repeated = await recovery.for_transaction(connection).establish_artifact_owner(
                        RESOURCE, ALICE, idempotency_key="original-owner", context=CONTEXT
                    )
                assert repeated == operation_id
                await recovery.replay(repeated)

            await asyncio.gather(recovery.replay(operation_id), retry())
            assert await recovery.pending() == ()
            assert (await access.artifact_owner(RESOURCE)).owner == ALICE
            assert len(await access.audit.list_audit(resource=RESOURCE)) == 1

    asyncio.run(scenario())


def test_candidate_retry_cannot_change_the_target(tmp_path):
    async def scenario():
        async with _stores(tmp_path) as (database, access, recovery):
            async with database.transaction() as connection:
                operation_id = await recovery.for_transaction(connection).attest_candidate_owner(
                    scope_id="scope",
                    candidate_id="candidate",
                    family="profile",
                    proposed_owner=ALICE,
                    target=RESOURCE,
                    idempotency_key="candidate-owner",
                )
            with pytest.raises(AccessConflictError):
                async with database.transaction() as connection:
                    await recovery.for_transaction(connection).attest_candidate_owner(
                        scope_id="scope",
                        candidate_id="candidate",
                        family="profile",
                        proposed_owner=ALICE,
                        target=None,
                        idempotency_key="candidate-owner",
                    )
            await recovery.replay(operation_id)
            assert (await access.candidate_owner("scope", "candidate")).target == RESOURCE

    asyncio.run(scenario())


def test_corrupted_recovery_identity_cannot_write_an_external_owner(tmp_path):
    async def scenario():
        async with _stores(tmp_path) as (database, access, recovery):
            operation_id = await _create(database, recovery)
            async with database.transaction() as connection:
                await connection.execute(
                    update(OWNER_RECOVERY_TABLE)
                    .where(OWNER_RECOVERY_TABLE.c.operation_id == operation_id)
                    .values(scope_id="another-scope")
                )
            with pytest.raises(AccessUnavailableError) as rejected:
                await recovery.replay(operation_id)
            assert rejected.value.code == "owner_recovery_payload_invalid"
            assert await access.artifact_owner(RESOURCE) is None
            assert await access.audit.list_audit(resource=RESOURCE) == ()

    asyncio.run(scenario())


def test_audit_replay_rejects_changed_fields_and_preserves_transaction_rollback(tmp_path):
    async def scenario():
        async with _stores(tmp_path) as (database, access, recovery):
            await recovery.replay(await _create(database, recovery))
            original = (await access.audit.list_audit(resource=RESOURCE))[0]
            assert await access.audit.append_audit_once(replace(original, cursor=None)) == original
            with pytest.raises(AccessConflictError):
                await access.audit.append_audit_once(replace(original, principal=BOB))
            with pytest.raises(RuntimeError, match="domain failed"):
                async with access.audit._database.transaction() as connection:
                    bound = access.audit.with_connection(connection)
                    await bound.append_audit_once(replace(original, cursor=None, event_id="rolled-back-event"))
                    raise RuntimeError(DOMAIN_FAILURE)
            assert await access.audit.list_audit(resource=RESOURCE) == (original,)

    asyncio.run(scenario())


def test_corrupted_intent_does_not_block_background_recovery_of_other_objects(tmp_path):
    async def scenario():
        async with _stores(tmp_path) as (database, access, recovery):
            damaged_id = await _create(database, recovery)
            other = ResourceRef.artifact("scope", family="experience", artifact_id="second")
            async with database.transaction() as connection:
                await connection.execute(insert(OBJECTS).values(identity="second"))
                await recovery.for_transaction(connection).establish_artifact_owner(
                    other, ALICE, idempotency_key="second-owner", context=CONTEXT
                )
                await connection.execute(
                    update(OWNER_RECOVERY_TABLE)
                    .where(OWNER_RECOVERY_TABLE.c.operation_id == damaged_id)
                    .values(scope_id="damaged-scope")
                )
            async with recovery.monitor(), asyncio.timeout(12):
                while await recovery.pending(scope_id="scope"):  # noqa: ASYNC110 - wait for durable delivery completion
                    await asyncio.sleep(0.05)
            assert await access.artifact_owner(RESOURCE) is None
            audits = await access.audit.list_audit(resource=other)
            assert len(audits) == 1 and audits[0].principal == ALICE
            with pytest.raises(AccessUnavailableError):
                await recovery.replay(damaged_id)

    asyncio.run(scenario())
