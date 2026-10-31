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

import logging
from collections.abc import AsyncGenerator, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal, cast

import rfc8785
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import AliasChoices, AliasGenerator, BaseModel, ConfigDict, JsonValue, TypeAdapter
from referencing import Registry
from referencing.exceptions import Unresolvable
from sqlalchemy import func, insert, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncTransaction

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
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_BINDING_STATES_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
    SOURCES_TABLE,
)
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
from powercontext.sources.evidence import log_evidence_neutralized

_AnySourceAdapter = SourceAdapter[Any, Any, Any]
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
SOURCE_PROCESSING_BINDINGS = (
    SOURCE_WINDOW_TRIGGER_NAME,
    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
    EXPERIENCE_INCUBATION_CURSOR_NAME,
    PROFILE_SOURCE_WINDOW_BINDING,
)
_SOURCE_LOOKUP_KEYS_PER_STATEMENT = 400  # Two bind values per key, including SQLite's 999-variable builds.
_VALIDATORS: dict[str, Any] = {}


def _source_validator(manifest: SourceDefinitionManifest) -> Any:
    """Compile a retained schema once per immutable manifest fingerprint."""

    validator = _VALIDATORS.get(manifest.fingerprint)
    if validator is None:
        schema = manifest.source_schema
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, registry=Registry())
        if len(_VALIDATORS) < 128:
            _VALIDATORS[manifest.fingerprint] = validator
    return validator


class _StoredSourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    encoding: Literal["powercontext-source-v1"]
    representation: Literal["native", "observation"]
    value: dict[str, JsonValue]
    # Server-owned header permits historical reads without adapter code. Never hash it.
    source: Source | None = None
    source_keys: dict[str, str] | None = None


class StoredSource(BaseModel):
    """A decoded Source and its per-scope journal position."""

    ref: SourceRef
    value: Source
    journal_position: int


@dataclass(frozen=True, slots=True)
class _SourceReadSet:
    connection: AsyncConnection
    transaction: AsyncTransaction | None
    nested_transaction: AsyncTransaction | None
    scope_id: str
    rows: Mapping[tuple[str, str], StoredSource]

    def find(self, connection: AsyncConnection, scope_id: str, ref: SourceRef) -> StoredSource | None:
        if (
            connection is self.connection
            and self.transaction is not None
            and connection.get_transaction() is self.transaction
            and connection.get_nested_transaction() is self.nested_transaction
            and scope_id == self.scope_id
        ):
            return self.rows.get((ref.source_type, ref.source_id))
        return None


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

        return (await self.add_many_with_status(connection, scope_id, (source,)))[0]

    async def add_many_with_status(
        self,
        connection: AsyncConnection,
        scope_id: str,
        sources: Sequence[Source],
        /,
    ) -> tuple[tuple[StoredSource, bool], ...]:
        """Classify one ordered Source batch and reserve positions for new identities together."""

        if not sources:
            return ()
        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        prepared = tuple((source, *self._encode(source)) for source in sources)

        position = await _lock_journal_head(connection, scope_id)
        existing = await self._existing_many(connection, scope_id, tuple(ref for _, ref, _ in prepared))
        results: list[tuple[StoredSource, bool]] = []
        new_rows = []
        new_sources: dict[tuple[str, str], tuple[SourceRef, Source]] = {}
        upgrades: dict[tuple[str, str], bytes] = {}
        for source, ref, payload in prepared:
            key = (ref.source_type, ref.source_id)
            stored = existing.get(key)
            if stored is not None:
                if (
                    isinstance(source, ContentSource)
                    and source.handoff_receipt
                    and isinstance(stored.value, ContentSource)
                    and not stored.value.handoff_receipt
                    and stored.value.model_copy(update={"handoff_receipt": True}) == source
                ):
                    stored = StoredSource(ref=ref, value=source, journal_position=stored.journal_position)
                    existing[key] = stored
                    upgrades[key] = payload
                elif stored.value != source:
                    raise StoredPayloadConflictError("source", (scope_id, ref))
                else:
                    await self._retain_native_header(connection, scope_id, ref, source)
                results.append((stored, False))
                continue
            position += 1
            stored = StoredSource(ref=ref, value=source, journal_position=position)
            existing[key] = stored
            new_sources[key] = (ref, source)
            new_rows.append({
                "scope_id": scope_id,
                "source_type": ref.source_type,
                "source_id": ref.source_id,
                "payload": payload,
                "journal_position": position,
            })
            results.append((stored, True))

        if new_rows:
            await connection.execute(
                update(SOURCE_JOURNAL_HEADS_TABLE)
                .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
                .values(position=position)
            )
            await connection.execute(insert(SOURCES_TABLE), new_rows)
            # Reuse only within this journal-locked batch; a rollback or later
            # registration must never inherit an earlier transaction's result.
            native_manifests: dict[tuple[str, str], SourceDefinitionManifest | None] = {}
            for ref, source in new_sources.values():
                await self._bind_memory_evidence(connection, scope_id, ref, source, native_manifests)
        for (source_type, source_id), payload in upgrades.items():
            await connection.execute(
                update(SOURCES_TABLE)
                .where(
                    SOURCES_TABLE.c.scope_id == scope_id,
                    SOURCES_TABLE.c.source_type == source_type,
                    SOURCES_TABLE.c.source_id == source_id,
                )
                .values(payload=payload)
            )
        if new_rows:
            intents = ArtifactProcessingIntentRepository()
            await intents.mark_dirty_many(connection, scope_id, SOURCE_PROCESSING_BINDINGS, increment=len(new_rows))
            await _mark_custom_bindings_dirty(connection, scope_id, intents, increment=len(new_rows))
        return tuple(results)

    def _encode(self, source: Source) -> tuple[SourceRef, bytes]:
        if isinstance(source, SourceObservation):
            ref = SourceRef(source_type=source.source_type, source_id=source.name)
            representation: Literal["native", "observation"] = "observation"
        else:
            definition = self._registry.definition_for_source(source)
            ref = SourceRef(source_type=definition.name, source_id=source.name)
            representation = "native"
        return ref, _dump_source(source, representation=representation, name=ref.source_type)

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
        return self.decode_source(row)

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

        decoded = await self._existing_many(connection, scope_id, tuple(ordered))
        for ref in ordered:
            if (ref.source_type, ref.source_id) not in decoded:
                raise RepositoryNotFoundError("source", (scope_id, ref))
        return tuple(decoded[(ref.source_type, ref.source_id)] for ref in ordered)

    async def _existing_many(
        self, connection: AsyncConnection, scope_id: str, refs: Sequence[SourceRef]
    ) -> dict[tuple[str, str], StoredSource]:
        keys = tuple(dict.fromkeys((ref.source_type, ref.source_id) for ref in refs))
        decoded: dict[tuple[str, str], StoredSource] = {}
        for offset in range(0, len(keys), _SOURCE_LOOKUP_KEYS_PER_STATEMENT):
            rows = (
                (
                    await connection.execute(
                        select(SOURCES_TABLE).where(
                            SOURCES_TABLE.c.scope_id == scope_id,
                            tuple_(SOURCES_TABLE.c.source_type, SOURCES_TABLE.c.source_id).in_(
                                keys[offset : offset + _SOURCE_LOOKUP_KEYS_PER_STATEMENT]
                            ),
                        )
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                try:
                    stored = self.decode_source(row)
                except RepositoryNotFoundError:
                    ref = SourceRef(source_type=str(row["source_type"]), source_id=str(row["source_id"]))
                    raise RepositoryNotFoundError("source", (scope_id, ref)) from None
                decoded[(stored.ref.source_type, stored.ref.source_id)] = stored
        return decoded

    async def preload(
        self,
        connection: AsyncConnection,
        scope_id: str,
        refs: Sequence[SourceRef],
        /,
        *,
        known: Sequence[StoredSource] = (),
    ) -> _SourceReadSet:
        """Load lineage evidence once; ``known`` must come from this transaction's reads or inserts."""
        rows = {(item.ref.source_type, item.ref.source_id): item for item in known}
        missing = tuple(ref for ref in refs if (ref.source_type, ref.source_id) not in rows)
        rows.update({
            (item.ref.source_type, item.ref.source_id): item
            for item in await self.get_many(connection, scope_id, missing)
        })
        return _SourceReadSet(
            connection, connection.get_transaction(), connection.get_nested_transaction(), scope_id, rows
        )

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
        by_ref: dict[tuple[str, str], Mapping[Any, Any]] = {}
        for offset in range(0, len(identities), _SOURCE_LOOKUP_KEYS_PER_STATEMENT):
            rows = (
                (
                    await connection.execute(
                        select(SOURCES_TABLE).where(
                            SOURCES_TABLE.c.scope_id == scope_id,
                            tuple_(SOURCES_TABLE.c.source_type, SOURCES_TABLE.c.source_id).in_(
                                identities[offset : offset + _SOURCE_LOOKUP_KEYS_PER_STATEMENT]
                            ),
                        )
                    )
                )
                .mappings()
                .all()
            )
            by_ref.update(((row["source_type"], row["source_id"]), row) for row in rows)
        materials: list[SourceEvidence] = []
        fingerprints: dict[tuple[str, str], str | None] = {}
        for ref in identities:
            reference = SourceRef(source_type=ref[0], source_id=ref[1])
            row = by_ref.get(ref)
            if row is None:
                raise RepositoryNotFoundError("source", (scope_id, reference))
            source, material, verifiable = await self._historical_material(connection, reference, row)
            if not verifiable:
                fingerprints[ref] = None  # Deliberately unverifiable, not a fingerprint mismatch.
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
    ) -> tuple[Source, dict[str, JsonValue], bool]:
        """Validate the stored representation and its retained schema, not a live model."""

        payload = stored_bytes(row["payload"], column="payload")
        try:
            envelope = load_model(_StoredSourcePayload, payload, kind="source-envelope", name=reference.source_type)
        except InvalidStoredPayloadError:
            return await self._historical_legacy_material(connection, reference, row, payload)
        material = envelope.value
        source = (
            await self._historical_canonical_source(connection, reference, row, material)
            if envelope.representation == "native" and envelope.source is None
            else None
        )
        if source is None:
            source = self._historical_source(reference, envelope)
        decoded = SourceRef(
            source_type=source.source_type if isinstance(source, SourceObservation) else reference.source_type,
            source_id=source.name,
        )
        if reference != decoded:
            raise IdentityMismatchError("source", reference, decoded)
        table = SOURCE_EVIDENCE_BINDINGS_TABLE
        binding = (
            await connection.execute(
                select(table.c.manifest_fingerprint, table.c.material_digest).where(
                    table.c.scope_id == row["scope_id"],
                    table.c.source_type == reference.source_type,
                    table.c.source_id == reference.source_id,
                    table.c.definition_name == reference.source_type,
                    table.c.definition_version == source.definition_version,
                )
            )
        ).one_or_none()
        manifest = await _find_evidence_manifest(connection, reference.source_type, source.definition_version)
        if manifest is not None and binding is not None and manifest.fingerprint == binding.manifest_fingerprint:
            try:
                value = source.payload if isinstance(source, SourceObservation) else material
                _source_validator(manifest).validate(value)
            except JsonSchemaValidationError as error:
                if envelope.representation == "native" and binding.material_digest == _source_material_digest_material(
                    material
                ):
                    # Older releases bound declarations without checking serialization
                    # compatibility. Exact accepted bytes remain readable, but neutral.
                    log_evidence_neutralized("schema_mismatch", unit="source", level=logging.DEBUG)
                    return source, material, False
                raise InvalidStoredPayloadError(
                    "source", reference.source_type, "payload does not match the historical schema"
                ) from error
            except (SchemaError, Unresolvable):
                self._validate_unbound_material(reference, envelope, source)
        else:
            self._validate_unbound_material(reference, envelope, source)
        return source, material, True

    async def _historical_legacy_material(
        self,
        connection: AsyncConnection,
        reference: SourceRef,
        row: Mapping[Any, Any],
        payload: bytes,
    ) -> tuple[Source, dict[str, JsonValue], bool]:
        """Read bare legacy material without guessing absent identity fields."""
        try:
            material = _JSON_OBJECT.validate_json(payload, strict=True)
        except ValueError as error:
            raise InvalidStoredPayloadError("source", reference.source_type, "payload is not JSON") from error
        source = await self._historical_canonical_source(connection, reference, row, material)
        if source is not None:
            return source, material, False
        try:
            self._registry.definition_for_name(reference.source_type)
        except SourceDefinitionNotFoundError:
            source = load_model(Source, payload, kind="source", name=reference.source_type)
            if not {"name", "definition_version", "materialization"} <= source.model_fields_set:
                raise InvalidStoredPayloadError(
                    "source", reference.source_type, "legacy native identity requires its registered decoder"
                ) from None
            if source.name != reference.source_id:
                raise IdentityMismatchError("source", reference.source_id, source.name) from None
        else:
            source = self.decode_source(row).value
        return source, material, True

    async def _historical_canonical_source(
        self,
        connection: AsyncConnection,
        reference: SourceRef,
        row: Mapping[Any, Any],
        material: dict[str, JsonValue],
    ) -> Source | None:
        """Recover evidence identity from canonical retained material, never a live alias."""

        if not {"name", "definition_version", "materialization"} <= material.keys():
            return None
        if material["name"] != reference.source_id:
            return None
        digest_material = material
        if reference.source_type == CONTENT_SOURCE_NAME:
            digest_material = {key: value for key, value in material.items() if key != "handoff_receipt"}
        material_digest = _source_material_digest_material(digest_material)
        table = SOURCE_EVIDENCE_BINDINGS_TABLE
        binding = (
            await connection.execute(
                select(table.c.definition_name, table.c.definition_version, table.c.material_digest).where(
                    table.c.scope_id == row["scope_id"],
                    table.c.source_type == reference.source_type,
                    table.c.source_id == reference.source_id,
                )
            )
        ).one_or_none()
        if binding is not None:
            if (
                binding.definition_name != reference.source_type
                or binding.material_digest != material_digest
                or binding.definition_version != material["definition_version"]
            ):
                return None
            version = binding.definition_version
        elif isinstance(material["definition_version"], str):
            # Canonical retained identity is explicit even when its historical
            # evidence binding predates the evidence tables. It stays neutral.
            version = material["definition_version"]
        else:
            return None
        try:
            definition = self._registry.definition_for_name(reference.source_type)
        except SourceDefinitionNotFoundError:
            definition = None
        if (
            definition is not None
            and definition.version == version
            and any(
                (
                    (
                        definition.source_class.model_fields[key].alias is not None
                        and definition.source_class.model_fields[key].alias != key
                    )
                    or (
                        definition.source_class.model_fields[key].serialization_alias is not None
                        and definition.source_class.model_fields[key].serialization_alias != key
                    )
                    or (
                        definition.source_class.model_fields[key].validation_alias is not None
                        and definition.source_class.model_fields[key].validation_alias != key
                    )
                )
                for key in ("definition_version", "materialization")
            )
        ):
            # The current exact-version codec is required to disambiguate retained identity keys.
            return None
        try:
            return Source.model_validate({
                "name": reference.source_id,
                "definition_version": version,
                "materialization": material["materialization"],
                "description": material.get("description"),
            })
        except (TypeError, ValueError):
            return None

    def _historical_source(self, reference: SourceRef, envelope: _StoredSourcePayload) -> Source:
        """Decode identity independently of retained material and today's business defaults."""

        material = envelope.value
        if envelope.representation == "observation":
            return load_model(SourceObservation, rfc8785.dumps(material), kind="source", name=reference.source_type)
        if envelope.source is not None:
            keys = envelope.source_keys or {key: key for key in ("name", "definition_version", "materialization")}
            for key, alias in keys.items():
                value = getattr(envelope.source, key)
                if alias not in material or material[alias] != value:
                    raise IdentityMismatchError("source-envelope", value, material.get(alias))
            return envelope.source
        # Legacy aliased rows need their registered decoder once; canonical rows do not.
        if {"name", "definition_version", "materialization"} <= material.keys() and material[
            "name"
        ] == reference.source_id:
            definition = None
            with suppress(SourceDefinitionNotFoundError):
                definition = self._registry.definition_for_name(reference.source_type)
            if definition is None or not _claims_identity_keys(definition.source_class):
                return load_model(Source, rfc8785.dumps(material), kind="source", name=reference.source_type)
        with suppress(SourceDefinitionNotFoundError):
            definition = self._registry.definition_for_name(reference.source_type)
            if any(
                (definition.source_class.model_fields[key].serialization_alias or key) != key
                for key in ("name", "definition_version", "materialization")
            ):
                source, _ = self._decode_envelope(reference.source_type, envelope)
                return source
        header = material
        with suppress(SourceDefinitionNotFoundError):
            definition = self._registry.definition_for_name(reference.source_type)
            header = _normalize_native_material(definition.source_class, material)
        if not {"name", "definition_version", "materialization"} <= header.keys():
            raise InvalidStoredPayloadError(
                "source", reference.source_type, "legacy native identity requires its registered decoder"
            )
        try:
            return load_model(Source, rfc8785.dumps(header), kind="source", name=reference.source_type)
        except InvalidStoredPayloadError:
            source, _ = self._decode_envelope(reference.source_type, envelope)
            return source

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
            # No configured Definition can perform the live-decoder cross-check.
            log_evidence_neutralized("definition_missing", unit="source", level=logging.DEBUG)
            return
        if _claims_identity_keys(definition.source_class):
            # The retained canonical identity/header is authoritative; a live business alias may claim it.
            return
        if envelope.source is None and any(
            (definition.source_class.model_fields[key].serialization_alias or key) != key
            for key in ("name", "definition_version", "materialization")
        ):
            # Today's identity aliases cannot validate an older unbound representation.
            return
        if definition.version == source.definition_version:
            self._decode_envelope(reference.source_type, envelope)

    @staticmethod
    async def _bound_evidence(
        connection: AsyncConnection,
        scope_id: str,
        materials: tuple[SourceEvidence, ...],
        fingerprints: Mapping[tuple[str, str], str | None],
    ) -> tuple[SourceEvidence, ...]:
        if not materials:
            return ()
        table = SOURCE_EVIDENCE_BINDINGS_TABLE
        identities = tuple((value.source_ref.source_type, value.source_ref.source_id) for value in materials)
        bindings: dict[tuple[str, str], Mapping[Any, Any]] = {}
        for offset in range(0, len(identities), _SOURCE_LOOKUP_KEYS_PER_STATEMENT):
            rows = (
                (
                    await connection.execute(
                        select(table).where(
                            table.c.scope_id == scope_id,
                            tuple_(table.c.source_type, table.c.source_id).in_(
                                identities[offset : offset + _SOURCE_LOOKUP_KEYS_PER_STATEMENT]
                            ),
                        )
                    )
                )
                .mappings()
                .all()
            )
            bindings.update(((row["source_type"], row["source_id"]), row) for row in rows)
        manifests: dict[tuple[str, str], SourceDefinitionManifest | None] = {}
        evidence: list[SourceEvidence] = []
        for value in materials:
            identity = (value.source_ref.source_type, value.source_ref.source_id)
            if identity in fingerprints and fingerprints[identity] is None:
                evidence.append(value)
                continue
            binding = bindings.get(identity)
            if binding is None:
                # Captured before evidence bindings existed; no declaration was accepted.
                log_evidence_neutralized("legacy_unverifiable", unit="source", level=logging.DEBUG)
                evidence.append(value)
                continue
            if (
                binding["material_digest"] != value.material_digest
                or binding["definition_name"] != value.definition_name
                or binding["definition_version"] != value.definition_version
                or (
                    binding["manifest_fingerprint"] is not None and not isinstance(binding["manifest_fingerprint"], str)
                )
            ):
                log_evidence_neutralized("digest_mismatch", unit="source", level=logging.WARNING)
                evidence.append(value)
                continue
            if binding["manifest_fingerprint"] is None:
                log_evidence_neutralized("no_manifest", unit="manifest")
                evidence.append(value)
                continue
            definition_identity = (value.definition_name, value.definition_version)
            if definition_identity not in manifests:
                manifests[definition_identity] = await _find_evidence_manifest(connection, *definition_identity)
            manifest = manifests[definition_identity]
            if manifest is None:
                log_evidence_neutralized("no_manifest", unit="manifest")
                evidence.append(value)
                continue
            evidence.append(
                _apply_bound_declaration(value, binding, manifest, fingerprints.get(identity, manifest.fingerprint))
            )
        return tuple(evidence)

    async def _bind_memory_evidence(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ref: SourceRef,
        source: Source,
        native_manifests: dict[tuple[str, str], SourceDefinitionManifest | None],
    ) -> None:
        """Freeze registration evidence only for a newly accepted Source row."""

        if isinstance(source, SourceObservation):
            manifest = await _find_evidence_manifest(connection, ref.source_type, source.definition_version)
            if manifest is not None and manifest.fingerprint != source.definition_fingerprint:
                log_evidence_neutralized("digest_mismatch", unit="manifest", level=logging.WARNING)
                manifest = None
        else:
            definition = self._registry.definition_for_source(source)
            identity = (definition.name, definition.version)
            if identity not in native_manifests:
                native_manifests[identity] = await SourceDefinitionManifestRepository().register_native(
                    connection, definition
                )
            manifest = native_manifests[identity]
            if manifest is not None:
                # A native decoder can accept representations its generated schema
                # cannot describe. Such captures are valid, but cannot attest evidence.
                try:
                    _source_validator(manifest).validate(source.model_dump(mode="json", by_alias=True))
                except (JsonSchemaValidationError, SchemaError, Unresolvable):
                    log_evidence_neutralized("schema_mismatch", unit="source", level=logging.DEBUG)
                    manifest = None
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
        source_types: tuple[str, ...] | None = None,
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
        if source_types is not None:
            predicates.append(SOURCES_TABLE.c.source_type.in_(source_types))
        statement = select(SOURCES_TABLE).where(*predicates).order_by(SOURCES_TABLE.c.journal_position)
        if limit is not None:
            statement = statement.limit(limit)
        async with connection.stream(statement.execution_options(yield_per=1)) as result:
            async for row in result.mappings():
                yield self.decode_source(row)

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
        return tuple(self.decode_source(row) for row in rows)

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

    def decode_source(self, row: Mapping[Any, Any], /) -> StoredSource:
        """Decode and validate one stored Source using the registered adapter contract."""

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

    async def _retain_native_header(
        self, connection: AsyncConnection, scope_id: str, ref: SourceRef, source: Source
    ) -> None:
        """Enrich only an identical replay's unreconstructible legacy header.

        The retained value, binding, manifest, digest and journal position never change.
        This grants no authority and does not publish another processing intent.
        """

        if isinstance(source, SourceObservation):
            return
        row = await self._find_row(connection, scope_id, ref)
        if row is None:
            # Same-batch duplicates have no retained row until the bulk insert.
            return
        payload = stored_bytes(row["payload"], column="payload")
        try:
            envelope = load_model(_StoredSourcePayload, payload, kind="source-envelope", name=ref.source_type)
        except InvalidStoredPayloadError:
            envelope = _StoredSourcePayload(
                encoding="powercontext-source-v1",
                representation="native",
                value=_JSON_OBJECT.validate_json(payload, strict=True),
            )
        if envelope.source is not None:
            return
        header = _source_header(source)
        with suppress(InvalidStoredPayloadError):
            if load_model(Source, rfc8785.dumps(envelope.value), kind="source", name=ref.source_type) == header:
                return
        enriched = envelope.model_copy(update={"source": header, "source_keys": _source_keys(source, envelope.value)})
        await connection.execute(
            update(SOURCES_TABLE)
            .where(
                SOURCES_TABLE.c.scope_id == scope_id,
                SOURCES_TABLE.c.source_type == ref.source_type,
                SOURCES_TABLE.c.source_id == ref.source_id,
            )
            .values(payload=rfc8785.dumps(enriched.model_dump(mode="json", exclude_none=True)))
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
            rfc8785.dumps(_normalize_native_material(definition.source_class, envelope.value)),
            kind="source",
            name=source_type,
        )
        self._registry.definition_for_source(source)
        if envelope.source is not None:
            for key in ("name", "definition_version", "materialization"):
                if getattr(envelope.source, key) != getattr(source, key):
                    raise IdentityMismatchError("source-envelope", envelope.source, _source_header(source))
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
            try:
                material = _JSON_OBJECT.validate_json(payload, strict=True)
            except ValueError as error:
                raise InvalidStoredPayloadError("source", source_type, "payload is not JSON") from error
            source = load_model(
                definition.source_class,
                rfc8785.dumps(_normalize_native_material(definition.source_class, material)),
                kind="source",
                name=source_type,
            )
            self._registry.definition_for_source(source)
            decoded = SourceRef(source_type=definition.name, source_id=source.name)
        return cast(Source, source), decoded


def _apply_bound_declaration(
    value: SourceEvidence,
    binding: Mapping[Any, Any],
    manifest: SourceDefinitionManifest,
    expected_fingerprint: str | None,
) -> SourceEvidence:
    """Apply only a declaration bound to the verified historical manifest."""

    if manifest.fingerprint != binding["manifest_fingerprint"] or expected_fingerprint != manifest.fingerprint:
        log_evidence_neutralized("digest_mismatch", unit="manifest", level=logging.WARNING)
        return value
    try:
        declaration = MemoryEvidenceDeclaration.model_validate_json(
            stored_bytes(binding["declaration"], column="declaration")
        )
    except (InvalidStoredColumnError, TypeError, ValueError):
        log_evidence_neutralized("decode_failed", unit="declaration", level=logging.WARNING)
        declaration = MemoryEvidenceDeclaration()
    return value.model_copy(
        update={
            "manifest_fingerprint": manifest.fingerprint,
            "declaration": declaration,
        }
    )


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


def _source_header(source: Source) -> Source:
    return Source(**{key: getattr(source, key) for key in Source.model_fields})


def _source_keys(source: Source, material: dict[str, JsonValue] | None = None) -> dict[str, str]:
    """Retain the exact serialized spelling used for each native identity."""
    keys: dict[str, str] = {}
    for key in ("name", "definition_version", "materialization"):
        field = type(source).model_fields[key]
        preferred = field.serialization_alias or key
        candidates = [preferred]
        if isinstance(field.validation_alias, str):
            candidates.append(field.validation_alias)
        elif isinstance(field.validation_alias, AliasChoices):
            candidates.extend(choice for choice in field.validation_alias.choices if isinstance(choice, str))
        if field.alias is not None:
            candidates.append(field.alias)
        generator = type(source).model_config.get("alias_generator")
        if isinstance(generator, AliasGenerator):
            generator = generator.serialization_alias or generator.alias
        if generator is not None:
            candidates.append(generator(key))
        if material is not None:
            keys[key] = next(
                (alias for alias in dict.fromkeys(candidates) if material.get(alias) == getattr(source, key)),
                preferred,
            )
        else:
            keys[key] = preferred
    return keys


def _claims_identity_keys(source_class: type[Source]) -> bool:
    """Report whether a business field serializes to a canonical identity spelling.

    A headerless envelope cannot distinguish an identity value from a business
    value with that name. When the registered model claims the spelling for a
    different field, only its declared aliases are authoritative.
    """

    return any(
        name not in Source.model_fields and key in (field.alias, field.validation_alias, field.serialization_alias)
        for name, field in source_class.model_fields.items()
        for key in Source.model_fields
    )


def _normalize_native_material(source_class: type[Source], material: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Read pre-canonical inherited envelope aliases, never business-field aliases.

    Derive spellings from the registered generator, not a version/key allowlist.
    Explicit adapter aliases and conflicting identities retain strict validation.
    """

    generator = source_class.model_config.get("alias_generator")
    if isinstance(generator, AliasGenerator):
        generator = generator.serialization_alias or generator.alias
    if generator is None:
        return material
    normalized = dict(material)
    for key in Source.model_fields:
        field = source_class.model_fields[key]
        if field.validation_alias != key and field.alias_priority != 1:
            continue
        alias = generator(key)
        if any(
            field_name != key and alias in (field.alias, field.validation_alias, field.serialization_alias)
            for field_name, field in source_class.model_fields.items()
        ):
            continue
        if alias != key and alias in normalized:
            if key in normalized and normalized[key] != normalized[alias]:
                raise IdentityMismatchError("source", normalized[key], normalized[alias])
            normalized[key] = normalized.pop(alias)
    return normalized


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
            source=None if representation == "observation" else _source_header(source),
            source_keys=None if representation == "observation" else _source_keys(source),
        )
        if envelope.source_keys is not None:
            for key, alias in envelope.source_keys.items():
                if alias not in envelope.value:
                    raise InvalidStoredPayloadError("source", name, f"serialized native identity omits {key!r}")
                if envelope.value[alias] != getattr(source, key):
                    raise InvalidStoredPayloadError("source", name, f"serialized native identity changes {key!r}")
        return rfc8785.dumps(envelope.model_dump(mode="json", exclude_none=True))
    except (TypeError, ValueError) as error:
        raise InvalidStoredPayloadError("source", name, "value is not JSON serializable") from error


async def _lock_journal_head(connection: AsyncConnection, scope_id: str) -> int:
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
    return position


def _require_identity(field: str, value: object, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidRepositoryArgumentError(field, "must be a non-empty trimmed string")
    if len(value) > maximum:
        raise InvalidRepositoryArgumentError(field, f"must not exceed {maximum} characters")


async def _mark_custom_bindings_dirty(
    connection: AsyncConnection,
    scope_id: str,
    intents: ArtifactProcessingIntentRepository,
    *,
    increment: int,
) -> None:
    plugins = (
        await connection.execute(
            select(
                ARTIFACT_PROCESSING_BINDING_STATES_TABLE.c.binding_name,
                ARTIFACT_PROCESSING_BINDING_STATES_TABLE.c.plugin_spec[("manifest", "scope_ids")].label("scope_ids"),
            ).where(ARTIFACT_PROCESSING_BINDING_STATES_TABLE.c.plugin_spec.is_not(None))
        )
    ).all()
    bindings = tuple(
        plugin.binding_name for plugin in plugins if plugin.scope_ids is None or scope_id in plugin.scope_ids
    )
    await intents.mark_dirty_many(connection, scope_id, bindings, increment=increment)
