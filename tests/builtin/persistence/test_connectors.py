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
from types import SimpleNamespace
from typing import cast

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.connectors import ConnectorCheckpointRepository
from powercontext.sources import ConnectorBinding


def test_connector_checkpoint_initial_creation_avoids_savepoints_on_mysql_compatible_connections() -> None:
    """OceanBase can discard this write-path SAVEPOINT before SQLAlchemy releases it."""

    async def scenario() -> None:
        class EmptyConnectorCheckpointRepository(ConnectorCheckpointRepository):
            async def _find_row(
                self,
                connection: AsyncConnection,
                binding: ConnectorBinding,
                *,
                for_update: bool,
            ) -> None:
                del connection, binding, for_update

        class MySQLCompatibleConnection:
            dialect = SimpleNamespace(name="mysql")

            def __init__(self) -> None:
                self.executions = 0

            async def execute(self, _statement: object) -> None:
                self.executions += 1

            def begin_nested(self) -> None:
                raise AssertionError

        binding = ConnectorBinding(
            scope_id="scope-a",
            binding_id="connector-a",
            connector_name="test-connector",
            connector_version="1",
        )
        connection = MySQLCompatibleConnection()
        repository = EmptyConnectorCheckpointRepository()

        await repository.save(
            cast(AsyncConnection, connection),
            binding,
            {"cursor": 1},
            expected=None,
        )

        assert connection.executions == 1

    asyncio.run(scenario())
