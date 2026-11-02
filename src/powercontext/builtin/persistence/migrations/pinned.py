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

"""Run domain database coroutines on the executor's pinned synchronous connection.

Only immediate database operations are supported. An attempted external await,
transaction boundary, or connection acquisition fails instead of escaping the
maintenance connection and its lock.
"""

from collections.abc import Coroutine
from typing import Any, cast

from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncConnection


class _PinnedConnection:
    def __init__(self, connection: Connection):
        self.connection = connection
        self.dialect = connection.dialect

    async def execute(self, *args, **kwargs):
        return self.connection.execute(*args, **kwargs)

    async def exec_driver_sql(self, *args, **kwargs):
        return self.connection.exec_driver_sql(*args, **kwargs)

    async def scalar(self, *args, **kwargs):
        return self.connection.scalar(*args, **kwargs)

    async def scalars(self, *args, **kwargs):
        return self.connection.scalars(*args, **kwargs)

    async def run_sync(self, function, *args, **kwargs):
        return function(self.connection, *args, **kwargs)


def pinned(connection: Connection) -> AsyncConnection:
    return cast(AsyncConnection, _PinnedConnection(connection))


def finish(coroutine: Coroutine[Any, Any, Any]) -> Any:
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    else:
        coroutine.close()
        raise RuntimeError("A migration domain attempted asynchronous work outside the pinned database connection")  # noqa: TRY003
