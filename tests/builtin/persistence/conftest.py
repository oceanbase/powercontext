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

"""Database baselines for focused persistence compatibility tests."""

import asyncio
from pathlib import Path

import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES


@pytest.fixture
def initialized_sqlite_database(tmp_path: Path) -> Path:
    """Keep unrelated schemas ready while a test restores its target's legacy shape."""
    database = tmp_path / "initialized.db"

    async def initialize() -> None:
        async with SQLiteProfile.open(SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), tables=BUILTIN_TABLES):
            pass

    asyncio.run(initialize())
    return database
