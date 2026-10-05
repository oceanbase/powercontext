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

"""Exercise the actual MySQL driver codec without needing a database server."""

import asyncio

import aiomysql
import pytest
from aiomysql.cursors import Cursor


@pytest.mark.parametrize("payload", [b"", b'{"content":"Source"}', bytes(range(256)), "中文'\\\0".encode()])
def test_async_mysql_driver_encodes_binary_parameters(payload: bytes) -> None:
    async def scenario() -> None:
        connection = aiomysql.Connection(charset="utf8mb4")
        # A disconnected connection has no handshake status. Zero selects the
        # normal escaping mode used by the supported profiles.
        connection.server_status = 0
        try:
            cursor = Cursor(connection)
            query = cursor.mogrify("INSERT INTO payloads (value) VALUES (%s)", (payload,))
            assert isinstance(query, str)
            assert "%s" not in query
            assert query.encode("utf8", "surrogateescape")
        finally:
            connection.close()

    asyncio.run(scenario())
