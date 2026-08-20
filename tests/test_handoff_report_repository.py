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
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from powercontext.builtin.handoff_report.models import ReportActivityEvent, ReportActivitySource, ReportTimeBasis
from powercontext.builtin.handoff_report.repository import (
    ActivityEventConflictError,
    InvalidActivityEventError,
    InvalidActivityRepositoryArgumentError,
)
from powercontext.builtin.handoff_report.sqlite import HANDOFF_REPORT_TABLES, SQLiteActivityEventRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile


def _event(
    event_id: str,
    *,
    project_id: str = "project-1",
    source: ReportActivitySource = "git_commit",
    source_event_id: str | None = None,
    observed_at: datetime,
    occurred_at: datetime | None = None,
    time_basis: ReportTimeBasis = "source_reported",
    title: str | None = None,
) -> ReportActivityEvent:
    return ReportActivityEvent(
        event_id=event_id,
        project_id=project_id,
        source=source,
        source_event_id=source_event_id or event_id,
        observed_at=observed_at,
        occurred_at=occurred_at,
        time_basis=time_basis,
        title=title,
    )


def test_activity_store_is_idempotent_and_rejects_payload_conflicts() -> None:
    async def scenario() -> None:
        now = datetime(2026, 8, 5, 10, tzinfo=UTC)
        repository = SQLiteActivityEventRepository()
        event = _event("event-1", observed_at=now, occurred_at=now - timedelta(hours=1))
        async with SQLiteProfile.open(SQLiteConfig(), tables=HANDOFF_REPORT_TABLES) as profile:
            async with profile.database.transaction() as connection:
                first = await repository.record(connection, event)
                repeated = await repository.record(connection, event)
            assert repeated == first
            assert first.cursor == 1
            assert first.payload["event_id"] == "event-1"

            conflicting = event.model_copy(update={"title": "different"})
            with pytest.raises(ActivityEventConflictError):
                async with profile.database.transaction() as connection:
                    await repository.record(connection, conflicting)

    asyncio.run(scenario())


def test_idempotent_retry_ignores_only_server_owned_identity_and_observation_time() -> None:
    async def scenario() -> None:
        now = datetime(2026, 8, 5, 10, tzinfo=UTC)
        repository = SQLiteActivityEventRepository()
        first_event = _event(
            "server-event-1",
            source_event_id="git:stable",
            observed_at=now,
            occurred_at=now - timedelta(hours=1),
            title="Implemented report storage",
        )
        retry = first_event.model_copy(
            update={
                "event_id": "server-event-2",
                "observed_at": now + timedelta(minutes=5),
            }
        )
        async with SQLiteProfile.open(SQLiteConfig(), tables=HANDOFF_REPORT_TABLES) as profile:
            async with profile.database.transaction() as connection:
                first = await repository.record(connection, first_event)
            async with profile.database.transaction() as connection:
                repeated = await repository.record(connection, retry)
                assert await repository.high_watermark(connection, "project-1") == 1

            assert repeated == first
            assert repeated.event_id == "server-event-1"
            assert repeated.observed_at == now
            assert repeated.payload["event_id"] == "server-event-1"

            semantic_changes = (
                retry.model_copy(update={"title": "Different activity"}),
                retry.model_copy(update={"occurred_at": now - timedelta(hours=2)}),
            )
            for changed in semantic_changes:
                with pytest.raises(ActivityEventConflictError):
                    async with profile.database.transaction() as connection:
                        await repository.record(connection, changed)

    asyncio.run(scenario())


def test_record_revalidates_constructed_payload_before_indexing() -> None:
    async def scenario() -> None:
        now = datetime(2026, 8, 5, 10, tzinfo=UTC)
        invalid = ReportActivityEvent.model_construct(
            event_id="event-invalid",
            project_id="project-1",
            source="git_commit",
            source_event_id="git:invalid",
            occurred_at=now,
            observed_at=now,
            time_basis="host_observed",
            trust="untrusted_observation",
        )
        repository = SQLiteActivityEventRepository()
        async with SQLiteProfile.open(SQLiteConfig(), tables=HANDOFF_REPORT_TABLES) as profile:
            with pytest.raises(InvalidActivityEventError):
                async with profile.database.transaction() as connection:
                    await repository.record(connection, invalid)
            async with profile.database.transaction() as connection:
                assert await repository.high_watermark(connection, "project-1") == 0

    asyncio.run(scenario())


def test_single_repository_serializes_concurrent_sqlite_records(tmp_path) -> None:
    async def scenario() -> None:
        now = datetime(2026, 8, 5, 10, tzinfo=UTC)
        repository = SQLiteActivityEventRepository()
        ready = asyncio.Event()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'activity.db'}")

        async with SQLiteProfile.open(config, tables=HANDOFF_REPORT_TABLES) as profile:

            async def record_one(index: int):
                await ready.wait()
                async with profile.database.transaction() as connection:
                    return await repository.record(
                        connection,
                        _event(
                            f"event-{index}",
                            observed_at=now,
                            occurred_at=now + timedelta(seconds=index),
                        ),
                    )

            tasks = tuple(asyncio.create_task(record_one(index)) for index in range(12))
            ready.set()
            stored = await asyncio.gather(*tasks)

            assert sorted(item.cursor for item in stored) == list(range(1, 13))
            async with profile.database.transaction() as connection:
                assert await repository.high_watermark(connection, "project-1") == 12
                assert len(await repository.list(connection, "project-1")) == 12

    asyncio.run(scenario())


def test_activity_store_lists_by_project_period_source_and_frozen_cursor() -> None:
    async def scenario() -> None:
        now = datetime(2026, 8, 5, 10, tzinfo=UTC)
        repository = SQLiteActivityEventRepository()
        events = (
            _event("old", observed_at=now, occurred_at=now - timedelta(days=8)),
            _event("period-git", observed_at=now, occurred_at=now - timedelta(days=2)),
            _event(
                "period-session",
                observed_at=now - timedelta(days=1),
                source="coding_session",
                time_basis="host_observed",
            ),
            _event(
                "current",
                observed_at=now,
                source="git_worktree",
                time_basis="current_only",
            ),
            _event(
                "other-project",
                project_id="project-2",
                observed_at=now,
                occurred_at=now - timedelta(days=1),
            ),
        )
        async with SQLiteProfile.open(SQLiteConfig(), tables=HANDOFF_REPORT_TABLES) as profile:
            async with profile.database.transaction() as connection:
                for event in events:
                    await repository.record(connection, event)
                frozen_cursor = await repository.high_watermark(connection, "project-1")
                assert frozen_cursor == 4
                page = await repository.list(
                    connection,
                    "project-1",
                    period_start=now - timedelta(days=7),
                    period_end=now + timedelta(seconds=1),
                    sources=("git_commit", "coding_session"),
                    through_cursor=frozen_cursor,
                )
                cursor_page = await repository.list(
                    connection,
                    "project-1",
                    after_cursor=2,
                    through_cursor=frozen_cursor,
                    limit=1,
                )
            assert [item.event_id for item in page] == ["period-git", "period-session"]
            assert [item.cursor for item in page] == [2, 3]
            assert [item.event_id for item in cursor_page] == ["period-session"]

    asyncio.run(scenario())


def test_list_requires_strict_integer_cursor_and_limit_values() -> None:
    async def scenario() -> None:
        repository = SQLiteActivityEventRepository()
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=HANDOFF_REPORT_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            for invalid in (True, 1.0, "1"):
                invalid_value: Any = invalid
                with pytest.raises(InvalidActivityRepositoryArgumentError, match="after_cursor"):
                    await repository.list(connection, "project-1", after_cursor=invalid_value)
                with pytest.raises(InvalidActivityRepositoryArgumentError, match="through_cursor"):
                    await repository.list(connection, "project-1", through_cursor=invalid_value)
                with pytest.raises(InvalidActivityRepositoryArgumentError, match="limit"):
                    await repository.list(connection, "project-1", limit=invalid_value)

    asyncio.run(scenario())


def test_retention_purge_is_project_scoped_and_does_not_regress_cursor() -> None:
    async def scenario() -> None:
        now = datetime(2026, 8, 5, 10, tzinfo=UTC)
        repository = SQLiteActivityEventRepository()
        async with SQLiteProfile.open(SQLiteConfig(), tables=HANDOFF_REPORT_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await repository.record(
                    connection,
                    _event("expired", observed_at=now - timedelta(days=91), occurred_at=now - timedelta(days=91)),
                )
                await repository.record(connection, _event("kept", observed_at=now, occurred_at=now))
                await repository.record(
                    connection,
                    _event(
                        "other-expired",
                        project_id="project-2",
                        observed_at=now - timedelta(days=91),
                        occurred_at=now - timedelta(days=91),
                    ),
                )
                assert await repository.purge(connection, "project-1", now - timedelta(days=90)) == 1
                assert await repository.high_watermark(connection, "project-1") == 2
                remaining = await repository.list(connection, "project-1")
                other = await repository.list(connection, "project-2")
            assert [item.event_id for item in remaining] == ["kept"]
            assert [item.event_id for item in other] == ["other-expired"]

    asyncio.run(scenario())


def test_all_report_table_names_use_the_isolated_prefix() -> None:
    assert HANDOFF_REPORT_TABLES
    assert all(table.name.startswith("pc_handoff_report_") for table in HANDOFF_REPORT_TABLES)
