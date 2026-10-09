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

"""Scope completion must agree with domain progress and Server ownership."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from functools import partial
from typing import cast

import pytest
from sqlalchemy import func, select

import powercontext.builtin.runtime.composition as composition
import powercontext.builtin.runtime.family_processing as family_processing
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate, AtomicMemoryExtractionOutput
from powercontext.builtin.artifacts.experience import ExperienceCandidateInput, ExperienceContent
from powercontext.builtin.inference import InferenceTimeoutError
from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
from powercontext.builtin.inference.usage import UsageReportingStructuredGenerator
from powercontext.builtin.persistence.atomic_memory_schema import ATOMIC_MEMORY_TABLES
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.processing_migration import bootstrap_processing_schema
from powercontext.builtin.persistence.schema import create_tables
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import ArtifactProcessingFence, ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_CANDIDATE_HEADS_TABLE,
    ARTIFACT_HEADS_TABLE,
    BUILTIN_TABLES,
    MODEL_USAGE_DAILY_TABLE,
)
from powercontext.builtin.runtime.artifact_processing import SpawnArtifactProcessingWorkerLauncher
from powercontext.builtin.runtime.atomic_memory_security import AtomicMemoryExecutionContext
from powercontext.builtin.runtime.composition import _initialize_atomic_memory_authority, open_builtin_contexts
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.family_processing import (
    FAMILY_BINDINGS,
    FamilyWorkerSpec,
    _process_family_invocation,
    process_family_invocation,
    run_family_worker,
)
from powercontext.builtin.runtime.models import MemoryFlushResult
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerOutcome,
)
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import SourceCursor
from powercontext.server.authz import AccessDeniedError, PrincipalRef
from powercontext.server.authz.repository import ACCESS_OWNERS_TABLE, ACCESS_TABLES
from powercontext.server.processing_security import WorkerSecuritySpec, open_worker_security
from powercontext.sources import SourceRef
from tests.e2e.dream_support import atomic_memory_pipeline, memory_source_text


def _open_sqlite(config: BuiltinConfig, *, tables):
    assert isinstance(config.database, SQLiteConfig)
    return SQLiteProfile.open(config.database, tables=tables)


class MemoryPipeline:
    async def generate(self, request):
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(kind="fact", text=text, evidence_ids=(source.evidence_id,))
                    for source in request.evidence
                    if (text := memory_source_text(source)) is not None
                )
            )
        )


class ExperiencePipeline:
    async def incubate(self, sources):
        return tuple(
            ExperienceCandidateInput(
                proposal=ExperienceContent(
                    situation="Configuration", action="Test", outcome=source.content, lesson="Verify"
                ),
                sources=(SourceRef(source_type="content", source_id=source.name),),
            )
            for source in sources
        )


class ProfileGenerator:
    async def generate(self, value):
        return "# Preferences\n\nVerify every change."


class _FakeDecisionModel:
    policy_id = "test.worker.decision.v1"

    async def evaluate(self, request, /):
        raise AssertionError


class _WorkerProfiles:
    generator = None
    max_sources = 0


class _WorkerContexts:
    profiles = _WorkerProfiles()


class _HeldMemoryContexts:
    async def process_memory(self, *_args, **_kwargs):
        return MemoryFlushResult(
            previous_cursor=0,
            high_watermark=1,
            current_cursor=1,
            source_count=1,
            memory_ref=None,
            held_count=1,
            hold_codes=("evidence_limit_exceeded",),
        )


async def prepare(profile, family):
    contexts = RelationalContexts(
        database=profile.database,
        candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
        experience_pipeline=ExperiencePipeline(),
    )
    async with profile.database.transaction() as connection:
        await _initialize_atomic_memory_authority(connection)
        await create_tables(connection, ATOMIC_MEMORY_TABLES)
        await contexts.atomic_memory.index.initialize(connection)
    contexts.profiles.generator = ProfileGenerator()
    scope = (
        await contexts.scopes.create(ScopeDraft(title="Worker", summary="Worker", idempotency_key="worker"))
    ).scope_id
    await contexts.records.create_source(scope, "content", "Run the configuration tests.")
    if family == "profile":
        await contexts.profiles.put_policy(scope, generation_enabled=True, expected_version=0)
    async with profile.database.transaction() as connection:
        lease = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "worker-test")
        intent = await ArtifactProcessingIntentRepository().request(connection, scope, FAMILY_BINDINGS[family])
    assignment = ArtifactProcessingWorkAssignment(
        binding_name=FAMILY_BINDINGS[family],
        scope_id=scope,
        artifact_family=family,
        claimed_request_generation=intent.requested_generation,
        fence=lease.fence("single-process"),
        worker_id="worker-1",
    )
    return contexts, assignment


def security_spec(*, allowed=True):
    return WorkerSecuritySpec(
        principal=PrincipalRef(type="service", id="worker-test"),
        deployment_id="test",
        static_preset=allowed,
    ).model_dump(mode="json")


def test_memory_timeout_retries_shrink_without_acknowledging_or_skipping_input(tmp_path, monkeypatch):
    windows = []
    original = MemoryPipeline.generate

    async def bounded_extract(self, request):
        windows.append(tuple(source.source_ref.source_id for source in request.evidence))
        if len(request.evidence) > 1:
            raise InferenceTimeoutError("generate", 60)
        return await original(self, request)

    monkeypatch.setattr(MemoryPipeline, "generate", bounded_extract)

    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'timeout.db'}"))
        async with _open_sqlite(config, tables=BUILTIN_TABLES) as profile:
            contexts, assignment = await prepare(profile, "memory")
            for index in range(3):
                await contexts.records.create_source(assignment.scope_id, "content", f"Additional fact {index}")
            for _ in range(2):
                with pytest.raises(InferenceTimeoutError):
                    await process_family_invocation(contexts, assignment, config=config)
                async with profile.database.transaction() as connection:
                    cursor = await SourceCursorRepository().load(
                        connection, assignment.scope_id, assignment.binding_name
                    )
                    intent = await ArtifactProcessingIntentRepository().load(
                        connection, assignment.scope_id, assignment.binding_name
                    )
                assert cursor is not None and cursor.cursor.sequence == 0
                assert intent is not None and intent.handled_generation == 0
            for position in range(1, 5):
                assert (await process_family_invocation(contexts, assignment, config=config)).outcome == "succeeded"
                # Replaying an acknowledged invocation cannot process its successor.
                await process_family_invocation(contexts, assignment, config=config)
                async with profile.database.transaction() as connection:
                    cursor = await SourceCursorRepository().load(
                        connection, assignment.scope_id, assignment.binding_name
                    )
                    assert cursor is not None and cursor.cursor.sequence == position
                    intent = await ArtifactProcessingIntentRepository().load(
                        connection, assignment.scope_id, assignment.binding_name
                    )
                    assert intent is not None and intent.handled_generation == assignment.claimed_request_generation
                    if position < 4:
                        assert intent.dirty_generation > intent.clean_generation
                        request = await ArtifactProcessingIntentRepository().request(
                            connection, assignment.scope_id, assignment.binding_name
                        )
                        assignment = replace(assignment, claimed_request_generation=request.requested_generation)
                    else:
                        assert intent.dirty_generation == intent.clean_generation
            assert tuple(item for window in windows[2:] for item in window) == windows[0]

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["memory", "experience", "profile"])
def test_owner_failure_rolls_back_domain_cursor_and_ack_then_retry_owns_result(tmp_path, family):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'atomic.db'}"))
        async with _open_sqlite(config, tables=(*BUILTIN_TABLES, *ACCESS_TABLES)) as profile:
            contexts, assignment = await prepare(profile, family)
            async with open_worker_security(security_spec(), profile.database) as security:
                assert security is not None
                hook_target = contexts.atomic_memory.security if family == "memory" else security
                hook_name = "establish_owner" if family == "memory" else f"{family}_commit"
                original = getattr(hook_target, hook_name)

                async def failed_commit(*args, **kwargs):
                    await original(*args, **kwargs)
                    raise OSError("injected ownership failure")  # noqa: TRY003

                setattr(hook_target, hook_name, failed_commit)
                with pytest.raises(OSError, match="ownership failure"):
                    await process_family_invocation(contexts, assignment, config=config, security=security)
                async with profile.database.transaction() as connection:
                    cursor = await SourceCursorRepository().load(
                        connection, assignment.scope_id, assignment.binding_name
                    )
                    intent = await ArtifactProcessingIntentRepository().load(
                        connection, assignment.scope_id, assignment.binding_name
                    )
                    assert cursor is None
                    assert intent is not None and intent.handled_generation == 0
                    for table in (ARTIFACT_HEADS_TABLE, ARTIFACT_CANDIDATE_HEADS_TABLE, ACCESS_OWNERS_TABLE):
                        assert await connection.scalar(select(func.count()).select_from(table)) == 0
                setattr(hook_target, hook_name, original)
                result = await process_family_invocation(contexts, assignment, config=config, security=security)
                assert result.outcome == ArtifactProcessingWorkerOutcome.SUCCEEDED
                async with profile.database.transaction() as connection:
                    cursor = await SourceCursorRepository().load(
                        connection, assignment.scope_id, assignment.binding_name
                    )
                    intent = await ArtifactProcessingIntentRepository().load(
                        connection, assignment.scope_id, assignment.binding_name
                    )
                    assert cursor is not None and cursor.cursor.sequence == 1
                    assert intent is not None and intent.handled_generation == assignment.claimed_request_generation
                    assert await connection.scalar(select(func.count()).select_from(ACCESS_OWNERS_TABLE)) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["memory", "experience", "profile"])
def test_denied_scope_preserves_request_and_source_progress(tmp_path, family):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'denied.db'}"))
        async with _open_sqlite(config, tables=(*BUILTIN_TABLES, *ACCESS_TABLES)) as profile:
            contexts, assignment = await prepare(profile, family)
            async with open_worker_security(security_spec(allowed=False), profile.database) as security:
                with pytest.raises(AccessDeniedError):
                    await process_family_invocation(contexts, assignment, config=config, security=security)
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, assignment.scope_id, assignment.binding_name)
                intent = await ArtifactProcessingIntentRepository().load(
                    connection, assignment.scope_id, assignment.binding_name
                )
                assert cursor is None
                assert intent is not None and intent.handled_generation == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["memory", "experience"])
def test_worker_records_existing_model_usage_purposes_and_replay_does_not_infer(tmp_path, monkeypatch, family):
    class Generator:
        async def generate(self, _value):
            return GenerationResult(output=None, usage=InferenceUsage(requests=1, input_tokens=3, output_tokens=2))

    pipeline = MemoryPipeline if family == "memory" else ExperiencePipeline
    method = "generate" if family == "memory" else "incubate"
    original = getattr(pipeline, method)

    async def generated(self, value):
        await UsageReportingStructuredGenerator(Generator()).generate(None)
        return await original(self, value)

    monkeypatch.setattr(pipeline, method, generated)

    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}"))
        async with _open_sqlite(config, tables=BUILTIN_TABLES) as profile:
            contexts, assignment = await prepare(profile, family)
            await process_family_invocation(contexts, assignment, config=config)
            await process_family_invocation(contexts, assignment, config=config)
            async with profile.database.transaction() as connection:
                row = (await connection.execute(select(MODEL_USAGE_DAILY_TABLE))).mappings().one()
                assert row["scope_id"] == assignment.scope_id
                assert row["purpose"] == ("memory_extraction" if family == "memory" else "experience_generation")
                assert row["operation"] == "generation"
                assert row["requests"] == 1

    asyncio.run(scenario())


def test_sdk_memory_worker_with_parent_schema_commits_formal_local_ownership(tmp_path):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'sdk-worker.db'}"))
        async with open_builtin_contexts(config, candidate_pipeline=atomic_memory_pipeline(MemoryPipeline())) as parent:
            _, assignment = await prepare(parent, "memory")
        async with open_builtin_contexts(
            config,
            candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
            _topic_memory_worker=True,
        ) as contexts:
            outcome = await process_family_invocation(contexts, assignment, config=config)
            assert outcome.outcome == ArtifactProcessingWorkerOutcome.SUCCEEDED
            replay = await process_family_invocation(contexts, assignment, config=config)
            assert replay.outcome == ArtifactProcessingWorkerOutcome.SUCCEEDED
            entries = (
                await contexts.atomic_memory.for_scope(assignment.scope_id).list(
                    context=AtomicMemoryExecutionContext(
                        principal=PrincipalRef(type="service", id="local-runtime"), trusted_local=True
                    ),
                )
            ).items
            assert len(entries) == 1
            assert entries[0].artifact.content.text == "Run the configuration tests."
            async with contexts.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, assignment.scope_id, assignment.binding_name)
                intent = await ArtifactProcessingIntentRepository().load(
                    connection, assignment.scope_id, assignment.binding_name
                )
                assert cursor is not None and cursor.cursor.sequence == 1
                assert intent is not None and intent.handled_generation == assignment.claimed_request_generation
                owner = (await connection.execute(select(ACCESS_OWNERS_TABLE))).mappings().one()
                assert owner["owner_type"] == "service" and owner["owner_id"] == "local-runtime"

    asyncio.run(scenario())


def test_completed_memory_generation_does_not_consume_new_input_and_new_trigger_remains(tmp_path):
    async def scenario():
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'replay.db'}"),
            runtime=RuntimeConfig(source_window_limit=1),
        )
        async with _open_sqlite(config, tables=BUILTIN_TABLES) as profile:
            contexts, assignment = await prepare(profile, "memory")
            await contexts.records.create_source(assignment.scope_id, "content", "Second input.")
            await process_family_invocation(contexts, assignment, config=config)
            await process_family_invocation(contexts, assignment, config=config)
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, assignment.scope_id, assignment.binding_name)
                intent = await ArtifactProcessingIntentRepository().load(
                    connection, assignment.scope_id, assignment.binding_name
                )
                assert cursor is not None and cursor.cursor.sequence == 1
                assert intent is not None and intent.dirty_generation > intent.clean_generation
                request = await ArtifactProcessingIntentRepository().request(
                    connection, assignment.scope_id, assignment.binding_name
                )
            await process_family_invocation(
                contexts, replace(assignment, claimed_request_generation=request.requested_generation), config=config
            )
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, assignment.scope_id, assignment.binding_name)
                assert cursor is not None and cursor.cursor.sequence == 2

    asyncio.run(scenario())


def test_profile_candidate_success_and_pending_noop_never_advance_review_cursor(tmp_path):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'profile.db'}"))
        async with _open_sqlite(config, tables=BUILTIN_TABLES) as profile:
            contexts, assignment = await prepare(profile, "profile")
            policy = await contexts.profiles.get_policy(assignment.scope_id)
            await contexts.profiles.put_policy(
                assignment.scope_id,
                generation_enabled=True,
                activation_mode="review_required",
                expected_version=policy.version,
            )
            assert (await process_family_invocation(contexts, assignment, config=config)).outcome == "succeeded"
            pending = (await contexts.profiles.get_policy(assignment.scope_id)).pending_candidate_id
            assert pending is not None
            async with profile.database.transaction() as connection:
                request = await ArtifactProcessingIntentRepository().request(
                    connection, assignment.scope_id, assignment.binding_name
                )
            successor = replace(assignment, claimed_request_generation=request.requested_generation)
            assert (await process_family_invocation(contexts, successor, config=config)).outcome == "succeeded"
            assert (await contexts.profiles.get_policy(assignment.scope_id)).pending_candidate_id == pending
            async with profile.database.transaction() as connection:
                assert (
                    await SourceCursorRepository().load(connection, assignment.scope_id, assignment.binding_name)
                    is None
                )
                intent = await ArtifactProcessingIntentRepository().load(
                    connection, assignment.scope_id, assignment.binding_name
                )
                assert intent is not None and intent.handled_generation == successor.claimed_request_generation
                assert intent.dirty_generation > intent.clean_generation

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["memory", "experience", "profile"])
def test_spawned_family_restores_trusted_identity_and_persists_noop_ack(tmp_path, family):
    async def scenario():
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'spawn.db'}"),
            inference=InferenceConfig(generation_model="test"),
        )
        async with open_builtin_contexts(config):
            pass
        async with _open_sqlite(config, tables=(*BUILTIN_TABLES, *ACCESS_TABLES)) as profile:
            async with profile.database.transaction() as connection:
                await bootstrap_processing_schema(connection, canonical_processing_manifest(config))
            _, assignment = await prepare(profile, family)
            async with profile.database.transaction() as connection:
                await SourceCursorRepository().save(
                    connection,
                    assignment.scope_id,
                    assignment.binding_name,
                    SourceCursor(sequence=1),
                    expected_generation=None,
                )
            spec = FamilyWorkerSpec(config=config, worker_security=security_spec())
            launcher = SpawnArtifactProcessingWorkerLauncher(partial(run_family_worker, spec))
            worker = await launcher.start(assignment)
            try:
                result = await asyncio.wait_for(worker.wait(), timeout=30)
                assert result.outcome == "succeeded"
            finally:
                await worker.terminate()
            async with profile.database.transaction() as connection:
                intent = await ArtifactProcessingIntentRepository().load(
                    connection, assignment.scope_id, assignment.binding_name
                )
                assert intent is not None and intent.handled_generation == assignment.claimed_request_generation
                assert intent.clean_generation == intent.dirty_generation

    asyncio.run(scenario())


def test_spawned_memory_worker_rejects_legacy_gate_before_model_creation(monkeypatch, tmp_path):
    def unexpected_initialization(*_args, **_kwargs):
        pytest.fail("Legacy gate configuration must fail before model and worker initialization")

    monkeypatch.setattr(composition, "_embedding_models", unexpected_initialization)
    monkeypatch.setattr(composition, "_prompt_registry", unexpected_initialization)
    monkeypatch.setattr(composition, "open_builtin_contexts", unexpected_initialization)
    monkeypatch.setattr(family_processing, "process_family_invocation", unexpected_initialization)

    async def scenario():
        database_path = tmp_path / "worker-gate.db"
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
            inference=InferenceConfig(generation_model="test"),
            runtime=RuntimeConfig(memory_write_gate_enabled=True),
        )
        assignment = ArtifactProcessingWorkAssignment(
            binding_name=FAMILY_BINDINGS["memory"],
            scope_id="scope-a",
            artifact_family="memory",
            claimed_request_generation=1,
            fence=ArtifactProcessingFence(
                supervisor_group="global",
                holder_id="worker-test",
                supervisor_generation=1,
                lease_mode="single-process",
            ),
            worker_id="worker-1",
        )

        with pytest.raises(composition.BuiltinConfigurationError, match="legacy Memory write gate"):
            await family_processing._run_family_worker(FamilyWorkerSpec(config=config), assignment)
        assert not database_path.exists()

    asyncio.run(scenario())


def test_memory_worker_completion_preserves_hold_details(tmp_path):
    async def scenario():
        assignment = ArtifactProcessingWorkAssignment(
            binding_name=FAMILY_BINDINGS["memory"],
            scope_id="scope-a",
            artifact_family="memory",
            claimed_request_generation=1,
            fence=ArtifactProcessingFence(
                supervisor_group="global",
                holder_id="worker-test",
                supervisor_generation=1,
                lease_mode="single-process",
            ),
            worker_id="worker-1",
        )
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'held-worker.db'}"))

        result = await _process_family_invocation(
            cast(RelationalContexts, _HeldMemoryContexts()),
            assignment,
            config=config,
            security=None,
            dream_generator=None,
        )

        assert result.outcome == ArtifactProcessingWorkerOutcome.SUCCEEDED
        assert result.held_count == 1
        assert result.hold_codes == ("evidence_limit_exceeded",)

    asyncio.run(scenario())


def test_dedicated_fence_cannot_commit_another_family(tmp_path):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'wrong-family.db'}"))
        async with _open_sqlite(config, tables=BUILTIN_TABLES) as profile:
            contexts, assignment = await prepare(profile, "memory")
            async with profile.database.transaction() as connection:
                lease = await ArtifactProcessingLeaseRepository().start_single_process_term(
                    connection, "profile-worker", supervisor_group="artifact:profile"
                )
            wrong = replace(assignment, fence=lease.fence("single-process"))
            with pytest.raises(ValueError, match="another Family"):
                await process_family_invocation(contexts, wrong, config=config)
            async with profile.database.transaction() as connection:
                assert (
                    await SourceCursorRepository().load(connection, assignment.scope_id, assignment.binding_name)
                    is None
                )
                intent = await ArtifactProcessingIntentRepository().load(
                    connection, assignment.scope_id, assignment.binding_name
                )
                assert intent is not None and intent.handled_generation == 0

    asyncio.run(scenario())
