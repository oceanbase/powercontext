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

"""Immutable per-revision evidence inputs and their neutral internal annotation."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

import rfc8785
from aiosqlite import Connection as SQLiteConnection
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.atomic_memory_evidence_schema import ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE
from powercontext.builtin.persistence.sources import SourceRepository, _require_identity
from powercontext.limits import MAX_SCOPE_ID_LENGTH
from powercontext.sources import SourceRef
from powercontext.sources.evidence import MemoryEvidenceDeclaration, SourceEvidence


class AtomicMemoryEvidenceAnnotation(BaseModel):
    """Exact revision facts, not a trust score or recursively inferred evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    artifact_ref: ArtifactRef
    sources: tuple[SourceEvidence, ...]
    format: Literal["powercontext.atomic-memory.evidence.v1"] = "powercontext.atomic-memory.evidence.v1"


class AtomicMemoryEvidenceRepository:
    """Join caller transactions; retained inputs are never deleted with the index.

    The annotation is derived from retained inputs at read time. There is no
    separate cache to confuse with accepted history or join into search.
    """

    async def save(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_ref: ArtifactRef,
        sources: Sequence[SourceEvidence],
    ) -> AtomicMemoryEvidenceAnnotation:
        artifact_ref = self._reference(scope_id, artifact_ref)
        payload = await self._payload(connection, scope_id, artifact_ref)
        if payload is not None:
            return self._decode(scope_id, artifact_ref, payload) or self._neutral(scope_id, artifact_ref, sources)
        accepted = []
        for source in sources:
            value = source.model_dump(mode="json")
            try:
                declaration = MemoryEvidenceDeclaration.model_validate(value.get("declaration"))
            except ValidationError:
                declaration = MemoryEvidenceDeclaration()
            accepted.append(SourceEvidence.model_validate({**value, "declaration": declaration}))
        annotation = AtomicMemoryEvidenceAnnotation(
            scope_id=scope_id, artifact_ref=artifact_ref, sources=tuple(accepted)
        )
        if connection.dialect.name == "sqlite":
            driver = (await connection.get_raw_connection()).driver_connection
            if isinstance(driver, SQLiteConnection) and not driver.in_transaction:
                # SQLite legacy transaction mode does not BEGIN on SELECT;
                # SAVEPOINT must not accidentally own and commit the write.
                await connection.exec_driver_sql("BEGIN")
        try:
            async with connection.begin_nested():
                await connection.execute(
                    insert(ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE).values(
                        scope_id=scope_id,
                        artifact_id=artifact_ref.artifact_id,
                        revision=artifact_ref.revision,
                        sources=rfc8785.dumps([source.model_dump(mode="json") for source in annotation.sources]),
                    )
                )
        except IntegrityError:
            payload = await self._payload(connection, scope_id, artifact_ref)
            if payload is None:
                raise
            return self._decode(scope_id, artifact_ref, payload) or self._neutral(scope_id, artifact_ref, sources)
        return annotation

    async def read(
        self, connection: AsyncConnection, scope_id: str, artifact_ref: ArtifactRef
    ) -> AtomicMemoryEvidenceAnnotation | None:
        artifact_ref = self._reference(scope_id, artifact_ref)
        payload = await self._payload(connection, scope_id, artifact_ref)
        return None if payload is None else self._decode(scope_id, artifact_ref, payload)

    async def verify(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_ref: ArtifactRef,
        source_refs: Sequence[SourceRef],
        *,
        sources: SourceRepository | None = None,
    ) -> AtomicMemoryEvidenceAnnotation:
        """Derive exact facts from retained inputs, never today's declarations.

        Missing or damaged declaration records are neutral. Missing Sources,
        corrupt authoritative payloads and unequal exact materials remain errors.
        No accepted snapshot is saved or repaired by this operation.
        """

        from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexError
        from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY

        artifact_ref = self._reference(scope_id, artifact_ref)
        repository = SourceRepository(BUILTIN_SOURCE_REGISTRY) if sources is None else sources
        materials = await repository.historical_evidence(connection, scope_id, source_refs)
        annotation = await self.read(connection, scope_id, artifact_ref)
        if annotation is not None:
            by_ref = {(item.source_ref.source_type, item.source_ref.source_id): item for item in materials}
            for accepted in annotation.sources:
                material = by_ref.get((accepted.source_ref.source_type, accepted.source_ref.source_id))
                if material is not None and accepted.material_digest != material.material_digest:
                    raise AtomicMemoryIndexError(
                        "evidence-material", "Historical evidence does not match the exact Source material"
                    )
        if annotation is None or tuple(item.source_ref for item in annotation.sources) != tuple(
            item.source_ref for item in materials
        ):
            return self._neutral(scope_id, artifact_ref, materials)
        verified = tuple(
            accepted
            if (
                accepted.materialization == material.materialization
                and accepted.definition_name == material.definition_name
                and accepted.definition_version == material.definition_version
                and accepted.manifest_fingerprint == material.manifest_fingerprint
                and accepted.declaration == material.declaration
            )
            else accepted.model_copy(update={"declaration": MemoryEvidenceDeclaration()})
            for accepted, material in zip(annotation.sources, materials, strict=True)
        )
        return annotation.model_copy(update={"sources": verified})

    @staticmethod
    def _reference(scope_id: str, artifact_ref: ArtifactRef) -> ArtifactRef:
        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        reference = ArtifactRef.model_validate(artifact_ref.model_dump())
        if reference.family != "atomic-memory":
            from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError

            raise InvalidRepositoryArgumentError("artifact_ref", "must reference an Atomic Memory revision")
        return reference

    @staticmethod
    async def _payload(connection: AsyncConnection, scope_id: str, artifact_ref: ArtifactRef) -> object | None:
        table = ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE
        return await connection.scalar(
            select(table.c.sources).where(
                table.c.scope_id == scope_id,
                table.c.artifact_id == artifact_ref.artifact_id,
                table.c.revision == artifact_ref.revision,
            )
        )

    @staticmethod
    def _decode(scope_id: str, artifact_ref: ArtifactRef, payload: object) -> AtomicMemoryEvidenceAnnotation | None:
        try:
            if not isinstance(payload, (bytes, bytearray, memoryview)):
                return None
            values = json.loads(bytes(payload))
            if not isinstance(values, list):
                return None
            sources = []
            for value in values:
                if not isinstance(value, dict):
                    return None
                try:
                    declaration = MemoryEvidenceDeclaration.model_validate(value.get("declaration"))
                except ValidationError:
                    declaration = MemoryEvidenceDeclaration()
                sources.append(SourceEvidence.model_validate({**value, "declaration": declaration}))
            return AtomicMemoryEvidenceAnnotation(scope_id=scope_id, artifact_ref=artifact_ref, sources=tuple(sources))
        except (ValueError, TypeError):
            # An unreadable declaration snapshot conveys no historical claim.
            # This does not catch SQL errors or actual Source resolution errors.
            return None

    @staticmethod
    def _neutral(
        scope_id: str, artifact_ref: ArtifactRef, sources: Sequence[SourceEvidence]
    ) -> AtomicMemoryEvidenceAnnotation:
        # Preserve a damaged row exactly, without replacing it with today's
        # stronger configuration. A caller can only receive neutral facts.
        return AtomicMemoryEvidenceAnnotation(
            scope_id=scope_id,
            artifact_ref=artifact_ref,
            sources=tuple(source.model_copy(update={"declaration": MemoryEvidenceDeclaration()}) for source in sources),
        )
