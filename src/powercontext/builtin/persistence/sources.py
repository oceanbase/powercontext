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

"""Relational Source repository routed by actual adapters."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable, Mapping, Sequence
from contextlib import suppress
from hashlib import sha256
from typing import Any, Literal, cast

import rfc8785
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter
from referencing import Registry
from referencing.exceptions import Unresolvable
from sqlalchemy import func, insert, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.experience import EXPERIENCE_INCUBATION_CURSOR_NAME
from powercontext.builtin.artifacts.profile.models import PROFILE_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.persistence.atomic_memory_evidence_schema import SOURCE_EVIDENCE_BINDINGS_TABLE
from powercontext.builtin.persistence.codec import load_model, stored_bytes
from powercontext.builtin.persistence.errors import (
    IdentityMismatchError,
    InvalidRepositoryArgumentError,
    InvalidStoredColumnError,
    InvalidStoredPayloadError,
    RepositoryNotFoundError,
    StoredPayloadConflictError,
)
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.source_definitions import SourceDefinitionManifestRepository
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE, SOURCES_TABLE
from powercontext.builtin.sources.content import CONTENT_SOURCE_NAME, ContentSource
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME
from powercontext.errors import SourceDefinitionNotFoundError
from powercontext.limits import MAX_SCOPE_ID_LENGTH
from powercontext.sources import (
    MemoryEvidenceDeclaration,
    Source,
    SourceAdapter,
    SourceDefinitionManifest,
    SourceDefinitionRegistry,
    SourceEvidence,
    SourceObservation,
    SourceRef,
)

_AnySourceAdapter = SourceAdapter[Any, Any, Any]
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
SOURCE_PROCESSING_BINDINGS = (
    SOURCE_WINDOW_TRIGGER_NAME,
    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
    EXPERIENCE_INCUBATION_CURSOR_NAME,
    PROFILE_SOURCE_WINDOW_BINDING,
)


class _StoredSourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    encoding: Literal["powercontext-source-v1"]
    representation: Literal["native", "observation"]
    value: dict[str, JsonValue]


class StoredSource(BaseModel):
    """A decoded Source and its per-scope journal position."""

    ref: SourceRef
    value: Source
    journal_position: int


class SourceRepository:
    """Persist Sources using the Runtime's fixed Source Definition registry."""

    def __init__(
        self,
        definitions: SourceDefinitionRegistry | Iterable[_AnySourceAdapter],
        /,
    ) -> None:
        self._registry = (
            definitions
            if isinstance(definitions, SourceDefinitionRegistry)
            else SourceDefinitionRegistry.from_adapters(definitions)
        )

    async def add(
        self,
        connection: AsyncConnection,
        scope_id: str,
        source: Source,
        /,
    ) -> StoredSource:
        """Add one stable Source or return an identical existing capture."""

        stored, _created = await self.add_with_status(connection, scope_id, source)
        return stored

    async def add_with_status(
        self,
        connection: AsyncConnection,
        scope_id: str,
        source: Source,
        /,
    ) -> tuple[StoredSource, bool]:
        """Add one Source and report whether this call inserted its journal row."""

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        if isinstance(source, SourceObservation):
            ref = SourceRef(source_type=source.source_type, source_id=source.name)
            representation: Literal["native", "observation"] = "observation"
        else:
            definition = self._registry.definition_for_source(source)
            ref = SourceRef(source_type=definition.name, source_id=source.name)
            representation = "native"
        payload = _dump_source(source, representation=representation, name=ref.source_type)
        await _lock_journal_head(connection, scope_id)
        existing = await self._find_row(connection, scope_id, ref)
        if existing is not None:
            stored = self._decode_row(existing)
            # A trusted acknowledgement may replay a pre-provenance receipt.
            # Upgrade only its server-owned attestation, never its content or position.
            if (
                isinstance(source, ContentSource)
                and source.handoff_receipt
                and isinstance(stored.value, ContentSource)
                and not stored.value.handoff_receipt
                and stored.value.model_copy(update={"handoff_receipt": True}) == source
            ):
                await connection.execute(
                    update(SOURCES_TABLE)
                    .where(
                        SOURCES_TABLE.c.scope_id == scope_id,
                        SOURCES_TABLE.c.source_type == ref.source_type,
                        SOURCES_TABLE.c.source_id == ref.source_id,
                    )
                    .values(payload=payload)
                )
                return StoredSource(ref=ref, value=source, journal_position=stored.journal_position), False
            if stored.value != source:
                raise StoredPayloadConflictError("source", (scope_id, ref))
            return stored, False

        position = await _next_journal_position(connection, scope_id)
        try:
            await connection.execute(
                insert(SOURCES_TABLE).values(
                    scope_id=scope_id,
                    source_type=ref.source_type,
                    source_id=ref.source_id,
                    payload=payload,
                    journal_position=position,
                )
            )
        except IntegrityError:
            # A concurrent transaction may have committed the same stable
            # Source identity after our initial read. Only an identical
            # canonical value is an idempotent success.
            existing = await self._find_row(connection, scope_id, ref)
            if existing is None:
                raise
            stored = self._decode_row(existing)
            if stored.value != source:
                raise StoredPayloadConflictError("source", (scope_id, ref)) from None
            return stored, False
        await self._bind_memory_evidence(connection, scope_id, ref, source)
        # Centralized here so capture, record projection and import all publish
        # discoverable input atomically. Replayed Source identities do not dirty
        # a binding again. Disabled processors simply retain ordinary dirty.
        intents = ArtifactProcessingIntentRepository()
        for binding_name in SOURCE_PROCESSING_BINDINGS:
            await intents.mark_dirty(connection, scope_id, binding_name)
        return StoredSource(ref=ref, value=source, journal_position=position), True

    async def get(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ref: SourceRef,
        /,
    ) -> StoredSource:
        """Load and validate one Source by its complete stable identity."""

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        row = await self._find_row(connection, scope_id, ref)
        if row is None:
            raise RepositoryNotFoundError("source", (scope_id, ref))
        return self._decode_row(row)

    async def get_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        refs: Sequence[SourceRef],
        /,
    ) -> tuple[StoredSource, ...]:
        """Load exact Sources in first-requested order using one query."""

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        ordered: list[SourceRef] = []
        identities: set[tuple[str, str]] = set()
        for ref in refs:
            identity = (ref.source_type, ref.source_id)
            if identity not in identities:
                ordered.append(ref)
                identities.add(identity)
        if not ordered:
            return ()

        rows = (
            (
                await connection.execute(
                    select(SOURCES_TABLE).where(
                        SOURCES_TABLE.c.scope_id == scope_id,
                        tuple_(SOURCES_TABLE.c.source_type, SOURCES_TABLE.c.source_id).in_(tuple(identities)),
                    )
                )
            )
            .mappings()
            .all()
        )
        decoded: dict[tuple[str, str], StoredSource] = {}
        for row in rows:
            try:
                stored = self._decode_row(row)
            except RepositoryNotFoundError:
                ref = SourceRef(source_type=str(row["source_type"]), source_id=str(row["source_id"]))
                raise RepositoryNotFoundError("source", (scope_id, ref)) from None
            decoded[(stored.ref.source_type, stored.ref.source_id)] = stored
        for ref in ordered:
            if (ref.source_type, ref.source_id) not in decoded:
                raise RepositoryNotFoundError("source", (scope_id, ref))
        return tuple(decoded[(ref.source_type, ref.source_id)] for ref in ordered)

    async def evidence(
        self,
        connection: AsyncConnection,
        scope_id: str,
        refs: Sequence[SourceRef],
        /,
    ) -> tuple[SourceEvidence, ...]:
        """Read accepted declarations against exact stored Source material.

        Source reads retain their existing errors and never become generation
        eligibility checks. Missing or invalid declaration bindings are neutral;
        database failures propagate. No current registry declaration or provider
        value is substituted for historical registration facts.
        """

        return await self.historical_evidence(connection, scope_id, refs)

    async def historical_evidence(
        self, connection: AsyncConnection, scope_id: str, refs: Sequence[SourceRef], /
    ) -> tuple[SourceEvidence, ...]:
        """Check retained material without loading today's native Definition.

        Only the stored representation selects decoding. Canonical JSON retains
        all native fields, rather than round-tripping through a newer model.
        This does not supply a decoder or provider value for ordinary reads.
        """

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        identities = tuple(dict.fromkeys((ref.source_type, ref.source_id) for ref in refs))
        if not identities:
            return ()
        rows = (
            (
                await connection.execute(
                    select(SOURCES_TABLE).where(
                        SOURCES_TABLE.c.scope_id == scope_id,
                        tuple_(SOURCES_TABLE.c.source_type, SOURCES_TABLE.c.source_id).in_(identities),
                    )
                )
            )
            .mappings()
            .all()
        )
        by_ref = {(row["source_type"], row["source_id"]): row for row in rows}
        materials: list[SourceEvidence] = []
        fingerprints: dict[tuple[str, str], str] = {}
        for ref in identities:
            reference = SourceRef(source_type=ref[0], source_id=ref[1])
            row = by_ref.get(ref)
            if row is None:
                raise RepositoryNotFoundError("source", (scope_id, reference))
            source, material = await self._historical_material(connection, reference, row)
            if isinstance(source, SourceObservation):
                fingerprints[ref] = source.definition_fingerprint
            elif ref[0] == CONTENT_SOURCE_NAME:
                # The server-owned Content witness is the sole permitted material
                # mutation; this exclusion is the same one used at acceptance.
                internal = material.get("internal")
                if (
                    isinstance(internal, dict)
                    and internal.get("role") == "lineage_only"
                    and internal.get("operation") in {"artifact_create", "artifact_replace"}
                ):
                    continue
                material = {key: value for key, value in material.items() if key != "handoff_receipt"}
            materials.append(
                SourceEvidence(
                    source_ref=reference,
                    materialization=source.materialization,
                    material_digest=_source_material_digest_material(material),
                    definition_name=ref[0],
                    definition_version=source.definition_version,
                )
            )
        return await self._bound_evidence(connection, scope_id, tuple(materials), fingerprints)

    async def _historical_material(
        self, connection: AsyncConnection, reference: SourceRef, row: Mapping[Any, Any]
    ) -> tuple[Source, dict[str, JsonValue]]:
        """Validate the stored representation and its retained schema, not a live model."""

        payload = stored_bytes(row["payload"], column="payload")
        try:
            envelope = load_model(_StoredSourcePayload, payload, kind="source-envelope", name=reference.source_type)
        except InvalidStoredPayloadError:
            # Bare legacy material has no representation discriminator. Use
            # its registered decoder when available; otherwise retain opaque
            # canonical fields for neutral maintenance, not a guessed schema.
            try:
                self._registry.definition_for_name(reference.source_type)
            except SourceDefinitionNotFoundError:
                source = load_model(Source, payload, kind="source", name=reference.source_type)
                if source.name != reference.source_id:
                    raise IdentityMismatchError("source", reference.source_id, source.name) from None
                try:
                    material = _JSON_OBJECT.validate_json(payload, strict=True)
                except ValueError as error:
                    raise InvalidStoredPayloadError("source", reference.source_type, "payload is not JSON") from error
                return source, material
            source = self._decode_row(row).value
            try:
                material = _JSON_OBJECT.validate_json(payload, strict=True)
            except ValueError as error:
                raise InvalidStoredPayloadError("source", reference.source_type, "payload is not JSON") from error
            return source, material
        material = envelope.value
        model = SourceObservation if envelope.representation == "observation" else Source
        source = load_model(model, rfc8785.dumps(material), kind="source", name=reference.source_type)
        decoded = SourceRef(
            source_type=source.source_type if isinstance(source, SourceObservation) else reference.source_type,
            source_id=source.name,
        )
        if reference != decoded:
            raise IdentityMismatchError("source", reference, decoded)
        table = SOURCE_EVIDENCE_BINDINGS_TABLE
        fingerprint = await connection.scalar(
            select(table.c.manifest_fingerprint).where(
                table.c.scope_id == row["scope_id"],
                table.c.source_type == reference.source_type,
                table.c.source_id == reference.source_id,
                table.c.definition_name == reference.source_type,
                table.c.definition_version == source.definition_version,
            )
        )
        manifest = await _find_evidence_manifest(connection, reference.source_type, source.definition_version)
        if manifest is not None and manifest.fingerprint == fingerprint:
            try:
                Draft202012Validator.check_schema(manifest.source_schema)
                value = source.payload if isinstance(source, SourceObservation) else material
                Draft202012Validator(manifest.source_schema, registry=Registry()).validate(value)
            except JsonSchemaValidationError as error:
                raise InvalidStoredPayloadError(
                    "source", reference.source_type, "payload does not match the historical schema"
                ) from error
            except (SchemaError, Unresolvable):
                self._validate_unbound_material(reference, envelope, source)
        else:
            self._validate_unbound_material(reference, envelope, source)
        return source, material

    def _validate_unbound_material(self, reference: SourceRef, envelope: _StoredSourcePayload, source: Source) -> None:
        """Keep supported exact-read errors when no historical schema is available.

        A live decoder validates only material of its own version; it never
        contributes a declaration or reinterprets a valid historical binding.
        """

        if envelope.representation == "observation":
            return  # SourceObservation already validated its envelope and payload.
        try:
            definition = self._registry.definition_for_name(reference.source_type)
        except SourceDefinitionNotFoundError:
            return
        if definition.version == source.definition_version:
            self._decode_envelope(reference.source_type, envelope)

    @staticmethod
    async def _bound_evidence(
        connection: AsyncConnection,
        scope_id: str,
        materials: tuple[SourceEvidence, ...],
        fingerprints: Mapping[tuple[str, str], str],
    ) -> tuple[SourceEvidence, ...]:
        if not materials:
            return ()
        table = SOURCE_EVIDENCE_BINDINGS_TABLE
        rows = (
            (
                await connection.execute(
                    select(table).where(
                        table.c.scope_id == scope_id,
                        tuple_(table.c.source_type, table.c.source_id).in_(
                            tuple((value.source_ref.source_type, value.source_ref.source_id) for value in materials)
                        ),
                    )
                )
            )
            .mappings()
            .all()
        )
        bindings = {(row["source_type"], row["source_id"]): row for row in rows}
        manifests: dict[tuple[str, str], SourceDefinitionManifest | None] = {}
        evidence: list[SourceEvidence] = []
        for value in materials:
            identity = (value.source_ref.source_type, value.source_ref.source_id)
            binding = bindings.get(identity)
            if binding is None or (
                binding["material_digest"] != value.material_digest
                or binding["definition_name"] != value.definition_name
                or binding["definition_version"] != value.definition_version
                or not isinstance(binding["manifest_fingerprint"], str)
            ):
                evidence.append(value)
                continue
            definition_identity = (value.definition_name, value.definition_version)
            if definition_identity not in manifests:
                manifests[definition_identity] = await _find_evidence_manifest(connection, *definition_identity)
            manifest = manifests[definition_identity]
            if (
                manifest is None
                or manifest.fingerprint != binding["manifest_fingerprint"]
                or (identity in fingerprints and fingerprints[identity] != manifest.fingerprint)
            ):
                evidence.append(value)
                continue
            try:
                declaration = MemoryEvidenceDeclaration.model_validate_json(
                    stored_bytes(binding["declaration"], column="declaration")
                )
            except (InvalidStoredColumnError, TypeError, ValueError):
                declaration = MemoryEvidenceDeclaration()
            evidence.append(
                value.model_copy(
                    update={
                        "manifest_fingerprint": manifest.fingerprint,
                        "declaration": declaration,
                    }
                )
            )
        return tuple(evidence)

    async def _bind_memory_evidence(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ref: SourceRef,
        source: Source,
    ) -> None:
        """Freeze registration evidence only for a newly accepted Source row."""

        if isinstance(source, SourceObservation):
            manifest = await _find_evidence_manifest(connection, ref.source_type, source.definition_version)
            if manifest is not None and manifest.fingerprint != source.definition_fingerprint:
                manifest = None
        else:
            definition = self._registry.definition_for_source(source)
            manifest = await SourceDefinitionManifestRepository().register_native(connection, definition)
        declaration = (
            MemoryEvidenceDeclaration() if manifest is None else self._registry.memory_evidence_for_source(source)
        )
        await connection.execute(
            insert(SOURCE_EVIDENCE_BINDINGS_TABLE).values(
                scope_id=scope_id,
                source_type=ref.source_type,
                source_id=ref.source_id,
                material_digest=_source_material_digest(source),
                definition_name=ref.source_type,
                definition_version=source.definition_version,
                manifest_fingerprint=None if manifest is None else manifest.fingerprint,
                declaration=declaration.model_dump_json().encode("utf-8"),
            )
        )

    async def list(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        after: int = 0,
        through: int | None = None,
        limit: int | None = None,
        source_type: str | None = None,
    ) -> tuple[StoredSource, ...]:
        """Return a stable journal-ordered page for one scope."""

        return tuple([
            item
            async for item in self.iter_list(
                connection,
                scope_id,
                after=after,
                through=through,
                limit=limit,
                source_type=source_type,
            )
        ])

    async def iter_list(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        after: int = 0,
        through: int | None = None,
        limit: int | None = None,
        source_type: str | None = None,
    ) -> AsyncGenerator[StoredSource, None]:
        """Stream a stable journal-ordered page without decoding it eagerly."""

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        if after < 0:
            raise InvalidRepositoryArgumentError("after", "must be non-negative")
        if limit is not None and limit < 1:
            raise InvalidRepositoryArgumentError("limit", "must be positive")
        if through is not None and through < after:
            raise InvalidRepositoryArgumentError("through", "must not precede after")
        predicates = [
            SOURCES_TABLE.c.scope_id == scope_id,
            SOURCES_TABLE.c.journal_position > after,
        ]
        if through is not None:
            predicates.append(SOURCES_TABLE.c.journal_position <= through)
        if source_type is not None:
            predicates.append(SOURCES_TABLE.c.source_type == source_type)
        statement = select(SOURCES_TABLE).where(*predicates).order_by(SOURCES_TABLE.c.journal_position)
        if limit is not None:
            statement = statement.limit(limit)
        async with connection.stream(statement.execution_options(yield_per=1)) as result:
            async for row in result.mappings():
                yield self._decode_row(row)

    async def list_window(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        after: int,
        through: int,
    ) -> tuple[StoredSource, ...]:
        """Return the exact journal interval ``(after, through]``."""

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        if after < 0:
            raise InvalidRepositoryArgumentError("after", "must be non-negative")
        if through < after:
            raise InvalidRepositoryArgumentError("through", "must not precede after")
        rows = (
            (
                await connection.execute(
                    select(SOURCES_TABLE)
                    .where(
                        SOURCES_TABLE.c.scope_id == scope_id,
                        SOURCES_TABLE.c.journal_position > after,
                        SOURCES_TABLE.c.journal_position <= through,
                    )
                    .order_by(SOURCES_TABLE.c.journal_position)
                )
            )
            .mappings()
            .all()
        )
        return tuple(self._decode_row(row) for row in rows)

    async def journal_position(self, connection: AsyncConnection, scope_id: str, /) -> int:
        """Return the current high watermark for one scope."""

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        value = await connection.scalar(
            select(func.coalesce(func.max(SOURCES_TABLE.c.journal_position), 0)).where(
                SOURCES_TABLE.c.scope_id == scope_id
            )
        )
        if value is None:
            raise InvalidStoredColumnError("journal_position", "an integer")
        return int(value)

    async def read_value(self, source: Source, /) -> object:
        """Read one decoded Source through the same registered adapter route."""

        return await self._registry.read(source)

    def _definition_by_name(self, name: str) -> _AnySourceAdapter:
        try:
            return self._registry.definition_for_name(name)
        except SourceDefinitionNotFoundError:
            raise RepositoryNotFoundError("source-definition", name) from None

    async def _find_row(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ref: SourceRef,
    ) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(SOURCES_TABLE).where(
                        SOURCES_TABLE.c.scope_id == scope_id,
                        SOURCES_TABLE.c.source_type == ref.source_type,
                        SOURCES_TABLE.c.source_id == ref.source_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    def _decode_row(self, row: Mapping[Any, Any]) -> StoredSource:
        source_type = str(row["source_type"])
        source_id = str(row["source_id"])
        payload = stored_bytes(row["payload"], column="payload")
        try:
            envelope = load_model(
                _StoredSourcePayload,
                payload,
                kind="source-envelope",
                name=source_type,
            )
        except InvalidStoredPayloadError:
            source, decoded = self._decode_legacy_payload(source_type, payload)
        else:
            source, decoded = self._decode_envelope(source_type, envelope)
        indexed = SourceRef(source_type=source_type, source_id=source_id)
        if indexed != decoded:
            raise IdentityMismatchError("source", indexed, decoded)
        return StoredSource(
            ref=indexed,
            value=source,
            journal_position=int(row["journal_position"]),
        )

    def _decode_envelope(
        self,
        source_type: str,
        envelope: _StoredSourcePayload,
    ) -> tuple[Source, SourceRef]:
        payload = rfc8785.dumps(envelope.value)
        if envelope.representation == "observation":
            source = load_model(
                SourceObservation,
                payload,
                kind="projected-source",
                name=source_type,
            )
            return source, SourceRef(source_type=source.source_type, source_id=source.name)

        definition = self._definition_by_name(source_type)
        source = load_model(
            definition.source_class,
            payload,
            kind="source",
            name=source_type,
        )
        self._registry.definition_for_source(source)
        return source, SourceRef(source_type=definition.name, source_id=source.name)

    def _decode_legacy_payload(self, source_type: str, payload: bytes) -> tuple[Source, SourceRef]:
        try:
            definition = self._definition_by_name(source_type)
        except RepositoryNotFoundError:
            source = load_model(
                SourceObservation,
                payload,
                kind="projected-source",
                name=source_type,
            )
            decoded = SourceRef(source_type=source.source_type, source_id=source.name)
        else:
            source = load_model(
                definition.source_class,
                payload,
                kind="source",
                name=source_type,
            )
            self._registry.definition_for_source(source)
            decoded = SourceRef(source_type=definition.name, source_id=source.name)
        return cast(Source, source), decoded


def _source_material_digest(source: Source) -> str:
    # Preserve every material field except the narrow server witness upgrade.
    material = source.model_dump(mode="json", by_alias=True)
    if isinstance(source, ContentSource):
        material.pop("handoff_receipt", None)
    return f"sha256:{sha256(rfc8785.dumps(material)).hexdigest()}"


def _management_write_source(source: Source) -> bool:
    return (
        isinstance(source, ContentSource)
        and source.internal is not None
        and source.internal.role == "lineage_only"
        and source.internal.operation in {"artifact_create", "artifact_replace"}
    )


async def _find_evidence_manifest(
    connection: AsyncConnection, name: str, version: str
) -> SourceDefinitionManifest | None:
    try:
        return await SourceDefinitionManifestRepository().find(connection, name, version)
    except (IdentityMismatchError, InvalidStoredColumnError, InvalidStoredPayloadError):
        return None


def _source_material_digest_material(material: Mapping[str, JsonValue]) -> str:
    return f"sha256:{sha256(rfc8785.dumps(material)).hexdigest()}"


def _dump_source(
    source: Source,
    *,
    representation: Literal["native", "observation"],
    name: str,
) -> bytes:
    try:
        envelope = _StoredSourcePayload(
            encoding="powercontext-source-v1",
            representation=representation,
            value=_JSON_OBJECT.validate_python(source.model_dump(mode="json", by_alias=True)),
        )
        return rfc8785.dumps(envelope.model_dump(mode="json"))
    except (TypeError, ValueError) as error:
        raise InvalidStoredPayloadError("source", name, "value is not JSON serializable") from error


async def _next_journal_position(connection: AsyncConnection, scope_id: str) -> int:
    """Atomically reserve a monotonically increasing position for one scope."""

    result = await connection.execute(
        update(SOURCE_JOURNAL_HEADS_TABLE)
        .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
        .values(position=SOURCE_JOURNAL_HEADS_TABLE.c.position + 1)
    )
    if result.rowcount != 1:
        raise InvalidStoredColumnError("journal_position", "an initialized scope head")

    position = await _journal_head_position(connection, scope_id)
    if position < 1:
        raise InvalidStoredColumnError("journal_position", "a positive integer")
    return position


async def _lock_journal_head(connection: AsyncConnection, scope_id: str) -> None:
    """Create and lock one scope allocator before reading Source identity."""

    # This no-op UPDATE is intentionally the operation's first database access.
    # SQLite therefore acquires its write reservation before any read, while
    # MySQL/OceanBase locks an existing allocator row.
    await connection.execute(
        update(SOURCE_JOURNAL_HEADS_TABLE)
        .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
        .values(position=SOURCE_JOURNAL_HEADS_TABLE.c.position)
    )
    position = await connection.scalar(
        select(SOURCE_JOURNAL_HEADS_TABLE.c.position)
        .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
        .with_for_update()
    )
    if position is None:
        with suppress(IntegrityError):
            await connection.execute(
                insert(SOURCE_JOURNAL_HEADS_TABLE).values(
                    scope_id=scope_id,
                    position=0,
                )
            )
        position = await connection.scalar(
            select(SOURCE_JOURNAL_HEADS_TABLE.c.position)
            .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
            .with_for_update()
        )
    if not isinstance(position, int) or isinstance(position, bool) or position < 0:
        raise InvalidStoredColumnError("journal_position", "a non-negative scope head")


async def _journal_head_position(connection: AsyncConnection, scope_id: str) -> int:
    position = await connection.scalar(
        select(SOURCE_JOURNAL_HEADS_TABLE.c.position).where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
    )
    if not isinstance(position, int) or isinstance(position, bool):
        raise InvalidStoredColumnError("journal_position", "an integer")
    return position


def _require_identity(field: str, value: object, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidRepositoryArgumentError(field, "must be a non-empty trimmed string")
    if len(value) > maximum:
        raise InvalidRepositoryArgumentError(field, f"must not exceed {maximum} characters")
