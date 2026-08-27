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

"""Durable Connector checkpoints and their runtime store adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import CONNECTOR_CHECKPOINTS_TABLE
from powercontext.errors import InvalidConnectorRunError
from powercontext.sources import ConnectorBinding


class _CheckpointPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: JsonValue | None


class StoredConnectorCheckpoint(BaseModel):
    """One decoded checkpoint bound to an exact Connector identity."""

    model_config = ConfigDict(frozen=True)

    binding: ConnectorBinding
    checkpoint: JsonValue | None


class ConnectorCheckpointRepository:
    """Persist opaque Connector checkpoints with value-based comparison."""

    async def load(
        self,
        connection: AsyncConnection,
        binding: ConnectorBinding,
        /,
        *,
        for_update: bool = False,
    ) -> StoredConnectorCheckpoint | None:
        statement = select(CONNECTOR_CHECKPOINTS_TABLE).where(
            CONNECTOR_CHECKPOINTS_TABLE.c.scope_id == binding.scope_id,
            CONNECTOR_CHECKPOINTS_TABLE.c.binding_id == binding.binding_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await connection.execute(statement)).mappings().one_or_none()
        if row is None:
            return None
        stored = _decode_row(row)
        if stored.binding != binding:
            raise InvalidConnectorRunError(
                "binding-conflict",
                f"checkpoint {binding.binding_id!r} belongs to a different Connector identity",
            )
        return stored

    async def save(
        self,
        connection: AsyncConnection,
        binding: ConnectorBinding,
        checkpoint: JsonValue | None,
        /,
        *,
        expected: JsonValue | None,
    ) -> StoredConnectorCheckpoint:
        existing = await self.load(connection, binding, for_update=True)
        actual = None if existing is None else existing.checkpoint
        if actual != expected:
            raise _checkpoint_conflict(binding)

        payload = _dump_checkpoint(binding, checkpoint)
        if existing is None:
            try:
                async with connection.begin_nested():
                    await connection.execute(
                        insert(CONNECTOR_CHECKPOINTS_TABLE).values(
                            scope_id=binding.scope_id,
                            binding_id=binding.binding_id,
                            connector_name=binding.connector_name,
                            connector_version=binding.connector_version,
                            checkpoint=payload,
                        )
                    )
            except IntegrityError:
                raise _checkpoint_conflict(binding) from None
        else:
            result = await connection.execute(
                update(CONNECTOR_CHECKPOINTS_TABLE)
                .where(
                    CONNECTOR_CHECKPOINTS_TABLE.c.scope_id == binding.scope_id,
                    CONNECTOR_CHECKPOINTS_TABLE.c.binding_id == binding.binding_id,
                    CONNECTOR_CHECKPOINTS_TABLE.c.checkpoint == _dump_checkpoint(binding, expected),
                )
                .values(checkpoint=payload)
            )
            if result.rowcount != 1:
                raise _checkpoint_conflict(binding)
        return StoredConnectorCheckpoint(binding=binding, checkpoint=checkpoint)


class RelationalConnectorCheckpointStore:
    """Adapt the Connector checkpoint protocol to an ``AsyncDatabase``."""

    def __init__(self, database: AsyncDatabase, repository: ConnectorCheckpointRepository, /) -> None:
        self._database = database
        self._repository = repository

    async def load(self, binding: ConnectorBinding, /) -> JsonValue | None:
        async with self._database.transaction() as connection:
            stored = await self._repository.load(connection, binding)
        return None if stored is None else stored.checkpoint

    async def save(
        self,
        binding: ConnectorBinding,
        checkpoint: JsonValue | None,
        /,
        *,
        expected: JsonValue | None,
    ) -> None:
        async with self._database.transaction() as connection:
            await self._repository.save(connection, binding, checkpoint, expected=expected)


def _dump_checkpoint(binding: ConnectorBinding, checkpoint: JsonValue | None) -> bytes:
    return dump_model(
        _CheckpointPayload(value=checkpoint),
        kind="connector-checkpoint",
        name=binding.binding_id,
    )


def _checkpoint_conflict(binding: ConnectorBinding) -> InvalidConnectorRunError:
    return InvalidConnectorRunError(
        "checkpoint-conflict",
        f"binding {binding.binding_id!r} changed during the run",
    )


def _decode_row(row: Mapping[Any, Any]) -> StoredConnectorCheckpoint:
    binding = ConnectorBinding(
        scope_id=str(row["scope_id"]),
        binding_id=str(row["binding_id"]),
        connector_name=str(row["connector_name"]),
        connector_version=str(row["connector_version"]),
    )
    payload = load_model(
        _CheckpointPayload,
        stored_bytes(row["checkpoint"], column="checkpoint"),
        kind="connector-checkpoint",
        name=binding.binding_id,
    )
    return StoredConnectorCheckpoint(binding=binding, checkpoint=payload.value)


__all__ = [
    "ConnectorCheckpointRepository",
    "RelationalConnectorCheckpointStore",
    "StoredConnectorCheckpoint",
]
