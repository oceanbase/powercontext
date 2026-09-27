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
import shutil

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.identity import SERVER_IDENTITY_TABLE, open_server_identity_repository


def test_server_identity_singleton_key_is_not_auto_incremented_by_mysql_profiles() -> None:
    ddl = str(CreateTable(SERVER_IDENTITY_TABLE).compile(dialect=mysql.dialect()))

    assert "AUTO_INCREMENT" not in ddl
    assert "CHECK (singleton_key = 1)" in ddl


def test_server_identity_survives_reopen_restore_and_explicit_clone_rotation(tmp_path) -> None:
    async def scenario() -> None:
        primary_path = tmp_path / "primary.db"
        restored_path = tmp_path / "restored.db"
        primary = SQLiteConfig(url=f"sqlite+aiosqlite:///{primary_path}")

        async with open_server_identity_repository(primary) as repository:
            original = await repository.load_or_create()
            assert await repository.load_or_create() == original

        shutil.copy2(primary_path, restored_path)
        restored = SQLiteConfig(url=f"sqlite+aiosqlite:///{restored_path}")
        async with open_server_identity_repository(restored) as repository:
            assert await repository.load_or_create() == original
            rotated = await repository.rotate()
            assert rotated != original
            assert await repository.load_or_create() == rotated

        async with open_server_identity_repository(primary) as repository:
            assert await repository.load_or_create() == original

    asyncio.run(scenario())


def test_concurrent_server_initializers_converge_on_one_identity(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'deployment.db'}")
        async with (
            open_server_identity_repository(config) as first,
            open_server_identity_repository(config) as second,
        ):
            first_identity, second_identity = await asyncio.gather(
                first.load_or_create(),
                second.load_or_create(),
            )

        assert first_identity == second_identity

    asyncio.run(scenario())


def test_in_memory_server_identity_is_stable_only_for_one_open_repository() -> None:
    async def scenario() -> None:
        config = SQLiteConfig()
        async with open_server_identity_repository(config) as repository:
            first = await repository.load_or_create()
            assert await repository.load_or_create() == first

        async with open_server_identity_repository(config) as repository:
            second = await repository.load_or_create()

        assert second != first

    asyncio.run(scenario())
