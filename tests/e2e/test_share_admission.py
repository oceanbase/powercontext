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

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.authz import AccessRole, PrincipalRef, ResourceRef
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.service import AccessAuditContext, CreateBinding
from powercontext.builtin.dream.bindings import binding_for_request
from powercontext.builtin.dream.models import (
    CreateDreamRunRequest,
    DreamError,
    GetDreamRunRequest,
    ListDreamRunsRequest,
)
from powercontext.builtin.evidence.models import content_digest
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.dream import DreamRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_INTENTS_TABLE, SHARE_SNAPSHOTS_TABLE
from powercontext.builtin.runtime import BuiltinConfig
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.builtin.runtime.relational import BUILTIN_ARTIFACT_TYPES
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
from tests.e2e.dream_support import atomic_memory_pipeline, open_dream_runtime, process_pending
from tests.e2e.test_artifact_dreaming import Generator, MemoryPipeline, NoChangeGenerator

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


def test_uncomposed_bound_run_refuses_before_model_input(tmp_path: Path) -> None:
    """Without the composed eligibility owners a bound run still fails closed.

    This is the production state: nothing composes the owners, so the run is refused at the
    resolution boundary and never reaches a generator.
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

            # Resolution refuses, so the attempt records the stable code and no model
            # input is assembled for a shared run.
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, run.run_id)
                try:
                    await service._resolve(record, connection)
                except DreamError as error:
                    assert error.code == "shared_use_unenforced"
                else:
                    pytest.fail("a bound run resolved without composed owners")
            assert len(generator.inputs) == calls_before

            # An ordinary run in the same Scope still resolves and is untouched.
            ordinary = await service._admit(
                scope_id,
                "runtime",
                request.model_copy(update={"idempotency_key": "ordinary-1"}),
                snapshot_id=None,
            )
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, ordinary.run_id)
                resolved = await service._resolve(record, connection)
            assert resolved.manifest.nodes

    asyncio.run(scenario())


def _compose_sharing(runtime, access, principal: PrincipalRef) -> None:
    """Wire the trusted composition that may enforce a bound run's pin.

    Only tests call this. Production composes none of these, so a bound run still refuses.
    """

    service = runtime._dream_service
    assert service is not None
    service.share_access = access
    service.share_scopes = runtime.scopes
    service.share_principal = lambda _principal_id: principal


def test_bound_run_uses_only_its_pin_and_reaches_no_change(tmp_path: Path) -> None:
    """An enforced bound run resolves exactly its pin and completes without a Candidate."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'enforced.db'}")
        generator = NoChangeGenerator()
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
                ScopeDraft(title="Shared", summary="Enforced", idempotency_key="shared")
            )
            scope_id = scope.scope_id
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_ADMIN)
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_CONTRIBUTOR)
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_VIEWER)
            pin = await _record_pin(runtime.primary_database, access, scope_id, key="discover-1")
            citation = await _seed_evidence(runtime, scope_id)

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="shared-1"
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)

            # Resolve the bound run: the only admitted qualified input is the pin's.
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, run.run_id)
                resolved = await service._resolve(record, connection)
            qualified = {
                (node.origin_scope_id, node.digest) for node in resolved.manifest.nodes if node.origin_scope_id
            }
            # The pinned origin is admitted as a qualified node in its owning Scope. A
            # resolved node may additionally contribute a child (the Artifact projection),
            # which is why this is a subset check and not set equality.
            assert {(item.origin_scope_id, item.digest) for item in pin.inputs} <= qualified
            # Nothing outside the pin's Scope is admitted.
            assert {origin for origin, _ in qualified} == {scope_id}

    asyncio.run(scenario())


def test_bound_run_completes_through_the_worker_as_no_change(tmp_path: Path) -> None:
    """An enforced pin-bound run executes on the real Worker path and records no-change."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}")
        generator = NoChangeGenerator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            scope = await runtime.scopes.create(ScopeDraft(title="Shared", summary="Worker", idempotency_key="shared"))
            scope_id = scope.scope_id
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_ADMIN)
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_CONTRIBUTOR)
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_VIEWER)
            pin = await _record_pin(runtime.primary_database, access, scope_id, key="discover-1")
            citation = await _seed_evidence(runtime, scope_id)

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="shared-1"
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)
            # `_admit` is the private seam, so the Supervisor wake the scoped application
            # performs on the public path is issued here explicitly.
            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            supervisor.wake(binding_for_request(request))
            await process_pending(runtime)

            finished = await runtime.dream.for_scope(scope_id, principal_id="runtime").get(
                GetDreamRunRequest(run_id=run.run_id)
            )
            assert finished.status == "succeeded"
            assert finished.outcome == "no_change"
            # A no-change result creates no Candidate: adopted-set binding and approval are
            # not implemented yet, so a shared run may not produce one.
            assert finished.candidate is None
            assert len(generator.inputs) == 1
            # The input the model saw was the pinned material, in its owning Scope.
            manifest = finished.input_manifest
            assert manifest is not None
            assert any(node.origin_scope_id == scope_id for node in manifest.nodes)

    asyncio.run(scenario())


def test_revoked_origin_authority_blocks_generation_with_no_model_call(tmp_path: Path) -> None:
    """A discriminating origin revocation refuses a bound run while the target stays current."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'revoke.db'}")
        generator = NoChangeGenerator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            scope = await runtime.scopes.create(ScopeDraft(title="Shared", summary="Revoke", idempotency_key="shared"))
            scope_id = scope.scope_id
            admin_binding = await _grant(access, OWNER, scope_id, AccessRole.SCOPE_ADMIN)
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_CONTRIBUTOR)
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_VIEWER)
            pin = await _record_pin(runtime.primary_database, access, scope_id, key="discover-1")
            citation = await _seed_evidence(runtime, scope_id)

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="shared-1"
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)

            # Withdraw only the acceptor's administration. The target stays current and
            # the recorded acceptance row is unchanged.
            await access.revoke_binding(
                ADMIN,
                admin_binding,
                expected_version=1,
                idempotency_key="revoke-owner-admin",
                context=AUDIT,
            )
            assert (await runtime.scopes.get(scope_id)).scope_id == scope_id

            calls_before = len(generator.inputs)
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, run.run_id)
                with pytest.raises(DreamError) as refused:
                    await service._resolve(record, connection)
            # The recorded acceptance no longer carries current authority, so the pin is
            # not eligible; no evidence is assembled and no model call is made.
            assert refused.value.code == "access_revoked"
            assert len(generator.inputs) == calls_before == 0

    asyncio.run(scenario())


def test_unsupported_use_and_ineligible_operation_stay_fail_closed(tmp_path: Path) -> None:
    """A rule naming an unsupported use, or an operation that cannot admit the pin, refuses."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'closed.db'}")
        generator = NoChangeGenerator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            scope = await runtime.scopes.create(ScopeDraft(title="Closed", summary="Closed", idempotency_key="closed"))
            scope_id = scope.scope_id
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_ADMIN)
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_CONTRIBUTOR)
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_VIEWER)
            sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            async with runtime.primary_database.transaction() as connection:
                stored = await sources.add(
                    connection,
                    scope_id,
                    ContentSource(name="t", materialization=SourceMaterialization.CAPTURED, content=ROOT_TEXT),
                )
            digest = content_digest(stored.value.model_dump_json().encode())
            citation = await _seed_evidence(runtime, scope_id)

            # A use label this checkpoint cannot enforce is recorded but never coverable.
            service_agreement = ShareAgreementService(
                runtime.primary_database, access=access, scopes=ScopeRepository(), sources=sources
            )
            unsupported = await service_agreement.create(
                scope_id,
                ShareRuleDraft(
                    selection=ScopeSelection(mode="exact", scope_ids=(scope_id,)),
                    terms=ShareRuleTerms(use="training-corpus", audience="project-members"),
                ),
                principal=OWNER,
                context=AUDIT,
            )
            await service_agreement.accept(
                scope_id,
                unsupported.rule_id,
                unsupported.version,
                principal=OWNER,
                side="origin",
                covered_scope_ids=(scope_id,),
                context=AUDIT,
            )
            async with runtime.primary_database.transaction() as connection:
                descriptor = await ScopeRepository().get(connection, scope_id)
            assert descriptor is not None
            unsupported_pin = await service_agreement.pin(
                scope_id,
                ShareSnapshotDraft(
                    rule_id=unsupported.rule_id,
                    rule_version=unsupported.version,
                    terms_digest=unsupported.terms_digest,
                    membership_digest=membership_digest(((scope_id, descriptor.version),)),
                    inputs=(ShareInput(kind="source", origin_scope_id=scope_id, source=stored.ref, digest=digest),),
                    notification_key="unsupported-use",
                ),
                principal=OWNER,
                context=AUDIT,
            )

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="closed-1"
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=unsupported_pin.snapshot_id)

            calls_before = len(generator.inputs)
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, run.run_id)
                with pytest.raises(DreamError) as refused:
                    await service._resolve(record, connection)
            assert refused.value.code == "shared_use_unsupported"
            assert len(generator.inputs) == calls_before == 0

    asyncio.run(scenario())


class _BodyDenyingAccess:
    """The real Access service with ARTIFACT_READ denied, to isolate the body permission.

    Only the body grant is removed; every other decision is the real provider's. This
    proves the eligibility check asks for the body right rather than accepting scope.read.
    """

    def __init__(self, access) -> None:
        self._access = access

    def __getattr__(self, name):
        return getattr(self._access, name)

    def with_connection(self, connection):
        return _BodyDenyingAccess(self._access.with_connection(connection))

    async def require(self, principal, action, resource, *, context):
        from powercontext.builtin.authz.errors import AccessDeniedError
        from powercontext.builtin.authz.models import AccessAction
        from powercontext.builtin.authz.service import AccessDecision

        if action is AccessAction.ARTIFACT_READ:
            raise AccessDeniedError
        await self._access.require(principal, action, resource, context=context)
        return AccessDecision(allowed=True, reason_code="allowed", policy_revision=None)


def test_scope_read_alone_does_not_replace_artifact_body_read(tmp_path: Path) -> None:
    """An Artifact-owned pin needs artifact.read; scope.read is not body access."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'body.db'}")
        generator = NoChangeGenerator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            scope = await runtime.scopes.create(ScopeDraft(title="Body", summary="Body", idempotency_key="body"))
            scope_id = scope.scope_id
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_ADMIN)
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_CONTRIBUTOR)
            # Scope read and contribution, but no Artifact body grant.
            await _grant(access, OWNER, scope_id, AccessRole.SCOPE_VIEWER)
            pin = await _record_pin(runtime.primary_database, access, scope_id, key="body-1")
            citation = await _seed_evidence(runtime, scope_id)

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="body-1"
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)

            # The pin's own input is a Source, which stays Scope-owned and therefore
            # readable. That run is eligible, proving the split is not a blanket denial.
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, run.run_id)
                resolved = await service._resolve(record, connection)
            assert resolved.manifest.nodes

            # Every content role grants ARTIFACT_READ as a child action, so this split is
            # proven at the provider boundary: the same principals and bindings, with the
            # body permission denied for the exact Artifact.
            deny_body = _BodyDenyingAccess(access)
            service.share_access = deny_body

            # Now pin the Experience Artifact itself, which this principal may not read
            # as a body even though it can read the Scope.
            # An Experience revision is Artifact-owned, so reading its body needs
            # artifact.read; the atomic-memory citation above does not, which is the point.
            from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceDraft

            artifacts = ArtifactRepository(BUILTIN_ARTIFACT_TYPES, sources=SourceRepository(BUILTIN_SOURCE_REGISTRY))
            async with runtime.primary_database.transaction() as connection:
                artifact = await artifacts.create(
                    connection,
                    scope_id,
                    "exp-body",
                    ExperienceDraft(content=ExperienceContent(situation="s", action="a", outcome="o", lesson="l")),
                )
            experience_ref = ArtifactRef(family="experience", artifact_id="exp-body", revision=1)
            artifact_digest = content_digest(artifact.model_dump_json().encode())

            agreement_sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            agreement = ShareAgreementService(
                runtime.primary_database,
                access=access,
                scopes=ScopeRepository(),
                sources=agreement_sources,
                artifacts=ArtifactRepository(BUILTIN_ARTIFACT_TYPES, sources=agreement_sources),
            )
            rule = await agreement.create(
                scope_id,
                ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(scope_id,)), terms=_terms()),
                principal=OWNER,
                context=AUDIT,
            )
            await agreement.accept(
                scope_id,
                rule.rule_id,
                rule.version,
                principal=OWNER,
                side="origin",
                covered_scope_ids=(scope_id,),
                context=AUDIT,
            )
            async with runtime.primary_database.transaction() as connection:
                descriptor = await ScopeRepository().get(connection, scope_id)
            assert descriptor is not None
            artifact_pin = await agreement.pin(
                scope_id,
                ShareSnapshotDraft(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    terms_digest=rule.terms_digest,
                    membership_digest=membership_digest(((scope_id, descriptor.version),)),
                    inputs=(
                        ShareInput(
                            kind="artifact",
                            origin_scope_id=scope_id,
                            artifact=experience_ref,
                            digest=artifact_digest,
                        ),
                    ),
                    notification_key="body-artifact",
                ),
                principal=OWNER,
                context=AUDIT,
            )

            calls_before = len(generator.inputs)
            bound = await service._admit(
                scope_id,
                "runtime",
                request.model_copy(update={"idempotency_key": "body-2"}),
                snapshot_id=artifact_pin.snapshot_id,
            )
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, bound.run_id)
                with pytest.raises(DreamError) as refused:
                    await service._resolve(record, connection)
            assert refused.value.code == "access_revoked"
            assert len(generator.inputs) == calls_before

    asyncio.run(scenario())
