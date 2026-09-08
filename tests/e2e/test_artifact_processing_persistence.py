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
from pathlib import Path
from typing import NoReturn

import pytest
from sqlalchemy.exc import IntegrityError

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.persistence import GenerationConflictError
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.runtime import (
    ArtifactProcessingBinding,
    ArtifactProcessingWorkAssignment,
    BuiltinConfig,
    BuiltinRuntime,
    CaptureSource,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER, SourceCursor

BINDING = TOPIC_MEMORY_SOURCE_WINDOW_BINDING


class _BlockedLauncher:
    async def start(self, assignment: ArtifactProcessingWorkAssignment) -> NoReturn:
        del assignment
        future: asyncio.Future[NoReturn] = asyncio.get_running_loop().create_future()
        return await future


def _processing_binding() -> ArtifactProcessingBinding:
    return ArtifactProcessingBinding(BINDING, 1, _BlockedLauncher())


async def _create_scope(runtime: BuiltinRuntime, idempotency_key: str) -> str:
    assert runtime.scopes is not None
    return (
        await runtime.scopes.create(
            ScopeDraft(
                title=idempotency_key, summary="Artifact processing persistence test", idempotency_key=idempotency_key
            )
        )
    ).scope_id


def test_runtime_capture_persists_idempotent_scope_isolated_pending(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = _sqlite_config(tmp_path / "capture.db")
        first_capture = CaptureSource(source_id="note-1", content="first", metadata={})

        async with open_builtin_runtime(
            BuiltinConfig(database=config),
            artifact_processing_bindings=(_processing_binding(),),
        ) as runtime:
            scope_a = await _create_scope(runtime, "scope-a")
            scope_b = await _create_scope(runtime, "scope-b")
            first = await runtime.sources.for_scope(scope_a).capture(first_capture)
            duplicate = await runtime.sources.for_scope(scope_a).capture(first_capture)
            second = await runtime.sources.for_scope(scope_a).capture(
                CaptureSource(source_id="note-2", content="second", metadata={})
            )
            other_scope = await runtime.sources.for_scope(scope_b).capture(first_capture)

        assert (first.sequence, duplicate.sequence, second.sequence, other_scope.sequence) == (1, 1, 2, 1)

        sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
        pending = ArtifactProcessingPendingRepository()
        async with (
            SQLiteProfile.open(config, tables=SHARED_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            scope_a_sources = await sources.list(connection, scope_a)
            scope_b_sources = await sources.list(connection, scope_b)
            scope_a_pending = await pending.load(connection, scope_a, BINDING)
            scope_b_pending = await pending.load(connection, scope_b, BINDING)

        assert tuple(source.journal_position for source in scope_a_sources) == (1, 2)
        assert tuple(source.journal_position for source in scope_b_sources) == (1,)
        assert scope_a_pending is not None
        assert (
            scope_a_pending.source_through,
            scope_a_pending.flush_generation,
            scope_a_pending.handled_flush_generation,
        ) == (2, 0, 0)
        assert scope_b_pending is not None
        assert (
            scope_b_pending.source_through,
            scope_b_pending.flush_generation,
            scope_b_pending.handled_flush_generation,
        ) == (1, 0, 0)

    asyncio.run(scenario())


def test_runtime_capture_rolls_back_source_when_pending_write_fails(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = _sqlite_config(tmp_path / "rollback.db")
        async with (
            SQLiteProfile.open(config, tables=SHARED_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.exec_driver_sql("""
                CREATE TRIGGER reject_pending_insert
                BEFORE INSERT ON pc_artifact_processing_pending
                BEGIN
                    SELECT RAISE(ABORT, 'forced pending insert failure');
                END;
            """)

        with pytest.raises(IntegrityError, match="forced pending insert failure"):
            async with open_builtin_runtime(
                BuiltinConfig(database=config),
                artifact_processing_bindings=(_processing_binding(),),
            ) as runtime:
                scope_id = await _create_scope(runtime, "scope-a")
                await runtime.sources.for_scope(scope_id).capture(
                    CaptureSource(source_id="note-1", content="body", metadata={})
                )

        sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
        pending = ArtifactProcessingPendingRepository()
        async with (
            SQLiteProfile.open(config, tables=SHARED_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            stored_sources = await sources.list(connection, scope_id)
            stored_pending = await pending.load(connection, scope_id, BINDING)

        assert stored_sources == ()
        assert stored_pending is None

    asyncio.run(scenario())


def test_pending_lifecycle_keeps_cursor_cas_as_publication_guard(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = _sqlite_config(tmp_path / "pending-lifecycle.db")
        async with open_builtin_runtime(
            BuiltinConfig(database=config),
            artifact_processing_bindings=(_processing_binding(),),
        ) as runtime:
            scope_id = await _create_scope(runtime, "scope-a")
            sources = runtime.sources.for_scope(scope_id)
            await sources.capture(CaptureSource(source_id="note-1", content="first", metadata={}))
            await sources.capture(CaptureSource(source_id="note-2", content="second", metadata={}))

        pending = ArtifactProcessingPendingRepository()
        cursors = SourceCursorRepository()
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                initial = await cursors.save(
                    connection,
                    scope_id,
                    BINDING,
                    SourceCursor(sequence=0),
                    expected_generation=None,
                )
            async with profile.database.transaction() as connection:
                first_flush = await pending.request_flush(connection, scope_id, BINDING)
            async with profile.database.transaction() as connection:
                second_flush = await pending.request_flush(connection, scope_id, BINDING)

            assert initial.generation == 1
            assert first_flush is not None and first_flush.flush_generation == 1
            assert second_flush is not None
            assert (second_flush.source_through, second_flush.flush_generation) == (2, 2)

            async with profile.database.transaction() as connection:
                first_reader = await cursors.load(connection, scope_id, BINDING)
            async with profile.database.transaction() as connection:
                stale_reader = await cursors.load(connection, scope_id, BINDING)

            assert first_reader is not None and first_reader.generation == 1
            assert stale_reader is not None and stale_reader.generation == 1

            async with profile.database.transaction() as connection:
                published = await cursors.save(
                    connection,
                    scope_id,
                    BINDING,
                    SourceCursor(sequence=2),
                    expected_generation=first_reader.generation,
                )

            with pytest.raises(GenerationConflictError) as conflict:
                async with profile.database.transaction() as connection:
                    await cursors.save(
                        connection,
                        scope_id,
                        BINDING,
                        SourceCursor(sequence=2),
                        expected_generation=stale_reader.generation,
                    )

            assert published.generation == 2
            assert conflict.value.actual == published.generation

            async with profile.database.transaction() as connection:
                handled_first = await pending.mark_flush_handled(
                    connection,
                    scope_id,
                    BINDING,
                    first_flush.flush_generation,
                )
                deleted_early = await pending.delete_if_covered(connection, scope_id, BINDING)

            assert handled_first is not None
            assert (
                handled_first.handled_flush_generation,
                handled_first.flush_generation,
                deleted_early,
            ) == (1, 2, False)

            async with profile.database.transaction() as connection:
                handled_latest = await pending.mark_flush_handled(
                    connection,
                    scope_id,
                    BINDING,
                    second_flush.flush_generation,
                )
                deleted_before_cursor_coverage = await pending.delete_if_covered(
                    connection,
                    scope_id,
                    BINDING,
                    cursor=1,
                )
                deleted = await pending.delete_if_covered(connection, scope_id, BINDING)
                stored_pending = await pending.load(connection, scope_id, BINDING)
                stored_cursor = await cursors.load(connection, scope_id, BINDING)

            assert handled_latest is not None and handled_latest.handled_flush_generation == 2
            assert not deleted_before_cursor_coverage
            assert deleted
            assert stored_pending is None
            assert stored_cursor == published

    asyncio.run(scenario())


def _sqlite_config(path: Path) -> SQLiteConfig:
    return SQLiteConfig(url=f"sqlite+aiosqlite:///{path}")
