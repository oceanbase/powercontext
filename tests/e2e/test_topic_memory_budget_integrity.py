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

"""Durable budgets and concurrent projection audits must coexist across reopen."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.topic_memory.generation import TopicMemoryGenerationError
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import BUILTIN_TABLES, TOPIC_MEMORY_WORK_BUDGETS_TABLE
from powercontext.builtin.persistence.topic_memory_budget import (
    MAX_TOPIC_MEMORY_WORK_REQUESTS,
    MAX_TOPIC_MEMORY_WORK_TOKENS,
    TopicMemoryWorkBudget,
    require_topic_memory_work_available,
)
from powercontext.builtin.runtime.topic_memory_processing import TopicMemoryAtomicPublisher
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER, ContentCapture
from tests.builtin.persistence.test_topic_memory_integrity import _OB, _concurrent_revision, _open_store
from tests.builtin.runtime.test_topic_memory_processing import _assignment


class _FailAfterCursorSave(SourceCursorRepository):
    async def save(self, *args, **kwargs):
        await super().save(*args, **kwargs)
        raise RuntimeError("injected publication rollback")  # noqa: TRY003


@pytest.mark.parametrize("backend", ["sqlite", _OB])
@pytest.mark.parametrize("terminal", [None, "requests", "tokens"])
def test_budget_reopen_supersession_and_publication_with_concurrent_integrity(tmp_path, backend, terminal):
    async def scenario():
        async with _open_store(tmp_path, backend, vector=False) as store:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            leases = ArtifactProcessingLeaseRepository()
            async with store.database.transaction() as connection:
                first, tail, healthy = [
                    await sources.add(
                        connection,
                        scope,
                        await CONTENT_SOURCE_ADAPTER.resolve(ContentCapture(source_id=name, content=name)),
                    )
                    for scope, name in (("scope-a", "first"), ("scope-a", "tail"), ("scope-b", "healthy"))
                ]
                await ArtifactProcessingPendingRepository().raise_source(
                    connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING, 2
                )
                if backend == "oceanbase":
                    term = await leases.try_acquire(connection, "budget-holder", 300)
                    assert term is not None
                    fence = term.fence("oceanbase")
                else:
                    term = await leases.start_single_process_term(connection, "budget-holder")
                    fence = term.fence("single-process")
            assignment = _assignment(fence)

            def budget(database, work=assignment):
                return TopicMemoryWorkBudget(
                    database,
                    scope_id=work.scope_id,
                    binding_name=work.binding_name,
                    source_after=work.source_after,
                    source_through=work.source_through,
                    cursor_generation=work.cursor_generation,
                    fence=work.fence,
                )

            async def budget_row(database):
                async with database.transaction() as connection:
                    row = (
                        (
                            await connection.execute(
                                select(TOPIC_MEMORY_WORK_BUDGETS_TABLE).where(
                                    TOPIC_MEMORY_WORK_BUDGETS_TABLE.c.scope_id == "scope-a"
                                )
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    return None if row is None else dict(row)

            old_attempt = budget(store.reader)
            await old_attempt.begin()
            await old_attempt.reserve(requests=2, tokens=250_000)
            before_audit = await budget_row(store.database)
            await _concurrent_revision(store, timing="after")
            assert await budget_row(store.database) == before_audit

            # Dispose the old reader and construct a fresh profile/engine, as a
            # replacement Worker would. No budget state is transferred in RAM.
            url = store.reader.engine.url.render_as_string(hide_password=False)
            await store.reader.close()
            tables = BUILTIN_TABLES + store.index.tables
            reopened = (
                OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(url)), tables=tables)
                if backend == "oceanbase"
                else SQLiteProfile.open(SQLiteConfig(url=url), tables=tables)
            )
            async with reopened as profile:
                database = profile.database
                async with database.transaction() as connection:
                    await store.repository.initialize(connection, configure_retrieval_shape=False)
                current = budget(database)
                await current.begin()
                await current.reserve(requests=2, tokens=250_000)
                row = await budget_row(database)
                assert row is not None and (row["attempts"], row["requests"], row["tokens"]) == (2, 4, 500_000)

                # The stale attempt can still reach the same DB through a live
                # connection, but it cannot spend or publish after supersession.
                old_attempt.database = store.database
                with pytest.raises(TopicMemoryGenerationError, match="window_attempt_superseded"):
                    await old_attempt.reserve(requests=1, tokens=1)
                publisher = TopicMemoryAtomicPublisher(database, sources, store.repository)
                with pytest.raises(TopicMemoryGenerationError, match="window_attempt_superseded"):
                    await publisher.publish(assignment, {"evidence-0001": first}, (), work_budget=old_attempt)

                failing = TopicMemoryAtomicPublisher(
                    database, sources, store.repository, cursors=_FailAfterCursorSave()
                )
                with pytest.raises(RuntimeError, match="injected publication rollback"):
                    await failing.publish(assignment, {"evidence-0001": first}, (), work_budget=current)
                assert await budget_row(database) == row
                async with database.transaction() as connection:
                    assert await SourceCursorRepository().load(connection, "scope-a", assignment.binding_name) is None

                if terminal:
                    with pytest.raises(TopicMemoryGenerationError, match="window_provider_budget_exceeded"):
                        await current.reserve(
                            requests=MAX_TOPIC_MEMORY_WORK_REQUESTS if terminal == "requests" else 1,
                            tokens=MAX_TOPIC_MEMORY_WORK_TOKENS if terminal == "tokens" else 1,
                        )
                    stopped = await budget_row(database)
                    assert stopped is not None and stopped["failure_code"] == "window_provider_budget_exceeded"
                    replacement = budget(database, replace(assignment, source_through=2, wave_target=2))
                    with pytest.raises(TopicMemoryGenerationError, match="window_provider_budget_exceeded"):
                        await replacement.begin()
                    assert await budget_row(database) == stopped
                    async with database.transaction() as connection:
                        with pytest.raises(TopicMemoryGenerationError, match="window_provider_budget_exceeded"):
                            await require_topic_memory_work_available(connection, "scope-a", assignment.binding_name, 0)
                        assert (
                            await SourceCursorRepository().load(connection, "scope-a", assignment.binding_name) is None
                        )
                        pending = await ArtifactProcessingPendingRepository().load(
                            connection, "scope-a", assignment.binding_name
                        )
                        assert pending is not None and pending.source_through == 2
                        assert len(await sources.list(connection, "scope-a", after=0, limit=10)) == 2
                else:
                    await publisher.publish(assignment, {"evidence-0001": first}, (), work_budget=current)
                    assert await budget_row(database) is None
                    async with database.transaction() as connection:
                        cursor = await SourceCursorRepository().load(connection, "scope-a", assignment.binding_name)
                        assert cursor is not None and cursor.cursor.sequence == 1
                    tail_work = replace(
                        assignment, source_after=1, source_through=2, wave_target=2, cursor_generation=cursor.generation
                    )
                    tail_budget = budget(database, tail_work)
                    await tail_budget.begin()
                    await tail_budget.reserve(requests=1, tokens=1)
                    await publisher.publish(tail_work, {"evidence-0001": tail}, (), work_budget=tail_budget)
                    assert await budget_row(database) is None
                    async with database.transaction() as connection:
                        cursor = await SourceCursorRepository().load(connection, "scope-a", assignment.binding_name)
                        assert cursor is not None and cursor.cursor.sequence == 2

                # A stopped Scope never prevents another Scope's NOOP publish.
                healthy_work = replace(assignment, scope_id="scope-b")
                healthy_budget = budget(database, healthy_work)
                await healthy_budget.begin()
                await healthy_budget.reserve(requests=1, tokens=1)
                await publisher.publish(healthy_work, {"evidence-0001": healthy}, (), work_budget=healthy_budget)
                async with database.transaction() as connection:
                    cursor = await SourceCursorRepository().load(connection, "scope-b", assignment.binding_name)
                    assert cursor is not None and cursor.cursor.sequence == 1
                    await store.repository.initialize(connection, configure_retrieval_shape=False)
                    a = await store.repository.browse_current(connection, "scope-a", limit=10)
                    b = await store.repository.browse_current(connection, "scope-b", limit=10)
                    assert [item.artifact_ref.revision for item in a] == [2]
                    assert [item.artifact_ref.revision for item in b] == [1]

    asyncio.run(scenario())
