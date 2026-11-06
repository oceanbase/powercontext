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

"""Expired Profile workers must not publish outside the fenced completion transaction."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from powercontext.builtin.artifacts.profile.models import PROFILE_SOURCE_WINDOW_BINDING as BINDING
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES, WORK_ITEMS_TABLE
from powercontext.builtin.persistence.work import StaleWorkClaimError, WorkRepository, WorkStatus
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.runtime.work_handlers import (
    CURRENT_WORK_PAYLOAD_VERSION,
    PROFILE_WORK_KIND,
    ProfileWorkHandler,
    profile_work_spec,
)
from powercontext.builtin.scope import ScopeDraft


class _Generator:
    async def generate(self, value):
        return "# Preferences\n\nGenerated profile for the fenced work commit."


def test_profile_work_publishes_only_inside_the_fenced_completion_transaction(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'profile-work.db'}"))
        assert isinstance(config.database, SQLiteConfig)
        async with SQLiteProfile.open(config.database, tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            scope = (await contexts.scopes.create(ScopeDraft(title="p", summary="p", idempotency_key="p"))).scope_id
            await contexts.records.create_source(scope, "content", "Profile evidence for the fenced commit.")
            await contexts.profiles.put_policy(scope, generation_enabled=True, expected_version=0)
            contexts.profiles.generator = _Generator()

            spec = await profile_work_spec(
                contexts, scope, max_attempts=5, payload_version=CURRENT_WORK_PAYLOAD_VERSION
            )
            assert spec is not None
            repository = WorkRepository()
            async with profile.database.transaction() as connection:
                enqueued = await repository.enqueue(connection, spec)
                (claim_a,) = await repository.claim(
                    connection,
                    worker_id="worker-a",
                    supported={PROFILE_WORK_KIND: frozenset({CURRENT_WORK_PAYLOAD_VERSION})},
                    lease_seconds=120,
                    limit=1,
                )

            handler = ProfileWorkHandler(contexts.profiles, max_concurrency=1)
            prepared_a = await handler.prepare(claim_a)

            # prepare() must not publish: nothing is durable before the fence.
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, scope, BINDING)
                artifact = await contexts.profiles.latest(connection, scope)
            assert cursor is None
            assert artifact is None

            # The lease expires and a superseding Worker takes over with a higher fence.
            # The first pass converts the expired attempt to retry_wait; the second
            # pass claims it with fence 2.
            async with profile.database.transaction() as connection:
                await connection.execute(
                    update(WORK_ITEMS_TABLE)
                    .where(WORK_ITEMS_TABLE.c.work_id == enqueued.work.work_id)
                    .values(lease_expires_at=datetime(2000, 1, 1, tzinfo=UTC))
                )
                assert not await repository.claim(
                    connection,
                    worker_id="worker-b",
                    supported={PROFILE_WORK_KIND: frozenset({CURRENT_WORK_PAYLOAD_VERSION})},
                    lease_seconds=120,
                    limit=1,
                    expired_retry_delay_seconds=0,
                )
                (claim_b,) = await repository.claim(
                    connection,
                    worker_id="worker-b",
                    supported={PROFILE_WORK_KIND: frozenset({CURRENT_WORK_PAYLOAD_VERSION})},
                    lease_seconds=120,
                    limit=1,
                    expired_retry_delay_seconds=0,
                )

            # Worker A's fenced completion is rejected and still publishes nothing.
            with pytest.raises(StaleWorkClaimError):
                async with profile.database.transaction() as connection:
                    await repository.complete(connection, claim_a, prepared_a.result, commit=prepared_a.commit)
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, scope, BINDING)
                artifact = await contexts.profiles.latest(connection, scope)
            assert cursor is None
            assert artifact is None

            # Worker B commits the Profile, its cursor and the Work success together.
            prepared_b = await handler.prepare(claim_b)
            async with profile.database.transaction() as connection:
                stored = await repository.complete(connection, claim_b, prepared_b.result, commit=prepared_b.commit)
            assert stored.status is WorkStatus.SUCCEEDED
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, scope, BINDING)
                artifact = await contexts.profiles.latest(connection, scope)
            assert cursor is not None and cursor.cursor.sequence == 1
            assert artifact is not None

    asyncio.run(scenario())
