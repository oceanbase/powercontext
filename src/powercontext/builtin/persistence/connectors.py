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

"""Durable Connector checkpoints with optimistic comparison."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.tables import CONNECTOR_CHECKPOINTS_TABLE
from powercontext.errors import InvalidConnectorRunError
from powercontext.sources import ConnectorBinding


class _CheckpointPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: JsonValue | None


class ConnectorCheckpointRepository:
    """Persist opaque Connector checkpoints with value-based comparison."""

    async def load(
        self,
        connection: AsyncConnection,
        binding: ConnectorBinding,
        /,
        *,
        for_update: bool = False,
    ) -> JsonValue | None:
        row = await self._find_row(connection, binding, for_update=for_update)
        if row is None:
            return None
        stored_binding, checkpoint = _decode_row(row)
        self._validate_binding(binding, stored_binding)
        return checkpoint

    async def _find_row(
        self,
        connection: AsyncConnection,
        binding: ConnectorBinding,
        *,
        for_update: bool,
    ) -> Mapping[Any, Any] | None:
        statement = select(CONNECTOR_CHECKPOINTS_TABLE).where(
            CONNECTOR_CHECKPOINTS_TABLE.c.scope_id == binding.scope_id,
            CONNECTOR_CHECKPOINTS_TABLE.c.binding_id == binding.binding_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await connection.execute(statement)).mappings().one_or_none()

    @staticmethod
    def _validate_binding(binding: ConnectorBinding, stored_binding: ConnectorBinding) -> None:
        if stored_binding != binding:
            raise InvalidConnectorRunError(
                "binding-conflict",
                f"checkpoint {binding.binding_id!r} belongs to a different Connector identity",
            )

    async def save(
        self,
        connection: AsyncConnection,
        binding: ConnectorBinding,
        checkpoint: JsonValue | None,
        /,
        *,
        expected: JsonValue | None,
    ) -> None:
        existing_row = await self._find_row(connection, binding, for_update=True)
        if existing_row is None:
            actual = None
        else:
            stored_binding, actual = _decode_row(existing_row)
            self._validate_binding(binding, stored_binding)
        if actual != expected:
            raise _checkpoint_conflict(binding)

        payload = _dump_checkpoint(binding, checkpoint)
        if existing_row is None:
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


def _decode_row(row: Mapping[Any, Any]) -> tuple[ConnectorBinding, JsonValue | None]:
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
    return binding, payload.value


__all__ = [
    "ConnectorCheckpointRepository",
]
