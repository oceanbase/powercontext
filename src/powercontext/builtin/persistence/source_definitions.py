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

"""Immutable historical manifests owned by the Source registration boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.errors import (
    IdentityMismatchError,
    InvalidStoredColumnError,
    InvalidStoredPayloadError,
    RepositoryNotFoundError,
    StoredPayloadConflictError,
)
from powercontext.builtin.persistence.tables import SOURCE_DEFINITION_MANIFESTS_TABLE
from powercontext.sources import SourceDefinition, SourceDefinitionManifest, manifest_for_definition


class SourceDefinitionManifestRepository:
    """Retain native and worker manifests without changing their identities."""

    async def register(
        self,
        connection: AsyncConnection,
        manifest: SourceDefinitionManifest,
        /,
    ) -> SourceDefinitionManifest:
        payload = dump_model(manifest, kind="source-definition-manifest", name=manifest.name)
        existing = await self.find(connection, manifest.name, manifest.version)
        if existing is not None:
            if existing != manifest:
                raise StoredPayloadConflictError("source-definition-manifest", (manifest.name, manifest.version))
            return existing
        try:
            await connection.execute(
                insert(SOURCE_DEFINITION_MANIFESTS_TABLE).values(
                    definition_name=manifest.name,
                    definition_version=manifest.version,
                    fingerprint=manifest.fingerprint,
                    manifest=payload,
                )
            )
        except IntegrityError:
            existing = await self.find(connection, manifest.name, manifest.version)
            if existing is None:
                raise
            if existing != manifest:
                raise StoredPayloadConflictError(
                    "source-definition-manifest",
                    (manifest.name, manifest.version),
                ) from None
            return existing
        return manifest

    async def register_native(
        self,
        connection: AsyncConnection,
        definition: SourceDefinition[Any, Any, Any],
        /,
    ) -> SourceDefinitionManifest | None:
        """Retain the existing manifest representation for trusted native material.

        This internal registration cannot replace an older manifest. A missing,
        invalid or conflicting ancillary manifest leaves the Source declaration
        neutral; database failures still abort the caller's transaction. External
        registration keeps its existing active-Definition ownership guard.
        """

        try:
            manifest = manifest_for_definition(definition)
        except Exception:  # Native schema providers may not expose a valid manifest.
            return None
        try:
            return await self.register(connection, manifest)
        except (IdentityMismatchError, InvalidStoredColumnError, InvalidStoredPayloadError, StoredPayloadConflictError):
            return None

    async def get(
        self,
        connection: AsyncConnection,
        name: str,
        version: str,
        /,
    ) -> SourceDefinitionManifest:
        stored = await self.find(connection, name, version)
        if stored is None:
            raise RepositoryNotFoundError("source-definition-manifest", (name, version))
        return stored

    async def find(
        self,
        connection: AsyncConnection,
        name: str,
        version: str,
        /,
    ) -> SourceDefinitionManifest | None:
        row = (
            (
                await connection.execute(
                    select(SOURCE_DEFINITION_MANIFESTS_TABLE).where(
                        SOURCE_DEFINITION_MANIFESTS_TABLE.c.definition_name == name,
                        SOURCE_DEFINITION_MANIFESTS_TABLE.c.definition_version == version,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _decode_row(row)


def _decode_row(row: Mapping[Any, Any]) -> SourceDefinitionManifest:
    name = str(row["definition_name"])
    version = str(row["definition_version"])
    fingerprint = str(row["fingerprint"])
    manifest = load_model(
        SourceDefinitionManifest,
        stored_bytes(row["manifest"], column="manifest"),
        kind="source-definition-manifest",
        name=name,
    )
    indexed = (name, version, fingerprint)
    decoded = (manifest.name, manifest.version, manifest.fingerprint)
    if indexed != decoded:
        raise IdentityMismatchError("source-definition-manifest", indexed, decoded)
    return manifest


__all__ = ["SourceDefinitionManifestRepository"]
