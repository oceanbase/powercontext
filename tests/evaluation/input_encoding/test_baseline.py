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

"""Controlled database-driver checks; no external OceanBase service is used."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from powercontext.builtin.persistence.bootstrap import MissingDatabaseError
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.oceanbase import profile as oceanbase
from powercontext.server.settings import ServerSettings
from tests.e2e.rfc2086_real_acceptance import inspect_database


class _DatabaseEngine:
    dialect = SimpleNamespace(paramstyle="named")

    def __init__(self, name, databases, statements):
        self.name = name
        self.databases = databases
        self.statements = statements

    @asynccontextmanager
    async def connect(self):
        if self.name and self.name not in self.databases:
            raise OperationalError(None, None, Exception(1049, "synthetic missing database"))
        yield self

    def begin(self):
        return self.connect()

    async def exec_driver_sql(self, statement):
        self.statements.append(statement)
        if statement.startswith("CREATE DATABASE"):
            self.databases.add(statement.split("`", 2)[1])
        return SimpleNamespace(first=lambda: ("ob_compatibility_mode", "MYSQL"), scalar_one=lambda: self.name)

    async def scalar(self, statement):
        self.statements.append(str(statement))
        return "synthetic OceanBase"

    async def run_sync(self, callback):
        return ["pc_scopes", "pc_sources", "pc_artifacts", "pc_artifact_heads"]

    async def dispose(self):
        pass


@pytest.mark.parametrize("exists", [False, True])
def test_baseline_database_inspection_never_provisions_a_database(monkeypatch, exists):
    databases = {"existing"} if exists else set()
    statements = []

    @asynccontextmanager
    async def initialized(profile, **kwargs):
        # Schema reflection is controlled; connection/provisioning uses the real profile path.
        yield SimpleNamespace(database=SimpleNamespace(transaction=profile.database.engine.begin))

    monkeypatch.setattr(
        oceanbase, "_create_engine", lambda config, url: _DatabaseEngine(url.database, databases, statements)
    )
    monkeypatch.setattr(oceanbase, "_initialized_profile", initialized)
    settings = ServerSettings(
        database=OceanBaseConfig(
            url=SecretStr("mysql+aoceanbase://user:synthetic@localhost:2881/existing?charset=utf8mb4")
        )
    )
    if exists:
        report = asyncio.run(inspect_database(settings))
        assert report["state"] == "connected"
        assert report["schema_changed"] is report["data_changed"] is False
        assert all(report["required_tables_present"].values())
    else:
        with pytest.raises(MissingDatabaseError):
            asyncio.run(inspect_database(settings))
    assert databases == ({"existing"} if exists else set())
    assert not any(statement.startswith("CREATE") for statement in statements)
