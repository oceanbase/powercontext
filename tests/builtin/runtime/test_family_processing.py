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

import pytest
from sqlalchemy import func, select

from powercontext.builtin.artifacts.experience import ExperienceCandidateInput, ExperienceContent
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
from powercontext.builtin.inference.usage import UsageReportingStructuredGenerator
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.processing_migration import bootstrap_processing_schema
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_CANDIDATE_HEADS_TABLE,
    ARTIFACT_HEADS_TABLE,
    BUILTIN_TABLES,
    MODEL_USAGE_DAILY_TABLE,
)
from powercontext.builtin.runtime.artifact_processing import SpawnArtifactProcessingWorkerLauncher
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.family_processing import (
    FAMILY_BINDINGS,
    FamilyWorkerSpec,
    process_family_invocation,
    run_family_worker,
)
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


def _open_sqlite(config: BuiltinConfig, *, tables):
    assert isinstance(config.database, SQLiteConfig)
    return SQLiteProfile.open(config.database, tables=tables)


class MemoryPipeline:
    async def extract(self, request):
        return tuple(
            MemoryEntryInput(kind="fact", text=source.content, sources=(source,)) for source in request.sources
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


async def prepare(profile, family):
    contexts = RelationalContexts(
        database=profile.database, candidate_pipeline=MemoryPipeline(), experience_pipeline=ExperiencePipeline()
    )
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


@pytest.mark.parametrize("family", ["memory", "experience", "profile"])
def test_owner_failure_rolls_back_domain_cursor_and_ack_then_retry_owns_result(tmp_path, family):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'atomic.db'}"))
        async with _open_sqlite(config, tables=(*BUILTIN_TABLES, *ACCESS_TABLES)) as profile:
            contexts, assignment = await prepare(profile, family)
            async with open_worker_security(security_spec(), profile.database) as security:
                assert security is not None
                hook_name = f"{family}_commit"
                original = getattr(security, hook_name)

                async def failed_commit(*args, **kwargs):
                    await original(*args, **kwargs)
                    raise OSError("injected ownership failure")  # noqa: TRY003

                setattr(security, hook_name, failed_commit)
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
                setattr(security, hook_name, original)
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
    method = "extract" if family == "memory" else "incubate"
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
