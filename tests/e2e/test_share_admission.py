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

"""The private one-to-one pin claim inside Dream admission.

Fixture SQLite only. The seam exercised here is the private ``DreamService._admit``
argument, which no production caller passes: this proves the transaction behavior of the
claim, not an enabled feature. No provider, backend or host acceptance is claimed.

The pin is recorded through the owning agreement service over real Access, so the row
this claim binds is a real snapshot, not a synthetic insert.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from powercontext.builtin.authz import AccessRole, PrincipalRef, ResourceRef
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.service import AccessAuditContext, CreateBinding
from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamError, ListDreamRunsRequest
from powercontext.builtin.evidence.models import content_digest
from powercontext.builtin.persistence.dream import DreamRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_INTENTS_TABLE, SHARE_SNAPSHOTS_TABLE
from powercontext.builtin.runtime import BuiltinConfig
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.builtin.scope import ScopeDraft, ScopeSelection
from powercontext.builtin.scope.repository import ScopeRepository
from powercontext.builtin.sharing.models import (
    ShareInput,
    ShareRuleDraft,
    ShareRuleTerms,
    ShareSnapshot,
    ShareSnapshotDraft,
    membership_digest,
)
from powercontext.builtin.sharing.service import ShareAgreementService
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY, ContentSource
from powercontext.sources import SourceMaterialization
from tests.e2e.dream_support import atomic_memory_pipeline, open_dream_runtime
from tests.e2e.test_artifact_dreaming import Generator, MemoryPipeline

ADMIN = PrincipalRef(type="service", id="admission-admin")
OWNER = PrincipalRef(type="user", id="admission-owner")
AUDIT = AccessAuditContext(transport="http", operation="share_admission")
ROOT_TEXT = "Replayed the write with key K; exactly one row remained."


class InjectedAdmissionFailure(Exception):
    """A deterministic fault raised after the claim, to prove the transaction rollback."""


async def _seed_evidence(runtime, scope_id: str):
    """Create one real admitted atomic-memory citation inside the admitting Scope."""

    from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
    from powercontext.builtin.runtime.models import CaptureSource

    await runtime.sources.for_scope(scope_id).capture(
        CaptureSource(source_id="write-test", content=ROOT_TEXT, metadata={})
    )
    assert runtime.atomic_memory is not None
    await runtime.atomic_memory.for_scope(scope_id).flush()
    await runtime.atomic_memory.for_scope(scope_id).create((
        AtomicMemoryContent(kind="private_note", text="EVIDENCE_SENTINEL", description="Fixture memory"),
    ))
    entries = await runtime.atomic_memory.for_scope(scope_id).list()
    return next(item.ref for item in entries.items if item.artifact.content.kind == "working_note")


async def _ensure_share_tables(admindb) -> None:
    """Fixture-only: create the sharing tables this checkpoint reads.

    The runtime's managed schema does not register the sharing records yet, so this test
    creates them directly. That proves own-metadata behavior in unmanaged SQLite; it is
    not a managed-migration or portability acceptance.
    """

    from powercontext.builtin.persistence.schema import create_tables
    from powercontext.builtin.persistence.tables import SHARE_TABLES

    async with admindb.transaction() as connection:
        await create_tables(connection, SHARE_TABLES)


def _config(database) -> BuiltinConfig:
    return BuiltinConfig(database=database, runtime=RuntimeConfig())


async def _grant(access, subject: PrincipalRef, scope_id: str, role: AccessRole) -> None:
    await access.create_binding(
        ADMIN,
        CreateBinding(
            subject=subject,
            resource=ResourceRef.scope(scope_id),
            role=role,
            idempotency_key=f"{role.value}-{subject.id}-{scope_id}",
        ),
        context=AUDIT,
    )


async def _record_pin(admindb, access, scope_id: str, *, key: str) -> ShareSnapshot:
    """Write one real unclaimed pin through the owning service and real Access."""

    sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
    async with admindb.transaction() as connection:
        stored = await sources.add(
            connection,
            scope_id,
            ContentSource(name="write-test", materialization=SourceMaterialization.CAPTURED, content=ROOT_TEXT),
        )
    ref = stored.ref
    digest = content_digest(stored.value.model_dump_json().encode())

    service = ShareAgreementService(admindb, access=access, scopes=ScopeRepository(), sources=sources)
    rule = await service.create(
        scope_id,
        ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(scope_id,)), terms=_terms()),
        principal=OWNER,
        context=AUDIT,
    )
    await service.accept(
        scope_id,
        rule.rule_id,
        rule.version,
        principal=OWNER,
        side="origin",
        covered_scope_ids=(scope_id,),
        context=AUDIT,
    )
    async with admindb.transaction() as connection:
        descriptor = await ScopeRepository().get(connection, scope_id)
    assert descriptor is not None
    return await service.pin(
        scope_id,
        ShareSnapshotDraft(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            terms_digest=rule.terms_digest,
            membership_digest=membership_digest(((scope_id, descriptor.version),)),
            inputs=(ShareInput(kind="source", origin_scope_id=scope_id, source=ref, digest=digest),),
            notification_key=key,
        ),
        principal=OWNER,
        context=AUDIT,
    )


def _terms() -> ShareRuleTerms:
    return ShareRuleTerms(use="project-conclusion", audience="project-members")


async def _intent_generation(admindb, scope_id: str, binding: str) -> int | None:
    async with admindb.transaction() as connection:
        return await connection.scalar(
            select(ARTIFACT_PROCESSING_INTENTS_TABLE.c.requested_generation).where(
                ARTIFACT_PROCESSING_INTENTS_TABLE.c.scope_id == scope_id,
                ARTIFACT_PROCESSING_INTENTS_TABLE.c.binding_name == binding,
            )
        )


async def _pin_owner(admindb, scope_id: str, snapshot_id: str) -> str | None:
    async with admindb.transaction() as connection:
        return await connection.scalar(
            select(SHARE_SNAPSHOTS_TABLE.c.dream_run_id).where(
                SHARE_SNAPSHOTS_TABLE.c.scope_id == scope_id,
                SHARE_SNAPSHOTS_TABLE.c.snapshot_id == snapshot_id,
            )
        )


def test_pin_claim_is_one_to_one_and_replays_exactly(tmp_path: Path) -> None:
    """One pin binds one run; the same key replays it; another pin or key conflicts."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'claim.db'}")
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=Generator(),
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            scope = await runtime.scopes.create(
                ScopeDraft(title="Admission", summary="Pin claim", idempotency_key="admission")
            )
            scope_id = scope.scope_id
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_ADMIN)
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_CONTRIBUTOR)
            pin = await _record_pin(runtime.primary_database, access, scope_id, key="discover-1")
            other_pin = await _record_pin(runtime.primary_database, access, scope_id, key="discover-2")
            assert pin.dream_run_id is None and other_pin.dream_run_id is None

            service = runtime._dream_service
            assert service is not None
            # Real admitted evidence from this Scope, not a synthetic reference.
            citation = await _seed_evidence(runtime, scope_id)
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="admit-1"
            )
            binding = "experience-incubation"
            before_generation = await _intent_generation(runtime.primary_database, scope_id, binding)

            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)
            assert run.status == "queued"
            assert await _pin_owner(runtime.primary_database, scope_id, pin.snapshot_id) == run.run_id
            assert (await _pin_owner(runtime.primary_database, scope_id, other_pin.snapshot_id)) is None
            after_generation = await _intent_generation(runtime.primary_database, scope_id, binding)
            assert after_generation == (before_generation or 0) + 1

            # Same principal, key, request and exact pin: the recorded run, no new work.
            replay = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)
            assert replay.run_id == run.run_id
            assert await _intent_generation(runtime.primary_database, scope_id, binding) == after_generation

            # The same key naming a different pin is not a replay of this admission.
            with pytest.raises(DreamError, match="idempotency_conflict"):
                await service._admit(scope_id, "runtime", request, snapshot_id=other_pin.snapshot_id)

            # The same key asking for no pin cannot answer a pin-bound admission either,
            # and the ordinary path stays exactly as it was for non-sharing callers.
            with pytest.raises(DreamError, match="idempotency_conflict"):
                await service._admit(scope_id, "runtime", request, snapshot_id=None)

            # A second request cannot take a pin the first run already holds.
            second = request.model_copy(update={"idempotency_key": "admit-2"})
            with pytest.raises(DreamError, match="share_snapshot_claimed"):
                await service._admit(scope_id, "runtime", second, snapshot_id=pin.snapshot_id)
            assert await _pin_owner(runtime.primary_database, scope_id, pin.snapshot_id) == run.run_id

            # A pin that does not exist cannot be silently created by admission.
            with pytest.raises(DreamError, match="share_snapshot_not_found"):
                await service._admit(
                    scope_id,
                    "runtime",
                    request.model_copy(update={"idempotency_key": "admit-3"}),
                    snapshot_id="snap_missing",
                )

    asyncio.run(scenario())


def test_refused_claim_rolls_back_with_the_pin_left_unclaimed(tmp_path: Path) -> None:
    """A rejected admission leaves no orphan: no run, no advance, pin still claimable."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}")
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=Generator(),
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            scope = await runtime.scopes.create(
                ScopeDraft(title="Admission", summary="Pin rollback", idempotency_key="admission")
            )
            scope_id = scope.scope_id
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_ADMIN)
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_CONTRIBUTOR)
            pin = await _record_pin(runtime.primary_database, access, scope_id, key="discover-1")

            service = runtime._dream_service
            assert service is not None
            citation = await _seed_evidence(runtime, scope_id)
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="admit-1"
            )
            binding = "experience-incubation"
            before_generation = await _intent_generation(runtime.primary_database, scope_id, binding)

            # Fail the owning transaction after the claim has been written to it. If the
            # claim were not part of this transaction, the pin would be stranded.
            original = DreamRepository.create

            async def refuse(self, connection, record):
                raise InjectedAdmissionFailure

            DreamRepository.create = refuse  # type: ignore[method-assign]
            try:
                with pytest.raises(InjectedAdmissionFailure):
                    await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)
            finally:
                DreamRepository.create = original  # type: ignore[method-assign]

            # Rolled back together: no owner on the pin, no run, intent not advanced.
            assert await _pin_owner(runtime.primary_database, scope_id, pin.snapshot_id) is None
            assert await _intent_generation(runtime.primary_database, scope_id, binding) == before_generation
            runs = await runtime.dream.for_scope(scope_id, principal_id="runtime").list(ListDreamRunsRequest())
            assert runs.runs == ()

            # The pin is still claimable by a real admission afterwards.
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)
            assert await _pin_owner(runtime.primary_database, scope_id, pin.snapshot_id) == run.run_id

    asyncio.run(scenario())


def test_bound_run_refuses_execution_instead_of_running_locally(tmp_path: Path) -> None:
    """A pin-bound run fails closed at claim time and never reaches a generator.

    This is the interim guard, not the finished enforcement: it refuses the attempt so no
    unpinned evidence is read and no model call is made on a shared run's behalf.
    """

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'refuse.db'}")
        generator = Generator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            scope = await runtime.scopes.create(
                ScopeDraft(title="Admission", summary="Refusal", idempotency_key="admission")
            )
            scope_id = scope.scope_id
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_ADMIN)
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_CONTRIBUTOR)
            pin = await _record_pin(runtime.primary_database, access, scope_id, key="discover-1")
            citation = await _seed_evidence(runtime, scope_id)

            service = runtime._dream_service
            assert service is not None
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="admit-1"
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)
            calls_before = len(generator.inputs)

            # The owning claim marks a pin-bound run terminal instead of letting the
            # ordinary local path execute it.
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, run.run_id)
                claimed = await service.repository.claim(connection, record, model_config_id=None)
            assert claimed.run.status == "failed"
            assert claimed.run.error == "shared_use_unenforced"
            assert len(generator.inputs) == calls_before

            # An ordinary run in the same Scope is untouched by this guard.
            ordinary = await service._admit(
                scope_id,
                "runtime",
                request.model_copy(update={"idempotency_key": "ordinary-1"}),
                snapshot_id=None,
            )
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, ordinary.run_id)
                claimed = await service.repository.claim(connection, record, model_config_id=None)
            assert claimed.run.status == "running"
            assert claimed.run.error is None

    asyncio.run(scenario())
