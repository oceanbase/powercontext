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

from __future__ import annotations

import asyncio
import logging
from functools import partial

import pytest
from sqlalchemy import insert, select, update

from powercontext.builtin.artifacts.topic_memory.generation import TopicMemoryGenerationError
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import TOPIC_MEMORY_WORK_BUDGETS_TABLE as BUDGETS
from powercontext.builtin.persistence.topic_memory_budget import (
    MAX_TOPIC_MEMORY_WORK_REQUESTS,
    MAX_TOPIC_MEMORY_WORK_TOKENS,
)
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    SpawnArtifactProcessingWorkerLauncher,
)
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig
from powercontext.builtin.runtime.topic_memory_processing import TopicMemoryWorkerSpec, run_topic_memory_worker
from powercontext.builtin.runtime.topic_memory_scope import topic_memory_processing_block
from powercontext.builtin.sources import ContentCapture
from tests.builtin.runtime.test_processing_scheduler import _Launcher, _wait
from tests.builtin.runtime.test_topic_memory_processing import _repositories
from tests.builtin.runtime.test_topic_memory_scope import BINDING, _capture, _Probe, _processor, _state


async def _seed(database, **overrides):
    async with database.transaction() as connection:
        await connection.execute(
            insert(BUDGETS).values({
                "scope_id": "scope-a",
                "binding_name": BINDING,
                "source_after": 0,
                "source_through": 1,
                "attempt_id": "retained-attempt",
                "attempts": 3,
                "requests": 9,
                "tokens": 1000,
                "failure_code": "",
                **overrides,
            })
        )


async def _budget(database):
    async with database.transaction() as connection:
        return dict((await connection.execute(select(BUDGETS))).mappings().one())


@pytest.mark.parametrize(
    "budget,code",
    [
        ({}, "window_attempt_limit"),
        ({"attempts": 1, "requests": MAX_TOPIC_MEMORY_WORK_REQUESTS}, "window_provider_budget_exceeded"),
        ({"attempts": 1, "tokens": MAX_TOPIC_MEMORY_WORK_TOKENS}, "window_provider_budget_exceeded"),
        ({"attempts": 1, "failure_code": "source_complexity_limit"}, "source_complexity_limit"),
    ],
)
def test_terminal_frontier_is_quiet_preserves_work_and_recovers_without_restart(tmp_path, caplog, budget, code):
    caplog.set_level(logging.WARNING, logger="powercontext.builtin.runtime.artifact_processing")

    async def scenario():
        manager, profile, sources, topics = await _repositories(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'terminal.db'}")
        )
        try:
            await _capture(profile, sources, 1)
            await _capture(profile, sources, 2)
            await _seed(profile.database, **budget)
            before = await _budget(profile.database)
            before_state = await _state(profile)
            checks = 0

            async def check(connection, scope, binding):
                nonlocal checks
                checks += 1
                return await topic_memory_processing_block(connection, scope, binding)

            probe = _Probe()
            processor = _processor(profile, sources, topics, probe)
            launcher = _Launcher(profile.database, on_work=processor.process)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding(BINDING, "topic-memory", launcher, work_block=check),),
                lease_mode="single-process",
                blocked_check_seconds=0.03,
            ) as supervisor:
                await _wait(lambda: checks >= 3)
                assert not launcher.assignments and not probe.inputs
                assert supervisor.family_status["topic-memory"]["failed"] == 0
                assert await _budget(profile.database) == before
                assert await _state(profile) == before_state
                records = [r for r in caplog.records if getattr(r, "event", None) == "artifact_processing.blocked"]
                assert len(records) == 1
                record = records[0]
                assert (record.error_code, record.stage, record.family, record.scope) == (
                    code,
                    "topic_memory",
                    "topic-memory",
                    "scope-a",
                )
                assert (record.source_after, record.source_through) == (0, 1)
                assert (record.attempts, record.requests, record.tokens) == (
                    before["attempts"],
                    before["requests"],
                    before["tokens"],
                )
                # A flush rechecks state, but never creates a fresh allowance.
                async with profile.database.transaction() as connection:
                    await ArtifactProcessingPendingRepository().request_flush(connection, "scope-a", BINDING)
                    await ArtifactProcessingIntentRepository().request(connection, "scope-a", BINDING)
                checks_before = checks
                supervisor.wake(BINDING)
                await _wait(lambda: checks > checks_before)
                assert not launcher.assignments
                assert await _budget(profile.database) == before
                assert (
                    len([r for r in caplog.records if getattr(r, "event", None) == "artifact_processing.blocked"]) == 1
                )
                # Deliberate operator repair is detected by a metadata recheck.
                async with profile.database.transaction() as connection:
                    await connection.execute(
                        update(BUDGETS).values(attempts=1, requests=9, tokens=1000, failure_code="")
                    )
                await _wait(lambda: supervisor.family_status["topic-memory"]["completed"] == 1)
                intent, cursor, pending, targets = await _state(profile)
                assert cursor == 2 and pending is None and not targets
                assert intent.handled_generation == intent.requested_generation == 3
                assert supervisor.family_status["topic-memory"]["failed"] == 0
                assert len(launcher.assignments) == 1 and len(probe.inputs) == 2
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_available_third_attempt_can_finish_and_old_budget_does_not_block_next_cursor(tmp_path):
    async def scenario():
        manager, profile, sources, topics = await _repositories(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'third-attempt.db'}")
        )
        try:
            await _capture(profile, sources, 1)
            await _capture(profile, sources, 2)
            await _seed(profile.database, attempts=2)
            processor = _processor(profile, sources, topics, _Probe())
            launcher = _Launcher(profile.database, on_work=processor.process)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        BINDING, "topic-memory", launcher, work_block=topic_memory_processing_block
                    ),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["topic-memory"]["completed"] == 1)
                # A historical row is not the current progress frontier.
                await _seed(profile.database)
                async with profile.database.transaction() as connection:
                    assert await topic_memory_processing_block(connection, "scope-a", BINDING) is None
                assert (await _state(profile))[1] == 2
                await _capture(profile, sources, 3)
                supervisor.wake(BINDING)
                await _wait(lambda: supervisor.family_status["topic-memory"]["completed"] == 2)
                assert (await _state(profile))[1] == 3
                assert (await _budget(profile.database))["source_after"] == 0
                assert supervisor.family_status["topic-memory"]["failed"] == 0
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_three_actual_failures_are_counted_once_before_terminal_waiting(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="powercontext.builtin.runtime.artifact_processing")

    async def scenario():
        manager, profile, sources, topics = await _repositories(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'failed-attempts.db'}")
        )
        try:
            await _capture(profile, sources, 1)

            async def unavailable(_count):
                raise TopicMemoryGenerationError("probe_unavailable")

            probe = _Probe(unavailable)
            processor = _processor(profile, sources, topics, probe)
            launcher = _Launcher(profile.database, on_work=processor.process)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        BINDING, "topic-memory", launcher, work_block=topic_memory_processing_block
                    ),
                ),
                lease_mode="single-process",
                retry_base_seconds=0.01,
                retry_cap_seconds=0.01,
                retry_jitter=lambda: 1,
                blocked_check_seconds=0.02,
            ) as supervisor:
                await _wait(
                    lambda: any(getattr(r, "event", None) == "artifact_processing.blocked" for r in caplog.records)
                )
                await asyncio.sleep(0.08)
                assert len(launcher.assignments) == len(probe.inputs) == 3
                assert supervisor.family_status["topic-memory"]["failed"] == 3
                failures = [r for r in caplog.records if getattr(r, "event", None) == "artifact_processing.failed"]
                assert len(failures) == 3
                assert all(r.stage == "topic_memory" and r.error_code == "probe_unavailable" for r in failures)
                assert (await _budget(profile.database))["failure_code"] == ""
                intent, cursor, pending, targets = await _state(profile)
                assert cursor == 0 and pending.source_through == 1 and targets
                assert intent.handled_generation == 0
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_admission_race_returns_block_diagnostics_from_real_spawn_child(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="powercontext.builtin.runtime.artifact_processing")

    async def scenario():
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'race.db'}"),
            inference=InferenceConfig(generation_model="test"),
        )
        async with open_builtin_contexts(config) as contexts:
            scope = await contexts.get("scope-a")
            await scope.sources.capture(ContentCapture(source_id="first", content="private source sentinel"))
            async with contexts.database.transaction() as connection:
                await ArtifactProcessingIntentRepository().request(connection, "scope-a", BINDING)
            await _seed(contexts.database, attempts=2)
            child_launcher = SpawnArtifactProcessingWorkerLauncher(
                partial(run_topic_memory_worker, TopicMemoryWorkerSpec(config=config))
            )

            class RacingLauncher:
                starts = 0

                async def start(self, assignment):
                    self.starts += 1
                    # Simulate a concurrent reservation after parent admission.
                    async with contexts.database.transaction() as connection:
                        await connection.execute(update(BUDGETS).values(attempts=3))
                    return await child_launcher.start(assignment)

            launcher = RacingLauncher()
            async with ArtifactProcessingSupervisor(
                database=contexts.database,
                bindings=(
                    ArtifactProcessingBinding(
                        BINDING, "topic-memory", launcher, work_block=topic_memory_processing_block
                    ),
                ),
                lease_mode="single-process",
                blocked_check_seconds=0.05,
            ) as supervisor:
                await _wait(
                    lambda: any(getattr(r, "event", None) == "artifact_processing.blocked" for r in caplog.records),
                    timeout_seconds=30,
                )
                await asyncio.sleep(0.15)
                assert launcher.starts == 1
                assert supervisor.family_status["topic-memory"]["failed"] == 0
                records = [r for r in caplog.records if getattr(r, "event", None) == "artifact_processing.blocked"]
                assert len(records) == 1
                assert records[0].stage == "topic_memory" and records[0].error_code == "window_attempt_limit"
                assert records[0].source_after == 0 and records[0].attempts == 3
                assert "private source sentinel" not in caplog.text
                async with contexts.database.transaction() as connection:
                    intent = await ArtifactProcessingIntentRepository().load(connection, "scope-a", BINDING)
                    assert intent is not None and intent.handled_generation == 0

    asyncio.run(scenario())
