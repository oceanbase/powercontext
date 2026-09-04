# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

from __future__ import annotations

import asyncio

import pytest

from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.sources import ContentCapture, SourceCursor
from powercontext.sources import SourceMaterialization
from tests.builtin.persistence.contract import SOURCE_ADAPTERS, NoteSource, repository_profile

BINDING = "topic-memory-source-window"


def test_runtime_source_capture_raises_topic_memory_pending_only_for_new_sources() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories):
            contexts = RelationalContexts(database=profile.database)
            context = await contexts.get("scope-a")
            capture = ContentCapture(source_id="note-1", content="body")

            await context.sources.capture(capture)
            await context.sources.capture(capture)

            async with profile.database.transaction() as connection:
                stored = await repositories.processing_pending.load(connection, "scope-a", BINDING)
                assert stored is not None
                assert stored.source_through == 1
                assert stored.flush_generation == 0

    asyncio.run(scenario())


def test_source_and_pending_watermark_roll_back_together() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories):
            source = NoteSource(
                name="note-1",
                materialization=SourceMaterialization.CAPTURED,
                body="body",
            )
            with pytest.raises(RuntimeError):
                async with profile.database.transaction() as connection:
                    stored, created = await repositories.sources.add_with_status(connection, "scope-a", source)
                    assert created
                    await repositories.processing_pending.raise_source(
                        connection, "scope-a", BINDING, stored.journal_position
                    )
                    raise RuntimeError

            async with profile.database.transaction() as connection:
                assert await repositories.sources.list(connection, "scope-a") == ()
                assert await repositories.processing_pending.load(connection, "scope-a", BINDING) is None

    asyncio.run(scenario())


def test_pending_watermarks_are_monotonic_and_keys_are_independent() -> None:
    async def scenario() -> None:
        async with (
            repository_profile() as (profile, repositories),
            profile.database.transaction() as connection,
        ):
            pending = repositories.processing_pending
            first = await pending.raise_source(connection, "scope-a", BINDING, 3)
            lowered = await pending.raise_source(connection, "scope-a", BINDING, 2)
            other_scope = await pending.raise_source(connection, "scope-b", BINDING, 7)
            other_binding = await pending.raise_source(connection, "scope-a", "other", 5)

            assert first.source_through == lowered.source_through == 3
            assert first.flush_generation == first.handled_flush_generation == 0
            assert other_scope.source_through == 7
            assert other_binding.source_through == 5

    asyncio.run(scenario())


def test_flush_requests_coalesce_and_handling_does_not_skip_a_later_generation() -> None:
    async def scenario() -> None:
        async with (
            repository_profile() as (profile, repositories),
            profile.database.transaction() as connection,
        ):
            source = NoteSource(
                name="note-1",
                materialization=SourceMaterialization.CAPTURED,
                body="body",
            )
            await repositories.sources.add(connection, "scope-a", source)
            pending = repositories.processing_pending

            first = await pending.request_flush(connection, "scope-a", BINDING)
            second = await pending.request_flush(connection, "scope-a", BINDING)
            assert first is not None and first.flush_generation == 1
            assert second is not None and second.flush_generation == 2
            assert second.source_through == 1

            handled = await pending.mark_flush_handled(connection, "scope-a", BINDING, 1)
            assert handled is not None
            assert handled.handled_flush_generation == 1
            assert handled.flush_generation == 2

    asyncio.run(scenario())


def test_concurrent_first_flush_requests_coalesce_into_one_pending_row(tmp_path) -> None:
    async def scenario() -> None:
        class CoordinatedPendingRepository(ArtifactProcessingPendingRepository):
            def __init__(self) -> None:
                self.arrivals = 0
                self.both_missing = asyncio.Event()

            async def load(self, connection, scope_id, binding_name, /, *, for_update=False):
                stored = await super().load(
                    connection,
                    scope_id,
                    binding_name,
                    for_update=for_update,
                )
                if for_update and stored is None:
                    self.arrivals += 1
                    if self.arrivals == 2:
                        self.both_missing.set()
                    await self.both_missing.wait()
                return stored

        config = SQLiteConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'concurrent-flush.db'}",
            busy_timeout_ms=10_000,
        )
        sources = SourceRepository(SOURCE_ADAPTERS)
        pending = CoordinatedPendingRepository()
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(
                        name="note-1",
                        materialization=SourceMaterialization.CAPTURED,
                        body="body",
                    ),
                )

            async def request():
                async with profile.database.transaction() as connection:
                    return await pending.request_flush(connection, "scope-a", BINDING)

            results = await asyncio.gather(request(), request())
            assert all(result is not None for result in results)
            async with profile.database.transaction() as connection:
                stored = await pending.load(connection, "scope-a", BINDING)
                assert stored is not None
                assert stored.flush_generation == 2
                assert stored.source_through == 1

    asyncio.run(scenario())


def test_flush_is_idle_when_cursor_covers_source_head() -> None:
    async def scenario() -> None:
        async with (
            repository_profile() as (profile, repositories),
            profile.database.transaction() as connection,
        ):
            source = NoteSource(
                name="note-1",
                materialization=SourceMaterialization.CAPTURED,
                body="body",
            )
            await repositories.sources.add(connection, "scope-a", source)
            await repositories.cursors.save(
                connection,
                "scope-a",
                BINDING,
                SourceCursor(sequence=1),
                expected_generation=None,
            )
            assert await repositories.processing_pending.request_flush(connection, "scope-a", BINDING) is None

    asyncio.run(scenario())


def test_pending_deletion_requires_cursor_coverage_and_equal_flush_generations() -> None:
    async def scenario() -> None:
        async with (
            repository_profile() as (profile, repositories),
            profile.database.transaction() as connection,
        ):
            pending = repositories.processing_pending
            for index in range(2):
                await repositories.sources.add(
                    connection,
                    "scope-a",
                    NoteSource(
                        name=f"note-{index}",
                        materialization=SourceMaterialization.CAPTURED,
                        body=f"body-{index}",
                    ),
                )
            await pending.raise_source(connection, "scope-a", BINDING, 2)
            requested = await pending.request_flush(connection, "scope-a", BINDING)
            assert requested is not None
            assert not await pending.delete_if_covered(connection, "scope-a", BINDING, cursor=2)
            await pending.mark_flush_handled(connection, "scope-a", BINDING, requested.flush_generation)
            assert not await pending.delete_if_covered(connection, "scope-a", BINDING, cursor=1)
            assert await pending.delete_if_covered(connection, "scope-a", BINDING, cursor=2)
            assert await pending.load(connection, "scope-a", BINDING) is None

    asyncio.run(scenario())
