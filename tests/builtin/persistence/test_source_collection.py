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

"""Runner registration binds a name to the whole principal, type and identifier."""

from __future__ import annotations

import asyncio

import pytest

from powercontext.builtin.persistence.source_collection import (
    RunnerPrincipal,
    SourceCollectionError,
    SourceCollectionRepository,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES


def test_a_principal_of_another_type_with_the_same_identifier_is_another_principal() -> None:
    service, user = RunnerPrincipal("service", "robot"), RunnerPrincipal("user", "robot")

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            repository = SourceCollectionRepository()
            async with profile.database.transaction() as connection:
                owned, created = await repository.register_fixed(connection, service, "robot-7")
                assert created and owned.principal == service
                with pytest.raises(SourceCollectionError) as taken:
                    await repository.register_fixed(connection, user, "robot-7")
                assert (taken.value.status, taken.value.code) == (409, "runner_name_taken")

                # The same registration ID belongs to each principal separately, and a suggestion never takes a name.
                first, _ = await repository.register_suggested(connection, service, "registration-0001", "robot-7")
                second, _ = await repository.register_suggested(connection, user, "registration-0001", "robot-7")
                again, created = await repository.register_suggested(connection, user, "registration-0001", None)
                assert (first.runner, second.runner) == ("robot-7-2", "robot-7-3")
                assert (again.runner, created) == ("robot-7-3", False)
                unnamed, _ = await repository.register_suggested(connection, service, "registration-0002", "  ")
                assert unnamed.runner == "runner"

    asyncio.run(scenario())
