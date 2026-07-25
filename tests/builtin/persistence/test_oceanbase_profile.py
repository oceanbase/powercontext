from __future__ import annotations

import asyncio
import os
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import cast

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import Table, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from powercontext.builtin.persistence.oceanbase import (
    OceanBaseConfig,
    OceanBaseProfile,
    UnsupportedOceanBaseTenantError,
)
from powercontext.builtin.persistence.oceanbase import profile as oceanbase_profile_module

VALID_URL = "mysql+aoceanbase://root%40tenant:secret@127.0.0.1:2881/powercontext?charset=utf8mb4"


class _Result:
    def __init__(self, row: tuple[str, str] | None = ("ob_compatibility_mode", "MYSQL")) -> None:
        self._row = row

    def first(self) -> tuple[str, str] | None:
        return self._row


class _Connection:
    def __init__(self, row: tuple[str, str] | None = ("ob_compatibility_mode", "MYSQL")) -> None:
        self.row = row
        self.statements: list[str] = []

    async def exec_driver_sql(self, statement: str) -> _Result:
        self.statements.append(statement)
        return _Result(self.row)


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
    def __init__(self, *, row: tuple[str, str] | None = ("ob_compatibility_mode", "MYSQL")) -> None:
        self.url = make_url(VALID_URL)
        self.connection = _Connection(row)

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
