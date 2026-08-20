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

"""OceanBase MySQL-mode profile using the official async SQLAlchemy dialect."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import Table
from sqlalchemy.dialects import registry as dialect_registry
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import PersistenceError
from powercontext.builtin.persistence.schema import create_tables

_DIALECT_DRIVER = "mysql+aoceanbase"
_DIALECT_REGISTRY_NAME = "mysql.aoceanbase"
_DIALECT_MODULE = "pyobvector"
_DIALECT_CLASS = "AsyncOceanBaseDialect"


class UnsupportedOceanBaseTenantError(PersistenceError):
    """Raised when the connected tenant is not an OceanBase MySQL tenant."""

    def __init__(self, compatibility_mode: str | None) -> None:
        self.compatibility_mode = compatibility_mode
        description = "missing ob_compatibility_mode" if compatibility_mode is None else repr(compatibility_mode)
        super().__init__(f"OceanBase profile requires a MySQL-compatible tenant; found {description}")


class OceanBaseConfig(BaseModel):
    """Validated component configuration for an OceanBase async engine."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    kind: Literal["oceanbase"] = "oceanbase"
    url: Annotated[SecretStr, Field(repr=False)]
    echo: bool = False
    pool_pre_ping: bool = True

    @field_validator("url")
    @classmethod
    def require_official_async_dialect(cls, value: SecretStr) -> SecretStr:
        """Reject sync, generic MySQL and malformed URLs without exposing credentials."""

        _validate_oceanbase_url(value.get_secret_value())
        return value


class OceanBaseProfile:
    """An initialized OceanBase relational profile with explicit engine ownership."""

    def __init__(self, *, database: AsyncDatabase, tables: tuple[Table, ...]) -> None:
        self.database = database
        self.tables = tables

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        config: OceanBaseConfig,
        *,
        tables: tuple[Table, ...],
    ) -> AsyncIterator[OceanBaseProfile]:
        """Create, initialize and exclusively own an official OceanBase engine."""

        _register_official_dialect()
        engine = create_async_engine(
            config.url.get_secret_value(),
            echo=config.echo,
            pool_pre_ping=config.pool_pre_ping,
        )
        database = AsyncDatabase.own(engine)
        async with _initialized_profile(cls(database=database, tables=tables)) as profile:
            yield profile

    @classmethod
    @asynccontextmanager
    async def attach(
        cls,
        engine: AsyncEngine,
        *,
        tables: tuple[Table, ...],
    ) -> AsyncIterator[OceanBaseProfile]:
        """Initialize a caller-owned official OceanBase engine without disposing it."""

        _validate_oceanbase_url(engine.url)
        database = AsyncDatabase.attach(engine)
        async with _initialized_profile(cls(database=database, tables=tables)) as profile:
            yield profile


@asynccontextmanager
async def _initialized_profile(profile: OceanBaseProfile) -> AsyncIterator[OceanBaseProfile]:
    try:
        async with profile.database.transaction() as connection:
            await _require_mysql_tenant(connection)
            await create_tables(connection, profile.tables)
        yield profile
    finally:
        await profile.database.close()


async def _require_mysql_tenant(connection: AsyncConnection) -> None:
    # OceanBase documents this read-only variable as the authoritative tenant
    # mode marker and SHOW VARIABLES as its supported query surface.
    result = await connection.exec_driver_sql("SHOW VARIABLES LIKE 'ob_compatibility_mode'")
    row = result.first()
    mode = None if row is None or len(row) < 2 else str(row[1]).upper()
    if mode != "MYSQL":
        raise UnsupportedOceanBaseTenantError(mode)


def _register_official_dialect() -> None:
    # pyobvector publishes the official AsyncOceanBaseDialect but currently
    # documents explicit SQLAlchemy registry setup instead of an entry point.
    dialect_registry.register(_DIALECT_REGISTRY_NAME, _DIALECT_MODULE, _DIALECT_CLASS)


def _validate_oceanbase_url(value: str | URL) -> URL:
    try:
        url = make_url(value)
    except (ArgumentError, TypeError, ValueError):
        raise ValueError("OceanBase profile URL is invalid") from None  # noqa: TRY003

    if url.drivername != _DIALECT_DRIVER:
        raise ValueError(f"OceanBase profile URL must use {_DIALECT_DRIVER}")  # noqa: TRY003
    if not url.username:
        raise ValueError("OceanBase profile URL must include a username")  # noqa: TRY003
    if not url.host:
        raise ValueError("OceanBase profile URL must include a host")  # noqa: TRY003
    if url.port is None:
        raise ValueError("OceanBase profile URL must include an explicit port")  # noqa: TRY003
    if not url.database:
        raise ValueError("OceanBase profile URL must include a database")  # noqa: TRY003
    if url.query.get("charset") != "utf8mb4":
        raise ValueError("OceanBase profile URL must set charset=utf8mb4")  # noqa: TRY003
    return url
