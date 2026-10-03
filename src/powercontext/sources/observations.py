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

"""Worker-owned Source Definition manifests and projected observations."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

import rfc8785
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    field_validator,
    model_serializer,
    model_validator,
)

from powercontext.errors import (
    InvalidSourceDefinitionError,
    InvalidSourceObservationError,
    InvalidSourceProjectionError,
)
from powercontext.limits import MAX_SOURCE_TYPE_LENGTH
from powercontext.sources.definitions import (
    SourceDefinition,
    SourceDefinitionRegistry,
    definition_memory_evidence,
)
from powercontext.sources.models import (
    DECLARATION_OWNED_FIELDS,
    MemoryEvidenceDeclaration,
    Source,
    SourceMaterialization,
    SourceProjectionKey,
)

_JSON_VALUE = TypeAdapter(JsonValue)
_JSON_SCHEMA_REFERENCE = re.compile(r"#/\$defs/([^\"\\]+)")


class SourceProjectionManifest(BaseModel):
    """Declarative schema for one worker-computed named projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: SourceProjectionKey
    schema_: dict[str, JsonValue] = Field(alias="schema")

    @field_validator("schema_")
    @classmethod
    def validate_schema_references(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _reject_remote_schema_references(value)
        return value


class SourceDefinitionManifest(BaseModel):
    """Immutable declarative identity registered by a remote worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str
    fingerprint: str
    source_schema: dict[str, JsonValue]
    projections: tuple[SourceProjectionManifest, ...] = ()
    memory_evidence: MemoryEvidenceDeclaration = MemoryEvidenceDeclaration()

    @field_validator("name", "version")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value or value.strip() != value or len(value) > MAX_SOURCE_TYPE_LENGTH:
            raise ValueError("manifest identity must be a bounded non-empty trimmed string")  # noqa: TRY003
        return value

    @field_validator("projections")
    @classmethod
    def validate_projection_limit(
        cls,
        value: tuple[SourceProjectionManifest, ...],
    ) -> tuple[SourceProjectionManifest, ...]:
        if len(value) > 16:
            raise ValueError("manifest must not declare more than 16 projections")  # noqa: TRY003
        return value

    @field_validator("source_schema")
    @classmethod
    def validate_source_schema_references(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _reject_remote_schema_references(value)
        return value

    @field_validator("fingerprint")
    @classmethod
    def validate_fingerprint_shape(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != 71:
            raise ValueError("manifest fingerprint must use sha256:<hex>")  # noqa: TRY003
        try:
            int(value.removeprefix("sha256:"), 16)
        except ValueError as error:
            raise ValueError("manifest fingerprint must contain lowercase hexadecimal") from error  # noqa: TRY003
        if value != value.lower():
            raise ValueError("manifest fingerprint must contain lowercase hexadecimal")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> SourceDefinitionManifest:
        keys = tuple(projection.key for projection in self.projections)
        if len(set(keys)) != len(keys):
            raise ValueError("manifest projection keys must be unique")  # noqa: TRY003
        expected = _source_definition_fingerprint(
            name=self.name,
            version=self.version,
            source_schema=self.source_schema,
            projections=self.projections,
            memory_evidence=self.memory_evidence,
        )
        if self.fingerprint != expected:
            raise ValueError("manifest fingerprint does not match its declaration")  # noqa: TRY003
        return self

    @model_serializer(mode="wrap")
    def serialize_manifest(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Serialize the manifest in the canonical form its fingerprint covers.

        A manifest whose declaration is absent or neutral carries no ranking
        preference, so it must serialize without that field. Writing the neutral
        default would make the stored payload validate against a fingerprint that
        excludes it, which makes registration unreadable.
        """

        dumped = handler(self)
        if _declared_evidence(self.memory_evidence) is None:
            dumped.pop("memory_evidence", None)
        return dumped


class SourceProjectionValue(BaseModel):
    """One named projection computed by the worker that owns the Definition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: SourceProjectionKey
    value: JsonValue


class SourceObservation(Source):
    """Canonical captured observation stored without loading worker plugin code."""

    materialization: Literal[SourceMaterialization.CAPTURED] = SourceMaterialization.CAPTURED
    source_type: str
    definition_fingerprint: str
    payload: dict[str, JsonValue]
    projections: tuple[SourceProjectionValue, ...] = ()

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, value: str) -> str:
        if not value or value.strip() != value or len(value) > MAX_SOURCE_TYPE_LENGTH:
            raise ValueError("source_type must be a bounded non-empty trimmed string")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def validate_envelope_identity(self) -> SourceObservation:
        expected = {
            "name": self.name,
            "definition_version": self.definition_version,
            "materialization": self.materialization.value,
            "description": self.description,
        }
        for field, value in expected.items():
            if self.payload.get(field) != value:
                raise ValueError(f"projected Source payload {field} does not match its envelope")  # noqa: TRY003
        keys = tuple(projection.key for projection in self.projections)
        if len(set(keys)) != len(keys):
            raise ValueError("projected Source projection keys must be unique")  # noqa: TRY003
        return self

    def projection(self, key: SourceProjectionKey, /) -> JsonValue:
        for projection in self.projections:
            if projection.key == key:
                return projection.value
        raise InvalidSourceProjectionError(key.name, "key", "was not supplied by the worker")


def _input_contract_schema(source_class: type[Source]) -> dict[str, JsonValue]:
    """Return a Source class's input contract, without Definition-owned metadata.

    The declaration is stamped from the Definition rather than carried by the
    captured value, so it is not part of what a worker's payload has to satisfy.
    Leaving it in would change the schema of every Source class that inherits it,
    and the manifest fingerprint covers that schema, so a Definition registered
    before the field existed would no longer match the one a worker emits. The
    orphaned definitions the removed property referenced are dropped with it,
    because an unused ``$defs`` entry still changes the hashed document.
    """

    schema = _json_object(source_class.model_json_schema())
    _remove_inherited_declaration(schema, _declaration_signature())
    _drop_unreferenced_definitions(schema)
    return schema


def _declaration_signature() -> JsonValue:
    """Return the schema Pydantic emits for the declaration a Source inherits."""

    return _json_object(Source.model_json_schema()["properties"])["memory_evidence"]


def _remove_inherited_declaration(node: JsonValue, signature: JsonValue) -> None:
    """Strip the inherited declaration from every object schema that carries it.

    Recursive and nested Sources keep their fields in a local definition rather
    than at the root, so the walk covers the whole document. The field is matched
    by the schema a Source inherits rather than by its name alone, because an
    ordinary captured model may declare a field of its own with the same name and
    a different meaning, and that field belongs to the captured contract.
    """

    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict) and properties.get("memory_evidence") == signature:
            del properties["memory_evidence"]
            required = node.get("required")
            if isinstance(required, list):
                node["required"] = [field for field in required if field != "memory_evidence"]
        for value in node.values():
            _remove_inherited_declaration(value, signature)
    elif isinstance(node, list):
        for item in node:
            _remove_inherited_declaration(item, signature)


def _drop_unreferenced_definitions(schema: dict[str, JsonValue]) -> None:
    """Remove ``$defs`` entries no longer referenced anywhere in the schema."""

    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return
    while True:
        # Reference discovery has to see the whole document: the properties that
        # point at a definition live outside ``$defs``.
        referenced = set(_JSON_SCHEMA_REFERENCE.findall(json.dumps(schema)))
        unreferenced = [name for name in definitions if name not in referenced]
        if not unreferenced:
            break
        for name in unreferenced:
            del definitions[name]
    if not definitions:
        del schema["$defs"]


def manifest_for_definition(definition: SourceDefinition[Any, Any, Any], /) -> SourceDefinitionManifest:
    """Build the immutable declaration transported by a remote worker."""

    source_schema = _input_contract_schema(definition.source_class)
    declared = definition_memory_evidence(definition)
    projections = tuple(
        SourceProjectionManifest(
            key=SourceProjectionKey(name=projection.name, version=projection.version),
            schema=projection.output_class.model_json_schema(),
        )
        for projection in definition.projections
    )
    # A neutral declaration is carried by omission, never as an explicit field. The
    # fingerprint excludes it, so setting it would make this manifest serialize
    # differently from the same manifest decoded from its stored form, and the
    # round trip a remote worker checks would no longer agree.
    return SourceDefinitionManifest(
        name=definition.name,
        version=definition.version,
        fingerprint=_source_definition_fingerprint(
            name=definition.name,
            version=definition.version,
            source_schema=source_schema,
            projections=projections,
            memory_evidence=declared,
        ),
        source_schema=source_schema,
        projections=projections,
        **({} if _declared_evidence(declared) is None else {"memory_evidence": declared}),
    )


def project_source_for_transport(
    registry: SourceDefinitionRegistry,
    source: Source,
    /,
) -> SourceObservation:
    """Execute one worker-owned Definition and serialize its durable result."""

    definition = registry.definition_for_source(source)
    if source.materialization is not SourceMaterialization.CAPTURED:
        raise InvalidSourceObservationError(
            "materialization",
            "remote Source observations must retain their canonical value",
        )
    manifest = manifest_for_definition(definition)
    # The captured payload has to satisfy the contract the manifest advertises, and
    # Definition-owned metadata is not part of it. Carrying it here would make a
    # generated observation fail its own schema, and would change the payload of an
    # unchanged Source, so replaying a pre-upgrade observation would conflict.
    payload = _json_object({
        key: value for key, value in source.model_dump(mode="json").items() if key not in DECLARATION_OWNED_FIELDS
    })
    projections = tuple(
        SourceProjectionValue(key=key, value=registry.project(source, key)) for key in registry.projection_keys(source)
    )
    return SourceObservation(
        name=source.name,
        definition_version=source.definition_version,
        materialization=SourceMaterialization.CAPTURED,
        description=source.description,
        source_type=definition.name,
        definition_fingerprint=manifest.fingerprint,
        memory_evidence=manifest.memory_evidence,
        payload=payload,
        projections=projections,
    )


def _declared_evidence(
    memory_evidence: MemoryEvidenceDeclaration | None,
) -> dict[str, JsonValue] | None:
    """Return the evidence fields a fingerprint covers, or ``None`` when neutral.

    The neutral declaration is Definition-owned metadata that carries no ranking
    preference, so it must not contribute to a Definition's content-addressed
    identity. Folding it in would change the fingerprint of every Definition that
    already registered one, and the immutable ``(name, version)`` registration
    would then reject the re-registration every remote worker performs on each
    run. A declaration that actually asserts an authority or verification level
    still changes the fingerprint, because it does change what the Definition
    attests.
    """

    if memory_evidence is None or memory_evidence == MemoryEvidenceDeclaration():
        return None
    return {"memory_evidence": memory_evidence.model_dump(mode="json")}


def _source_definition_fingerprint(
    *,
    name: str,
    version: str,
    source_schema: dict[str, JsonValue],
    projections: tuple[SourceProjectionManifest, ...],
    memory_evidence: MemoryEvidenceDeclaration | None,
) -> str:
    declaration = {
        "name": name,
        "version": version,
        "source_schema": source_schema,
        "projections": [projection.model_dump(mode="json", by_alias=True) for projection in projections],
        **(_declared_evidence(memory_evidence) or {}),
    }
    encoded = rfc8785.dumps(_JSON_VALUE.validate_python(declaration))
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _json_object(value: object) -> dict[str, JsonValue]:
    validated = _JSON_VALUE.validate_python(value)
    if not isinstance(validated, dict):
        raise InvalidSourceDefinitionError(type(value), "schema", "must be a JSON object")
    return validated


def _reject_remote_schema_references(schema: JsonValue) -> None:
    pending = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in {"$ref", "$dynamicRef"} and isinstance(nested, str) and not nested.startswith("#"):
                    raise ValueError("remote schema references are not allowed")  # noqa: TRY003
                pending.append(nested)
        elif isinstance(value, list):
            pending.extend(value)


__all__ = [
    "SourceDefinitionManifest",
    "SourceObservation",
    "SourceProjectionManifest",
    "SourceProjectionValue",
    "manifest_for_definition",
    "project_source_for_transport",
]
