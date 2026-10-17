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
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy.dialects.mysql import dialect as mysql_dialect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from powercontext.builtin.persistence.bootstrap import (
    DatabaseAccessError,
    DatabaseCreationError,
    MissingDatabaseError,
    ensure_database,
    require_existing_database,
)
from powercontext.builtin.persistence.errors import PersistenceError
from powercontext.builtin.persistence.oceanbase import (
    OceanBaseConfig,
    OceanBaseProfile,
    UnsupportedOceanBaseTenantError,
)
from powercontext.builtin.persistence.seekdb import LegacySeekDBDatabaseError, SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.seekdb import profile as seekdb_module


def driver_error(code: int) -> OperationalError:
    return OperationalError(None, None, Exception(code, "driver diagnostic"))


class Instance:
    def __init__(self, databases: set[str]) -> None:
        self.databases = databases
        self.failure: int | None = None
        self.creation_failure: int | None = None
        self.on_create: Callable[[], None] | None = None
        self.statements: list[str] = []
        self.legacy_tables = False
        self.case_mode = 0
        self.engines: list[Engine] = []

    def engine(self, name: str | None) -> Engine:
        engine = Engine(self, name)
        self.engines.append(engine)
        return engine


class Engine:
    def __init__(self, instance: Instance, name: str | None) -> None:
        self.instance = instance
        self.name = name
        self.dialect = mysql_dialect()
        self.disposed = False

    @asynccontextmanager
    async def connect(self):
        if self.instance.failure is not None:
            raise driver_error(self.instance.failure)
        if self.name and self.name not in self.instance.databases:
            raise driver_error(1049)
        yield self

    def begin(self):
        return self.connect()

    async def exec_driver_sql(self, statement: str):
        self.instance.statements.append(statement)
        if statement.startswith("CREATE DATABASE"):
            if self.instance.on_create is not None:
                self.instance.on_create()
            if self.instance.creation_failure is not None:
                raise driver_error(self.instance.creation_failure)
            self.instance.databases.add("custom-db")
        row = ("pc_sources",) if self.instance.legacy_tables else None
        value = self.instance.case_mode if statement == "SELECT @@lower_case_table_names" else self.name
        return SimpleNamespace(first=lambda: row, scalar_one=lambda: value)

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.parametrize("name", ["powercontext", "test", "custom-db", "知识库"])
def test_seekdb_accepts_default_and_custom_names(tmp_path: Path, name: str) -> None:
    config = SeekDBConfig(path=tmp_path, database=name)
    assert config.database == name
    assert SeekDBConfig(path=tmp_path).database == "powercontext"


@pytest.mark.parametrize(
    "name", ["", " ", " trailing ", "a/b", "a\\b", "a.b", "a\x00b", "a\x85b", "a\ud800b", "x" * 65, "mysql"]
)
def test_seekdb_rejects_invalid_database_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValidationError, match="Database name"):
        SeekDBConfig(path=tmp_path, database=name)


@pytest.mark.parametrize("name", ["quoted`db", "`database", "database`", "quoted``db"])
@pytest.mark.parametrize("backend", ["seekdb", "oceanbase"])
def test_configs_reject_backtick_database_names_before_initialization(tmp_path: Path, name: str, backend: str) -> None:
    path = tmp_path / "not-created"
    with pytest.raises(ValidationError, match="must not contain backticks") as error:
        if backend == "seekdb":
            SeekDBConfig(path=path, database=name)
        else:
            url = URL.create(
                "mysql+aoceanbase",
                username="user",
                password="private-password",  # noqa: S106
                host="localhost",
                port=2881,
                database=name,
                query={"charset": "utf8mb4"},
            )
            OceanBaseConfig(url=SecretStr(url.render_as_string(hide_password=False)))
    assert "Choose a name without backticks" in str(error.value)
    assert "private-password" not in str(error.value)
    assert not path.exists()


def test_bootstrap_rejects_backticks_before_any_database_operation() -> None:
    async def scenario() -> None:
        instance = Instance(set())
        with pytest.raises(ValueError, match="Choose a name without backticks"):
            await ensure_database(
                cast(AsyncEngine, instance.engine("quoted`db")),
                lambda: cast(AsyncEngine, instance.engine(None)),
                name="quoted`db",
            )
        assert instance.statements == []
        assert instance.databases == set()

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["", "/", "/custom_db"])
def test_oceanbase_resolves_only_missing_database_names(path: str) -> None:
    value = f"mysql+aoceanbase://user:encoded%40password@localhost:2881{path}?charset=utf8mb4&connect_timeout=12"
    config = OceanBaseConfig(url=SecretStr(value))
    url = make_url(config.url.get_secret_value())
    assert url.database == ("custom_db" if path == "/custom_db" else "powercontext")
    assert url.password == "encoded@password"  # noqa: S105
    assert url.query == {"charset": "utf8mb4", "connect_timeout": "12"}
    assert "encoded" not in repr(config)


def test_usable_database_needs_no_creation_privileges() -> None:
    async def scenario() -> None:
        instance = Instance({"custom-db"})
        instance.creation_failure = 1044
        await ensure_database(
            cast(AsyncEngine, instance.engine("custom-db")),
            lambda: cast(AsyncEngine, instance.engine(None)),
            name="custom-db",
        )
        assert not any(sql.startswith("CREATE") for sql in instance.statements)

    asyncio.run(scenario())


def test_missing_database_is_created_with_a_quoted_identifier() -> None:
    async def scenario() -> None:
        instance = Instance(set())
        await ensure_database(
            cast(AsyncEngine, instance.engine("custom-db")),
            lambda: cast(AsyncEngine, instance.engine(None)),
            name="custom-db",
        )
        assert instance.databases == {"custom-db"}
        assert [sql for sql in instance.statements if sql.startswith("CREATE")] == [
            "CREATE DATABASE IF NOT EXISTS `custom-db` CHARACTER SET utf8mb4"
        ]
        assert all(engine.disposed for engine in instance.engines if engine.name is None)

    asyncio.run(scenario())


@pytest.mark.parametrize("code", [1044, 1142, 1227])
def test_missing_database_permission_error_has_manual_instructions(code: int) -> None:
    async def scenario() -> None:
        instance = Instance(set())
        instance.creation_failure = code
        with pytest.raises(DatabaseCreationError, match="administrator") as error:
            await ensure_database(
                cast(AsyncEngine, instance.engine("custom-db")),
                lambda: cast(AsyncEngine, instance.engine(None)),
                name="custom-db",
            )
        assert "CREATE DATABASE IF NOT EXISTS `custom-db`" in str(error.value)
        assert "same instance/tenant" in str(error.value)
        assert instance.databases == set()

    asyncio.run(scenario())


@pytest.mark.parametrize("code", [1045, 2003])
def test_non_missing_connection_errors_do_not_create_databases(code: int) -> None:
    async def scenario() -> None:
        instance = Instance(set())
        instance.failure = code
        with pytest.raises(OperationalError) as error:
            await ensure_database(
                cast(AsyncEngine, instance.engine("custom-db")),
                lambda: cast(AsyncEngine, instance.engine(None)),
                name="custom-db",
            )
        driver_failure = error.value.orig
        assert driver_failure is not None
        assert driver_failure.args[0] == code
        assert instance.statements == []

    asyncio.run(scenario())


@pytest.mark.parametrize("code", [1007, 1044])
def test_concurrent_creator_makes_database_usable(code: int) -> None:
    async def scenario() -> None:
        instance = Instance(set())
        instance.creation_failure = code
        instance.on_create = lambda: instance.databases.add("custom-db")
        await ensure_database(
            cast(AsyncEngine, instance.engine("custom-db")),
            lambda: cast(AsyncEngine, instance.engine(None)),
            name="custom-db",
        )
        assert "custom-db" in instance.databases

    asyncio.run(scenario())


def test_unknown_creation_error_is_not_hidden() -> None:
    async def scenario() -> None:
        instance = Instance(set())
        instance.creation_failure = 1064
        with pytest.raises(OperationalError):
            await ensure_database(
                cast(AsyncEngine, instance.engine("custom-db")),
                lambda: cast(AsyncEngine, instance.engine(None)),
                name="custom-db",
            )
        assert instance.databases == set()

    asyncio.run(scenario())


def test_legacy_seekdb_requires_an_explicit_choice_before_table_initialization(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        instance = Instance({"test"})
        instance.legacy_tables = True
        native = SimpleNamespace(connection_options=lambda: {}, close=lambda: None)
        monkeypatch.setattr(
            seekdb_module, "_load_binding", lambda: SimpleNamespace(aopen=AsyncMock(return_value=native))
        )
        monkeypatch.setattr(
            seekdb_module,
            "_create_engine",
            lambda config, _options, **options: instance.engine(options.get("database", config.database)),
        )
        with pytest.raises(LegacySeekDBDatabaseError, match="POWERCONTEXT_SERVER_DATABASE_DATABASE=test"):
            async with SeekDBProfile.open(SeekDBConfig(path=tmp_path), tables=()):
                pytest.fail("Old data must require a database choice")
        assert instance.databases == {"test"}
        assert not any(sql.startswith("CREATE") for sql in instance.statements)
        assert all(engine.disposed for engine in instance.engines)

    asyncio.run(scenario())


def test_attached_oceanbase_engine_requires_an_explicit_database() -> None:
    async def scenario() -> None:
        engine = SimpleNamespace(url=make_url("mysql+aoceanbase://user:password@host:2881/?charset=utf8mb4"))
        with pytest.raises(ValueError, match="must select a database"):
            async with OceanBaseProfile.attach(cast(AsyncEngine, engine), tables=()):
                pytest.fail("Caller-owned engine must retain an explicit target")

    asyncio.run(scenario())


def test_access_denied_does_not_claim_database_is_missing_or_attempt_creation() -> None:
    async def scenario() -> None:
        instance = Instance(set())
        instance.failure = 1044
        with pytest.raises(DatabaseAccessError, match="If it is missing"):
            await ensure_database(
                cast(AsyncEngine, instance.engine("custom-db")),
                lambda: cast(AsyncEngine, instance.engine(None)),
                name="custom-db",
            )
        assert instance.statements == []

    asyncio.run(scenario())


def test_connection_override_cannot_silently_select_another_database() -> None:
    async def scenario() -> None:
        instance = Instance({"other"})
        with pytest.raises(PersistenceError, match="did not select configured database"):
            await ensure_database(
                cast(AsyncEngine, instance.engine("other")),
                lambda: cast(AsyncEngine, instance.engine(None)),
                name="custom-db",
            )
        assert not any(sql.startswith("CREATE") for sql in instance.statements)

    asyncio.run(scenario())


def test_unsupported_tenant_is_rejected_before_database_creation() -> None:
    async def scenario() -> None:
        instance = Instance(set())

        async def reject_tenant(_connection) -> None:
            raise UnsupportedOceanBaseTenantError("ORACLE")

        with pytest.raises(UnsupportedOceanBaseTenantError):
            await ensure_database(
                cast(AsyncEngine, instance.engine("custom-db")),
                lambda: cast(AsyncEngine, instance.engine(None)),
                name="custom-db",
                require_backend=reject_tenant,
            )
        assert instance.databases == set()
        assert not any(sql.startswith("CREATE") for sql in instance.statements)
        assert all(engine.disposed for engine in instance.engines if engine.name is None)

    asyncio.run(scenario())


@pytest.mark.parametrize("case_mode", [0, 1, 2])
def test_target_identity_respects_server_identifier_case_rules(case_mode: int) -> None:
    async def scenario() -> None:
        instance = Instance({"mixed"})
        instance.case_mode = case_mode

        async def connect() -> None:
            await ensure_database(
                cast(AsyncEngine, instance.engine("mixed")),
                lambda: cast(AsyncEngine, instance.engine(None)),
                name="Mixed",
            )

        if case_mode == 0:
            with pytest.raises(PersistenceError, match="did not select configured database"):
                await connect()
        else:
            await connect()
        assert not any(sql.startswith("CREATE") for sql in instance.statements)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "options",
    [
        "db=private-secret",
        "database=private-secret",
        "db=first&db=private-secret",
        "database=first&database=private-secret",
    ],
)
def test_oceanbase_rejects_conflicting_query_database_selection(options: str) -> None:
    with pytest.raises(ValidationError, match="URL path") as error:
        OceanBaseConfig.model_validate({
            "url": f"mysql+aoceanbase://user:password@host:2881/custom?charset=utf8mb4&{options}"
        })
    assert "private-secret" not in str(error.value)


def test_read_only_inspection_reuses_an_existing_database() -> None:
    async def scenario() -> None:
        instance = Instance({"custom-db"})
        await require_existing_database(cast(AsyncEngine, instance.engine("custom-db")), name="custom-db")
        assert not any(statement.startswith("CREATE") for statement in instance.statements)

    asyncio.run(scenario())


def test_read_only_inspection_reports_a_missing_database_without_creating_it() -> None:
    async def scenario() -> None:
        instance = Instance(set())
        with pytest.raises(MissingDatabaseError, match="does not exist") as error:
            await require_existing_database(cast(AsyncEngine, instance.engine("custom-db")), name="custom-db")
        assert error.value.database_name == "custom-db"
        assert not any(statement.startswith("CREATE") for statement in instance.statements)

    asyncio.run(scenario())


def test_read_only_inspection_reports_denied_access_without_claiming_absence() -> None:
    async def scenario() -> None:
        instance = Instance(set())
        instance.failure = 1044
        with pytest.raises(DatabaseAccessError, match="denied") as error:
            await require_existing_database(cast(AsyncEngine, instance.engine("custom-db")), name="custom-db")
        assert "CREATE DATABASE IF NOT EXISTS `custom-db`" in str(error.value)
        assert not any(statement.startswith("CREATE") for statement in instance.statements)

    asyncio.run(scenario())
