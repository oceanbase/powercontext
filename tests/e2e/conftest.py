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

"""Opt-in real backends for the clean Atomic Memory migration scenarios."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from tests.e2e.atomic_memory_migration_backend import MigrationBackend, migration_profile


@pytest.fixture(params=("sqlite", "seekdb", "oceanbase"))
def migration_backend(request: pytest.FixtureRequest, short_tmp_path: Path) -> Iterator[MigrationBackend]:
    """Own a fresh local directory or a newly created OceanBase database for each case."""

    if request.param == "sqlite":
        yield MigrationBackend(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{short_tmp_path / 'migration.db'}"), short_tmp_path
        )
        return
    if request.param == "seekdb":
        if os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1":
            pytest.skip("real clean migration seekdb acceptance not enabled")
        pytest.importorskip("pylibseekdb")
        yield MigrationBackend(SeekDBConfig(path=short_tmp_path / "seekdb"), short_tmp_path)
        return
    value = os.environ.get("POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL")
    if not value:
        pytest.skip("real clean migration OceanBase acceptance not enabled")
    config = OceanBaseConfig(url=SecretStr(value))
    url = make_url(config.url.get_secret_value())
    if not url.database or any(key.lower() in {"db", "database"} for key in url.query):
        pytest.fail(
            "OceanBase acceptance requires an explicit management database without query overrides", pytrace=False
        )
    database = f"pc_atomic_migrate_{uuid4().hex}"
    owned = False

    async def manage(*, create: bool) -> None:
        nonlocal owned
        async with migration_profile(config) as profile, profile.database.transaction() as connection:
            assert (await connection.exec_driver_sql("SELECT DATABASE()")).scalar_one() == url.database
            await connection.exec_driver_sql(
                f"CREATE DATABASE `{database}`" if create else f"DROP DATABASE `{database}`"
            )
            # MySQL DDL commits before the surrounding transaction exits.
            owned = create

    try:
        asyncio.run(manage(create=True))
        child_url = url.set(database=database).render_as_string(hide_password=False)
        yield MigrationBackend(OceanBaseConfig(url=SecretStr(child_url)), short_tmp_path)
    finally:
        if owned:
            asyncio.run(manage(create=False))
