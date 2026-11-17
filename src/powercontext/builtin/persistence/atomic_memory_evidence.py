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
import logging
from collections.abc import Sequence
from typing import Literal, cast

import rfc8785
from aiosqlite import Connection as SQLiteConnection
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import insert, select, tuple_
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.atomic_memory_evidence_schema import ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE
from powercontext.builtin.persistence.atomic_memory_index_schema import ATOMIC_MEMORY_EVIDENCE_PROJECTION_TABLE
from powercontext.builtin.persistence.sources import SourceRepository, _require_identity
from powercontext.errors import AtomicMemoryEvidenceError
from powercontext.limits import MAX_SCOPE_ID_LENGTH
from powercontext.sources import SourceRef
from powercontext.sources.evidence import (
    MemoryEvidenceDeclaration,
    SourceEvidence,
    log_evidence_neutralized,
)

_EVIDENCE_BATCH_SIZE = 200
_EVIDENCE_WRITE_BYTES = 1024 * 1024


class AtomicMemoryEvidenceAnnotation(BaseModel):
    """Exact revision facts, not a trust score or recursively inferred evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    artifact_ref: ArtifactRef
    sources: tuple[SourceEvidence, ...]
    format: Literal["powercontext.atomic-memory.evidence.v1"] = "powercontext.atomic-memory.evidence.v1"


class AtomicMemoryEvidenceRepository:
    """Join caller transactions; retained inputs are never deleted with the index.

    Accepted history is immutable. The index-owned projection can be discarded
    and replaced from verified history without joining it into search.
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
        annotation = self._annotation(scope_id, artifact_ref, sources)
        await self._ensure_transaction(connection)
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

    async def capture_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        revisions: Sequence[tuple[ArtifactRef, Sequence[SourceRef]]],
        *,
        sources: SourceRepository,
    ) -> None:
        """Capture committed revisions in bounded batches inside the caller's transaction."""

        unique = {}
        for reference, refs in revisions:
            reference = self._reference(scope_id, reference)
            unique.setdefault((reference.artifact_id, reference.revision), (reference, refs))
        batch = []
        source_ids = set()
        for reference, refs in unique.values():
            identifiers = {(ref.source_type, ref.source_id) for ref in refs}
            if batch and (len(batch) == _EVIDENCE_BATCH_SIZE or len(source_ids | identifiers) > _EVIDENCE_BATCH_SIZE):
                await self._capture_batch(connection, scope_id, batch, sources)
                batch = []
                source_ids.clear()
            batch.append((reference, refs))
            source_ids.update(identifiers)
        if batch:
            await self._capture_batch(connection, scope_id, batch, sources)

    @staticmethod
    async def _payloads(connection, scope_id, references, *, current=False):
        table = ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE
        statement = select(table.c.artifact_id, table.c.revision, table.c.sources).where(
            table.c.scope_id == scope_id,
            tuple_(table.c.artifact_id, table.c.revision).in_(
                tuple((ref.artifact_id, ref.revision) for ref in references)
            ),
        )
        if current:
            statement = statement.with_for_update()
        rows = (await connection.execute(statement)).mappings()
        return {(row["artifact_id"], row["revision"]): row["sources"] for row in rows}

    async def _capture_batch(self, connection, scope_id, revisions, sources) -> None:
        table = ATOMIC_MEMORY_EVIDENCE_HISTORY_TABLE
        payloads = await self._payloads(connection, scope_id, tuple(ref for ref, _ in revisions))
        pending = tuple(
            (ref, refs)
            for ref, refs in revisions
            if (payload := payloads.get((ref.artifact_id, ref.revision))) is None
            or self._decode(scope_id, ref, payload) is None
        )
        if not pending:
            return
        materials = await sources.evidence(connection, scope_id, tuple(ref for _, refs in pending for ref in refs))
        by_source = {(item.source_ref.source_type, item.source_ref.source_id): item for item in materials}
        accepted = {}
        annotations = {}
        inserts = []
        for ref, refs in pending:
            key = (ref.artifact_id, ref.revision)
            identities = dict.fromkeys((source.source_type, source.source_id) for source in refs)
            # Historical evidence deliberately excludes server-owned Content witnesses.
            accepted[key] = tuple(by_source[identity] for identity in identities if identity in by_source)
            payload = payloads.get(key)
            if payload is not None:
                annotation = self._decode(scope_id, ref, payload) or self._neutral(scope_id, ref, accepted[key])
            else:
                annotation = self._annotation(scope_id, ref, accepted[key])
                inserts.append({
                    "scope_id": scope_id,
                    "artifact_id": ref.artifact_id,
                    "revision": ref.revision,
                    "sources": rfc8785.dumps([source.model_dump(mode="json") for source in annotation.sources]),
                })
            annotations[key] = annotation
        if inserts:
            if connection.dialect.name == "sqlite":
                statement = sqlite_insert(table).on_conflict_do_nothing(
                    index_elements=("scope_id", "artifact_id", "revision")
                )
            else:
                statement = mysql_insert(table)
                statement = statement.on_duplicate_key_update(artifact_id=statement.inserted.artifact_id)
            await self._execute_rows(connection, statement, inserts)
            new_refs = tuple(ref for ref, _ in pending if payloads.get((ref.artifact_id, ref.revision)) is None)
            # Locking reads retain first-writer history even under repeatable-read isolation.
            retained = await self._payloads(connection, scope_id, new_refs, current=True)
            for ref in new_refs:
                key = (ref.artifact_id, ref.revision)
                payload = retained.get(key)
                if payload is None:
                    raise AtomicMemoryEvidenceError
                annotations[key] = self._decode(scope_id, ref, payload) or self._neutral(scope_id, ref, accepted[key])
        await self._replace_projections(connection, tuple(annotations.values()))

    async def read(
        self, connection: AsyncConnection, scope_id: str, artifact_ref: ArtifactRef
    ) -> AtomicMemoryEvidenceAnnotation | None:
        artifact_ref = self._reference(scope_id, artifact_ref)
        payload = await self._payload(connection, scope_id, artifact_ref)
        return None if payload is None else self._decode(scope_id, artifact_ref, payload)

    async def read_projection(
        self, connection: AsyncConnection, scope_id: str, artifact_ref: ArtifactRef
    ) -> AtomicMemoryEvidenceAnnotation | None:
        artifact_ref = self._reference(scope_id, artifact_ref)
        table = ATOMIC_MEMORY_EVIDENCE_PROJECTION_TABLE
        payload = await connection.scalar(
            select(table.c.sources).where(
                table.c.scope_id == scope_id,
                table.c.artifact_id == artifact_ref.artifact_id,
                table.c.revision == artifact_ref.revision,
            )
        )
        return None if payload is None else self._decode(scope_id, artifact_ref, payload)

    async def replace_projection(self, connection: AsyncConnection, annotation: AtomicMemoryEvidenceAnnotation) -> None:
        await self._replace_projections(connection, (annotation,))

    async def _replace_projections(
        self, connection: AsyncConnection, annotations: Sequence[AtomicMemoryEvidenceAnnotation]
    ) -> None:
        if not annotations:
            return
        table = ATOMIC_MEMORY_EVIDENCE_PROJECTION_TABLE
        values = []
        for annotation in annotations:
            reference = self._reference(annotation.scope_id, annotation.artifact_ref)
            values.append({
                "scope_id": annotation.scope_id,
                "artifact_id": reference.artifact_id,
                "revision": reference.revision,
                "sources": rfc8785.dumps([source.model_dump(mode="json") for source in annotation.sources]),
            })
        if connection.dialect.name == "sqlite":
            statement = sqlite_insert(table)
            statement = statement.on_conflict_do_update(
                index_elements=("scope_id", "artifact_id", "revision"), set_={"sources": statement.excluded.sources}
            )
        else:
            statement = mysql_insert(table)
            statement = statement.on_duplicate_key_update(sources=statement.inserted.sources)
        await self._execute_rows(connection, statement, values)

    @staticmethod
    async def _execute_rows(connection, statement, values) -> None:
        batch = []
        size = 0
        for value in values:
            payload_size = len(value["sources"])
            if batch and size + payload_size > _EVIDENCE_WRITE_BYTES:
                await connection.execute(statement, batch)
                batch = []
                size = 0
            batch.append(value)
            size += payload_size
        if batch:
            await connection.execute(statement, batch)

    @staticmethod
    def _annotation(
        scope_id: str, artifact_ref: ArtifactRef, sources: Sequence[SourceEvidence]
    ) -> AtomicMemoryEvidenceAnnotation:
        accepted = []
        for source in sources:
            value = source.model_dump(mode="json")
            try:
                declaration = MemoryEvidenceDeclaration.model_validate(value.get("declaration"))
            except ValidationError:
                log_evidence_neutralized("invalid_declaration", unit="declaration", level=logging.WARNING)
                declaration = MemoryEvidenceDeclaration()
            accepted.append(SourceEvidence.model_validate({**value, "declaration": declaration}))
        return AtomicMemoryEvidenceAnnotation(scope_id=scope_id, artifact_ref=artifact_ref, sources=tuple(accepted))

    @staticmethod
    async def _ensure_transaction(connection: AsyncConnection) -> None:
        if connection.dialect.name == "sqlite":
            driver = (await connection.get_raw_connection()).driver_connection
            if isinstance(driver, SQLiteConnection) and not driver.in_transaction:
                # A SAVEPOINT must not own and commit a legacy-mode SQLite write.
                await connection.exec_driver_sql("BEGIN")

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
                    raise AtomicMemoryEvidenceError
        if annotation is None or tuple(item.source_ref for item in annotation.sources) != tuple(
            item.source_ref for item in materials
        ):
            log_evidence_neutralized("evidence_mismatch", unit="revision")
            return self._neutral(scope_id, artifact_ref, materials)
        verified = []
        for accepted, material in zip(annotation.sources, materials, strict=True):
            if (
                accepted.materialization == material.materialization
                and accepted.definition_name == material.definition_name
                and accepted.definition_version == material.definition_version
                and accepted.manifest_fingerprint == material.manifest_fingerprint
                and accepted.declaration == material.declaration
            ):
                verified.append(accepted)
                continue
            log_evidence_neutralized("evidence_mismatch", unit="source", level=logging.WARNING)
            verified.append(accepted.model_copy(update={"declaration": MemoryEvidenceDeclaration()}))
        return annotation.model_copy(update={"sources": tuple(verified)})

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
            return _decode_annotation(scope_id, artifact_ref, payload)
        except (ValueError, TypeError):
            # An unreadable declaration snapshot conveys no historical claim.
            # This does not catch SQL errors or actual Source resolution errors.
            log_evidence_neutralized("decode_failed", unit="declaration", level=logging.WARNING)
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


class _InvalidEvidenceSnapshot(ValueError):
    """A retained evidence snapshot is not a decodable record list."""


def _decode_annotation(scope_id: str, artifact_ref: ArtifactRef, payload: object) -> AtomicMemoryEvidenceAnnotation:
    """Decode one stored snapshot, raising when it is not a valid record list."""

    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise _InvalidEvidenceSnapshot
    values = json.loads(bytes(payload))
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        raise _InvalidEvidenceSnapshot
    sources = []
    for value in cast("list[dict[str, object]]", values):
        try:
            declaration = MemoryEvidenceDeclaration.model_validate(value.get("declaration"))
        except ValidationError:
            log_evidence_neutralized("invalid_declaration", unit="declaration", level=logging.WARNING)
            declaration = MemoryEvidenceDeclaration()
        sources.append(SourceEvidence.model_validate({**value, "declaration": declaration}))
    return AtomicMemoryEvidenceAnnotation(scope_id=scope_id, artifact_ref=artifact_ref, sources=tuple(sources))
