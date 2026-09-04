# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

from __future__ import annotations

import asyncio

from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.sources import SourceCursor
from powercontext.sources import SourceMaterialization
from tests.builtin.persistence.contract import SOURCE_ADAPTERS, NoteSource, repository_profile

BINDING = "topic-memory-source-window"


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


def test_concurrent_first_flush_requests_coalesce_into_one_pending_row(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(
            url=f"sqlite+aiosqlite:///{tmp_path / 'concurrent-flush.db'}",
            busy_timeout_ms=10_000,
        )
        sources = SourceRepository(SOURCE_ADAPTERS)
        pending = ArtifactProcessingPendingRepository()
        barrier = asyncio.Barrier(2)
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as first_profile:
            async with first_profile.database.transaction() as connection:
                await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(
                        name="note-1",
                        materialization=SourceMaterialization.CAPTURED,
                        body="body",
                    ),
                )

            async with SQLiteProfile.open(config, tables=SHARED_TABLES) as second_profile:

                async def request(profile: SQLiteProfile):
                    async with profile.database.transaction() as connection:
                        await barrier.wait()
                        return await pending.request_flush(connection, "scope-a", BINDING)

                results = await asyncio.gather(request(first_profile), request(second_profile))

            async with first_profile.database.transaction() as connection:
                stored = await pending.load(connection, "scope-a", BINDING)

            assert all(result is not None for result in results)
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
