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

"""Database selection and non-destructive MySQL-compatible database creation."""

from __future__ import annotations

import asyncio
import unicodedata
from collections.abc import Awaitable, Callable

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from powercontext.builtin.persistence.errors import PersistenceError

DEFAULT_DATABASE = "powercontext"
_SYSTEM_DATABASES = {"information_schema", "mysql", "performance_schema", "sys", "oceanbase"}
_PERMISSION_ERRORS = {1044, 1142, 1227}


class DatabaseAccessError(PersistenceError):
    """The server denies access without proving whether the database exists."""

    def __init__(self, name: str, statement: str) -> None:
        self.database_name = name
        super().__init__(
            f"Access to database {name!r} was denied. Ask an administrator to verify it in the same instance/tenant. "
            f"If it is missing, execute {statement}; then grant the application account access and "
            "table-initialization privileges and restart. No fallback database was selected."
        )


class DatabaseCreationError(PersistenceError):
    """A missing database must be provisioned by an administrator."""

    def __init__(self, name: str, statement: str) -> None:
        self.database_name = name
        super().__init__(
            f"Database {name!r} does not exist and this account cannot create it. "
            f"Ask an administrator to execute {statement}; in the same instance/tenant, "
            "grant the application account access and table-initialization privileges, then restart. "
            "No fallback database was selected."
        )


class MissingDatabaseError(PersistenceError):
    """An inspection path requires an already provisioned database and never creates one."""

    def __init__(self, name: str) -> None:
        self.database_name = name
        super().__init__(
            f"Database {name!r} does not exist. This command inspects the configured database without creating it; "
            "provision it first, or start the Server once to create the default database, then repeat the command."
        )


def validate_database_name(name: str) -> str:
    """Accept database names supported throughout initialization and reflection."""
    if "`" in name:
        raise ValueError(  # noqa: TRY003
            "Database name must not contain backticks (`). Choose a name without backticks, "
            "such as powercontext or team_context"
        )
    if (
        not name
        or len(name) > 64
        or name != name.strip()
        or any(unicodedata.category(char) in {"Cc", "Cs"} or ord(char) > 0xFFFF or char in "/\\." for char in name)
        or name.casefold() in _SYSTEM_DATABASES
    ):
        raise ValueError(  # noqa: TRY003
            "Database name must contain 1-64 BMP characters, no leading/trailing whitespace, "
            "control characters, slash, backslash or dot, and must not name a system database"
        )
    return name


def mysql_error_code(error: DBAPIError) -> int | None:
    """Read the driver's numeric code without parsing or exposing its message."""
    arguments = getattr(error.orig, "args", ())
    return arguments[0] if arguments and isinstance(arguments[0], int) else None


async def database_is_available(engine: AsyncEngine, *, name: str | None = None) -> bool:
    """Only an explicit unknown-database error permits database creation."""
    try:
        async with engine.connect() as connection:
            if name is not None:
                actual = (await connection.exec_driver_sql("SELECT DATABASE()")).scalar_one()
                if actual != name:
                    case_mode = (await connection.exec_driver_sql("SELECT @@lower_case_table_names")).scalar_one()
                    if case_mode not in {1, 2} or not isinstance(actual, str) or actual.lower() != name.lower():
                        raise PersistenceError(  # noqa: TRY003
                            f"The connection did not select configured database {name!r}; check URL connection overrides"
                        )
            return True
    except DBAPIError as error:
        if mysql_error_code(error) != 1049:
            raise
        return False


async def ensure_database(
    engine: AsyncEngine,
    instance_engine: Callable[[], AsyncEngine],
    *,
    name: str,
    require_backend: Callable[[AsyncConnection], Awaitable[None]] | None = None,
) -> None:
    """Reuse usable databases without issuing CREATE or listing other databases."""
    statement = creation_statement(engine, name)
    try:
        if await database_is_available(engine, name=name):
            return
    except DBAPIError as error:
        if mysql_error_code(error) == 1044:
            raise DatabaseAccessError(name, statement) from None
        raise

    admin = instance_engine()
    try:
        try:
            async with admin.begin() as connection:
                if require_backend is not None:
                    await require_backend(connection)
                await connection.exec_driver_sql(statement)
        except DBAPIError as error:
            await _recover_creation_race(engine, name, statement, error)
    finally:
        await dispose_engine(admin)

    if not await database_is_available(engine, name=name):
        raise PersistenceError(f"Database {name!r} is still unavailable after creation; retry startup")  # noqa: TRY003


async def require_existing_database(engine: AsyncEngine, *, name: str) -> None:
    """Connect to a provisioned database without creating it or listing other databases."""
    try:
        available = await database_is_available(engine, name=name)
    except DBAPIError as error:
        if mysql_error_code(error) != 1044:
            raise
        raise DatabaseAccessError(name, creation_statement(engine, name)) from None
    if not available:
        raise MissingDatabaseError(name)


def creation_statement(engine: AsyncEngine, name: str) -> str:
    """Render the exact statement an administrator runs to provision this database."""
    quoted = engine.dialect.identifier_preparer.quote_identifier(validate_database_name(name))
    return f"CREATE DATABASE IF NOT EXISTS {quoted} CHARACTER SET utf8mb4"


async def _recover_creation_race(engine: AsyncEngine, name: str, statement: str, error: DBAPIError) -> None:
    """A concurrent creator can make a failed CREATE harmless, without granting CREATE to this account."""
    code = mysql_error_code(error)
    if code not in _PERMISSION_ERRORS and code != 1007:
        raise error
    try:
        available = await database_is_available(engine, name=name)
    except DBAPIError as retry_error:
        if mysql_error_code(retry_error) != 1044:
            raise
        available = False
    if available:
        return
    if code in _PERMISSION_ERRORS:
        raise DatabaseCreationError(name, statement) from None
    raise error


async def dispose_engine(engine: AsyncEngine) -> None:
    """Finish connection disposal before propagating cancellation."""
    task = asyncio.create_task(engine.dispose())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise
