# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.database import database_now


def test_mysql_coordination_time_is_queried_in_utc() -> None:
    statements: list[str] = []
    expected = datetime(2026, 9, 4, 12, 0, tzinfo=UTC).replace(tzinfo=None)

    class Result:
        @staticmethod
        def scalar_one() -> datetime:
            return expected

    class Connection:
        dialect = SimpleNamespace(name="mysql")

        @staticmethod
        async def exec_driver_sql(statement: str) -> Result:
            statements.append(statement)
            return Result()

    value = asyncio.run(database_now(cast(AsyncConnection, Connection())))

    assert value == expected
    assert statements == ["SELECT UTC_TIMESTAMP(6)"]
