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

"""Schema upgrades for authoritative Memory entry history."""

from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory.errors import MemoryBackendConfigurationError
from powercontext.builtin.persistence.tables import (
    MEMORY_ENTRY_VERSION_SCOPE_INDEX_NAME,
    MEMORY_ENTRY_VERSIONS_TABLE,
)

_SQLITE_INDEX_EXISTS = text(
    "SELECT COUNT(*) FROM pragma_index_list('pc_memory_entry_versions') WHERE name = :index_name"
).bindparams(index_name=MEMORY_ENTRY_VERSION_SCOPE_INDEX_NAME)
_MYSQL_INDEX_EXISTS = text(
    "SELECT COUNT(*) FROM information_schema.statistics "
    "WHERE table_schema = DATABASE() "
    "AND table_name = 'pc_memory_entry_versions' "
    "AND index_name = :index_name"
).bindparams(index_name=MEMORY_ENTRY_VERSION_SCOPE_INDEX_NAME)
_CREATE_SCOPE_VERSION_INDEX = (
    f"CREATE UNIQUE INDEX {MEMORY_ENTRY_VERSION_SCOPE_INDEX_NAME} "
    "ON pc_memory_entry_versions (scope_id, entry_version_id)"
)


async def ensure_memory_entry_version_scope_identity(connection: AsyncConnection, /) -> None:
    """Make entry-version identities scope-global on new and existing databases."""

    if await _scope_version_index_exists(connection):
        return
    duplicate = (
        await connection.execute(
            select(
                MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id,
                MEMORY_ENTRY_VERSIONS_TABLE.c.entry_version_id,
            )
            .group_by(
                MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id,
                MEMORY_ENTRY_VERSIONS_TABLE.c.entry_version_id,
            )
            .having(func.count() > 1)
            .limit(1)
        )
    ).first()
    if duplicate is not None:
        raise _duplicate_identity_error()
    try:
        await connection.exec_driver_sql(_CREATE_SCOPE_VERSION_INDEX)
    except DBAPIError as error:
        if await _scope_version_index_exists(connection):
            return
        if isinstance(error, IntegrityError):
            raise _duplicate_identity_error() from error
        raise


async def _scope_version_index_exists(connection: AsyncConnection) -> bool:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        statement = _SQLITE_INDEX_EXISTS
    elif dialect == "mysql":
        statement = _MYSQL_INDEX_EXISTS
    else:
        raise ValueError(f"unsupported Memory schema migration dialect: {dialect}")  # noqa: TRY003
    return int(await connection.scalar(statement) or 0) > 0


def _duplicate_identity_error() -> MemoryBackendConfigurationError:
    return MemoryBackendConfigurationError(
        "pc_memory_entry_versions contains duplicate scope-global entry_version_id values"
    )


__all__ = ["ensure_memory_entry_version_scope_identity"]
