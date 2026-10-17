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

"""Processing failure accounting includes discovery and counts worker errors once."""

from __future__ import annotations

import asyncio

from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding, ArtifactProcessingSupervisor
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkerCompletion


def test_discovery_recovery_and_worker_retry_count_each_failure_once(tmp_path):
    async def scenario():
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'errors.db'}"), tables=SHARED_TABLES
        ) as profile:
            discovery_failed = asyncio.Event()
            allow_discovery = asyncio.Event()

            class Intents(ArtifactProcessingIntentRepository):
                async def scan(self, *args, **kwargs):
                    if not discovery_failed.is_set():
                        discovery_failed.set()
                        raise RuntimeError("private discovery detail")  # noqa: TRY003 - redaction fixture
                    await allow_discovery.wait()
                    return await super().scan(*args, **kwargs)

            intents = Intents()
            attempts = 0

            class Handle:
                async def wait(self):
                    return ArtifactProcessingWorkerCompletion(noop=True)

                async def terminate(self):
                    pass

            class Launcher:
                async def start(self, assignment):
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise RuntimeError("private worker detail")  # noqa: TRY003 - redaction fixture
                    async with profile.database.transaction() as connection:
                        await intents.acknowledge(connection, "scope", "binding", assignment.claimed_request_generation)
                    return Handle()

            async with profile.database.transaction() as connection:
                await intents.request(connection, "scope", "binding")
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=[ArtifactProcessingBinding("binding", "memory", Launcher())],
                intents=intents,
                lease_mode="single-process",
                oceanbase_tick_seconds=0.01,
                retry_base_seconds=0.001,
                retry_jitter=lambda: 1,
            )
            try:
                await supervisor.start()
                await asyncio.wait_for(discovery_failed.wait(), 3)
                async with asyncio.timeout(3):
                    while not supervisor.processing_observations["memory"].error_counts:  # noqa: ASYNC110 - observe recovery
                        await asyncio.sleep(0.001)
                assert dict(supervisor.processing_observations["memory"].error_counts) == {"internal": 1}
                assert supervisor.family_status["memory"]["status"] == "degraded"
                allow_discovery.set()
                async with asyncio.timeout(3):
                    while supervisor.family_status["memory"]["completed"] != 1:  # noqa: ASYNC110 - observe completion
                        await asyncio.sleep(0.001)
                observation = supervisor.processing_observations["memory"]
                assert dict(observation.error_counts) == {"internal": 2}
                assert dict(observation.retry_counts) == {"failure": 1}
                assert {item.outcome: item.count for item in observation.attempt_durations} == {"failure": 1, "noop": 1}
                assert supervisor.family_status["memory"]["failed"] == 1
                assert supervisor.family_status["memory"]["status"] == "leader"
                assert "private" not in repr(observation)
            finally:
                allow_discovery.set()
                await supervisor.close()

    asyncio.run(scenario())
