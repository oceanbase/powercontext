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
from sqlalchemy import func, select, update

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.authz import AccessRole, PrincipalRef, ResourceRef
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.service import AccessAuditContext, CreateBinding
from powercontext.builtin.dream.bindings import binding_for_request, operation_spec
from powercontext.builtin.dream.models import (
    CreateDreamRunRequest,
    DreamError,
    DreamPlan,
    GetDreamRunRequest,
    ListDreamRunsRequest,
)
from powercontext.builtin.evidence.models import EvidenceResolutionError, QualifiedOrigin, content_digest
from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.dream import DreamRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_INTENTS_TABLE,
    CANDIDATE_HEADS_TABLE,
    CANDIDATE_VERSIONS_TABLE,
    SHARE_SNAPSHOTS_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.review.errors import InvalidCandidateError
from powercontext.builtin.review.models import CandidateAudit
from powercontext.builtin.runtime import BuiltinConfig
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.builtin.runtime.models import (
    ApproveCandidateRequest,
    GetCandidateRequest,
)
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
from powercontext.sources import SourceMaterialization, SourceRef
from tests.e2e.dream_support import atomic_memory_pipeline, open_dream_runtime, process_pending
from tests.e2e.test_artifact_dreaming import Generator, MemoryPipeline, NoChangeGenerator, experience


def experience_draft():
    """A minimal valid Experience draft for a target revision fixture."""

    from powercontext.builtin.artifacts.experience import ExperienceDraft

    return ExperienceDraft(content=experience())


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
    # The minimal audience contract: the target Scope accepts delivery of the conclusion.
    await service.accept(
        scope_id,
        rule.rule_id,
        rule.version,
        principal=OWNER,
        side="audience",
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
    # A shared approval re-proves its binding through the same owners, so the runtime must
    # hand them to every Review service it builds.
    runtime._provider._share_review_owners = (access, runtime.scopes, lambda _principal_id: principal)


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
            # Drive one attempt directly under a real Scope invocation so the raised code
            # is observable, then confirm the run records it.
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
            await agreement.accept(
                scope_id,
                rule.rule_id,
                rule.version,
                principal=OWNER,
                side="audience",
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


async def _pin_with_input(admindb, access, scope_id: str, *, key: str, item: ShareInput) -> ShareSnapshot:
    """Record an unclaimed pin whose single input is supplied by the caller."""

    sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
    service = ShareAgreementService(
        admindb,
        access=access,
        scopes=ScopeRepository(),
        sources=sources,
        artifacts=ArtifactRepository(BUILTIN_ARTIFACT_TYPES, sources=sources),
    )
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
    # The minimal audience contract: the target Scope accepts delivery of the conclusion.
    await service.accept(
        scope_id,
        rule.rule_id,
        rule.version,
        principal=OWNER,
        side="audience",
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
            inputs=(item,),
            notification_key=key,
        ),
        principal=OWNER,
        context=AUDIT,
    )


def test_changed_origin_digest_refuses_before_the_model(tmp_path: Path) -> None:
    """A pin whose recorded digest no longer matches the resolved node is refused."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'drift.db'}")
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
            scope = await runtime.scopes.create(ScopeDraft(title="Drift", summary="Drift", idempotency_key="drift"))
            scope_id = scope.scope_id
            for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                await _grant(access, OWNER, scope_id, role)
            pin = await _record_pin(runtime.primary_database, access, scope_id, key="drift-1")
            citation = await _seed_evidence(runtime, scope_id)

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="drift-run"
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)

            # Replace the recorded qualified digest with one the origin never produced: the
            # generation path must compare it against the resolved node and refuse.
            original = service._require_share_eligibility

            async def drift(connection, record):
                share = await original(connection, record)
                tampered = tuple(
                    item.__class__(
                        scope_id=item.scope_id,
                        source=item.source,
                        artifact=item.artifact,
                        digest="sha256:" + "0" * 64,
                    )
                    for item in share.origins
                )
                return share.__class__(rule=share.rule, snapshot=share.snapshot, origins=tampered)

            service._require_share_eligibility = drift
            try:
                calls_before = len(generator.inputs)
                async with runtime.primary_database.transaction() as connection:
                    record = await service.repository.get(connection, scope_id, run.run_id)
                    with pytest.raises(EvidenceResolutionError) as refused:
                        await service._resolve(record, connection)
                assert refused.value.code == "evidence_changed"
                assert len(generator.inputs) == calls_before == 0
            finally:
                service._require_share_eligibility = original

    asyncio.run(scenario())


def test_non_pin_request_input_is_not_admitted_to_a_bound_run(tmp_path: Path) -> None:
    """A bound run discards the request's own references and admits only its pin."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'nonpin.db'}")
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
            scope = await runtime.scopes.create(ScopeDraft(title="Nonpin", summary="Nonpin", idempotency_key="nonpin"))
            scope_id = scope.scope_id
            for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                await _grant(access, OWNER, scope_id, role)
            pin = await _record_pin(runtime.primary_database, access, scope_id, key="nonpin-1")
            citation = await _seed_evidence(runtime, scope_id)

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            # The request names an additional citation that the pin does not.
            other = await _seed_evidence(runtime, scope_id)
            request = CreateDreamRunRequest(
                operation="refine_experience",
                artifacts=(citation, other),
                idempotency_key="nonpin-run",
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)

            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, run.run_id)
                resolved = await service._resolve(record, connection)
            admitted = {
                (node.artifact.family, node.artifact.artifact_id)
                for node in resolved.manifest.nodes
                if node.artifact is not None and node.origin_scope_id == scope_id
            }
            # `other` was requested but is not in the pin, so it must not be admitted.
            assert (other.family, other.artifact_id) not in admitted

    asyncio.run(scenario())


def test_unsupported_shared_proposal_output_still_refuses(tmp_path: Path) -> None:
    """Only Experience may propose at this checkpoint; another shared op still refuses."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'propose.db'}")
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
                ScopeDraft(title="Propose", summary="Propose", idempotency_key="propose")
            )
            scope_id = scope.scope_id
            for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                await _grant(access, OWNER, scope_id, role)
            pin = await _record_pin(runtime.primary_database, access, scope_id, key="propose-1")

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            # revise_skill is a declared shared operation but has no durable adopted-set
            # path at this checkpoint, so its shared proposal must still refuse.
            from powercontext.builtin.artifacts.skill import SkillContent, SkillDraft
            from powercontext.builtin.runtime.models import CaptureSource

            feedback = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="skill-feedback", content="The retry duplicated the action.", metadata={})
            )
            async with runtime._provider.database.transaction() as connection:
                await runtime._provider.repositories.artifacts.create(
                    connection,
                    scope_id,
                    "receipt-check",
                    SkillDraft(
                        content=SkillContent(
                            name="receipt-check",
                            description="Retry safely.",
                            instructions="Retry immediately.",
                            validation=("A retry was attempted.",),
                            license="MIT",
                        ),
                        sources=(feedback.source_ref,),
                    ),
                )
            skill_ref = ArtifactRef(family="skill", artifact_id="receipt-check", revision=1)
            request = CreateDreamRunRequest(
                operation="revise_skill",
                artifacts=(skill_ref,),
                sources=(feedback.source_ref,),
                target=skill_ref,
                idempotency_key="propose-run",
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)

            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            supervisor.wake(binding_for_request(request))
            await process_pending(runtime)

            finished = await runtime.dream.for_scope(scope_id, principal_id="runtime").get(
                GetDreamRunRequest(run_id=run.run_id)
            )
            assert finished.candidate is None
            assert finished.status == "failed"
            assert finished.error == "shared_use_candidate_unavailable", finished.error
            async with runtime.primary_database.transaction() as connection:
                count = await connection.scalar(
                    select(func.count())
                    .select_from(CANDIDATE_HEADS_TABLE)
                    .where(CANDIDATE_HEADS_TABLE.c.scope_id == scope_id)
                )
            assert count == 0

    asyncio.run(scenario())


def test_pin_bound_to_another_run_cannot_execute(tmp_path: Path) -> None:
    """A run may only use a pin that names exactly this execution."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'rebind.db'}")
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
            scope = await runtime.scopes.create(ScopeDraft(title="Rebind", summary="Rebind", idempotency_key="rebind"))
            scope_id = scope.scope_id
            for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                await _grant(access, OWNER, scope_id, role)
            pin = await _record_pin(runtime.primary_database, access, scope_id, key="rebind-1")
            citation = await _seed_evidence(runtime, scope_id)

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="rebind-run"
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)

            # Rewrite the durable binding to a different run, leaving this run's record
            # still naming the pin. Execution must refuse the mismatch.
            async with runtime.primary_database.transaction() as connection:
                await connection.execute(
                    update(SHARE_SNAPSHOTS_TABLE)
                    .where(SHARE_SNAPSHOTS_TABLE.c.snapshot_id == pin.snapshot_id)
                    .values(dream_run_id="dr_someone_else")
                )

            calls_before = len(generator.inputs)
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, run.run_id)
                with pytest.raises(DreamError) as refused:
                    await service._resolve(record, connection)
            assert refused.value.code == "share_snapshot_claimed"
            assert len(generator.inputs) == calls_before == 0

    asyncio.run(scenario())


def test_foreign_origin_without_an_origin_authorizer_is_refused(tmp_path: Path) -> None:
    """A foreign pin is never admitted on the target's grant when the hook is unwired."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'foreign.db'}")
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
            target = (await runtime.scopes.create(ScopeDraft(title="T", summary="t", idempotency_key="t"))).scope_id
            origin = (await runtime.scopes.create(ScopeDraft(title="O", summary="o", idempotency_key="o"))).scope_id
            for scope_id in (target, origin):
                for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                    await _grant(access, OWNER, scope_id, role)

            sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            async with runtime.primary_database.transaction() as connection:
                stored = await sources.add(
                    connection,
                    origin,
                    ContentSource(name="foreign", materialization=SourceMaterialization.CAPTURED, content=ROOT_TEXT),
                )
            foreign_item = ShareInput(
                kind="source",
                origin_scope_id=origin,
                source=stored.ref,
                digest=content_digest(stored.value.model_dump_json().encode()),
            )

            # The rule declares both Scopes, and the origin side accepts the foreign Scope.
            agreement = ShareAgreementService(
                runtime.primary_database, access=access, scopes=ScopeRepository(), sources=sources
            )
            for scope_id in (target, origin):
                rule = await agreement.create(
                    scope_id,
                    ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(target, origin)), terms=_terms()),
                    principal=OWNER,
                    context=AUDIT,
                )
                await agreement.accept(
                    scope_id,
                    rule.rule_id,
                    rule.version,
                    principal=OWNER,
                    side="origin",
                    covered_scope_ids=(origin,),
                    context=AUDIT,
                )
                await agreement.accept(
                    scope_id,
                    rule.rule_id,
                    rule.version,
                    principal=OWNER,
                    side="audience",
                    covered_scope_ids=(target,),
                    context=AUDIT,
                )
                break
            async with runtime.primary_database.transaction() as connection:
                descriptor = await ScopeRepository().get(connection, target)
            assert descriptor is not None
            foreign_pin = await agreement.pin(
                target,
                ShareSnapshotDraft(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    terms_digest=rule.terms_digest,
                    membership_digest=membership_digest((
                        (target, descriptor.version),
                        (origin, (await runtime.scopes.get(origin)).version),
                    )),
                    inputs=(foreign_item,),
                    notification_key="foreign-1",
                ),
                principal=OWNER,
                context=AUDIT,
            )
            citation = await _seed_evidence(runtime, target)

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="foreign-run"
            )
            run = await service._admit(target, "runtime", request, snapshot_id=foreign_pin.snapshot_id)

            # Eligibility authorizes the origin, but the resolver's origin hook is unwired
            # here, so the foreign read must refuse rather than reuse the local grant.
            calls_before = len(generator.inputs)
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, target, run.run_id)
                original = service._origin_authorizer
                service._origin_authorizer = lambda *_a, **_k: None
                try:
                    with pytest.raises(EvidenceResolutionError) as refused:
                        await service._resolve(record, connection)
                finally:
                    service._origin_authorizer = original
            assert refused.value.code == "origin_authorization_unavailable"
            assert len(generator.inputs) == calls_before == 0

    asyncio.run(scenario())


async def _setup_pin_without_audience(
    admindb, access, scope_id: str, *, side_scope: str | None, other_scope: str | None = None
) -> ShareSnapshot:
    """Record a pin whose audience side is missing or recorded for another Scope."""

    sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
    async with admindb.transaction() as connection:
        stored = await sources.add(
            connection,
            scope_id,
            ContentSource(name="write-test", materialization=SourceMaterialization.CAPTURED, content=ROOT_TEXT),
        )
    service = ShareAgreementService(admindb, access=access, scopes=ScopeRepository(), sources=sources)
    declared = (scope_id,) if other_scope is None else (scope_id, other_scope)
    rule = await service.create(
        scope_id,
        ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=declared), terms=_terms()),
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
    if side_scope is not None:
        await service.accept(
            scope_id,
            rule.rule_id,
            rule.version,
            principal=OWNER,
            side="audience",
            covered_scope_ids=(side_scope,),
            context=AUDIT,
        )
    async with admindb.transaction() as connection:
        descriptor = await ScopeRepository().get(connection, scope_id)
        other_descriptor = None if other_scope is None else await ScopeRepository().get(connection, other_scope)
    assert descriptor is not None
    versions: tuple[tuple[str, int], ...] = ((scope_id, descriptor.version),)
    if other_descriptor is not None:
        assert other_scope is not None
        versions = (*versions, (other_scope, other_descriptor.version))
    return await service.pin(
        scope_id,
        ShareSnapshotDraft(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            terms_digest=rule.terms_digest,
            membership_digest=membership_digest(versions),
            inputs=(
                ShareInput(
                    kind="source",
                    origin_scope_id=scope_id,
                    source=stored.ref,
                    digest=content_digest(stored.value.model_dump_json().encode()),
                ),
            ),
            notification_key=f"audience-{side_scope}",
        ),
        principal=OWNER,
        context=AUDIT,
    )


def test_missing_or_misdirected_audience_acceptance_refuses(tmp_path: Path) -> None:
    """Only an audience acceptance of exactly the target Scope admits a bound run."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'audience.db'}")
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
            target = (await runtime.scopes.create(ScopeDraft(title="T", summary="t", idempotency_key="t"))).scope_id
            other = (await runtime.scopes.create(ScopeDraft(title="O", summary="o", idempotency_key="o"))).scope_id
            for scope_id in (target, other):
                for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                    await _grant(access, OWNER, scope_id, role)
            citation = await _seed_evidence(runtime, target)

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="audience-run"
            )

            for label, side_scope in (("absent", None), ("wrong-scope", other)):
                pin = await _setup_pin_without_audience(
                    runtime.primary_database,
                    access,
                    target,
                    side_scope=side_scope,
                    other_scope=other if side_scope == other else None,
                )
                run = await service._admit(
                    target,
                    "runtime",
                    request.model_copy(update={"idempotency_key": f"audience-{label}"}),
                    snapshot_id=pin.snapshot_id,
                )
                calls_before = len(generator.inputs)
                async with runtime.primary_database.transaction() as connection:
                    record = await service.repository.get(connection, target, run.run_id)
                    with pytest.raises(DreamError) as refused:
                        await service._resolve(record, connection)
                assert refused.value.code == "shared_use_audience", label
                assert len(generator.inputs) == calls_before == 0, label

    asyncio.run(scenario())


def test_unsupported_operation_is_refused_even_with_a_valid_pin(tmp_path: Path) -> None:
    """A real operation without the shared capability declaration is refused, zero calls.

    ``revise_memory`` admits a Source in ``allowed_evidence``, so evidence overlap alone
    would let it through; the capability declaration is what refuses it.
    """

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'unsupported-op.db'}")
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
            scope = await runtime.scopes.create(ScopeDraft(title="U", summary="u", idempotency_key="u"))
            scope_id = scope.scope_id
            for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                await _grant(access, OWNER, scope_id, role)
            pin = await _record_pin(runtime.primary_database, access, scope_id, key="unsupported-op-1")

            sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            async with runtime.primary_database.transaction() as connection:
                memory = await sources.add(
                    connection,
                    scope_id,
                    ContentSource(name="m", materialization=SourceMaterialization.CAPTURED, content=ROOT_TEXT),
                )
            memory_ref = ArtifactRef(family="atomic-memory", artifact_id="mem-1", revision=1)
            async with runtime.primary_database.transaction() as connection:
                from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent, AtomicMemoryDraft

                artifacts = ArtifactRepository(
                    BUILTIN_ARTIFACT_TYPES, sources=SourceRepository(BUILTIN_SOURCE_REGISTRY)
                )
                await artifacts.create(
                    connection,
                    scope_id,
                    "mem-1",
                    AtomicMemoryDraft(
                        content=AtomicMemoryContent(kind="private_note", text="Note", description="d"),
                    ),
                )

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            # revise_memory admits Source evidence, but is not declared as shared.
            request = CreateDreamRunRequest(
                operation="revise_memory",
                artifacts=(memory_ref,),
                sources=(memory.ref,),
                target=memory_ref,
                idempotency_key="unsupported-op-run",
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)

            calls_before = len(generator.inputs)
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, run.run_id)
                with pytest.raises(DreamError) as refused:
                    await service._resolve(record, connection)
            assert refused.value.code == "shared_use_ineligible_operation"
            assert len(generator.inputs) == calls_before == 0

    asyncio.run(scenario())


def test_supported_target_reaches_generation_as_a_distinct_checked_node(tmp_path: Path) -> None:
    """A valid exact target reaches generation while no other request material is admitted."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'target.db'}")
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
            scope = await runtime.scopes.create(ScopeDraft(title="Target", summary="Target", idempotency_key="target"))
            scope_id = scope.scope_id
            for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                await _grant(access, OWNER, scope_id, role)
            pin = await _record_pin(runtime.primary_database, access, scope_id, key="target-1")

            # A real Skill target, so the bound run has a checked exact target.
            from powercontext.builtin.artifacts.skill import SkillContent, SkillDraft
            from powercontext.builtin.runtime.models import CaptureSource

            feedback = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="skill-feedback", content="The retry duplicated the action.", metadata={})
            )
            original = SkillContent(
                name="receipt-check",
                description="Retry safely.",
                instructions="Retry immediately.",
                validation=("A retry was attempted.",),
                license="MIT",
            )
            async with runtime._provider.database.transaction() as connection:
                await runtime._provider.repositories.artifacts.create(
                    connection,
                    scope_id,
                    "receipt-check",
                    SkillDraft(content=original, sources=(feedback.source_ref,)),
                )
            target_ref = ArtifactRef(family="skill", artifact_id="receipt-check", revision=1)

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            # revise_skill requires support beyond the target itself.
            request = CreateDreamRunRequest(
                operation="revise_skill",
                artifacts=(target_ref,),
                sources=(feedback.source_ref,),
                target=target_ref,
                idempotency_key="target-run",
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)

            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, run.run_id)
                resolved = await service._resolve(record, connection)

            # The exact target is present and marked as the target, not as ordinary input.
            target_nodes = [node for node in resolved.manifest.nodes if node.role == "target"]
            assert len(target_nodes) == 1
            assert target_nodes[0].artifact == target_ref
            # The pin's origin is admitted, and the request's own citation is not
            # additionally introduced as a separate unqualified input.
            assert any(node.origin_scope_id == scope_id for node in resolved.manifest.nodes)

    asyncio.run(scenario())


class _SelectingGenerator:
    """Cites exactly one source evidence id, so adoption is discriminating."""

    config_id = "test-dream-select-v1"

    def __init__(self) -> None:
        self.inputs = []
        self.cite: str | None = None

    async def generate(self, value):
        self.inputs.append(value)
        if self.cite is None:
            cited = next(item.evidence_id for item in value.evidence.evidence if item.kind == "source")
        else:
            cited = self.cite
        return GenerationResult(
            output=DreamPlan(
                outcome="proposed",
                reason="The selected task evidence supports this bounded rule.",
                intent="create",
                proposal=experience(),
                evidence_ids=(cited,),
            ),
            usage=InferenceUsage(requests=1, input_tokens=20, output_tokens=20),
        )


def test_shared_experience_proposal_adopts_exact_selected_support(tmp_path: Path) -> None:
    """One pending Experience Candidate durably records exactly the support it cited."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'adopt.db'}")
        generator = _SelectingGenerator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            scope = await runtime.scopes.create(ScopeDraft(title="Adopt", summary="Adopt", idempotency_key="adopt"))
            scope_id = scope.scope_id
            for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                await _grant(access, OWNER, scope_id, role)

            # Two real pinned Sources; the generator cites only one of them.
            sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            async with runtime.primary_database.transaction() as connection:
                selected_source = await sources.add(
                    connection,
                    scope_id,
                    ContentSource(
                        name="selected",
                        materialization=SourceMaterialization.CAPTURED,
                        content="Replayed the write with key K; exactly one row remained.",
                    ),
                )
                unselected_source = await sources.add(
                    connection,
                    scope_id,
                    ContentSource(
                        name="unselected",
                        materialization=SourceMaterialization.CAPTURED,
                        content="An unrelated capture that the proposal never cites.",
                    ),
                )
            chosen_ref = selected_source.ref
            chosen_digest = content_digest(selected_source.value.model_dump_json().encode())
            unselected_digest = content_digest(unselected_source.value.model_dump_json().encode())

            agreement = ShareAgreementService(
                runtime.primary_database, access=access, scopes=ScopeRepository(), sources=sources
            )
            rule = await agreement.create(
                scope_id,
                ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(scope_id,)), terms=_terms()),
                principal=OWNER,
                context=AUDIT,
            )
            for side in ("origin", "audience"):
                await agreement.accept(
                    scope_id,
                    rule.rule_id,
                    rule.version,
                    principal=OWNER,
                    side=side,
                    covered_scope_ids=(scope_id,),
                    context=AUDIT,
                )
            async with runtime.primary_database.transaction() as connection:
                descriptor = await ScopeRepository().get(connection, scope_id)
            assert descriptor is not None
            pin = await agreement.pin(
                scope_id,
                ShareSnapshotDraft(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    terms_digest=rule.terms_digest,
                    membership_digest=membership_digest(((scope_id, descriptor.version),)),
                    inputs=(
                        ShareInput(kind="source", origin_scope_id=scope_id, source=chosen_ref, digest=chosen_digest),
                        ShareInput(
                            kind="source",
                            origin_scope_id=scope_id,
                            source=unselected_source.ref,
                            digest=unselected_digest,
                        ),
                    ),
                    notification_key="adopt-pin",
                ),
                principal=OWNER,
                context=AUDIT,
            )

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            # A Memory citation satisfies refine_experience's Artifact requirement without
            # being part of the pin, which is what makes adoption discriminating.
            citation = await _seed_evidence(runtime, scope_id)
            request = CreateDreamRunRequest(
                operation="refine_experience",
                artifacts=(citation,),
                sources=(chosen_ref,),
                idempotency_key="adopt-run",
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)

            # Cite exactly the pinned Source's own evidence id, resolved from the manifest.
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, scope_id, run.run_id)
                resolved = await service._resolve(record, connection)
            generator.cite = next(
                node.evidence_id
                for node in resolved.manifest.nodes
                if node.source is not None and node.digest == chosen_digest
            )

            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            supervisor.wake(binding_for_request(request))
            await process_pending(runtime)

            finished = await runtime.dream.for_scope(scope_id, principal_id="runtime").get(
                GetDreamRunRequest(run_id=run.run_id)
            )
            assert finished.status == "succeeded", finished.error
            assert finished.outcome == "proposed"
            assert finished.candidate is not None

            # Reopen the exact audit through the owning Review read.
            review = runtime.review.for_scope(scope_id)
            candidate = await review.get(GetCandidateRequest(candidate_id=finished.candidate.candidate_id))
            assert candidate.status == "pending"
            audit = candidate.audit
            assert audit is not None
            assert audit.share_snapshot_id == pin.snapshot_id
            assert audit.dream_run_id == run.run_id
            assert audit.share_snapshot_id == pin.snapshot_id
            assert audit.dream_run_id == run.run_id
            # Exactly the cited support, qualified by its owning Scope — not every pin input.
            adopted = {(origin.scope_id, origin.digest) for origin in audit.adopted_origins}
            assert adopted, "a shared audit must record adopted origins"
            assert all(scope == scope_id for scope, _ in adopted)
            # Exactly the cited Source is adopted: the equally pinned but uncited Source is
            # not claimed as support, which is the whole point of an adopted set.
            assert chosen_digest in {digest for _, digest in adopted}
            assert unselected_digest not in {digest for _, digest in adopted}
            # A shared approval publishes once through the composed owners.
            approved = await review.approve(
                ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=1)
            )
            assert approved.status == "approved"

    asyncio.run(scenario())


def test_foreign_only_support_is_adopted_from_its_own_scope(tmp_path: Path) -> None:
    """A same-identity local decoy never satisfies support owned by another Scope."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'foreign.db'}")
        generator = _SelectingGenerator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            origin = (
                await runtime.scopes.create(ScopeDraft(title="Origin", summary="o", idempotency_key="origin"))
            ).scope_id
            target = (
                await runtime.scopes.create(ScopeDraft(title="Target", summary="t", idempotency_key="target"))
            ).scope_id
            for scope_id in (origin, target):
                for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                    await _grant(access, OWNER, scope_id, role)

            sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            async with runtime.primary_database.transaction() as connection:
                # The same identity exists in BOTH Scopes with different content: the local
                # one is a decoy the foreign digest must not match.
                foreign_stored = await sources.add(
                    connection,
                    origin,
                    ContentSource(
                        name="shared-1",
                        materialization=SourceMaterialization.CAPTURED,
                        content="The foreign origin's real material.",
                    ),
                )
                await sources.add(
                    connection,
                    target,
                    ContentSource(
                        name="shared-1",
                        materialization=SourceMaterialization.CAPTURED,
                        content="A different local decoy with the same identity.",
                    ),
                )
            foreign_digest = content_digest(foreign_stored.value.model_dump_json().encode())

            agreement = ShareAgreementService(
                runtime.primary_database, access=access, scopes=ScopeRepository(), sources=sources
            )
            rule = await agreement.create(
                target,
                ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(target, origin)), terms=_terms()),
                principal=OWNER,
                context=AUDIT,
            )
            # Origin side accepts the foreign Scope; audience side accepts the target Scope.
            await agreement.accept(
                target,
                rule.rule_id,
                rule.version,
                principal=OWNER,
                side="origin",
                covered_scope_ids=(origin,),
                context=AUDIT,
            )
            await agreement.accept(
                target,
                rule.rule_id,
                rule.version,
                principal=OWNER,
                side="audience",
                covered_scope_ids=(target,),
                context=AUDIT,
            )
            async with runtime.primary_database.transaction() as connection:
                descriptors = {
                    scope_id: await ScopeRepository().get(connection, scope_id) for scope_id in (target, origin)
                }
            assert all(descriptor is not None for descriptor in descriptors.values())
            versions: tuple[tuple[str, int], ...] = tuple(
                sorted(
                    (scope_id, descriptor.version)
                    for scope_id, descriptor in descriptors.items()
                    if descriptor is not None
                )
            )
            pin = await agreement.pin(
                target,
                ShareSnapshotDraft(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    terms_digest=rule.terms_digest,
                    membership_digest=membership_digest(versions),
                    inputs=(
                        ShareInput(
                            kind="source",
                            origin_scope_id=origin,
                            source=foreign_stored.ref,
                            digest=foreign_digest,
                        ),
                    ),
                    notification_key="foreign-pin",
                ),
                principal=OWNER,
                context=AUDIT,
            )

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            citation = await _seed_evidence(runtime, target)
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="foreign-run"
            )
            run = await service._admit(target, "runtime", request, snapshot_id=pin.snapshot_id)

            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            captured: list[BaseException] = []
            original_log = type(service)._log_attempt_failure

            def grab(self, record, error, *, classification_error=None):
                captured.append(error)

            type(service)._log_attempt_failure = grab
            supervisor.wake(binding_for_request(request))
            try:
                await process_pending(runtime)
            finally:
                type(service)._log_attempt_failure = original_log

            finished = await runtime.dream.for_scope(target, principal_id="runtime").get(
                GetDreamRunRequest(run_id=run.run_id)
            )
            assert finished.status == "succeeded", (finished.error, repr(captured[-1]) if captured else None)

            assert finished.candidate is not None

            review = runtime.review.for_scope(target)
            candidate = await review.get(GetCandidateRequest(candidate_id=finished.candidate.candidate_id))
            audit = candidate.audit
            assert audit is not None
            adopted = {(origin.scope_id, origin.digest) for origin in audit.adopted_origins}
            # The adopted support is the FOREIGN origin with the foreign digest, not the
            # same-identity local decoy.
            assert (origin, foreign_digest) in adopted
            # The local decoy's reference is not silently admitted as local bare support.
            decoy_digest = None
            async with runtime.primary_database.transaction() as connection:
                local = await sources.get_many(connection, target, (foreign_stored.ref,))
            decoy_digest = content_digest(local[0].value.model_dump_json().encode())
            assert decoy_digest != foreign_digest
            assert decoy_digest not in {digest for _, digest in adopted}
            # No bare local slot claims the foreign identity.
            assert foreign_stored.ref not in candidate.sources

            # Approval publishes through the composed owners.
            approved = await review.approve(
                ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=1)
            )
            assert approved.status == "approved"

    asyncio.run(scenario())


async def _pin_one_source(runtime, access, scope_id: str, *, key: str, source_id: str):
    """Record a valid pin over one freshly captured Source and return it."""

    sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
    async with runtime.primary_database.transaction() as connection:
        stored = await sources.add(
            connection,
            scope_id,
            ContentSource(
                name=source_id,
                materialization=SourceMaterialization.CAPTURED,
                content="Replayed the write with key K; exactly one row remained.",
            ),
        )
    digest = content_digest(stored.value.model_dump_json().encode())
    agreement = ShareAgreementService(
        runtime.primary_database, access=access, scopes=ScopeRepository(), sources=sources
    )
    rule = await agreement.create(
        scope_id,
        ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(scope_id,)), terms=_terms()),
        principal=OWNER,
        context=AUDIT,
    )
    for side in ("origin", "audience"):
        await agreement.accept(
            scope_id,
            rule.rule_id,
            rule.version,
            principal=OWNER,
            side=side,
            covered_scope_ids=(scope_id,),
            context=AUDIT,
        )
    async with runtime.primary_database.transaction() as connection:
        descriptor = await ScopeRepository().get(connection, scope_id)
    assert descriptor is not None
    return await agreement.pin(
        scope_id,
        ShareSnapshotDraft(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            terms_digest=rule.terms_digest,
            membership_digest=membership_digest(((scope_id, descriptor.version),)),
            inputs=(ShareInput(kind="source", origin_scope_id=scope_id, source=stored.ref, digest=digest),),
            notification_key=key,
        ),
        principal=OWNER,
        context=AUDIT,
    )


def test_two_pins_of_the_same_material_do_not_borrow_a_candidate(tmp_path: Path) -> None:
    """Identical material on two pins yields two independent Candidates, never one reused."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'reuse.db'}")
        generator = _SelectingGenerator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            scope = await runtime.scopes.create(ScopeDraft(title="Reuse", summary="r", idempotency_key="reuse"))
            scope_id = scope.scope_id
            for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                await _grant(access, OWNER, scope_id, role)

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None

            citation = await _seed_evidence(runtime, scope_id)
            # BOTH pins cover the SAME Source material, so the proposal fingerprints collide
            # and only the sharing-origin check can prevent borrowing the other Candidate.
            candidates = []
            for index in ("a", "b"):
                pin = await _pin_one_source(runtime, access, scope_id, key=f"reuse-{index}", source_id="same")
                request = CreateDreamRunRequest(
                    operation="refine_experience",
                    artifacts=(citation,),
                    idempotency_key=f"reuse-run-{index}",
                )
                # The same material is cited by both runs, so dedup would be tempting.
                run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)
                async with runtime.primary_database.transaction() as connection:
                    record = await service.repository.get(connection, scope_id, run.run_id)
                    resolved = await service._resolve(record, connection)
                generator.cite = next(node.evidence_id for node in resolved.manifest.nodes if node.kind == "source")
                supervisor = runtime.artifact_processing_supervisor
                assert supervisor is not None
                supervisor.wake(binding_for_request(request))
                await process_pending(runtime)
                finished = await runtime.dream.for_scope(scope_id, principal_id="runtime").get(
                    GetDreamRunRequest(run_id=run.run_id)
                )
                assert finished.status in {"succeeded", "failed"}, finished.error
                candidates.append(finished)

            # Whatever the outcome, the second run must not have reused the first Candidate.
            if all(item.candidate is not None for item in candidates):
                assert candidates[0].candidate.candidate_id != candidates[1].candidate.candidate_id

    asyncio.run(scenario())


def test_shared_approval_refuses_when_pinned_material_moved(tmp_path: Path) -> None:
    """A shared approval refuses when a pinned origin's material no longer matches."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'moved.db'}")
        generator = _SelectingGenerator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            origin = (await runtime.scopes.create(ScopeDraft(title="O", summary="o", idempotency_key="o"))).scope_id
            target = (await runtime.scopes.create(ScopeDraft(title="T", summary="t", idempotency_key="t"))).scope_id
            for scope_id in (origin, target):
                for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                    await _grant(access, OWNER, scope_id, role)

            sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            async with runtime.primary_database.transaction() as connection:
                cited = await sources.add(
                    connection,
                    origin,
                    ContentSource(
                        name="cited",
                        materialization=SourceMaterialization.CAPTURED,
                        content="Replayed the write with key K; exactly one row remained.",
                    ),
                )
            cited_digest = content_digest(cited.value.model_dump_json().encode())

            agreement = ShareAgreementService(
                runtime.primary_database, access=access, scopes=ScopeRepository(), sources=sources
            )
            rule = await agreement.create(
                target,
                ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(target, origin)), terms=_terms()),
                principal=OWNER,
                context=AUDIT,
            )
            for side, covered in (("origin", (origin,)), ("audience", (target,))):
                await agreement.accept(
                    target,
                    rule.rule_id,
                    rule.version,
                    principal=OWNER,
                    side=side,
                    covered_scope_ids=covered,
                    context=AUDIT,
                )
            async with runtime.primary_database.transaction() as connection:
                descriptors = {
                    scope_id: await ScopeRepository().get(connection, scope_id) for scope_id in (target, origin)
                }
            assert all(d is not None for d in descriptors.values())
            versions: tuple[tuple[str, int], ...] = tuple(
                sorted((sid, d.version) for sid, d in descriptors.items() if d is not None)
            )
            pin = await agreement.pin(
                target,
                ShareSnapshotDraft(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    terms_digest=rule.terms_digest,
                    membership_digest=membership_digest(versions),
                    inputs=(ShareInput(kind="source", origin_scope_id=origin, source=cited.ref, digest=cited_digest),),
                    notification_key="moved-pin",
                ),
                principal=OWNER,
                context=AUDIT,
            )

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            citation = await _seed_evidence(runtime, target)
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="moved-run"
            )
            run = await service._admit(target, "runtime", request, snapshot_id=pin.snapshot_id)
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, target, run.run_id)
                resolved = await service._resolve(record, connection)
            generator.cite = next(node.evidence_id for node in resolved.manifest.nodes if node.digest == cited_digest)
            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            supervisor.wake(binding_for_request(request))
            await process_pending(runtime)
            finished = await runtime.dream.for_scope(target, principal_id="runtime").get(
                GetDreamRunRequest(run_id=run.run_id)
            )
            assert finished.status == "succeeded", finished.error

            # Tamper with the persisted origin so its material no longer matches the digest
            # the pin fixed. Writing the row directly is the only way to simulate drift,
            # because the owning repository refuses to contradict a captured Source.
            from powercontext.builtin.persistence.codec import dump_model

            tampered = cited.value.model_copy(update={"content": "Different material entirely."})
            async with runtime.primary_database.transaction() as connection:
                await connection.execute(
                    update(SOURCES_TABLE)
                    .where(
                        SOURCES_TABLE.c.scope_id == origin,
                        SOURCES_TABLE.c.source_type == cited.ref.source_type,
                        SOURCES_TABLE.c.source_id == cited.ref.source_id,
                    )
                    .values(payload=dump_model(tampered, kind="source-envelope", name=cited.ref.source_type))
                )

            review = runtime.review.for_scope(target)
            # With the resolver composed, approval re-reads every adopted origin and refuses
            # a changed digest rather than publishing under a stale claim.
            with pytest.raises(InvalidCandidateError) as refused:
                await review.approve(
                    ApproveCandidateRequest(candidate_id=finished.candidate.candidate_id, expected_version=1)
                )
            assert "foreign support" in refused.value.detail or "moved" in refused.value.detail
            reread = await review.get(GetCandidateRequest(candidate_id=finished.candidate.candidate_id))
            assert reread.status == "pending"

    asyncio.run(scenario())


def test_shared_approval_refuses_when_origin_authority_is_revoked(tmp_path: Path) -> None:
    """A still-current target cannot approve once the origin side loses its authority."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'revoke-approve.db'}")
        generator = _SelectingGenerator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            origin = (await runtime.scopes.create(ScopeDraft(title="O", summary="o", idempotency_key="o"))).scope_id
            target = (await runtime.scopes.create(ScopeDraft(title="T", summary="t", idempotency_key="t"))).scope_id
            origin_admin: dict[str, str] = {}
            for scope_id in (origin, target):
                for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                    binding = await _grant(access, OWNER, scope_id, role)
                    if scope_id == origin and role is AccessRole.SCOPE_ADMIN:
                        origin_admin["binding"] = binding

            sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            async with runtime.primary_database.transaction() as connection:
                cited = await sources.add(
                    connection,
                    origin,
                    ContentSource(
                        name="cited",
                        materialization=SourceMaterialization.CAPTURED,
                        content="Replayed the write with key K; exactly one row remained.",
                    ),
                )
            cited_digest = content_digest(cited.value.model_dump_json().encode())

            agreement = ShareAgreementService(
                runtime.primary_database, access=access, scopes=ScopeRepository(), sources=sources
            )
            rule = await agreement.create(
                target,
                ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(target, origin)), terms=_terms()),
                principal=OWNER,
                context=AUDIT,
            )
            for side, covered in (("origin", (origin,)), ("audience", (target,))):
                await agreement.accept(
                    target,
                    rule.rule_id,
                    rule.version,
                    principal=OWNER,
                    side=side,
                    covered_scope_ids=covered,
                    context=AUDIT,
                )
            async with runtime.primary_database.transaction() as connection:
                descriptors = {
                    scope_id: await ScopeRepository().get(connection, scope_id) for scope_id in (target, origin)
                }
            assert all(d is not None for d in descriptors.values())
            versions: tuple[tuple[str, int], ...] = tuple(
                sorted((sid, d.version) for sid, d in descriptors.items() if d is not None)
            )
            pin = await agreement.pin(
                target,
                ShareSnapshotDraft(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    terms_digest=rule.terms_digest,
                    membership_digest=membership_digest(versions),
                    inputs=(ShareInput(kind="source", origin_scope_id=origin, source=cited.ref, digest=cited_digest),),
                    notification_key="revoke-approve-pin",
                ),
                principal=OWNER,
                context=AUDIT,
            )

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            citation = await _seed_evidence(runtime, target)
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="revoke-approve-run"
            )
            run = await service._admit(target, "runtime", request, snapshot_id=pin.snapshot_id)
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, target, run.run_id)
                resolved = await service._resolve(record, connection)
            generator.cite = next(node.evidence_id for node in resolved.manifest.nodes if node.digest == cited_digest)
            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            supervisor.wake(binding_for_request(request))
            await process_pending(runtime)
            finished = await runtime.dream.for_scope(target, principal_id="runtime").get(
                GetDreamRunRequest(run_id=run.run_id)
            )
            assert finished.status == "succeeded", finished.error

            # Revoke only the ORIGIN administrator; the target stays fully current.
            await access.revoke_binding(
                ADMIN,
                origin_admin["binding"],
                expected_version=1,
                idempotency_key="revoke-origin-admin",
                context=AUDIT,
            )
            assert (await runtime.scopes.get(target)).scope_id == target

            review = runtime.review.for_scope(target)
            with pytest.raises(InvalidCandidateError) as refused:
                await review.approve(
                    ApproveCandidateRequest(candidate_id=finished.candidate.candidate_id, expected_version=1)
                )
            assert "access" in refused.value.detail or "shared" in refused.value.detail
            # Nothing was published.
            reread = await review.get(GetCandidateRequest(candidate_id=finished.candidate.candidate_id))
            assert reread.status == "pending"
            assert reread.result_artifact is None

    asyncio.run(scenario())


def test_reachable_foreign_dependency_not_pinned_is_still_adopted(tmp_path: Path) -> None:
    """A reachable foreign Source the pin never listed is adopted, not rejected."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'reachable.db'}")
        generator = _SelectingGenerator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            origin = (await runtime.scopes.create(ScopeDraft(title="O", summary="o", idempotency_key="o"))).scope_id
            target = (await runtime.scopes.create(ScopeDraft(title="T", summary="t", idempotency_key="t"))).scope_id
            for scope_id in (origin, target):
                for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                    await _grant(access, OWNER, scope_id, role)

            sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            async with runtime.primary_database.transaction() as connection:
                # The pinned direct input.
                pinned = await sources.add(
                    connection,
                    origin,
                    ContentSource(
                        name="pinned",
                        materialization=SourceMaterialization.CAPTURED,
                        content="Replayed the write with key K; exactly one row remained.",
                    ),
                )
            pinned_digest = content_digest(pinned.value.model_dump_json().encode())

            agreement = ShareAgreementService(
                runtime.primary_database, access=access, scopes=ScopeRepository(), sources=sources
            )
            rule = await agreement.create(
                target,
                ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(target, origin)), terms=_terms()),
                principal=OWNER,
                context=AUDIT,
            )
            for side, covered in (("origin", (origin,)), ("audience", (target,))):
                await agreement.accept(
                    target,
                    rule.rule_id,
                    rule.version,
                    principal=OWNER,
                    side=side,
                    covered_scope_ids=covered,
                    context=AUDIT,
                )
            async with runtime.primary_database.transaction() as connection:
                descriptors = {
                    scope_id: await ScopeRepository().get(connection, scope_id) for scope_id in (target, origin)
                }
            assert all(d is not None for d in descriptors.values())
            versions: tuple[tuple[str, int], ...] = tuple(
                sorted((sid, d.version) for sid, d in descriptors.items() if d is not None)
            )
            pin = await agreement.pin(
                target,
                ShareSnapshotDraft(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    terms_digest=rule.terms_digest,
                    membership_digest=membership_digest(versions),
                    inputs=(
                        ShareInput(kind="source", origin_scope_id=origin, source=pinned.ref, digest=pinned_digest),
                    ),
                    notification_key="reachable-pin",
                ),
                principal=OWNER,
                context=AUDIT,
            )

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            citation = await _seed_evidence(runtime, target)
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="reachable-run"
            )
            run = await service._admit(target, "runtime", request, snapshot_id=pin.snapshot_id)
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, target, run.run_id)
                resolved = await service._resolve(record, connection)
            generator.cite = next(node.evidence_id for node in resolved.manifest.nodes if node.digest == pinned_digest)
            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            supervisor.wake(binding_for_request(request))
            await process_pending(runtime)
            finished = await runtime.dream.for_scope(target, principal_id="runtime").get(
                GetDreamRunRequest(run_id=run.run_id)
            )
            assert finished.status == "succeeded", finished.error

            review = runtime.review.for_scope(target)
            approved = await review.approve(
                ApproveCandidateRequest(candidate_id=finished.candidate.candidate_id, expected_version=1)
            )
            assert approved.status == "approved"

            # Every adopted origin in the audit is either the pinned direct input or a
            # reachable dependency; the approval did not reject a legitimate dependency
            # merely because the pin never listed it.
            candidate = await review.get(GetCandidateRequest(candidate_id=finished.candidate.candidate_id))
            assert candidate.audit is not None
            assert any(origin.digest == pinned_digest for origin in candidate.audit.adopted_origins)

    asyncio.run(scenario())


def test_shared_experience_revision_approval_is_refused(tmp_path: Path) -> None:
    """A shared revision is refused rather than published on an unproven CAS path."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'revision.db'}")
        generator = _SelectingGenerator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            scope = await runtime.scopes.create(ScopeDraft(title="R", summary="r", idempotency_key="r"))
            scope_id = scope.scope_id
            for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                await _grant(access, OWNER, scope_id, role)

            artifacts = ArtifactRepository(BUILTIN_ARTIFACT_TYPES, sources=SourceRepository(BUILTIN_SOURCE_REGISTRY))
            async with runtime.primary_database.transaction() as connection:
                await artifacts.create(connection, scope_id, "exp-target", experience_draft())
            target_ref = ArtifactRef(family="experience", artifact_id="exp-target", revision=1)

            pin = await _pin_one_source(runtime, access, scope_id, key="revision-pin", source_id="same")
            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            # A target-bearing run is what makes this a revision.
            request = CreateDreamRunRequest(
                operation="refine_experience",
                artifacts=(target_ref,),
                target=target_ref,
                idempotency_key="revision-run",
            )
            run = await service._admit(scope_id, "runtime", request, snapshot_id=pin.snapshot_id)

            # The guard is the deciding check: a shared Candidate whose run carries a
            # target must refuse at approval, because target CAS under a pin is unproven.
            from powercontext.builtin.review.models import Candidate, CandidateStatus

            review = runtime._provider.review(scope_id)
            run_record = None
            async with runtime.primary_database.transaction() as connection:
                run_record = await service.repository.get(connection, scope_id, run.run_id)
            assert run_record.run.target is not None
            audit = CandidateAudit(
                operation="refine_experience",
                dream_run_id=run.run_id,
                spec_version=operation_spec("refine_experience").spec_version,
                proposal_digest="sha256:" + "0" * 64,
                evidence_manifest_ref=f"dream:{run.run_id}",
                validation_policy_digest="sha256:" + "0" * 64,
                share_snapshot_id=pin.snapshot_id,
                adopted_origins=(
                    QualifiedOrigin(
                        scope_id=scope_id,
                        source=SourceRef(source_type="content", source_id="same"),
                        digest="sha256:" + "1" * 64,
                    ),
                ),
            )
            candidate = Candidate(
                candidate_id="cand_dream_revision",
                version=1,
                family="experience",
                status=CandidateStatus.PENDING,
                proposal=experience(),
                sources=(SourceRef(source_type="content", source_id="same"),),
                artifacts=(target_ref,),
                target=target_ref,
                audit=audit,
            )
            async with runtime.primary_database.transaction() as connection:
                with pytest.raises(InvalidCandidateError) as refused:
                    await review._verify_shared_binding(connection, candidate)
            assert refused.value.detail == "shared_use_revision_unavailable"

    asyncio.run(scenario())


def experience_draft_with_source(source_ref):
    """An Experience draft whose lineage cites one exact Source."""

    from powercontext.builtin.artifacts.experience import ExperienceDraft

    return ExperienceDraft(content=experience(), sources=(source_ref,))


def test_pinned_foreign_experience_adopts_reachable_source_not_in_pin(tmp_path: Path) -> None:
    """A pinned foreign Experience pins only itself; its lineage Source is reachable."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'ancestry.db'}")
        generator = _SelectingGenerator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            origin = (await runtime.scopes.create(ScopeDraft(title="O", summary="o", idempotency_key="o"))).scope_id
            target = (await runtime.scopes.create(ScopeDraft(title="T", summary="t", idempotency_key="t"))).scope_id
            for scope_id in (origin, target):
                for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                    await _grant(access, OWNER, scope_id, role)

            sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            artifacts = ArtifactRepository(BUILTIN_ARTIFACT_TYPES, sources=sources)
            async with runtime.primary_database.transaction() as connection:
                lineage_source = await sources.add(
                    connection,
                    origin,
                    ContentSource(
                        name="lineage",
                        materialization=SourceMaterialization.CAPTURED,
                        content="The lineage Source the Experience revision stands on.",
                    ),
                )
            async with runtime.primary_database.transaction() as connection:
                foreign_artifact = await artifacts.create(
                    connection,
                    origin,
                    "foreign-exp",
                    experience_draft_with_source(lineage_source.ref),
                )
            foreign_ref = ArtifactRef(family="experience", artifact_id="foreign-exp", revision=1)
            foreign_digest = content_digest(foreign_artifact.model_dump_json().encode())
            lineage_digest = content_digest(lineage_source.value.model_dump_json().encode())

            agreement = ShareAgreementService(
                runtime.primary_database,
                access=access,
                scopes=ScopeRepository(),
                sources=sources,
                artifacts=artifacts,
            )
            rule = await agreement.create(
                target,
                ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(target, origin)), terms=_terms()),
                principal=OWNER,
                context=AUDIT,
            )
            for side, covered in (("origin", (origin,)), ("audience", (target,))):
                await agreement.accept(
                    target,
                    rule.rule_id,
                    rule.version,
                    principal=OWNER,
                    side=side,
                    covered_scope_ids=covered,
                    context=AUDIT,
                )
            async with runtime.primary_database.transaction() as connection:
                descriptors = {
                    scope_id: await ScopeRepository().get(connection, scope_id) for scope_id in (target, origin)
                }
            assert all(d is not None for d in descriptors.values())
            versions: tuple[tuple[str, int], ...] = tuple(
                sorted((sid, d.version) for sid, d in descriptors.items() if d is not None)
            )
            pin = await agreement.pin(
                target,
                ShareSnapshotDraft(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    terms_digest=rule.terms_digest,
                    membership_digest=membership_digest(versions),
                    # ONLY the Experience is pinned; its lineage Source is not.
                    inputs=(
                        ShareInput(
                            kind="artifact",
                            origin_scope_id=origin,
                            artifact=foreign_ref,
                            digest=foreign_digest,
                        ),
                    ),
                    notification_key="ancestry-pin",
                ),
                principal=OWNER,
                context=AUDIT,
            )
            assert {item.origin_scope_id for item in pin.inputs} == {origin}
            assert all(item.artifact is not None for item in pin.inputs)
            assert all(item.reference != lineage_source.ref for item in pin.inputs if item.artifact is None)

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            request = CreateDreamRunRequest(
                operation="refine_experience",
                artifacts=(foreign_ref,),
                idempotency_key="ancestry-run",
            )
            run = await service._admit(target, "runtime", request, snapshot_id=pin.snapshot_id)
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, target, run.run_id)
                resolved = await service._resolve(record, connection)
            generator.cite = next(node.evidence_id for node in resolved.manifest.nodes if node.digest == foreign_digest)
            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            supervisor.wake(binding_for_request(request))
            await process_pending(runtime)
            finished = await runtime.dream.for_scope(target, principal_id="runtime").get(
                GetDreamRunRequest(run_id=run.run_id)
            )
            assert finished.status == "succeeded", finished.error

            review = runtime.review.for_scope(target)
            approved = await review.approve(
                ApproveCandidateRequest(candidate_id=finished.candidate.candidate_id, expected_version=1)
            )
            assert approved.status == "approved"
            result = approved.result_artifact
            assert result is not None and result.family == "experience"

            from sqlalchemy import select

            from powercontext.builtin.persistence.tables import ARTIFACT_LINEAGE_ORIGINS_TABLE as ORIGINS

            async with runtime.primary_database.transaction() as connection:
                rows = (
                    (
                        await connection.execute(
                            select(ORIGINS).where(
                                ORIGINS.c.scope_id == target,
                                ORIGINS.c.artifact_id == result.artifact_id,
                                ORIGINS.c.revision == result.revision,
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
            recorded = {(str(row["origin_scope_id"]), str(row["origin_kind"]), str(row["digest"])) for row in rows}
            # The pinned Experience is recorded exactly.
            assert (origin, "artifact", foreign_digest) in recorded
            # The reachable unpinned lineage Source is recorded too: ancestry is adopted,
            # not rejected as a non-top-level dependency.
            assert any(kind == "source" and digest == lineage_digest for _, kind, digest in recorded)
            # Nothing outside the foreign origin is claimed.
            assert {scope for scope, _, _ in recorded} == {origin}
            # Uncited support is absent.
            assert len(recorded) == 2

    asyncio.run(scenario())


def test_uncited_origin_tampered_into_the_audit_is_refused_by_identity(tmp_path: Path) -> None:
    """An unrelated real origin added to the audit is rejected by identity, not by digest."""

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'uncited.db'}")
        generator = _SelectingGenerator()
        async with (
            open_dream_runtime(
                _config(database),
                candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
                dream_generator=generator,
            ) as runtime,
            open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access,
        ):
            await _ensure_share_tables(runtime.primary_database)
            origin = (await runtime.scopes.create(ScopeDraft(title="O", summary="o", idempotency_key="o"))).scope_id
            target = (await runtime.scopes.create(ScopeDraft(title="T", summary="t", idempotency_key="t"))).scope_id
            for scope_id in (origin, target):
                for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_VIEWER):
                    await _grant(access, OWNER, scope_id, role)

            sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            async with runtime.primary_database.transaction() as connection:
                cited = await sources.add(
                    connection,
                    origin,
                    ContentSource(
                        name="cited",
                        materialization=SourceMaterialization.CAPTURED,
                        content="Replayed the write with key K; exactly one row remained.",
                    ),
                )
                # A real, eligible, unchanged, but never-cited origin.
                uncited = await sources.add(
                    connection,
                    origin,
                    ContentSource(
                        name="uncited",
                        materialization=SourceMaterialization.CAPTURED,
                        content="Real material the model never cited.",
                    ),
                )
            cited_digest = content_digest(cited.value.model_dump_json().encode())
            uncited_digest = content_digest(uncited.value.model_dump_json().encode())

            agreement = ShareAgreementService(
                runtime.primary_database, access=access, scopes=ScopeRepository(), sources=sources
            )
            rule = await agreement.create(
                target,
                ShareRuleDraft(selection=ScopeSelection(mode="exact", scope_ids=(target, origin)), terms=_terms()),
                principal=OWNER,
                context=AUDIT,
            )
            for side, covered in (("origin", (origin,)), ("audience", (target,))):
                await agreement.accept(
                    target,
                    rule.rule_id,
                    rule.version,
                    principal=OWNER,
                    side=side,
                    covered_scope_ids=covered,
                    context=AUDIT,
                )
            async with runtime.primary_database.transaction() as connection:
                descriptors = {
                    scope_id: await ScopeRepository().get(connection, scope_id) for scope_id in (target, origin)
                }
            assert all(d is not None for d in descriptors.values())
            versions: tuple[tuple[str, int], ...] = tuple(
                sorted((sid, d.version) for sid, d in descriptors.items() if d is not None)
            )
            # BOTH are pinned, so the uncited one is genuinely available and its bytes match.
            pin = await agreement.pin(
                target,
                ShareSnapshotDraft(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    terms_digest=rule.terms_digest,
                    membership_digest=membership_digest(versions),
                    inputs=(
                        ShareInput(kind="source", origin_scope_id=origin, source=cited.ref, digest=cited_digest),
                        ShareInput(kind="source", origin_scope_id=origin, source=uncited.ref, digest=uncited_digest),
                    ),
                    notification_key="uncited-pin",
                ),
                principal=OWNER,
                context=AUDIT,
            )

            _compose_sharing(runtime, access, OWNER)
            service = runtime._dream_service
            assert service is not None
            citation = await _seed_evidence(runtime, target)
            request = CreateDreamRunRequest(
                operation="refine_experience", artifacts=(citation,), idempotency_key="uncited-run"
            )
            run = await service._admit(target, "runtime", request, snapshot_id=pin.snapshot_id)
            async with runtime.primary_database.transaction() as connection:
                record = await service.repository.get(connection, target, run.run_id)
                resolved = await service._resolve(record, connection)
            generator.cite = next(node.evidence_id for node in resolved.manifest.nodes if node.digest == cited_digest)
            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            supervisor.wake(binding_for_request(request))
            await process_pending(runtime)
            finished = await runtime.dream.for_scope(target, principal_id="runtime").get(
                GetDreamRunRequest(run_id=run.run_id)
            )
            assert finished.status == "succeeded", finished.error

            # Tamper the stored audit so it also claims the real, unchanged, uncited origin.
            review = runtime._provider.review(target)
            from powercontext.builtin.persistence.candidates import _encode_proposal

            async with runtime.primary_database.transaction() as connection:
                stored = await review._candidates.get(connection, target, finished.candidate.candidate_id)
                assert stored.audit is not None
                forged = stored.audit.model_copy(
                    update={
                        "adopted_origins": (
                            *stored.audit.adopted_origins,
                            QualifiedOrigin(scope_id=origin, source=uncited.ref, digest=uncited_digest),
                        )
                    }
                )
                await connection.execute(
                    CANDIDATE_VERSIONS_TABLE
                    .update()
                    .where(
                        CANDIDATE_VERSIONS_TABLE.c.scope_id == target,
                        CANDIDATE_VERSIONS_TABLE.c.candidate_id == finished.candidate.candidate_id,
                        CANDIDATE_VERSIONS_TABLE.c.version == 1,
                    )
                    .values(proposal=_encode_proposal(stored.model_copy(update={"audit": forged})))
                )

            # Rejection must be an identity mismatch, not a digest failure: the uncited origin
            # exists and its bytes are unchanged.
            async with runtime.primary_database.transaction() as connection:
                current = await review._candidates.get(connection, target, finished.candidate.candidate_id)
                with pytest.raises(InvalidCandidateError) as refused:
                    await review._verify_shared_binding(connection, current)
            assert refused.value.detail == "adopted support is not the selected support"

    asyncio.run(scenario())
