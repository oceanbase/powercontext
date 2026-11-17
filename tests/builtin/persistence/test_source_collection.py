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


def test_management_writes_apply_only_to_the_target_state_they_were_decided_on() -> None:
    owner = RunnerPrincipal("service", "robot")

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            repository = SourceCollectionRepository()

            async def conflicts(write) -> None:
                with pytest.raises(SourceCollectionError) as refused:
                    async with profile.database.transaction() as connection:
                        await write(connection)
                assert (refused.value.status, refused.value.code) == (409, "conflict")

            async with profile.database.transaction() as connection:
                for name in ("robot-7", "robot-8"):
                    await repository.register_fixed(connection, owner, name)
                read, _ = await repository.add_target(
                    connection,
                    scope_id="scope",
                    runner="robot-7",
                    capability="chat",
                    target_key="key",
                    parameters={"room": "one"},
                    actor="service:robot",
                )
            # Another request changes the parameters after this one read the target.
            async with profile.database.transaction() as connection:
                current = await repository.update_target(
                    connection, read, parameters={"room": "two"}, enabled=None, actor="service:other"
                )
            assert (current.parameters, current.config_version) == ({"room": "two"}, 2)
            # Writing back what the first request read would undo that change under the same version.
            await conflicts(
                lambda connection: repository.update_target(
                    connection, read, parameters={"room": "one"}, enabled=None, actor="service:robot"
                )
            )
            # The target moves to the Server's runner; a request authorized for the device target no longer applies.
            async with profile.database.transaction() as connection:
                moved = await repository.transfer_target(
                    connection, current, "server", reset_checkpoint=False, actor="service:admin"
                )
            for write in (
                lambda connection: repository.update_target(
                    connection, current, parameters=None, enabled=False, actor="service:robot"
                ),
                lambda connection: repository.transfer_target(
                    connection, current, "robot-8", reset_checkpoint=False, actor="service:robot"
                ),
                lambda connection: repository.delete_target(connection, current),
            ):
                await conflicts(write)
            async with profile.database.transaction() as connection:
                kept = await repository.target(connection, "scope", read.target_id)
            assert kept == moved and moved.enabled and moved.runner == "server"

    asyncio.run(scenario())


def test_the_server_runner_takes_targets_with_more_to_collect_first() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            repository = SourceCollectionRepository()

            async def due() -> list[str]:
                async with profile.database.transaction() as connection:
                    return [item.target_key for item in await repository.due(connection, "scope", "server", "chat", 1)]

            async def attempt(key: str, **outcome) -> None:
                async with profile.database.transaction() as connection:
                    assert await repository.record_status(connection, "scope", ids[key], "server", **outcome)

            ids = {}
            async with profile.database.transaction() as connection:
                for key in ("a", "b"):
                    target, _ = await repository.add_target(
                        connection,
                        scope_id="scope",
                        runner="server",
                        capability="chat",
                        target_key=key,
                        parameters={},
                        actor="service:admin",
                    )
                    ids[key] = target.target_id
            first = (await due())[0]
            other = "b" if first == "a" else "a"
            # Attempted most recently, but with more to collect: it stays ahead of the target not yet attempted.
            await attempt(first, has_more=True)
            assert await due() == [first]
            # A failed attempt, or one that leaves nothing, puts it back into the rotation.
            await attempt(first, error_code="collect_failed")
            assert await due() == [other]
            await attempt(other, has_more=True)
            await attempt(other)
            assert await due() == [first]

    asyncio.run(scenario())
