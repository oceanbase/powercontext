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
from unittest.mock import AsyncMock, MagicMock

import pytest

from powercontext.builtin.persistence import schema
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES


@pytest.fixture
def mock_installed_evidence_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep native index tests independent of the separately tested evidence migration."""
    monkeypatch.setattr(schema, "apply_atomic_memory_evidence_migration", AsyncMock())


@pytest.fixture
def mock_provisioned_mysql_connection(
    monkeypatch: pytest.MonkeyPatch, mock_installed_evidence_schema: None
) -> MagicMock:
    """Run synchronous index probes with existing tables and a stable engine pool."""
    connection = MagicMock()
    inspector = MagicMock()
    inspector.get_table_names.return_value = []
    inspector.has_table.return_value = True
    monkeypatch.setattr(schema, "inspect", lambda _connection: inspector)
    return connection


@pytest.fixture
def initialized_sqlite_database(tmp_path: Path) -> Path:
    """Keep unrelated schemas ready while a test restores its target's legacy shape."""
    database = tmp_path / "initialized.db"

    async def initialize() -> None:
        async with SQLiteProfile.open(SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), tables=BUILTIN_TABLES):
            pass

    asyncio.run(initialize())
    return database
