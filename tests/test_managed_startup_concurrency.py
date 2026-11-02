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

"""Managed startup validation must allow concurrent SQLite WAL writes."""

import asyncio

import pytest

from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.runtime import BuiltinConfig, composition, open_builtin_contexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.settings import ServerSettings


@pytest.mark.parametrize("topic_worker", [False, True])
def test_managed_runtime_startup_allows_an_existing_runtime_to_commit(tmp_path, monkeypatch, topic_worker):
    database = SQLiteConfig(
        url=f"sqlite+aiosqlite:///{tmp_path / 'managed.db'}", busy_timeout_ms=250, journal_mode="WAL"
    )
    runner = deployment_runner(ServerSettings(database=database))
    plan = runner.plan()
    runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)

    async def scenario():
        validating = asyncio.Event()
        resume = asyncio.Event()
        config = BuiltinConfig(database=database)

        class PausedTopicValidation(TopicMemoryRepository):
            async def initialize(self, connection, /, **options):
                await super().initialize(connection, **options)
                validating.set()
                await resume.wait()

        async def open_runtime():
            async with open_builtin_contexts(config, _topic_memory_worker=topic_worker):
                pass

        async with open_builtin_contexts(config) as running:
            monkeypatch.setattr(composition, "TopicMemoryRepository", PausedTopicValidation)
            opening = asyncio.create_task(open_runtime())
            try:
                await asyncio.wait_for(validating.wait(), timeout=10)
                created = await asyncio.wait_for(
                    running.scopes.create(
                        ScopeDraft(
                            title="Concurrent capture",
                            summary="Committed while another runtime validates its managed storage.",
                            idempotency_key="concurrent-startup",
                        )
                    ),
                    timeout=2,
                )
                assert created.title == "Concurrent capture"
            finally:
                resume.set()
                await opening

    asyncio.run(scenario())
