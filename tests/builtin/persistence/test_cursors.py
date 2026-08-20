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

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence import GenerationConflictError
from powercontext.builtin.persistence.cursors import SourceCursorRepository, StoredSourceCursor
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.sources import SourceCursor
from tests.builtin.persistence.contract import repository_profile


def test_source_cursor_uses_generation_compare_and_swap() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories):  # noqa: SIM117
            async with profile.database.transaction() as connection:
                first = await repositories.cursors.save(
                    connection,
                    "scope-a",
                    "memory-source-window",
                    SourceCursor(sequence=1),
                    expected_generation=None,
                )
                second = await repositories.cursors.save(
                    connection,
                    "scope-a",
                    "memory-source-window",
                    SourceCursor(sequence=2),
                    expected_generation=first.generation,
                )

                assert first.generation == 1
                assert second.generation == 2
                assert await repositories.cursors.load(connection, "scope-a", "memory-source-window") == second
                with pytest.raises(GenerationConflictError) as error:
                    await repositories.cursors.save(
                        connection,
                        "scope-a",
                        "memory-source-window",
                        SourceCursor(sequence=3),
                        expected_generation=first.generation,
                    )
                assert error.value.actual == 2

    asyncio.run(scenario())


def test_source_cursor_initial_creation_is_safe_across_connections(tmp_path: Path) -> None:
    async def scenario() -> None:
        barrier = asyncio.Barrier(2)

        class SynchronizedRepository(SourceCursorRepository):
            async def load(
                self,
                connection: AsyncConnection,
                scope_id: str,
                binding_name: str,
                /,
                *,
                for_update: bool = False,
            ) -> StoredSourceCursor | None:
                result = await super().load(connection, scope_id, binding_name, for_update=for_update)
                if not for_update and result is None:
                    await barrier.wait()
                return result

        url = f"sqlite+aiosqlite:///{tmp_path / 'cursor-race.db'}"
        config = SQLiteConfig(url=url)

        async def create_cursor(profile: SQLiteProfile, sequence: int) -> StoredSourceCursor | GenerationConflictError:
            repository = SynchronizedRepository()
            try:
                async with profile.database.transaction() as connection:
                    return await repository.save(
                        connection,
                        "scope-a",
                        "memory-source-window",
                        SourceCursor(sequence=sequence),
                        expected_generation=None,
                    )
            except GenerationConflictError as error:
                return error

        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as first_profile:  # noqa: SIM117
            async with SQLiteProfile.open(config, tables=SHARED_TABLES) as second_profile:
                results = await asyncio.gather(
                    create_cursor(first_profile, 1),
                    create_cursor(second_profile, 2),
                )

        created = [result for result in results if isinstance(result, StoredSourceCursor)]
        conflicts = [result for result in results if isinstance(result, GenerationConflictError)]
        assert len(created) == 1
        assert created[0].generation == 1
        assert len(conflicts) == 1
        assert conflicts[0].actual == 1

    asyncio.run(scenario())
