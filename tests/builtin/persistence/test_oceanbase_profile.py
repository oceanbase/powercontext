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
import os
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import cast
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import Column, MetaData, String, Table, select
from sqlalchemy.dialects.mysql import dialect as mysql_dialect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from powercontext.builtin.persistence.oceanbase import (
    IncompatibleOceanBaseSchemaError,
    OceanBaseConfig,
    OceanBaseProfile,
    UnsupportedOceanBaseTenantError,
)
from powercontext.builtin.persistence.oceanbase import profile as oceanbase_profile_module
from powercontext.builtin.persistence.tables import (
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACTS_TABLE,
    SOURCES_TABLE,
    identity_string,
)

VALID_URL = "mysql+aoceanbase://root%40tenant:secret@127.0.0.1:2881/powercontext?charset=utf8mb4"


class _Result:
    def __init__(self, rows: tuple[tuple[object, ...], ...]) -> None:
        self._rows = rows

    def first(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class _Connection:
    def __init__(self, row: tuple[str, str] | None = ("ob_compatibility_mode", "MYSQL")) -> None:
        self.dialect = mysql_dialect()
        self.row = row
        self.statements: list[str] = []

    async def exec_driver_sql(self, statement: str, parameters: object | None = None) -> _Result:
        self.statements.append(statement)
        if "ob_compatibility_mode" in statement:
            return _Result(() if self.row is None else (self.row,))
        return _Result(())


class _SchemaConnection(_Connection):
    def __init__(self, columns: tuple[tuple[object, ...], ...]) -> None:
        super().__init__()
        self.columns = columns

    async def exec_driver_sql(self, statement: str, parameters: object | None = None) -> _Result:
        self.statements.append(statement)
        if "ob_compatibility_mode" in statement:
            return _Result((("ob_compatibility_mode", "MYSQL"),))
        if "LEFT(TABLE_NAME" in statement:
            return _Result(tuple(row for row in self.columns if str(row[0]).startswith("pc_")))
        return _Result(self.columns)


class _Begin(AbstractAsyncContextManager[_Connection]):
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class _Engine:
    def __init__(
        self,
        *,
        row: tuple[str, str] | None = ("ob_compatibility_mode", "MYSQL"),
        connection: _Connection | None = None,
    ) -> None:
        self.url = make_url(VALID_URL)
        self.connection = connection or _Connection(row)

    def begin(self) -> _Begin:
        return _Begin(self.connection)

    async def dispose(self) -> None:
        return None


@pytest.mark.parametrize(
    "url",
    [
        "mysql+pymysql://root:secret@127.0.0.1:2881/powercontext?charset=utf8mb4",
        "mysql+aiomysql://root:secret@127.0.0.1:2881/powercontext?charset=utf8mb4",
        "postgresql+asyncpg://root:secret@127.0.0.1:2881/powercontext?charset=utf8mb4",
        "mysql+aoceanbase://root:secret@127.0.0.1/powercontext?charset=utf8mb4",
        "mysql+aoceanbase://root:secret@127.0.0.1:2881/?charset=utf8mb4",
        "mysql+aoceanbase://root:secret@127.0.0.1:2881/powercontext",
    ],
)
def test_config_rejects_non_official_or_incomplete_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        OceanBaseConfig(url=SecretStr(url))


def test_config_and_validation_errors_never_render_the_password() -> None:
    password = "do-not-render-this-password"  # noqa: S105
    config = OceanBaseConfig(
        url=SecretStr(f"mysql+aoceanbase://root:{password}@127.0.0.1:2881/powercontext?charset=utf8mb4")
    )

    assert password not in repr(config)
    assert password not in str(config.model_dump())

    with pytest.raises(ValidationError) as caught:
        OceanBaseConfig.model_validate({
            "url": f"mysql+pymysql://root:{password}@127.0.0.1:2881/powercontext?charset=utf8mb4"
        })
    assert password not in str(caught.value)


def test_official_dialect_builds_an_async_engine_without_opening_a_connection() -> None:
    async def scenario() -> None:
        oceanbase_profile_module._register_official_dialect()
        engine = create_async_engine(VALID_URL)
        try:
            assert engine.url.drivername == "mysql+aoceanbase"
            assert engine.dialect.is_async
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_oceanbase_profile_hides_sql_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        captured: dict[str, object] = {}
        engine = _Engine()

        def create_engine(_url: object, **options: object) -> AsyncEngine:
            captured.update(options)
            return cast(AsyncEngine, engine)

        async def create_no_tables(_connection: object, _tables: tuple[Table, ...]) -> None:
            return None

        monkeypatch.setattr(oceanbase_profile_module, "create_async_engine", create_engine)
        monkeypatch.setattr(oceanbase_profile_module, "create_tables", create_no_tables)

        async with OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(VALID_URL)), tables=()):
            pass

        assert captured["hide_parameters"] is True

    asyncio.run(scenario())


@pytest.mark.parametrize("row", [None, ("ob_compatibility_mode", "ORACLE")])
def test_profile_requires_an_oceanbase_mysql_tenant(
    row: tuple[str, str] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = _Engine(row=row)

        async def create_no_tables(_connection: object, _tables: tuple[Table, ...]) -> None:
            return None

        monkeypatch.setattr(oceanbase_profile_module, "create_tables", create_no_tables)
        with pytest.raises(UnsupportedOceanBaseTenantError):
            async with OceanBaseProfile.attach(cast(AsyncEngine, engine), tables=()):
                pass

    asyncio.run(scenario())


def test_profile_rejects_legacy_identity_column_collation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        extension_table = Table(
            "owned_extensions",
            MetaData(),
            Column("extension_id", identity_string(64)),
            Column("display_name", String(64)),
        )
        engine = _Engine(
            connection=_SchemaConnection((
                ("owned_extensions", "extension_id", "utf8mb4_general_ci"),
                ("owned_extensions", "display_name", "utf8mb4_general_ci"),
                ("pc_sources", "source_id", "utf8mb4_unicode_ci"),
                ("pc_sources", "scope_id", "utf8mb4_general_ci"),
                ("pc_artifacts", "family", "utf8mb4_general_ci"),
                ("pc_artifact_lineage_sources", "source_type", "utf8mb4_general_ci"),
                ("pc_sources", "display_name", "utf8mb4_general_ci"),
                ("pc_notes", "display_name", "utf8mb4_general_ci"),
            ))
        )
        created = False

        async def create_no_tables(_connection: object, _tables: tuple[Table, ...]) -> None:
            nonlocal created
            created = True

        monkeypatch.setattr(oceanbase_profile_module, "create_tables", create_no_tables)
        with pytest.raises(IncompatibleOceanBaseSchemaError, match="utf8mb4_general_ci") as caught:
            async with OceanBaseProfile.attach(
                cast(AsyncEngine, engine),
                tables=(SOURCES_TABLE, ARTIFACTS_TABLE, ARTIFACT_LINEAGE_SOURCES_TABLE, extension_table),
            ):
                pass

        assert not created
        assert caught.value.columns == (
            "owned_extensions.extension_id",
            "pc_artifact_lineage_sources.source_type",
            "pc_artifacts.family",
            "pc_sources.scope_id",
            "pc_sources.source_id",
        )
        message = str(caught.value)
        assert "owned_extensions.extension_id" in message
        assert "pc_artifact_lineage_sources.source_type" in message
        assert "pc_artifacts.family" in message
        assert "pc_sources.scope_id" in message
        assert "pc_sources.source_id" in message
        assert "display_name" not in message
        assert "back up" in message.casefold()
        assert "recreate" in message.casefold()
        assert "restore" in message.casefold()
        assert "secret" not in message

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "columns",
    [
        (),
        (("pc_sources", "scope_id", "UTF8MB4_BIN"),),
        (
            ("pc_notes", "display_name", "utf8mb4_general_ci"),
            ("pc_sources", "display_name", "utf8mb4_general_ci"),
        ),
    ],
)
def test_profile_creates_tables_for_empty_or_compatible_schema(
    columns: tuple[tuple[object, ...], ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine = _Engine(connection=_SchemaConnection(columns))
        created: list[tuple[Table, ...]] = []

        async def create_selected_tables(_connection: object, tables: tuple[Table, ...]) -> None:
            created.append(tables)

        monkeypatch.setattr(oceanbase_profile_module, "create_tables", create_selected_tables)
        async with OceanBaseProfile.attach(cast(AsyncEngine, engine), tables=(SOURCES_TABLE,)):
            pass

        assert created == [(SOURCES_TABLE,)]

    asyncio.run(scenario())


LIVE_URL = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")


@pytest.mark.skipif(
    not LIVE_URL,
    reason="set POWERCONTEXT_TEST_OCEANBASE_URL to a dedicated OceanBase MySQL-mode test database",
)
def test_live_oceanbase_profile_smoke() -> None:
    async def scenario() -> None:
        assert LIVE_URL is not None
        async with (
            OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(LIVE_URL)), tables=()) as profile,
            profile.database.transaction() as connection,
        ):
            assert await connection.scalar(select(1)) == 1

    asyncio.run(scenario())


@pytest.mark.skipif(
    not LIVE_URL,
    reason="set POWERCONTEXT_TEST_OCEANBASE_URL to an OceanBase MySQL-mode URL with database creation and deletion privileges",
)
def test_live_oceanbase_profile_rejects_old_schema_collation() -> None:
    async def scenario() -> None:
        assert LIVE_URL is not None
        config = OceanBaseConfig(url=SecretStr(LIVE_URL))
        database_name = f"pc_test_{uuid4().hex}"
        database_created = False
        async with OceanBaseProfile.open(config, tables=()) as server_profile:
            try:
                async with server_profile.database.transaction() as connection:
                    await connection.exec_driver_sql(f"CREATE DATABASE `{database_name}`")
                    database_created = True

                test_url = make_url(LIVE_URL).set(database=database_name).render_as_string(hide_password=False)
                async with OceanBaseProfile.open(
                    OceanBaseConfig(url=SecretStr(test_url)),
                    tables=(),
                ) as setup_profile:
                    async with setup_profile.database.transaction() as connection:
                        await connection.exec_driver_sql(
                            "CREATE TABLE `pc_sources` ("
                            "scope_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL, "
                            "source_type VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL, "
                            "source_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL, "
                            "payload MEDIUMBLOB NOT NULL, journal_position BIGINT NOT NULL, "
                            "PRIMARY KEY (scope_id, source_type, source_id))"
                        )

                    with pytest.raises(IncompatibleOceanBaseSchemaError) as caught:
                        async with OceanBaseProfile.attach(setup_profile.database.engine, tables=(SOURCES_TABLE,)):
                            pass

                    assert caught.value.columns == (
                        "pc_sources.scope_id",
                        "pc_sources.source_id",
                        "pc_sources.source_type",
                    )
            finally:
                if database_created:
                    async with server_profile.database.transaction() as connection:
                        await connection.exec_driver_sql(f"DROP DATABASE `{database_name}`")

    asyncio.run(scenario())
