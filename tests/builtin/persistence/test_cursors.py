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

import pytest

from powercontext.builtin.persistence import GenerationConflictError
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
