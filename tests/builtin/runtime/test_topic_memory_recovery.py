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

import asyncio
import logging
import sqlite3
from dataclasses import replace

import pytest
from sqlalchemy import insert, update

from powercontext.builtin.artifacts.topic_memory.generation import TopicMemoryGenerationError
from powercontext.builtin.artifacts.topic_memory.processing import (
    TopicMemoryBlockConflictError,
    TopicMemoryBlockNotFoundError,
)
from powercontext.builtin.inference import InferenceTimeoutError, character_token_estimator
from powercontext.builtin.persistence.migrations import MigrationError
from powercontext.builtin.persistence.migrations.topic_memory_recovery import TopicMemoryRecoveryMigrationRunner
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import TOPIC_MEMORY_WORK_BUDGETS_TABLE as BUDGETS
from powercontext.builtin.persistence.topic_memory_budget import record_topic_memory_work_failure
from powercontext.builtin.persistence.topic_memory_recovery_schema import recovery_statements
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding, ArtifactProcessingSupervisor
from powercontext.builtin.runtime.topic_memory_processing import (
    TopicMemoryAtomicPublisher,
    TopicMemoryProcessor,
    TopicMemoryStageSet,
    TopicMemoryWindowSelector,
)
from powercontext.builtin.runtime.topic_memory_recovery import TopicMemoryRecoveryService
from powercontext.builtin.runtime.topic_memory_scope import TopicMemoryScopeProcessor, topic_memory_processing_block
from powercontext.sources import SourceMaterialization
from tests.builtin.persistence.contract import NoteSource
from tests.builtin.runtime.test_processing_scheduler import _Launcher, _wait
from tests.builtin.runtime.test_topic_memory_processing import _repositories
from tests.builtin.runtime.test_topic_memory_scope import BINDING, _assignment, _capture, _Probe, _processor, _state
from tests.builtin.runtime.test_topic_memory_terminal_frontier import _budget, _seed


def test_inspection_cannot_block_or_reset_an_active_third_attempt():
    async def scenario():
        manager, profile, sources, topics = await _repositories()
        release = asyncio.Event()
        entered = asyncio.Event()
        task = None
        try:
            await _capture(profile, sources, 1)
            assignment = await _assignment(profile)
            await _seed(profile.database, attempts=2)

            async def running(_count):
                entered.set()
                await release.wait()

            processor = _processor(profile, sources, topics, _Probe(running))
            task = asyncio.create_task(processor.process(assignment))
            async with asyncio.timeout(5):
                await entered.wait()
            service = TopicMemoryRecoveryService(profile.database)
            before = await _budget(profile.database)
            assert before["attempts"] == 3
            status = await service.status("scope-a")
            assert status.state == "pending" and status.block is None
            assert await _budget(profile.database) == before
            release.set()
            assert (await task).outcome.value == "succeeded"
            assert (await service.status("scope-a")).state == "idle"
        finally:
            release.set()
            if task is not None:
                await task
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_attempt_range_stays_exact_after_context_window_shrinks_and_database_reopens(tmp_path):
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'attempt-range.db'}")

    async def scenario():
        manager, profile, sources, topics = await _repositories(config)
        try:
            for number in (1, 2, 3):
                await _capture(profile, sources, number, flush=number == 3)
            assignment = await _assignment(profile)

            async def unavailable(_count):
                raise TopicMemoryGenerationError("probe_unavailable")

            probe = _Probe(unavailable)
            estimator = character_token_estimator()
            domain = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=TopicMemoryStageSet(
                    probe=probe,
                    global_evolver=probe,
                    planner=probe,
                    evolver=probe,
                    temporary=probe,
                    reconciler=probe,
                    estimator=estimator,
                    input_tokens_limit=100_000,
                ),
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics),
            )
            for context_window in (100_000, 1, 1):
                processor = TopicMemoryScopeProcessor(
                    profile.database,
                    domain,
                    TopicMemoryWindowSelector(
                        profile.database, sources, estimator, context_window_tokens=context_window
                    ),
                    source_window_limit=2,
                )
                with pytest.raises(TopicMemoryGenerationError, match="probe_unavailable"):
                    await processor.process(assignment)
            assert [len(value.evidence) for value in probe.inputs] == [2, 1, 1]
            service = TopicMemoryRecoveryService(profile.database)
            status = await service.status("scope-a")
            block = status.block
            assert status.state == "blocked" and block is not None
            assert (status.source_cursor, status.source_head, status.target_source_through) == (0, 3, 3)
            budget = await _budget(profile.database)
            assert budget["source_through"] == 2 and budget["attempts"] == 3
            assert block.attempt_id == budget["attempt_id"]
            assert (block.source_after, block.source_through, block.target_source_through) == (0, 1, 3)
            assert block.budget_source_through == 2 and block.legacy_source_through is None
            assert block.attempts == 3 and block.error_code == "window_attempt_limit"
            assert block.last_error_code == "probe_unavailable"
            assert await service.get_block("scope-a", block.block_id) == block
        finally:
            await manager.__aexit__(None, None, None)

        manager, profile, _, _ = await _repositories(config)
        try:
            service = TopicMemoryRecoveryService(profile.database)
            assert (await service.status("scope-a")).block == block
            assert await service.get_block("scope-a", block.block_id) == block
            assert await _budget(profile.database) == budget
            accepted = await service.retry("scope-a", block.block_id)
            assert accepted.block.budget_source_through == 2
            assert await service.get_block("scope-a", block.block_id) == accepted.block
            assert await service.retry("scope-a", block.block_id) == accepted
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_explicit_retry_retains_diagnostics_and_duplicate_cannot_reset_a_new_block():
    async def scenario():
        manager, profile, sources, topics = await _repositories()
        try:
            await _capture(profile, sources, 1)
            assignment = await _assignment(profile)

            async def unavailable(_count):
                raise TopicMemoryGenerationError("probe_unavailable")

            processor = _processor(profile, sources, topics, _Probe(unavailable))
            service = TopicMemoryRecoveryService(profile.database)
            for _ in range(3):
                with pytest.raises(TopicMemoryGenerationError, match="probe_unavailable"):
                    await processor.process(assignment)
            first = (await service.status("scope-a")).block
            assert first is not None
            assert first.error_code == "window_attempt_limit"
            assert first.last_error_code == "probe_unavailable" and first.attempts == 3
            before = await _state(profile)
            accepted, duplicate = await asyncio.gather(
                service.retry("scope-a", first.block_id), service.retry("scope-a", first.block_id)
            )
            assert accepted == duplicate
            after = await _state(profile)
            assert after[1] == before[1] and after[3] == before[3]
            assert after[2].source_through == before[2].source_through
            assert after[2].handled_flush_generation == before[2].handled_flush_generation
            assert after[2].flush_generation == before[2].flush_generation + 1
            assert after[0].requested_generation == before[0].requested_generation + 1
            assert accepted.block.retried_at is not None
            assert accepted.block.blocked_at == first.blocked_at
            assignment = replace(assignment, claimed_request_generation=accepted.block.retry_request_generation)
            for _ in range(3):
                with pytest.raises(TopicMemoryGenerationError, match="probe_unavailable"):
                    await processor.process(assignment)
            second = (await service.status("scope-a")).block
            assert second is not None and second.block_id != first.block_id
            budget = await _budget(profile.database)
            assert await service.retry("scope-a", first.block_id) == accepted
            assert await _budget(profile.database) == budget
            assert await service.get_block("scope-a", first.block_id) == accepted.block
            assert (await service.status("scope-a")).block == second
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_obsolete_final_attempt_can_be_retried_without_restoring_its_publication_authority():
    async def scenario():
        manager, profile, sources, topics = await _repositories()
        release = asyncio.Event()
        entered = asyncio.Event()
        task = None
        try:
            await _capture(profile, sources, 1)
            assignment = await _assignment(profile)
            await _seed(profile.database, attempts=2)

            async def running(_count):
                entered.set()
                await release.wait()

            task = asyncio.create_task(_processor(profile, sources, topics, _Probe(running)).process(assignment))
            async with asyncio.timeout(5):
                await entered.wait()
            await _assignment(profile)  # Revoke the old term before it can publish.
            service = TopicMemoryRecoveryService(profile.database)
            block = (await service.status("scope-a")).block
            assert block is not None and block.attempts == 3
            accepted = await service.retry("scope-a", block.block_id)
            release.set()
            assert (await task).outcome.value == "leadership_lost"
            assert (await _state(profile))[1] == 0
            assert await service.get_block("scope-a", block.block_id) == accepted.block
            assert (await service.status("scope-a")).block is None
        finally:
            release.set()
            if task is not None:
                await task
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_unretried_stale_block_cannot_reset_replacement_allowance():
    async def scenario():
        manager, profile, sources, _ = await _repositories()
        try:
            await _capture(profile, sources, 1)
            await _seed(profile.database)
            service = TopicMemoryRecoveryService(profile.database)
            block = (await service.status("scope-a")).block
            assert block is not None
            async with profile.database.transaction() as connection:
                await connection.execute(update(BUDGETS).values(attempt_id="replacement-attempt", attempts=1))
            before = await _budget(profile.database)
            with pytest.raises(TopicMemoryBlockConflictError):
                await service.retry("scope-a", block.block_id)
            assert await _budget(profile.database) == before
            assert (await service.get_block("scope-a", block.block_id)).retried_at is None
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_legacy_attempt_ids_are_scoped_and_blocks_cannot_cross_scopes():
    async def scenario():
        manager, profile, sources, _ = await _repositories()
        try:
            await _capture(profile, sources, 1)
            await _seed(profile.database)
            before = await _budget(profile.database)
            async with profile.database.transaction() as connection:
                source = await sources.add(
                    connection,
                    "scope-b",
                    NoteSource(name="other", body="other scope", materialization=SourceMaterialization.CAPTURED),
                )
                await ArtifactProcessingPendingRepository().raise_source(
                    connection, "scope-b", BINDING, source.journal_position
                )
                await connection.execute(insert(BUDGETS).values(before | {"scope_id": "scope-b"}))
            service = TopicMemoryRecoveryService(profile.database)
            a = (await service.status("scope-a")).block
            b = (await service.status("scope-b")).block
            assert a is not None and b is not None
            assert a.block_id != b.block_id and a.attempt_id == b.attempt_id
            with pytest.raises(TopicMemoryBlockNotFoundError):
                await service.get_block("scope-b", a.block_id)
            with pytest.raises(TopicMemoryBlockNotFoundError):
                await service.retry("scope-b", a.block_id)
            assert (await service.status("scope-b")).block == b
            await service.retry("scope-a", a.block_id)
            assert (await service.status("scope-b")).block == b
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize("custom_error_code", [None, "private provider payload " * 10])
def test_generic_failed_workers_retain_last_error_and_stop_after_three_attempts(tmp_path, caplog, custom_error_code):
    caplog.set_level(logging.WARNING, logger="powercontext.builtin.runtime.artifact_processing")

    class _ProviderError(RuntimeError):
        error_code = custom_error_code

    async def scenario():
        manager, profile, sources, topics = await _repositories(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'failure.db'}")
        )
        try:
            await _capture(profile, sources, 1)

            async def unavailable(_count):
                raise _ProviderError("private provider payload")  # noqa: TRY003 - verify content-free diagnostics

            launcher = _Launcher(
                profile.database, on_work=_processor(profile, sources, topics, _Probe(unavailable)).process
            )
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        BINDING,
                        "topic-memory",
                        launcher,
                        work_block=topic_memory_processing_block,
                        work_failure=record_topic_memory_work_failure,
                    ),
                ),
                lease_mode="single-process",
                retry_base_seconds=0.01,
                retry_cap_seconds=0.01,
                retry_jitter=lambda: 1,
                blocked_check_seconds=0.03,
            ) as supervisor:
                await _wait(
                    lambda: any(getattr(r, "event", None) == "artifact_processing.blocked" for r in caplog.records)
                )
                service = TopicMemoryRecoveryService(profile.database)
                block = (await service.status("scope-a")).block
                assert block is not None and block.last_error_code == "processing_failed"
                assert block.attempts == 3 and block.error_code == "window_attempt_limit"
                await asyncio.sleep(0.1)
                assert supervisor.family_status["topic-memory"]["failed"] == 3
                assert (await service.status("scope-a")).block == block
                assert "private provider payload" not in block.model_dump_json()
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize("error_code", ["worker_timeout", "model_timeout"])
def test_timeout_diagnostics_survive_database_reopen(tmp_path, caplog, error_code):
    caplog.set_level(logging.WARNING, logger="powercontext.builtin.runtime.artifact_processing")
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'timeout.db'}")

    async def scenario():
        manager, profile, sources, topics = await _repositories(config)
        try:
            await _capture(profile, sources, 1)

            operation = "private model operation"

            async def timeout(_count):
                if error_code == "model_timeout":
                    raise InferenceTimeoutError(operation, 0.01)
                await asyncio.Event().wait()

            # The launcher is a test double; the Supervisor enforces the real deadline.
            processor = _processor(profile, sources, topics, _Probe(timeout))
            startup_pending = error_code == "worker_timeout"

            async def run_assignment(assignment):
                nonlocal startup_pending
                if startup_pending:
                    startup_pending = False
                    # A startup timeout must not count as a reserved Topic attempt.
                    await asyncio.Event().wait()
                return await processor.process(assignment)

            launcher = _Launcher(profile.database, on_work=run_assignment)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        BINDING,
                        "topic-memory",
                        launcher,
                        worker_timeout_seconds=2.0,
                        work_block=topic_memory_processing_block,
                        work_failure=record_topic_memory_work_failure,
                    ),
                ),
                lease_mode="single-process",
                retry_base_seconds=0.01,
                retry_cap_seconds=0.01,
                retry_jitter=lambda: 1,
                blocked_check_seconds=0.03,
            ) as supervisor:
                # Worker failures may precede admission; wait for the exhausted frontier.
                await _wait(
                    lambda: any(
                        getattr(record, "event", None) == "artifact_processing.blocked"
                        and getattr(record, "scope", None) == "scope-a"
                        for record in caplog.records
                    ),
                    timeout_seconds=30,
                )
                service = TopicMemoryRecoveryService(profile.database)
                status = await service.status("scope-a")
                block = status.block
                assert status.state == "blocked" and block is not None
                assert (status.source_cursor, status.source_head) == (0, 1)
                assert block.attempts == 3 and block.error_code == "window_attempt_limit"
                assert int(supervisor.family_status["topic-memory"]["failed"]) >= block.attempts
                if error_code == "worker_timeout":
                    assert int(supervisor.family_status["topic-memory"]["timeouts"]) >= block.attempts
                assert supervisor.family_status["topic-memory"]["last_error"] == error_code
                assert block.last_error_code == error_code
                assert await service.get_block("scope-a", block.block_id) == block
                assert operation not in block.model_dump_json()
        finally:
            await manager.__aexit__(None, None, None)

        manager, profile, _, _ = await _repositories(config)
        try:
            service = TopicMemoryRecoveryService(profile.database)
            assert (await service.status("scope-a")).block == block
            assert await service.get_block("scope-a", block.block_id) == block
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_concurrent_retries_from_independent_database_connections_grant_one_allowance(tmp_path):
    async def scenario():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}")
        manager_a, profile_a, sources, _ = await _repositories(config)
        manager_b = None
        try:
            await _capture(profile_a, sources, 1)
            await _seed(profile_a.database)
            manager_b, profile_b, _, _ = await _repositories(config)
            service_a = TopicMemoryRecoveryService(profile_a.database)
            service_b = TopicMemoryRecoveryService(profile_b.database)
            first, second = await asyncio.gather(service_a.status("scope-a"), service_b.status("scope-a"))
            block = first.block
            assert block is not None and second.block == block
            before = await _state(profile_a)
            accepted, duplicate = await asyncio.gather(
                service_a.retry("scope-a", block.block_id), service_b.retry("scope-a", block.block_id)
            )
            assert accepted == duplicate
            assert accepted.block.retried_at is not None
            after = await _state(profile_a)
            assert after[0].requested_generation == before[0].requested_generation + 1
            assert after[2].flush_generation == before[2].flush_generation + 1
            assert after[1] == before[1] and after[3] == before[3]
            assert (await service_a.status("scope-a")).block is None
            assert await service_b.get_block("scope-a", block.block_id) == accepted.block
        finally:
            if manager_b is not None:
                await manager_b.__aexit__(None, None, None)
            await manager_a.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_startup_requires_offline_migration_and_preserves_legacy_budget(tmp_path):
    database_path = tmp_path / "legacy.db"
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}")

    async def seed():
        manager, profile, sources, _ = await _repositories(config)
        try:
            await _capture(profile, sources, 1)
            await _seed(profile.database)
            return await _budget(profile.database)
        finally:
            await manager.__aexit__(None, None, None)

    before = asyncio.run(seed())
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE pc_topic_memory_work_blocks")
        connection.execute("DROP TABLE pc_topic_memory_work_budgets")
        connection.execute(recovery_statements("budget_v0", "sqlite")[0])
        names = tuple(
            name
            for name in before
            if name
            not in {
                "attempt_source_through",
                "started_at",
                "finished_at",
                "supervisor_group",
                "supervisor_generation",
                "holder_id",
                "last_error_code",
            }
        )
        connection.execute(
            f"INSERT INTO pc_topic_memory_work_budgets ({','.join(names)}) VALUES ({','.join(':' + name for name in names)})",  # noqa: S608 - fixed fixture fields, bound values
            before,
        )

    original = database_path.read_bytes()
    with pytest.raises(MigrationError, match="migration_required"):
        asyncio.run(_repositories(config))
    assert database_path.read_bytes() == original
    runner = TopicMemoryRecoveryMigrationRunner(config)
    plan = runner.plan()
    result = runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    assert result.changed and result.backup_state == "completed"
    assert runner.verify().state == "ready"

    async def inspect():
        manager, profile, _, _ = await _repositories(config)
        try:
            service = TopicMemoryRecoveryService(profile.database)
            block = (await service.status("scope-a")).block
            assert block is not None and block.last_error_code is None
            assert block.source_through is None and block.budget_source_through == 1
            assert block.legacy_source_through is None
            assert await _budget(profile.database) == before
            assert (await service.status("scope-a")).block == block
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(inspect())


def test_success_leaves_no_retained_attempt_or_block_history_after_reopen(tmp_path):
    path = tmp_path / "successful.db"
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{path}")

    async def scenario():
        manager, profile, sources, topics = await _repositories(config)
        try:
            await _capture(profile, sources, 1)
            result = await _processor(profile, sources, topics, _Probe()).process(await _assignment(profile))
            assert result.outcome.value == "succeeded"
            assert (await TopicMemoryRecoveryService(profile.database).status("scope-a")).state == "idle"
        finally:
            await manager.__aexit__(None, None, None)
        manager, profile, _, _ = await _repositories(config)
        try:
            assert (await TopicMemoryRecoveryService(profile.database).status("scope-a")).state == "idle"
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM pc_topic_memory_work_budgets").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM pc_topic_memory_work_blocks").fetchone() == (0,)
        assert (
            connection.execute("SELECT name FROM sqlite_schema WHERE name='pc_topic_memory_work_attempts'").fetchall()
            == []
        )


def test_late_worker_failure_does_not_rewrite_frozen_block():
    async def scenario():
        manager, profile, sources, _ = await _repositories()
        try:
            await _capture(profile, sources, 1)
            await _seed(profile.database)
            service = TopicMemoryRecoveryService(profile.database)
            block = (await service.status("scope-a")).block
            assert block is not None
            async with profile.database.transaction() as connection:
                await record_topic_memory_work_failure(connection, "scope-a", BINDING, "worker_timeout")
            assert (await service.status("scope-a")).block == block
            assert await service.get_block("scope-a", block.block_id) == block
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())
