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

"""Durable declarations and JSON storage for user-defined Artifact families."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateColumn
from typing_extensions import override

from powercontext.artifacts import Artifact
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_BINDING_STATES_TABLE


class StoredArtifactContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    content: dict[str, JsonValue]


def custom_artifact_repository(
    family: str, sources: SourceRepository | None = None, *, prepared=None
) -> ArtifactRepository:
    artifact_type = type("CustomArtifact", (Artifact[StoredArtifactContent],), {"family": family})
    return _CustomArtifactRepository((artifact_type,), sources=sources, prepared=prepared)


class _CustomArtifactRepository(ArtifactRepository):
    def __init__(self, artifact_types, *, sources=None, prepared=None):
        super().__init__(artifact_types, sources=sources)
        self.prepared = prepared or {}

    @override
    async def create(self, connection, scope_id, artifact_id, draft, /, **kwargs):
        from powercontext.builtin.persistence.artifact_indexes import ArtifactProjectionPublisher

        publisher = ArtifactProjectionPublisher(None)
        prepared = self.prepared.get(artifact_id)
        await publisher.validate_prepared(
            connection, scope_id, draft.family, draft.content.schema_version, draft.content.content, prepared
        )
        result = await super().create(connection, scope_id, artifact_id, draft, **kwargs)
        await publisher.publish(connection, scope_id, result, prepared)
        return result

    @override
    async def lock_heads(self, connection, scope_id, refs, /):
        from powercontext.builtin.persistence.artifact_indexes import lock_state

        for family in sorted({ref.family for ref in refs}):
            await lock_state(connection, scope_id, family)
        return await super().lock_heads(connection, scope_id, refs)

    @override
    async def _revise(self, connection, scope_id, artifact, draft, **kwargs):
        from powercontext.builtin.persistence.artifact_indexes import ArtifactProjectionPublisher

        publisher = ArtifactProjectionPublisher(None)
        prepared = self.prepared.get(artifact.artifact_id)
        await publisher.validate_prepared(
            connection, scope_id, draft.family, draft.content.schema_version, draft.content.content, prepared
        )
        result = await super()._revise(connection, scope_id, artifact, draft, **kwargs)
        await publisher.publish(connection, scope_id, result, prepared)
        return result


async def ensure_artifact_plugin_schema(connection: AsyncConnection) -> None:
    """Add registration fields to existing databases without replacing processing state."""

    existing = await connection.run_sync(
        lambda sync: {item["name"] for item in inspect(sync).get_columns(ARTIFACT_PROCESSING_BINDING_STATES_TABLE.name)}
    )
    for column in ARTIFACT_PROCESSING_BINDING_STATES_TABLE.columns:
        if column.name not in existing:
            definition = str(CreateColumn(column).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(
                f"ALTER TABLE pc_artifact_processing_binding_states ADD COLUMN {definition}"
            )
