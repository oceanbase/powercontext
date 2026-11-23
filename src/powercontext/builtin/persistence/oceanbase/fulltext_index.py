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

"""Create derived FULLTEXT indexes without replaying their owning initializer."""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

_INDEX_DDL_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.6)
_INDEX_EXISTS_SQL = text(
    "SELECT COUNT(*) FROM information_schema.statistics "
    "WHERE table_schema = DATABASE() AND table_name = :table_name AND index_name = :index_name"
)


async def ensure_fulltext_index(connection: AsyncConnection, table_name: str, index_name: str) -> None:
    for attempt in range(len(_INDEX_DDL_RETRY_DELAYS) + 1):
        count = await connection.scalar(
            _INDEX_EXISTS_SQL,
            {"table_name": table_name, "index_name": index_name},
        )
        if int(count or 0):
            return
        try:
            await connection.exec_driver_sql(
                f"CREATE FULLTEXT INDEX {index_name} ON {table_name} (searchable_text) WITH PARSER SPACE"
            )
        except OperationalError as error:
            # OceanBase maps a NOWAIT DDL lock conflict to MySQL 1205.
            # Retry only this idempotent DDL, checking for an index another
            # initializer may have created. Never replay startup writes or
            # roll back/commit the caller's transaction here.
            if (
                error.orig is None
                or not error.orig.args
                or error.orig.args[0] != 1205
                or attempt == len(_INDEX_DDL_RETRY_DELAYS)
            ):
                raise
            await asyncio.sleep(_INDEX_DDL_RETRY_DELAYS[attempt])
        else:
            return
